"""Authorized activity-window intake, with one durable transition per key."""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest
import json
import math
import time
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from pymentor_analytics.adapters.auth.http import extract_bearer
from pymentor_analytics.contracts.auth_v1 import (
    AuthenticationError, AuthorizationError, FenceUnavailable, require_live_subject,
)
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.windows_v1 import (
    MAX_BATCH_BYTES, MAX_KEYS, MAX_SNAPSHOT_BYTES, SUBJECT_BATCHES_PER_MINUTE,
    PublishedCourse, SnapshotIdentity, WindowBatchAck, WindowError, WindowItemAck, WindowKey,
    WindowSnapshot, authorize_snapshot, build_ack, snapshot_rejection, validate_window_age, window_bounds,
)
from pymentor_analytics.modules.collector.ports import IntakeForbidden, IntakeOverloaded, IntakeUnavailable

PATH = "/api/analytics/windows/batch"
ATTESTATION_HEADER = b"x-analytics-windows-authorization"
router = APIRouter()


class InvalidEnvelope(Exception):
    pass


class UnsupportedVersion(Exception):
    pass


class TooLarge(Exception):
    pass


class WindowBudget:
    """Bound the v3 request path independently of v2 and before SQL admission."""
    def __init__(self, concurrency):
        self.slots = asyncio.Semaphore(concurrency)
        self.history = OrderedDict()

    def admit(self, subject):
        now = time.monotonic()
        # Keep only writers with a recently accepted request.
        while self.history and next(iter(self.history.values()))[-1] <= now - 60:
            self.history.popitem(last=False)
        seen = self.history.setdefault(subject, deque())
        while seen and seen[0] <= now - 60:
            seen.popleft()
        if len(seen) >= SUBJECT_BATCHES_PER_MINUTE:
            raise IntakeOverloaded(max(1, math.ceil(seen[0] + 60 - now)))
        seen.append(now)
        self.history.move_to_end(subject)


def error(status, code, message, *, retry_after=None):
    body = WindowError(code=code, message=message, correlation_id=uuid4())
    headers = {"Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"), headers=headers)


async def read_body(request):
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if request.scope.get("query_string") or media != "application/json":
        raise InvalidEnvelope()
    lengths = [value for name, value in request.scope["headers"] if name.lower() == b"content-length"]
    if len(lengths) > 1 or (lengths and (not lengths[0].isdigit() or len(lengths[0]) > 10)):
        raise InvalidEnvelope()
    if lengths and int(lengths[0]) > MAX_BATCH_BYTES:
        raise TooLarge()
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BATCH_BYTES:
            raise TooLarge()
        raw.extend(chunk)
    return bytes(raw)


def parse_batch(raw):
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("invalid JSON number")

    try:
        body = raw.decode("utf-8").lstrip(" \t\r\n")
        decoder = json.JSONDecoder(object_pairs_hook=no_duplicates, parse_constant=invalid_constant)
        parsed = decoder.decode(body)
        if not isinstance(parsed, dict) or set(parsed) != {"protocol_version", "windows"}:
            raise InvalidEnvelope()
        if type(parsed["protocol_version"]) is not int or parsed["protocol_version"] != 1:
            raise UnsupportedVersion()
        windows = parsed["windows"]
        if not isinstance(windows, list) or not 1 <= len(windows) <= MAX_KEYS:
            raise InvalidEnvelope()

        def whitespace(pos):
            while body[pos] in " \t\r\n":
                pos += 1
            return pos

        pos = whitespace(1)
        key, pos = decoder.raw_decode(body, pos)
        pos = whitespace(whitespace(pos) + 1)
        if key != "windows":
            _, pos = decoder.raw_decode(body, pos)
            _, pos = decoder.raw_decode(body, whitespace(whitespace(pos) + 1))
            pos = whitespace(whitespace(pos) + 1)

        pos = whitespace(pos + 1)
        items = []
        keys = set()
        for row in windows:
            start = pos
            _, end = decoder.raw_decode(body, start)
            pos = whitespace(whitespace(end) + 1)
            if not isinstance(row, dict):
                raise InvalidEnvelope()
            identity = SnapshotIdentity.model_validate_json(json.dumps({
                field: row.get(field) for field in SnapshotIdentity.model_fields
            }))
            key = (identity.activity_date, identity.slot_no)
            if key in keys:
                raise InvalidEnvelope()
            keys.add(key)
            items.append((identity, row, body[start:end].encode("utf-8")))
        return items
    except (IndexError, ValueError, TypeError, UnicodeError, RecursionError):
        raise InvalidEnvelope() from None


def rejected(index, identity, code, digest=None):
    return WindowItemAck(**identity.model_dump(), index=index, status="rejected", code=code,
                         payload_hash=digest, confirmed=None)


async def known_catalog(state, snapshot, deadline, seen):
    courses = {}
    for row in snapshot.stats:
        if row.lesson_id is None:
            continue
        version = snapshot.catalog_versions[row.course_id]
        key = (row.course_id, version, row.lesson_id)
        if key not in seen:
            seen[key] = await state.catalog.known(course_id=row.course_id, catalog_version=version,
                                                  lesson_id=row.lesson_id, deadline=deadline)
        if seen[key]:
            courses.setdefault(row.course_id, set()).add(row.lesson_id)
    return {course: PublishedCourse(snapshot.catalog_versions[course], frozenset(lessons))
            for course, lessons in courses.items()}


def gateway_proof(request, verifier, grant, token, raw):
    headers = [value for key, value in request.scope["headers"] if key.lower() == ATTESTATION_HEADER]
    if len(headers) != 1 or not headers[0].isascii() or len(headers[0]) > 2048:
        raise AuthenticationError()
    proof = verifier.verify_window_forward(headers[0].decode("ascii"))
    if (proof.sub != grant.sub or not compare_digest(proof.body_sha256, sha256(raw).hexdigest())
            or not compare_digest(proof.grant_sha256, sha256(token.encode("ascii")).hexdigest())):
        raise AuthenticationError()
    return proof


@router.post(PATH, response_model=WindowBatchAck, operation_id="collectActivityWindowBatch",
             responses={code: {"model": WindowError} for code in (400, 401, 403, 404, 413, 429, 503)},
             openapi_extra={"security": [{"AnalyticsGrant": []}], "requestBody": {"required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/WindowWireBatch"}}}}})
async def windows_batch(request: Request):
    started = time.monotonic()
    response = await collect_windows(request)
    request.app.state.telemetry.window_response(response.status_code, time.monotonic() - started, response.body)
    return response


async def collect_windows(request: Request):
    state = request.app.state
    budget = state.windows_budget
    if budget.slots.locked():
        return error(429, "rate_limited", "Window intake busy", retry_after=1)
    try:
        timeout = min(9.0, state.settings.db_timeout_seconds)
        async with asyncio.timeout(timeout), budget.slots:
            deadline = time.monotonic() + timeout
            if not state.trust_manifest_validated or state.grant_verifier is None:
                return error(503, "unavailable", "Window trust unavailable")
            token = extract_bearer(request.scope["headers"])
            grant = state.grant_verifier.verify_windows(token)
            received_at = datetime.now(timezone.utc)
            settings = state.settings
            if (received_at < settings.windows_t0
                    or not (settings.windows_all_eligible or str(grant.sub) in settings.windows_cohort)):
                return error(403, "forbidden", "Access denied")
            raw = await read_body(request)
            proof = gateway_proof(request, state.grant_verifier, grant, token, raw)
            items = parse_batch(raw)
            if len(proof.rejected) != len(items):
                raise AuthenticationError()
            budget.admit(grant.sub)
            if state.windows_store is None:
                return error(503, "unavailable", "Window schema unavailable")
            try:
                privacy_ready = await state.windows_probe.is_ready()
            except Exception:
                privacy_ready = False
            if privacy_ready is not True:
                return error(503, "privacy_unavailable", "Privacy unavailable")
            await require_live_subject(state.subject_fence, grant.sub)
            return await process(state, grant, proof, items, received_at, deadline)
    except AuthenticationError:
        return error(401, "unauthorized", "Invalid credential")
    except AuthorizationError:
        return error(403, "forbidden", "Access denied")
    except (FenceUnavailable, PrivacyUnavailable):
        return error(503, "privacy_unavailable", "Privacy unavailable")
    except UnsupportedVersion:
        return error(400, "unsupported_version", "Window protocol unsupported")
    except InvalidEnvelope:
        return error(400, "invalid_request", "Invalid window batch")
    except TooLarge:
        return error(413, "too_large", "Window batch too large")
    except IntakeOverloaded as exc:
        return error(429, "rate_limited", "Retry the same windows", retry_after=exc.retry_after)
    except Exception:
        return error(503, "unavailable", "Outcome unknown; retry the same windows")


async def process(state, grant, proof, items, received_at, deadline):
    acknowledgements = []
    seen = {}
    for index, (identity, row, raw) in enumerate(items):
        if len(raw) > MAX_SNAPSHOT_BYTES:
            acknowledgements.append(rejected(index, identity, "too_large"))
            continue
        try:
            snapshot = WindowSnapshot.model_validate_json(raw)
        except ValidationError as exc:
            acknowledgements.append(rejected(index, identity, snapshot_rejection(exc, row)))
            continue
        code = proof.rejected[index] or validate_window_age(snapshot, received_at)
        if not code and window_bounds(snapshot)[1] <= state.settings.windows_t0:
            code = "too_late"
        if not code:
            code = authorize_snapshot(snapshot, grant, await known_catalog(state, snapshot, deadline, seen))
        if code:
            acknowledgements.append(rejected(index, identity, code))
            continue
        if time.time() >= min(grant.exp, proof.exp):
            if any(item.status in {"committed", "duplicate", "pending"} for item in acknowledgements):
                return error(503, "unavailable", "Outcome unknown; refresh grant and retry the same windows")
            return error(401, "unauthorized", "Invalid credential")
        key = WindowKey(student_id=grant.sub, activity_date=snapshot.activity_date, slot_no=snapshot.slot_no)
        started = time.monotonic()
        try:
            transition = await state.windows_store.write_window(key, snapshot, received_at=received_at)
        except IntakeForbidden:
            acknowledgements.append(rejected(index, identity, "privacy_forbidden"))
            continue
        except (IntakeOverloaded, IntakeUnavailable, FenceUnavailable, PrivacyUnavailable):
            raise
        except Exception:
            raise IntakeUnavailable() from None
        finally:
            state.telemetry.window_transaction(time.monotonic() - started)
        prior = transition.state.confirmed
        checked_contexts = {row.context for row in snapshot.stats}
        include_confirmed = (transition.status in {"committed", "duplicate"}
                             or (prior is not None and all(row.context in checked_contexts for row in prior.stats)))
        acknowledgements.append(build_ack(index, snapshot, transition, include_confirmed=include_confirmed))
    return JSONResponse(WindowBatchAck(protocol_version=1, items=tuple(acknowledgements)).model_dump(mode="json"),
                        headers={"Cache-Control": "no-store"})
