"""P1-only opt-in publication endpoint; no namespace comes from the body."""
import asyncio
import json
import time

from fastapi import APIRouter, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from pymentor_analytics.adapters.auth.http import extract_bearer
from pymentor_analytics.contracts.auth_v1 import AuthenticationError
from pymentor_analytics.modules.catalog.service import MAX_BODY_BYTES

bearer = HTTPBearer(auto_error=False, scheme_name="CoreProducer")


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def create_router(*, service, verifier, namespace_by_producer, readiness, timeout_seconds=3.0, concurrency=16):
    if not namespace_by_producer or not 0 < timeout_seconds <= 30 or not 1 <= concurrency <= 128:
        raise ValueError("invalid catalog router configuration")
    mapping = dict(namespace_by_producer)
    if any(not isinstance(k, str) or not k or not isinstance(v, str) or not v or len(v) > 128 for k, v in mapping.items()):
        raise ValueError("invalid catalog producer mapping")
    router, semaphore = APIRouter(), asyncio.Semaphore(concurrency)

    async def run(request, operation):
        try:
            producer = verifier.verify(extract_bearer(request.scope["headers"]))
        except (AuthenticationError, AttributeError):
            return JSONResponse({"error": "unauthorized"}, 401, headers={"WWW-Authenticate": "Bearer"})
        namespace = mapping.get(producer.sub)
        if namespace is None or producer.scope != "control:write":
            return JSONResponse({"error": "forbidden"}, 403)
        if time.time() >= producer.expires_at:
            return JSONResponse({"error": "unauthorized"}, 401)
        if semaphore.locked():
            return JSONResponse({"error": "overloaded"}, 429, headers={"Retry-After": "1"})
        async with semaphore:
            deadline = time.monotonic() + timeout_seconds
            try:
                async with asyncio.timeout(timeout_seconds):
                    if await readiness.is_ready() is not True:
                        return JSONResponse({"error": "unavailable"}, 503)
                    result = await operation(namespace, deadline, producer)
                    if result not in {"accepted", "absent", "conflict"}:
                        raise RuntimeError("unexpected store result")
                    return JSONResponse({"status": result}, 409 if result == "conflict" else 200)
            except AuthenticationError:
                return JSONResponse({"error": "unauthorized"}, 401, headers={"WWW-Authenticate": "Bearer"})
            except (ValueError, TypeError, UnicodeError, RecursionError):
                return JSONResponse({"error": "invalid_request"}, 400)
            except Exception:
                return JSONResponse({"error": "unavailable"}, 503)

    @router.post("/internal/analytics/catalog/v1")
    async def accept(request: Request, _credential: HTTPAuthorizationCredentials | None = Security(bearer)):
        async def operation(namespace, deadline, producer):
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise ValueError
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_BODY_BYTES:
                    raise ValueError
                raw.extend(chunk)
            body = json.loads(raw, object_pairs_hook=_unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(body, dict) or set(body) != {"message", "fingerprint"}:
                raise ValueError
            if time.time() >= producer.expires_at:
                raise AuthenticationError()
            return await service.accept(namespace, body["message"], body["fingerprint"], deadline=deadline)
        return await run(request, operation)

    @router.get("/internal/analytics/catalog/v1/receipts/{course_id}/{catalog_version}")
    async def receipt(request: Request, course_id: str, catalog_version: str, _credential: HTTPAuthorizationCredentials | None = Security(bearer)):
        async def operation(namespace, deadline, producer):
            if set(request.query_params) != {"fingerprint"} or len(request.query_params.getlist("fingerprint")) != 1:
                raise ValueError
            return await service.receipt(namespace, course_id, catalog_version, request.query_params["fingerprint"], deadline=deadline)
        return await run(request, operation)

    return router
