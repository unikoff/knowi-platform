"""Internal read bridge; G1 grant and privacy barrier required on every call."""
import asyncio
from datetime import date, datetime, timedelta
import time
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from pymentor_analytics.adapters.auth.http import extract_bearer
from pymentor_analytics.contracts.auth_v1 import AuthenticationError, AuthorizationError
from pymentor_analytics.modules.collector.ports import IntakeForbidden

router = APIRouter()


class CoverageSubject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_ref: UUID
    coverage_through_revision: int = Field(ge=0, le=9223372036854775807)
    source_snapshot_at: str
    student: bool


class CompletionCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_ref: UUID
    coverage_through_revision: int = Field(ge=0, le=9223372036854775807)
    source_snapshot_at: datetime


class CompletionDay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    lessons_completed: int = Field(ge=0)


class CompletionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_lessons_completed: list[CompletionDay] = Field(min_length=30, max_length=30)
    lessons_completed_7d: int = Field(ge=0)
    weekly_by_subject: dict[UUID, int] = Field(max_length=80)
    coverage_subjects: list[CompletionCoverage] = Field(max_length=80)


class CourseCompletionSummary(CompletionSummary):
    completed_lessons_month: int | None = Field(ge=0)


class LegacyEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"dependentRequired": {
        "browser_quality": ["browser_as_of"], "browser_as_of": ["browser_quality"]}, "allOf": [{
        "if": {"properties": {"coverage": {"const": "complete"}}},
        "then": {"required": ["source_hwm", "freshness"], "oneOf": [
            {"required": ["coverage_through_revision", "source_snapshot_at"], "not": {"required": ["coverage_subjects"]}},
            {"required": ["coverage_subjects"], "not": {"anyOf": [{"required": ["coverage_through_revision"]}, {"required": ["source_snapshot_at"]}]}}]},
        "else": {"not": {"anyOf": [{"required": ["coverage_through_revision"]}, {"required": ["source_snapshot_at"]},
                                     {"required": ["coverage_subjects"]}, {"required": ["source_hwm"]}, {"required": ["freshness"]}]}}
    }]})
    coverage: Literal["unknown", "complete"]
    data: dict
    coverage_through_revision: int | None = Field(default=None, ge=0, le=9223372036854775807)
    source_snapshot_at: str | None = None
    coverage_subjects: list[CoverageSubject] | None = Field(default=None, max_length=80)
    source_hwm: None = None
    freshness: Literal["unknown"] | None = None
    browser_quality: Literal["unknown"] | None = None
    browser_as_of: str | None = None


class HistoryDay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    theory_ms: Literal[3600000]
    practice_ms: Literal[3600000]
    provenance: Literal["core_legacy_conditional"]


class HistoryCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_ref: UUID
    coverage_through_revision: int = Field(ge=0, le=9223372036854775807)
    source_snapshot_at: str


class HistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: list[HistoryDay] = Field(max_length=31)
    coverage: HistoryCoverage


errors = {code: {"description": description} for code, description in {
    400: "Invalid parameters", 401: "Invalid grant", 403: "Scope or subject denied",
    503: "Privacy, schema, roster or storage unavailable"}.items()}


async def _run(request, operation, *, course_only=False):
    state = request.app.state
    if not getattr(state, "trust_manifest_validated", False):
        return JSONResponse({"error": "unavailable"}, 503)
    try:
        if state.grant_verifier is None:
            raise AuthenticationError()
        grant = state.grant_verifier.verify(extract_bearer(request.scope["headers"]))
    except AuthenticationError:
        return JSONResponse({"error": "unauthorized"}, 401, headers={"WWW-Authenticate": "Bearer"})
    if grant.scope != "report:read" or (grant.course_completion_roster_v1 is not None) != course_only:
        return JSONResponse({"error": "forbidden"}, 403)
    try:
        timeout = state.settings.db_timeout_seconds
        deadline = time.monotonic() + timeout
        async with asyncio.timeout(timeout):
            if await state.core_transfer_probe.is_ready() is not True:
                return JSONResponse({"error": "unavailable"}, 503)
            return JSONResponse(await operation(state.legacy_service, grant, deadline))
    except ValueError:
        return JSONResponse({"error": "invalid_request"}, 400)
    except (AuthorizationError, IntakeForbidden):
        return JSONResponse({"error": "forbidden"}, 403)
    except Exception:
        return JSONResponse({"error": "unavailable"}, 503)


@router.get("/internal/analytics/v1/legacy/history", response_model=HistoryResponse, responses=errors,
            openapi_extra={"security": [{"AnalyticsGrant": []}], "parameters": [
                {"name": name, "in": "query", "required": True, "schema": {"type": "string", "format": "date"}}
                for name in ("start", "end")]})
async def history_route(request: Request):
    async def operation(service, grant, deadline):
        params = request.query_params
        if set(params) != {"start", "end"} or any(len(params.getlist(key)) != 1 for key in ("start", "end")):
            raise ValueError
        start, end = (date.fromisoformat(params[key]) for key in ("start", "end"))
        if start.isoformat() != params["start"] or end.isoformat() != params["end"]:
            raise ValueError
        return await service.read_history(grant, start, end, deadline=deadline)
    return await _run(request, operation)


@router.get("/internal/analytics/v1/legacy/calendar", response_model=LegacyEnvelope, responses=errors,
            openapi_extra={"security": [{"AnalyticsGrant": []}], "parameters": [
                {"name": "year", "in": "query", "required": True, "schema": {"type": "integer", "minimum": 2000, "maximum": 2100}},
                {"name": "month", "in": "query", "required": True, "schema": {"type": "integer", "minimum": 1, "maximum": 12}}]})
async def calendar_route(request: Request):
    async def operation(service, grant, deadline):
        params = request.query_params
        if set(params) != {"year", "month"} or any(len(params.getlist(key)) != 1 for key in ("year", "month")):
            raise ValueError
        year, month = params["year"], params["month"]
        if not year.isascii() or not year.isdecimal() or len(year) != 4 or not month.isascii() or not month.isdecimal() or len(month) > 2:
            raise ValueError
        return await service.read_calendar(grant, int(year), int(month), deadline=deadline)
    return await _run(request, operation)


@router.get("/internal/analytics/v1/legacy/summary", response_model=LegacyEnvelope, responses=errors,
            openapi_extra={"security": [{"AnalyticsGrant": []}]})
async def summary_route(request: Request):
    async def operation(service, grant, deadline):
        if request.query_params:
            raise ValueError
        return await service.read_summary(grant, deadline=deadline)
    return await _run(request, operation)


@router.get("/internal/analytics/v1/legacy/admin/activity", response_model=LegacyEnvelope, responses=errors,
            openapi_extra={"security": [{"AnalyticsGrant": []}]})
async def admin_route(request: Request):
    async def operation(service, grant, deadline):
        if request.query_params:
            raise ValueError
        return await service.read_admin(grant, deadline=deadline)
    return await _run(request, operation)


@router.get("/internal/analytics/v1/legacy/admin/completions", response_model=CompletionSummary, responses=errors,
            openapi_extra={"security": [{"AnalyticsGrant": []}], "parameters": [
                {"name": "at", "in": "query", "required": True, "schema": {"type": "string", "format": "date-time"}}]})
async def admin_completions_route(request: Request):
    async def operation(service, grant, deadline):
        params = request.query_params
        if set(params) != {"at"} or len(params.getlist("at")) != 1 or len(params["at"]) > 32:
            raise ValueError
        at = datetime.fromisoformat(params["at"])
        if at.utcoffset() != timedelta(0) or at.isoformat() != params["at"]:
            raise ValueError
        return await service.read_admin_completions(grant, at, deadline=deadline)
    return await _run(request, operation)


@router.get("/internal/analytics/v1/legacy/course/completions", response_model=CourseCompletionSummary,
            responses=errors, openapi_extra={"security": [{"AnalyticsGrant": []}], "parameters": [
                {"name": "course_id", "in": "query", "required": True, "schema": {"type": "string", "maxLength": 80}},
                {"name": "at", "in": "query", "required": True, "schema": {"type": "string", "format": "date-time"}},
                {"name": "year", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 2000, "maximum": 2100}},
                {"name": "month", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 1, "maximum": 12}}]})
async def course_completions_route(request: Request):
    async def operation(service, grant, deadline):
        params = request.query_params
        if set(params) not in ({"course_id", "at"}, {"course_id", "at", "year", "month"}) or any(len(params.getlist(k)) != 1 for k in params):
            raise ValueError
        course, stamp = params["course_id"], params["at"]
        if not 1 <= len(course) <= 80 or len(stamp) > 32:
            raise ValueError
        at = datetime.fromisoformat(stamp)
        if at.utcoffset() != timedelta(0) or at.isoformat() != stamp:
            raise ValueError
        year = month = None
        if "year" in params:
            y, m = params["year"], params["month"]
            if not (y.isascii() and y.isdecimal() and len(y) == 4 and m.isascii() and m.isdecimal() and len(m) <= 2):
                raise ValueError
            year, month = int(y), int(m)
            if not (2000 <= year <= 2100 and 1 <= month <= 12):
                raise ValueError
        return await service.read_course_completions(grant, course, at, year, month, deadline=deadline)
    return await _run(request, operation, course_only=True)


@router.get("/internal/analytics/v1/legacy/admin/profile", response_model=LegacyEnvelope, responses=errors,
            openapi_extra={"security": [{"AnalyticsGrant": []}], "parameters": [
                {"name": "subject_ref", "in": "query", "required": True, "schema": {"type": "string", "format": "uuid"}},
                {"name": "year", "in": "query", "required": True, "schema": {"type": "integer", "minimum": 2000, "maximum": 2100}},
                {"name": "month", "in": "query", "required": True, "schema": {"type": "integer", "minimum": 1, "maximum": 12}}]})
async def admin_profile_route(request: Request):
    async def operation(service, grant, deadline):
        params = request.query_params
        if set(params) != {"subject_ref", "year", "month"} or any(len(params.getlist(key)) != 1 for key in ("subject_ref", "year", "month")):
            raise ValueError
        subject, year, month = params["subject_ref"], params["year"], params["month"]
        if not year.isascii() or not year.isdecimal() or len(year) != 4 or not month.isascii() or not month.isdecimal() or len(month) > 2:
            raise ValueError
        target = UUID(subject)
        if str(target) != subject:
            raise ValueError
        return await service.read_admin_profile(grant, target, int(year), int(month), deadline=deadline)
    return await _run(request, operation)
