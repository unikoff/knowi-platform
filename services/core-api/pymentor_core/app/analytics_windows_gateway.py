"""Separately bounded Core window grant and Collector forward."""
from __future__ import annotations

import asyncio
from hashlib import sha256
import time
from urllib.parse import urlsplit
from uuid import uuid4

import jwt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from pymentor_core.adapters.analytics_http.http import request as http_request
from pymentor_core.adapters.course_local.runtime import default_runtime
from pymentor_core.app.analytics_browser_gateway import configure as configure_browser_gateway
from pymentor_core.modules.analytics_delivery import windows_grants as grants

GRANT_PATH = "/api/analytics/windows/grant"
BATCH_PATH = "/api/analytics/windows/batch"
DEADLINE = 10.0
MAX_REPLY = 256 * 1024
MAX_IN_FLIGHT = 4
ROUTE_STATUSES = {200, 400, 401, 403, 404, 413, 429, 503}
router = APIRouter()
disabled_router = APIRouter()


def configure(env):
    policy = grants.configure_policy(env)
    if policy is None:
        return None
    local = env.get("ANALYTICS_WINDOWS_LOCAL_COLLECTOR_ORIGIN")
    if local is None:
        browser = configure_browser_gateway(env)
        if browser is None:
            raise ValueError("Analytics windows require a private Collector")
        origin = browser["endpoint"].removesuffix("/api/analytics/events/batch")
        verify = browser["verify"]
    else:
        try:
            parsed = urlsplit(local)
            if (env.get("PYMENTOR_ANALYTICS_LOCAL_COLLECTION_ENABLED") != "1"
                    or parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                    or not parsed.port or local != f"http://127.0.0.1:{parsed.port}"):
                raise ValueError
        except ValueError:
            raise ValueError("Invalid local Analytics windows Collector") from None
        origin, verify = local, True
    return {"policy": policy, "endpoint": origin + BATCH_PATH, "verify": verify, "slots": asyncio.Semaphore(MAX_IN_FLIGHT)}


def error(exc: grants.WindowHTTPError):
    headers = {"Cache-Control": "no-store"}
    if exc.status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if exc.status == 429:
        headers["Retry-After"] = "1"
    return JSONResponse({"code": exc.code, "message": exc.message, "correlation_id": str(uuid4())},
                        status_code=exc.status, headers=headers)


@disabled_router.post(GRANT_PATH, include_in_schema=False)
@disabled_router.post(BATCH_PATH, include_in_schema=False)
async def capability_off():
    return error(grants.WindowHTTPError(404, "capability_off", "Window collection is disabled"))


async def json_body(request, limit):
    if request.scope.get("query_string") or request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise grants.WindowHTTPError(400, "invalid_request", "Expected application/json")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise grants.WindowHTTPError(413, "too_large", "Window request too large")
        body.extend(chunk)
    return bytes(body)


def browser_token(request):
    values = [value for name, value in request.scope["headers"] if name.lower() == b"authorization"]
    if len(values) == 1 and len(values[0]) <= grants.MAX_TOKEN_BYTES + 7:
        scheme, _, token = values[0].partition(b" ")
        if scheme == b"Bearer" and token and all(part and part.isascii() and
                all(ch in b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for ch in part)
                for part in token.split(b".")) and token.count(b".") == 2:
            return token.decode("ascii")
    raise grants.WindowHTTPError(401, "unauthorized", "Invalid credential")


def token_claims(signer, token):
    try:
        claims = signer.verify_window_token(token)
        if (type(claims.get("protocol_version")) is not int or claims["protocol_version"] != 1
                or type(claims.get("grant_version")) is not int or claims["grant_version"] != 1
                or claims.get("scope") != "windows:collect" or not isinstance(claims.get("course_ids"), list)
                or len(claims["course_ids"]) > grants.MAX_SCOPE_COURSES
                or any(not isinstance(value, str) for value in claims["course_ids"])):
            raise ValueError
        return claims
    except (jwt.PyJWTError, ValueError, TypeError):
        raise grants.WindowHTTPError(401, "unauthorized", "Invalid credential") from None


@router.post(GRANT_PATH)
async def window_grant(request: Request):
    state = request.app.state
    try:
        async with asyncio.timeout(DEADLINE):
            raw = await json_body(request, 2048)
            try:
                parsed = grants.decode_json(raw)
                if isinstance(parsed, dict) and "protocol_version" in parsed and (
                        type(parsed["protocol_version"]) is not int or parsed["protocol_version"] != 1):
                    raise grants.WindowHTTPError(400, "unsupported_version", "Window protocol unsupported")
                value = grants.WindowGrantRequest.model_validate_json(raw)
            except (UnicodeError, RecursionError, ValidationError, ValueError):
                raise grants.WindowHTTPError(400, "invalid_request", "Invalid window grant request") from None
            principal, scope = await grants.current_scope(request, state.analytics_windows_gateway["policy"])
            return JSONResponse(grants.issue(state.analytics_grant_signer, principal, scope, value),
                                headers={"Cache-Control": "no-store"})
    except grants.WindowHTTPError as exc:
        return error(exc)
    except (TimeoutError, ValueError):
        return error(grants.WindowHTTPError(503, "unavailable", "Window grant unavailable"))


def _load_lessons(course_id: str):
    return default_runtime().snapshot(course_id).catalog.LESSONS


@router.post(BATCH_PATH)
async def window_batch(request: Request):
    state = request.app.state
    config = state.analytics_windows_gateway
    if config["slots"].locked():
        return error(grants.WindowHTTPError(429, "rate_limited", "Window intake busy"))
    try:
        async with asyncio.timeout(DEADLINE):
            async with config["slots"]:
                deadline = time.monotonic() + DEADLINE
                token = browser_token(request)
                signer = state.analytics_grant_signer
                claims = token_claims(signer, token)
                principal, scope = await grants.current_scope(request, config["policy"])
                if claims["sub"] != principal.account_ref:
                    raise grants.WindowHTTPError(403, "forbidden", "Access denied")
                raw = await json_body(request, grants.MAX_BATCH_BYTES)
                rejected = await run_in_threadpool(
                    grants.batch_permissions, raw, principal, scope, claims, load_lessons=_load_lessons,
                )
                proof = signer.attest_windows_request(
                    subject=principal.account_ref, body_sha256=sha256(raw).hexdigest(),
                    grant_sha256=sha256(token.encode("ascii")).hexdigest(), rejected=rejected,
                )
                result = await http_request("POST", config["endpoint"], headers={
                    "Authorization": "Bearer " + token, "Content-Type": "application/json",
                    grants.ATTESTATION_HEADER: proof,
                }, body=raw, deadline_seconds=deadline - time.monotonic(), max_bytes=MAX_REPLY,
                    verify=config["verify"], include_safe_headers=True)
    except grants.WindowHTTPError as exc:
        return error(exc)
    except TimeoutError:
        result = None
    if result is None or result[0] not in ROUTE_STATUSES:
        return error(grants.WindowHTTPError(503, "unavailable", "Outcome unknown; retry the same windows"))
    return Response(result[1], status_code=result[0], media_type="application/json",
                    headers={"Cache-Control": "no-store", **result[2]})
