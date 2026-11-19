"""Versioned browser intake transport; durable work is delegated to IntakePort."""
import json
import time
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import ValidationError

from pymentor_analytics.contracts.auth_v1 import AuthorizationError, FenceUnavailable, AuthenticationError
from pymentor_analytics.adapters.auth.http import extract_bearer
from pymentor_analytics.contracts.v2 import (BatchAck, ErrorResponse, ItemAck, MAX_EVENT_BYTES,
                                              parse_browser_item, parse_browser_items)
from pymentor_analytics.modules.collector.application.admission import authorize_collection
from pymentor_analytics.modules.collector.application.metadata import build_record
from pymentor_analytics.contracts.metadata_v1 import MetadataRejected
from pymentor_analytics.modules.collector.ports import IntakeForbidden, IntakeOverloaded, IntakeUnavailable
from .errors import error_response

router = APIRouter()


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def _identity_and_version(raw: bytes):
    try:
        obj = json.loads(raw, object_pairs_hook=_unique)
        if not isinstance(obj, dict):
            return None, False
        identifier = obj.get("event_id")
        event_id = UUID(identifier) if isinstance(identifier, str) else None
        version = obj.get("schema_version")
        return event_id, type(version) is int and version != 2
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return None, False


def _grant_is_expired(grant):
    return time.time() >= grant.expires_at


@router.post("/api/analytics/events/batch", response_model=BatchAck, operation_id="collectBrowserBatch",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid envelope"},
        401: {"model": ErrorResponse, "description": "Invalid grant", "headers": {"WWW-Authenticate": {"schema": {"type": "string"}}}},
        403: {"model": ErrorResponse, "description": "Scope or subject denied"},
        413: {"model": ErrorResponse, "description": "Batch too large"},
        429: {"model": ErrorResponse, "description": "Backpressure", "headers": {"Retry-After": {"schema": {"type": "integer", "minimum": 1}}}},
        503: {"model": ErrorResponse, "description": "Unavailable or outcome unknown"},
    },
    openapi_extra={"security": [{"AnalyticsGrant": []}], "requestBody": {"required": True,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/WireBatch"}}}}})
async def batch(request: Request):
    state = request.app.state
    if not state.trust_manifest_validated:
        return error_response(503, "unavailable", "Intake unavailable")
    verifier = state.grant_verifier
    try:
        token = extract_bearer(request.scope["headers"])
        if verifier is None:
            raise AuthenticationError()
        grant = verifier.verify(token)
    except AuthenticationError:
        return error_response(401, "unauthorized", "Invalid credential", {"WWW-Authenticate": "Bearer"})
    if grant.role != "user" or grant.scope != "collect":
        return error_response(403, "forbidden", "Access denied")
    try:
        if not await state.probe.is_ready():
            return error_response(503, "unavailable", "Intake unavailable")
    except Exception:
        return error_response(503, "unavailable", "Intake unavailable")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        return error_response(400, "invalid_request", "Expected application/json")
    try:
        raw_items = parse_browser_items(await request.body())
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return error_response(400, "invalid_request", "Invalid batch")

    items = []
    for index, raw in enumerate(raw_items):
        if len(raw) > MAX_EVENT_BYTES:
            items.append((index, None, None, "too_large"))
            continue
        event_id, unsupported = _identity_and_version(raw)
        if unsupported:
            items.append((index, event_id, None, "unsupported_version"))
            continue
        try:
            event = parse_browser_item(raw)
            items.append((index, event.event_id, event, None))
        except (ValueError, TypeError, UnicodeError, RecursionError, ValidationError):
            items.append((index, event_id, None, "invalid"))

    deadline = request.scope["analytics_deadline"]
    identities = {}
    durable_receipt_seen = False
    try:
        # Preflight every valid item before any write, so a forged course does
        # not produce a partially committed batch followed by a scope denial.
        for _, _, event, _ in items:
            if event is not None and event.course_id not in identities:
                identities[event.course_id] = await authorize_collection(grant, course_id=event.course_id, fence=state.subject_fence)
        output = []
        for index, event_id, event, error in items:
            if error:
                output.append(ItemAck(index=index, event_id=event_id, status="rejected", code=error))
                continue
            if event.type != "tool_segment" and not await state.catalog.known(course_id=event.course_id, catalog_version=event.catalog_version,
                                             lesson_id=event.lesson_id, deadline=deadline):
                output.append(ItemAck(index=index, event_id=event_id, status="pending", code="unknown_catalog"))
                continue
            if _grant_is_expired(grant):
                if durable_receipt_seen:
                    return error_response(503, "unavailable", "Outcome unknown; refresh credential and retry the same event IDs", {"WWW-Authenticate": "Bearer"})
                return error_response(401, "unauthorized", "Invalid credential", {"WWW-Authenticate": "Bearer"})
            record = build_record(identities[event.course_id], event)
            try:
                outcome = await state.intake.accept(record, deadline=deadline)
            except MetadataRejected as exc:
                output.append(ItemAck(index=index, event_id=event_id, status="rejected", code=exc.reason))
                continue
            codes = {"accepted": "committed", "duplicate": "same_payload", "conflict": "payload_conflict"}
            if outcome not in codes:
                raise IntakeUnavailable()
            durable_receipt_seen = durable_receipt_seen or outcome in {"accepted", "duplicate"}
            output.append(ItemAck(index=index, event_id=event_id, status=outcome, code=codes[outcome]))
        return BatchAck(items=output)
    except (AuthorizationError, IntakeForbidden):
        if durable_receipt_seen:
            return error_response(503, "unavailable", "Outcome unknown; retry the same event IDs after restoring authorization")
        return error_response(403, "forbidden", "Access denied")
    except IntakeOverloaded as exc:
        return error_response(429, "rate_limited", "Retry the same event IDs", {"Retry-After": str(exc.retry_after)})
    except (FenceUnavailable, IntakeUnavailable):
        return error_response(503, "unavailable", "Outcome unknown; retry the same event IDs")
    except Exception:
        return error_response(503, "unavailable", "Outcome unknown; retry the same event IDs")
