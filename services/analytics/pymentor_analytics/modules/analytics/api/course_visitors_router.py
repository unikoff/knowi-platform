"""Private query route with a separate capability from browser Analytics grants."""
import asyncio
from hashlib import sha256
from hmac import compare_digest
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter, ValidationError

from pymentor_analytics.adapters.auth.http import extract_bearer
from pymentor_analytics.adapters.postgres.course_visitors import (
    discover_course_visitor_candidates, read_course_visitor_report,
)
from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport
from pymentor_analytics.adapters.postgres.course_study_time import read_course_study_time
from pymentor_analytics.contracts.auth_v1 import AuthenticationError, AuthorizationError
from pymentor_analytics.contracts.course_visitors import (
    CourseStudyTimeRequest, CourseVisitorDiscovery, CourseVisitorRequest, MAX_BODY_BYTES,
)

router = APIRouter()
_REQUEST = TypeAdapter(CourseVisitorRequest)
_STUDY_TIME_REQUEST = TypeAdapter(CourseStudyTimeRequest)


def _error(code, status):
    return JSONResponse({"error": code}, status, headers={"Cache-Control": "no-store"})


@router.post("/internal/analytics/v1/course/visitors", include_in_schema=False)
async def course_visitors(request: Request):
    return await _course_aggregate(request, study_time=False)


@router.post("/internal/analytics/v1/course/study-time", include_in_schema=False)
async def course_study_time(request: Request):
    return await _course_aggregate(request, study_time=True)


async def _course_aggregate(request: Request, *, study_time: bool):
    state = request.app.state
    if not getattr(state, "trust_manifest_validated", False) or state.grant_verifier is None:
        return _error("unavailable", 503)
    try:
        verify = state.grant_verifier.verify_course_study_time if study_time else state.grant_verifier.verify_course_visitors
        grant = verify(extract_bearer(request.scope["headers"]))
    except AuthenticationError:
        return _error("unauthorized", 401)
    if request.query_params or request.headers.get("content-type") != "application/json":
        return _error("invalid_request", 400)

    if state.course_visitor_slots.locked():
        return _error("unavailable", 503)
    try:
        async with state.course_visitor_slots, asyncio.timeout(state.settings.db_timeout_seconds):
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    return _error("invalid_request", 413)
                body.extend(chunk)
            if not compare_digest(sha256(body).hexdigest(), grant.body_sha256):
                return _error("forbidden", 403)
            validator = _STUDY_TIME_REQUEST if study_time else _REQUEST
            snapshot = validator.validate_json(body)
            if not 0 <= grant.iat - int(snapshot.at.timestamp()) <= 15:
                return _error("invalid_request", 400)
            if time.time() >= grant.exp:
                return _error("forbidden", 403)
            if await state.probe.is_ready() is not True:
                return _error("unavailable", 503)
            if study_time:
                reports = getattr(state, "activity_reports", None)
                if not isinstance(reports, PostgresProjectionActivityReport):
                    return _error("unavailable", 503)
                result = await read_course_study_time(reports, grant, snapshot)
            elif isinstance(snapshot, CourseVisitorDiscovery):
                result = await discover_course_visitor_candidates(state.database, grant, snapshot)
            else:
                result = await read_course_visitor_report(state.database, grant, snapshot)
            return JSONResponse(result.model_dump(mode="json"), headers={"Cache-Control": "no-store"})
    except (ValidationError, ValueError, OverflowError):
        return _error("invalid_request", 400)
    except AuthorizationError:
        return _error("forbidden", 403)
    except Exception:
        return _error("unavailable", 503)
