"""Activity route; factory must explicitly install and configure it to open access."""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone as dt_timezone
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError, model_validator
from pymentor_analytics.adapters.auth.http import extract_bearer
from pymentor_analytics.adapters.postgres.activity_projection_reports import ProjectionPending, ProjectionUnavailable
from pymentor_analytics.adapters.postgres.activity_reports import ReportLimitExceeded
from pymentor_analytics.contracts.auth_v1 import AuthenticationError, AuthorizationError, FenceUnavailable
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.reports_v1 import ActivityDay, ActivityQuery, ActivityReport, ActivityDetailsReport, TrustedProgress
from pymentor_analytics.contracts.reports_v1 import ActivityBreakdownRow, ChapterActivityRequest, ChapterActivityReport, ChapterActivityRow
from pymentor_analytics.contracts.reports_v1 import (
    ActivityLifetimeQuery, ActivityLifetimeSnapshot, LifetimeActivityRequest, OutlineActivityLesson, OutlineActivityReport,
    StudentLifetimeActivityRequest,
)
from pymentor_analytics.modules.analytics.reports.activity_scope import resolve_scope
from pymentor_analytics.contracts.v2 import AwareTime, Contract, ErrorResponse, Identifier
from pymentor_analytics.adapters.http.errors import error_response

router = APIRouter()
logger = logging.getLogger(__name__)


def _report_unavailable(reason: str):
    logger.warning("activity_report_unavailable reason=%s", reason)
    return error_response(503, "unavailable", "Report unavailable")


_REPORT_FAILURES = (
    (ProjectionPending, "projection_pending_timeout"),
    (ProjectionUnavailable, "projection_invalid"),
    (ReportLimitExceeded, "report_limit"),
    (PrivacyUnavailable, "privacy_unavailable"),
    (FenceUnavailable, "fence_unavailable"),
    (TimeoutError, "report_deadline"),
)


def _report_failure(error: Exception):
    return _report_unavailable(next((reason for kind, reason in _REPORT_FAILURES if isinstance(error, kind)), "query_failure"))


class CalendarRevision(Contract):
    date: Annotated[date, Field(strict=False)]
    token: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CalendarRequest(Contract):
    course_id: Identifier
    from_time: Annotated[AwareTime, Field(strict=False)]
    to_time: Annotated[AwareTime, Field(strict=False)]
    known_generation: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None
    known_revisions: Annotated[list[CalendarRevision], Field(max_length=28)]

    @model_validator(mode="after")
    def unique_dates(self):
        if len({item.date for item in self.known_revisions}) != len(self.known_revisions):
            raise ValueError("duplicate dates")
        return self


class CalendarRevisionOutput(Contract):
    date: date
    token: str | None


class CalendarResponse(Contract):
    history_generation: str
    revisions: list[CalendarRevisionOutput]
    reused_dates: list[date]
    daily: list[ActivityDay]
    as_of: AwareTime | None


@router.post("/api/analytics/reports/activity/calendar", operation_id="getActivityCalendar", response_model=CalendarResponse,
             openapi_extra={"security": [{"AnalyticsGrant": []}]})
async def activity_calendar(request: Request, body: CalendarRequest):
    state = request.app.state
    if not getattr(state, "trust_manifest_validated", False):
        return _report_unavailable("query_not_ready")
    try:
        token = extract_bearer(request.scope["headers"])
        verifier = getattr(state, "grant_verifier", None)
        if verifier is None:
            raise AuthenticationError()
        grant = verifier.verify(token)
    except AuthenticationError:
        return error_response(401, "unauthorized", "Invalid credential", {"WWW-Authenticate": "Bearer"})
    if grant.role != "user" or grant.scope != "report:read" or grant.global_read or grant.subject_refs:
        return error_response(403, "forbidden", "Access denied")
    try:
        query = ActivityQuery(from_time=body.from_time, to_time=body.to_time, course_id=body.course_id, subject_ref=None)
        from pymentor_analytics.adapters.postgres.activity_history_revisions import whole_days
        if not whole_days(query):
            return error_response(400, "invalid_request", "Invalid report query")
        resolve_scope(grant, query)
    except ValidationError:
        return error_response(400, "invalid_request", "Invalid report query")
    except AuthorizationError:
        return error_response(403, "forbidden", "Access denied")
    store, probe = getattr(state, "activity_reports", None), getattr(state, "probe", None)
    if store is None or probe is None:
        return _report_unavailable("query_not_ready")
    if not getattr(store, "day_projection_enabled", False) or not hasattr(store, "_guarded_snapshot"):
        return error_response(501, "unavailable", "Calendar revisions unavailable")
    try:
        timeout = state.settings.db_timeout_seconds
        if not await asyncio.wait_for(probe.is_ready(), timeout=timeout):
            return _report_unavailable("query_not_ready")
        from pymentor_analytics.adapters.postgres.activity_calendar import read_calendar
        result = await asyncio.wait_for(read_calendar(store, grant, query, body.known_generation, body.known_revisions), timeout=timeout)
        from fastapi.encoders import jsonable_encoder
        return JSONResponse(jsonable_encoder(result), headers={"Cache-Control": "no-store"})
    except AuthorizationError:
        return error_response(403, "forbidden", "Access denied")
    except Exception as error:
        return _report_failure(error)

@router.get("/api/analytics/reports/activity", response_model=ActivityReport,
            operation_id="getActivityReport", responses={
                400: {"model": ErrorResponse}, 401: {"model": ErrorResponse},
                403: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
            openapi_extra={"security": [{"AnalyticsGrant": []}]})
async def activity_report(request: Request, response: Response, from_time: datetime, to_time: datetime,
                          course_id: str | None = None, subject_ref: UUID | None = None,
                          lesson_id: str | None = None,
                          history_revisions: bool = False,
                          timezone: Annotated[str, Query()] = "Europe/Moscow"):
    state = request.app.state
    if not getattr(state, "trust_manifest_validated", False):
        return error_response(503, "unavailable", "Report unavailable")
    try:
        token = extract_bearer(request.scope["headers"])
        verifier = getattr(state, "grant_verifier", None)
        if verifier is None:
            raise AuthenticationError()
        grant = verifier.verify(token)
    except AuthenticationError:
        return error_response(401, "unauthorized", "Invalid credential", {"WWW-Authenticate": "Bearer"})
    if grant.scope not in {"report:read", "report:student"}:
        return error_response(403, "forbidden", "Access denied")
    allowed = {"from_time", "to_time", "course_id", "subject_ref", "timezone", "lesson_id", "history_revisions"}
    params = list(request.query_params.multi_items())
    if any(key not in allowed for key, _ in params) or len(params) != len({key for key, _ in params}):
        return error_response(400, "invalid_request", "Invalid report query")
    try:
        query = ActivityQuery(from_time=from_time, to_time=to_time, course_id=course_id,
                              subject_ref=subject_ref, timezone=timezone, lesson_id=lesson_id)
    except ValidationError:
        return error_response(400, "invalid_request", "Invalid report query")
    if history_revisions:
        from pymentor_analytics.adapters.postgres.activity_history_revisions import whole_days
        if grant.role != "user" or grant.scope != "report:read" or grant.global_read or grant.subject_refs:
            return error_response(403, "forbidden", "Access denied")
        if query.course_id is None or query.subject_ref is not None or query.lesson_id is not None or not whole_days(query):
            return error_response(400, "invalid_request", "Invalid report query")
        try:
            resolve_scope(grant, query)
        except AuthorizationError:
            return error_response(403, "forbidden", "Access denied")
    store = getattr(state, "activity_reports", None)
    probe = getattr(state, "probe", None)
    if store is None or probe is None:
        return error_response(503, "unavailable", "Report unavailable")
    try:
        timeout = state.settings.db_timeout_seconds
        if not await asyncio.wait_for(probe.is_ready(), timeout=timeout):
            return _report_unavailable("query_not_ready")
        if history_revisions and getattr(store, "supports_history_revisions", False) is True:
            call = store.read_activity(grant, query, include_history_revisions=True)
        else:
            call = store.read_activity(grant, query)
        snapshot = await asyncio.wait_for(call, timeout=timeout)
        revisions = snapshot.pop("history_revisions", None)
        generation = snapshot.pop("history_generation", None)
        response.headers["Cache-Control"] = "no-store"
        report = ActivityReport(status="ok", from_time=query.from_time, to_time=query.to_time,
                                timezone=query.timezone, trusted_progress=None, **snapshot)
        if history_revisions:
            if revisions is None:
                from pymentor_analytics.adapters.postgres.activity_history_revisions import MOSCOW
                today = datetime.now(dt_timezone.utc).astimezone(MOSCOW).date()
                revisions = [{"date": (today-timedelta(days=offset)).isoformat(), "token": None}
                             for offset in range(29, 1, -1)]
            return JSONResponse({**report.model_dump(mode="json"), "history_revisions": revisions,
                                 "history_generation": generation},
                                headers={"Cache-Control": "no-store"})
        return report
    except AuthorizationError:
        return error_response(403, "forbidden", "Access denied")
    except Exception as error:
        return _report_failure(error)

@router.get("/api/analytics/reports/activity/details", response_model=ActivityDetailsReport,
            operation_id="getActivityReportDetails", responses={
                400: {"model": ErrorResponse}, 401: {"model": ErrorResponse},
                403: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
            openapi_extra={"security": [{"AnalyticsGrant": []}]})
async def activity_report_details(request: Request, from_time: datetime, to_time: datetime,
                                  course_id: str | None = None, subject_ref: UUID | None = None,
                                  lesson_id: str | None = None,
                                  timezone: Annotated[str, Query()] = "Europe/Moscow",
                                  offset: Annotated[int, Query(ge=0, le=20000)] = 0,
                                  limit: Annotated[int, Query(ge=1, le=100)] = 100):
    state = request.app.state
    if not getattr(state, "trust_manifest_validated", False):
        return error_response(503, "unavailable", "Report unavailable")
    try:
        token = extract_bearer(request.scope["headers"])
        verifier = getattr(state, "grant_verifier", None)
        if verifier is None:
            raise AuthenticationError()
        grant = verifier.verify(token)
    except AuthenticationError:
        return error_response(401, "unauthorized", "Invalid credential", {"WWW-Authenticate": "Bearer"})
    if grant.scope != "report:read":
        return error_response(403, "forbidden", "Access denied")
    allowed = {"from_time", "to_time", "course_id", "subject_ref", "timezone", "lesson_id", "offset", "limit"}
    params = list(request.query_params.multi_items())
    if any(key not in allowed for key, _ in params) or len(params) != len({key for key, _ in params}):
        return error_response(400, "invalid_request", "Invalid report query")
    try:
        query = ActivityQuery(from_time=from_time, to_time=to_time, course_id=course_id,
                              subject_ref=subject_ref, timezone=timezone, lesson_id=lesson_id)
    except ValidationError:
        return error_response(400, "invalid_request", "Invalid report query")
    store, probe = getattr(state, "activity_reports", None), getattr(state, "probe", None)
    if store is None or probe is None:
        return error_response(503, "unavailable", "Report unavailable")
    try:
        timeout = state.settings.db_timeout_seconds
        if not await asyncio.wait_for(probe.is_ready(), timeout=timeout):
            return _report_unavailable("query_not_ready")
        snapshot = await asyncio.wait_for(store.read_activity(grant, query, include_breakdown=True), timeout=timeout)
        breakdown = snapshot.pop("breakdown")
        report = ActivityReport(status="ok", from_time=query.from_time, to_time=query.to_time,
                                timezone=query.timezone,
                                trusted_progress=TrustedProgress(state="unavailable", completed_lessons=None), **snapshot)
        next_offset = offset + limit if offset + limit < len(breakdown) else None
        return ActivityDetailsReport(report=report, lesson_id=lesson_id,
                                     rows=breakdown[offset:offset + limit], next_offset=next_offset)
    except AuthorizationError:
        return error_response(403, "forbidden", "Access denied")
    except Exception as error:
        return _report_failure(error)

@router.post("/api/analytics/reports/activity/chapters", response_model=ChapterActivityReport,
             operation_id="getChapterActivityReport", responses={
                 401: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
                 503: {"model": ErrorResponse}},
             openapi_extra={"security": [{"AnalyticsGrant": []}]})
async def chapter_activity_report(request: Request, body: ChapterActivityRequest):
    return await _selected_activity_report(request, body, include_lessons=False)


@router.post("/api/analytics/reports/activity/outline", response_model=OutlineActivityReport,
             operation_id="getOutlineActivityReport", responses={
                 400: {"model": ErrorResponse}, 401: {"model": ErrorResponse},
                 403: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
             openapi_extra={"security": [{"AnalyticsGrant": []}]})
async def outline_activity_report(request: Request, body: ChapterActivityRequest):
    return await _selected_activity_report(request, body, include_lessons=True)


@router.post("/api/analytics/reports/activity/outline/lifetime", response_model=OutlineActivityReport,
             operation_id="getLifetimeActivityOutline", responses={
                 400: {"model": ErrorResponse}, 401: {"model": ErrorResponse},
                 403: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
             openapi_extra={"security": [{"AnalyticsGrant": []}]})
async def lifetime_activity_outline(request: Request, body: LifetimeActivityRequest):
    return await _selected_activity_report(request, body, include_lessons=True)


@router.post("/api/analytics/reports/activity/outline/lifetime/student", response_model=OutlineActivityReport,
             operation_id="getStudentLifetimeActivityOutline", responses={
                 400: {"model": ErrorResponse}, 401: {"model": ErrorResponse},
                 403: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
             openapi_extra={"security": [{"AnalyticsGrant": []}]})
async def student_lifetime_activity_outline(request: Request, response: Response, body: StudentLifetimeActivityRequest):
    response.headers["Cache-Control"] = "no-store"
    return await _selected_activity_report(request, body, include_lessons=True)


async def _selected_activity_report(
    request: Request, body: ChapterActivityRequest | LifetimeActivityRequest, *, include_lessons: bool,
):
    state = request.app.state
    if not getattr(state, "trust_manifest_validated", False):
        return error_response(503, "unavailable", "Report unavailable")
    try:
        token = extract_bearer(request.scope["headers"])
        verifier = getattr(state, "grant_verifier", None)
        if verifier is None:
            raise AuthenticationError()
        grant = verifier.verify(token)
    except AuthenticationError:
        return error_response(401, "unauthorized", "Invalid credential", {"WWW-Authenticate": "Bearer"})
    student_request = isinstance(body, StudentLifetimeActivityRequest)
    scope = "report:student" if student_request else "report:read"
    roles = {"admin", "leader"} if student_request else {"user"}
    if grant.scope != scope or grant.role not in roles:
        return error_response(403, "forbidden", "Access denied")
    if include_lessons and request.query_params:
        return error_response(400, "invalid_request", "Invalid report query")
    if isinstance(body, LifetimeActivityRequest):
        now = datetime.now(dt_timezone.utc)
        query = ActivityLifetimeQuery(course_id=body.course_id, subject_ref=body.subject_ref if student_request else None,
                                      to_time=now.replace(microsecond=now.microsecond // 1000 * 1000))
    else:
        query = ActivityQuery(from_time=body.from_time, to_time=body.to_time,
                              course_id=body.course_id, subject_ref=None)
    store, probe = getattr(state, "activity_reports", None), getattr(state, "probe", None)
    if store is None or probe is None:
        return error_response(503, "unavailable", "Report unavailable")
    try:
        resolve_scope(grant, query)
        timeout = state.settings.db_timeout_seconds
        if not await asyncio.wait_for(probe.is_ready(), timeout=timeout):
            return _report_unavailable("query_not_ready")
        if isinstance(query, ActivityLifetimeQuery):
            read_lifetime = getattr(store, "read_lifetime_activity", None)
            if read_lifetime is None:
                return _report_unavailable("lifetime_projection_required")
            snapshot = await asyncio.wait_for(read_lifetime(grant, query), timeout=timeout)
            breakdown = snapshot.pop("breakdown")
            report = ActivityLifetimeSnapshot.model_validate(snapshot)
        else:
            snapshot = await asyncio.wait_for(store.read_activity(grant, query, include_breakdown=True), timeout=timeout)
            breakdown = snapshot.pop("breakdown")
            report = ActivityReport(status="ok", from_time=query.from_time, to_time=query.to_time,
                                    timezone=query.timezone, trusted_progress=None, **snapshot)
        if include_lessons and report.course_id != body.course_id:
            raise ValueError("report course does not match selection")
        membership = {lesson: chapter.chapter_id for chapter in body.chapters for lesson in chapter.lesson_ids}
        fields = ("active_ms", "theory_ms", "practice_ms")
        sums = {chapter.chapter_id: dict.fromkeys(fields, 0) for chapter in body.chapters}
        observed: dict[str, OutlineActivityLesson] = {}
        for item in breakdown:
            row = ActivityBreakdownRow.model_validate(item)
            if row.lesson_id is None or row.course_id != body.course_id:
                continue
            if include_lessons:
                if row.lesson_id in observed:
                    raise ValueError("duplicate lesson in activity snapshot")
                observed[row.lesson_id] = OutlineActivityLesson(
                    lesson_id=row.lesson_id, active_ms=row.active_ms, theory_ms=row.theory_ms, practice_ms=row.practice_ms,
                )
            chapter_id = membership.get(row.lesson_id)
            if chapter_id is not None:
                for field in fields:
                    sums[chapter_id][field] += getattr(row, field)
        rows = [ChapterActivityRow(chapter_id=chapter.chapter_id, **{
            field: value if value or report.quality == "complete" else None
            for field, value in sums[chapter.chapter_id].items()}) for chapter in body.chapters]
        result = ChapterActivityReport(course_id=body.course_id, from_time=query.from_time, to_time=query.to_time,
                                       status=report.status, quality=report.quality, as_of=report.as_of, chapters=rows)
        if not include_lessons:
            return result
        # A course opening counts as presence with no duration. All observed
        # course duration must be attributed to lesson rows, including unmapped
        # lessons, before an absent row can be reported as zero.
        if report.totals is None or any(
            sum(getattr(row, field) for row in observed.values()) != getattr(report.totals, field)
            for field in fields
        ):
            return _report_unavailable("breakdown_inconsistent")
        lessons = [
            observed[lesson_id] if lesson_id in observed else
            OutlineActivityLesson(lesson_id=lesson_id, active_ms=0, theory_ms=0, practice_ms=0)
            for lesson_id in membership
        ]
        return OutlineActivityReport(
            **result.model_dump(),
            lessons=lessons,
            unmapped_lessons=[observed[lesson_id] for lesson_id in sorted(observed.keys() - membership.keys())],
        )
    except AuthorizationError:
        return error_response(403, "forbidden", "Access denied")
    except Exception as error:
        return _report_failure(error)
