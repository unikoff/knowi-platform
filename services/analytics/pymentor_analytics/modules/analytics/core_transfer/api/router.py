"""Opt-in router. Mount only with a validated privacy barrier and server-owned mapping."""
import asyncio
import json
import time
from uuid import UUID
from typing import Literal

from fastapi import APIRouter, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from pymentor_analytics.adapters.auth.http import extract_bearer
from pymentor_analytics.contracts.auth_v1 import AuthenticationError
from pymentor_analytics.contracts.core_transfer_v1 import MAX_BODY_BYTES
from pymentor_analytics.modules.collector.ports import IntakeForbidden, IntakeOverloaded


class ReceiptResponse(BaseModel):
    status: Literal["accepted", "absent", "conflict"]


class DeletionResponse(BaseModel):
    status: Literal["accepted", "pending", "absent", "conflict"]


class ProofReceiptResponse(BaseModel):
    model_config = ConfigDict(json_schema_extra={"allOf": [
        {"if": {"properties": {"status": {"const": "accepted"}}},
         "then": {"properties": {"coverage": {"enum": ["pending", "verified", "unavailable"]}}},
         "else": {"properties": {"coverage": {"type": "null"}}}}]})
    status: Literal["accepted", "absent", "conflict"]
    coverage: Literal["pending", "verified", "unavailable"] | None


bearer = HTTPBearer(auto_error=False, scheme_name="CoreProducer")
event_schema = {
    "type": "object", "additionalProperties": False,
    "required": ["event_id", "schema_version", "kind", "subject_ref", "revision", "payload"],
    "properties": {"event_id": {"type": "string", "format": "uuid"},
                   "schema_version": {"type": "integer", "const": 1},
                   "kind": {"enum": ["legacy_activity_day", "lesson_progress", "task_progress"]},
                   "subject_ref": {"type": "string", "format": "uuid"},
                   "revision": {"type": "integer", "minimum": 0, "maximum": 9223372036854775807},
                   "payload": {"type": "object"}},
    "allOf": [
        {"if": {"properties": {"kind": {"const": "legacy_activity_day"}}},
         "then": {"properties": {"payload": {"type": "object", "additionalProperties": False,
             "required": ["activity_date", "visited_at", "provenance"], "properties": {
                 "activity_date": {"type": "string", "format": "date"},
                 "visited_at": {"anyOf": [{"type": "string", "format": "date-time"}, {"type": "null"}]},
                 "provenance": {"const": "legacy_global"}}}}}},
        {"if": {"properties": {"kind": {"const": "lesson_progress"}}},
         "then": {"properties": {"payload": {"type": "object", "additionalProperties": False,
             "required": ["course_id", "lesson_id", "completed_at"], "properties": {
                 "course_id": {"type": "string", "minLength": 1}, "lesson_id": {"type": "string", "minLength": 1},
                 "completed_at": {"anyOf": [{"type": "string", "format": "date-time"}, {"type": "null"}]}}}}}},
        {"if": {"properties": {"kind": {"const": "task_progress"}}},
         "then": {"properties": {"payload": {"type": "object", "additionalProperties": False,
             "required": ["course_id", "task_id", "task_revision", "completed_at"], "properties": {
                 "course_id": {"type": "string", "minLength": 1, "maxLength": 80},
                 "task_id": {"type": "string", "minLength": 1, "maxLength": 160},
                 "task_revision": {"type": "string", "minLength": 1, "maxLength": 64},
                 "completed_at": {"anyOf": [{"type": "string", "format": "date-time"}, {"type": "null"}]}}}}}}],
}
fingerprint_param = {"name": "fingerprint", "in": "query", "required": True,
                     "schema": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}
proof_schema = {"type": "object", "additionalProperties": False,
    "required": ["schema_version", "proof_id", "subject_ref", "scope", "cut_revision", "entity_count", "entities_digest", "created_at"],
    "properties": {"schema_version": {"type": "integer", "enum": [1, 2]}, "proof_id": {"type": "string", "format": "uuid"},
        "subject_ref": {"type": "string", "format": "uuid"}, "scope": {"enum": ["subject_all_v1", "subject_all_v2"]},
        "cut_revision": {"type": "integer", "minimum": 0, "maximum": 9223372036854775807},
        "entity_count": {"type": "integer", "minimum": 0, "maximum": 15000},
        "entities_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "created_at": {"type": "string", "format": "date-time", "description": "Canonical UTC +00:00 ISO timestamp"}},
    "oneOf": [
        {"properties": {"schema_version": {"const": 1}, "scope": {"const": "subject_all_v1"}}},
        {"properties": {"schema_version": {"const": 2}, "scope": {"const": "subject_all_v2"}}},
    ]}
errors = {status: {"description": description} for status, description in {
    400: "Invalid request", 401: "Invalid producer credential", 403: "Access denied",
    429: "Admission overloaded", 503: "Unavailable or outcome unknown"}.items()}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def create_router(*, service, verifier, namespace_by_producer, readiness, deletion_status=None, proof_service=None, timeout_seconds=3.0, concurrency=16):
    if not namespace_by_producer or not 0 < timeout_seconds <= 30 or not 1 <= concurrency <= 128:
        raise ValueError("invalid transfer router configuration")
    mapping = dict(namespace_by_producer)
    if any(not isinstance(k, str) or not k or not isinstance(v, str) or not v or len(v) > 128 for k, v in mapping.items()):
        raise ValueError("invalid producer namespace mapping")
    router = APIRouter()
    semaphore = asyncio.Semaphore(concurrency)

    async def run(request, operation):
        try:
            producer = verifier.verify(extract_bearer(request.scope["headers"]))
        except (AuthenticationError, AttributeError):
            return JSONResponse({"error": "unauthorized"}, 401, headers={"WWW-Authenticate": "Bearer"})
        namespace = mapping.get(producer.sub)
        if namespace is None or producer.scope != "control:write":
            return JSONResponse({"error": "forbidden"}, 403)
        if time.time() >= producer.expires_at:
            return JSONResponse({"error": "unauthorized"}, 401, headers={"WWW-Authenticate": "Bearer"})
        if semaphore.locked():
            return JSONResponse({"error": "overloaded"}, 429, headers={"Retry-After": "1"})
        async with semaphore:
            deadline = time.monotonic() + timeout_seconds
            try:
                async with asyncio.timeout(timeout_seconds):
                    if await readiness.is_ready() is not True:
                        return JSONResponse({"error": "unavailable"}, 503)
                    result = await operation(namespace, deadline)
                    if isinstance(result, dict):
                        if (set(result) != {"status", "coverage"} or result["status"] not in {"accepted", "absent", "conflict"}
                                or result["coverage"] not in {None, "pending", "verified", "unavailable"}
                                or (result["status"] == "accepted" and result["coverage"] is None)
                                or (result["status"] != "accepted" and result["coverage"] is not None)):
                            raise RuntimeError("invalid receipt outcome")
                        return JSONResponse(result)
                    return JSONResponse({"status": result})
            except ValueError:
                return JSONResponse({"error": "invalid_request"}, 400)
            except IntakeForbidden:
                return JSONResponse({"error": "forbidden"}, 403)
            except IntakeOverloaded as exc:
                return JSONResponse({"error": "overloaded"}, 429, headers={"Retry-After": str(exc.retry_after)})
            except Exception:
                return JSONResponse({"error": "unavailable"}, 503)

    @router.post("/internal/core-transfer/v1/events", response_model=ReceiptResponse, responses=errors,
                 openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": {
                     "type": "object", "additionalProperties": False, "required": ["event", "fingerprint"],
                     "properties": {"event": event_schema, "fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}}}}}})
    async def accept(request: Request, _credential: HTTPAuthorizationCredentials | None = Security(bearer)):
        async def operation(namespace, deadline):
            if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise ValueError
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_BODY_BYTES:
                    raise ValueError
                raw.extend(chunk)
            body = json.loads(raw, object_pairs_hook=_unique)
            if not isinstance(body, dict) or set(body) != {"event", "fingerprint"}:
                raise ValueError
            return await service.accept(namespace, body["event"], body["fingerprint"], deadline=deadline)
        return await run(request, operation)

    @router.get("/internal/core-transfer/v1/receipts/{event_id}", response_model=ReceiptResponse,
                responses=errors, openapi_extra={"parameters": [fingerprint_param]})
    async def receipt(request: Request, event_id: str, _credential: HTTPAuthorizationCredentials | None = Security(bearer)):
        async def operation(namespace, deadline):
            parsed = UUID(event_id)
            if str(parsed) != event_id or len(request.query_params.getlist("fingerprint")) != 1 or set(request.query_params) != {"fingerprint"}:
                raise ValueError
            return await service.receipt(namespace, parsed, request.query_params["fingerprint"], deadline=deadline)
        return await run(request, operation)

    @router.get("/internal/core-transfer/v1/deletions/{control_id}", response_model=DeletionResponse, responses=errors,
                openapi_extra={"parameters": [
                    {"name": "subject_ref", "in": "query", "required": True, "schema": {"type": "string", "format": "uuid"}},
                    {"name": "epoch", "in": "query", "required": True, "schema": {"type": "integer", "minimum": 1, "maximum": 9223372036854775807}}]})
    async def deletion(request: Request, control_id: str, _credential: HTTPAuthorizationCredentials | None = Security(bearer)):
        async def operation(namespace, deadline):
            control = UUID(control_id)
            if str(control) != control_id or set(request.query_params) != {"subject_ref", "epoch"}:
                raise ValueError
            if any(len(request.query_params.getlist(k)) != 1 for k in ("subject_ref", "epoch")):
                raise ValueError
            raw_subject, raw_epoch = request.query_params["subject_ref"], request.query_params["epoch"]
            subject = UUID(raw_subject)
            if str(subject) != raw_subject or not raw_epoch.isascii() or not raw_epoch.isdecimal() or raw_epoch.startswith("0") or len(raw_epoch) > 19:
                raise ValueError
            epoch = int(raw_epoch)
            if epoch > 9223372036854775807:
                raise ValueError
            if deletion_status is None:
                raise RuntimeError("deletion status unavailable")
            status = await deletion_status.status(namespace, control, subject, epoch, deadline=deadline)
            if status not in {"accepted", "pending", "absent", "conflict"}:
                raise RuntimeError("invalid deletion status")
            return status
        return await run(request, operation)

    if proof_service is not None:
        @router.post("/internal/core-transfer/v1/proofs", response_model=ReceiptResponse, responses=errors,
                     openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": {
                         "type": "object", "additionalProperties": False, "required": ["proof", "fingerprint"],
                         "properties": {"proof": proof_schema, "fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}}}}}})
        async def accept_proof(request: Request, _credential: HTTPAuthorizationCredentials | None = Security(bearer)):
            async def operation(namespace, deadline):
                if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                    raise ValueError
                raw = bytearray()
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > MAX_BODY_BYTES:
                        raise ValueError
                    raw.extend(chunk)
                body = json.loads(raw, object_pairs_hook=_unique)
                if not isinstance(body, dict) or set(body) != {"proof", "fingerprint"}:
                    raise ValueError
                result = await proof_service.accept(namespace, body["proof"], body["fingerprint"], deadline=deadline)
                if result not in {"accepted", "conflict"}:
                    raise RuntimeError("invalid proof outcome")
                return result
            return await run(request, operation)

        @router.get("/internal/core-transfer/v1/proofs/{proof_id}", response_model=ProofReceiptResponse,
                    responses=errors, openapi_extra={"parameters": [fingerprint_param]})
        async def proof_receipt(request: Request, proof_id: str, _credential: HTTPAuthorizationCredentials | None = Security(bearer)):
            async def operation(namespace, deadline):
                parsed = UUID(proof_id)
                if str(parsed) != proof_id or len(request.query_params.getlist("fingerprint")) != 1 or set(request.query_params) != {"fingerprint"}:
                    raise ValueError
                return await proof_service.receipt(namespace, parsed, request.query_params["fingerprint"], deadline=deadline)
            return await run(request, operation)

    return router
