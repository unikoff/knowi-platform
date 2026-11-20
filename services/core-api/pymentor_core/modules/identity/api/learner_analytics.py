"""Read learner history and measured dates through Core's session."""
from calendar import monthrange
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, Security
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from pymentor_core.modules.analytics_delivery.errors import LearnerReadUnavailable
from pymentor_core.app.async_request import run_request_sql
from pymentor_core.app.wiring import read_learner_completions
from pymentor_core.modules.analytics_delivery.grants import authorize_grant
from pymentor_core.modules.analytics_delivery.source_watermark import capture
from pymentor_core.modules.identity.api.sessions import async_current_principal, session_cookie_scheme
from pymentor_core.modules.identity.activity_policy import MOSCOW_TZ
from pymentor_core.modules.identity.learner_statistics import LearnerReport, ProjectionReport

router = APIRouter(dependencies=[Security(session_cookie_scheme)])


class HistoryDay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    theory_ms: Literal[3600000]
    practice_ms: Literal[3600000]
    provenance: Literal["core_legacy_conditional"]


class HistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: list[HistoryDay] = Field(max_length=31)


class MeasuredCalendarResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: list[date] = Field(max_length=31)
    current_streak: int | None = Field(ge=0, le=31, strict=True)
    streak_is_lower_bound: bool = Field(strict=True)
    coverage: Literal["unknown", "partial", "complete"]
    as_of: AwareDatetime | None
    course_id: str | None = None


def _validate_days(days, start, end):
    if days != sorted(set(days)) or any(not start <= day <= end for day in days):
        raise LearnerReadUnavailable()


async def _read(request, kind, params, *, course_id=None):
    principal = await async_current_principal(request)
    state = request.app.state
    client = getattr(state, "analytics_learner_client", None)
    if client is None:
        raise LearnerReadUnavailable()

    def authorize(db):
        if kind == "history":
            claims, revision = capture(db, principal, "calendar")
        else:
            claims = authorize_grant(db, principal, purpose="self_report", course_id=course_id, target_ref=None)
            revision = None
        if claims["role"] != "user":
            raise HTTPException(403, "Learner Analytics unavailable for this role")
        return claims, revision

    before = await run_request_sql(request, authorize)
    claims, revision = before
    grant = state.analytics_grant_signer.grant(**claims)
    value = await client.read(kind, grant, params)
    after = await run_request_sql(request, authorize)
    if after != before:
        raise LearnerReadUnavailable()
    if kind == "history":
        if set(value) != {"coverage", "days"}:
            raise LearnerReadUnavailable()
        proof = value["coverage"]
        if (not isinstance(proof, dict) or set(proof) != {"subject_ref", "coverage_through_revision", "source_snapshot_at"}
                or proof["subject_ref"] != claims["subject"] or type(proof["coverage_through_revision"]) is not int
                or proof["coverage_through_revision"] != revision or not isinstance(proof["source_snapshot_at"], str)
                or datetime.fromisoformat(proof["source_snapshot_at"]).utcoffset() != timedelta(0)):
            raise LearnerReadUnavailable()
        return {"days": value["days"]}
    return value


def _request_keys(request, keys):
    params = request.query_params
    return set(params) == keys and all(len(params.getlist(key)) == 1 for key in keys)


def _unavailable():
    return HTTPException(503, "Learner Analytics unavailable", headers={"Cache-Control": "no-store"})


@router.get("/analytics/statistics", response_model=LearnerReport, responses={401: {}, 403: {}, 503: {}})
async def statistics(
    request: Request, response: Response,
    days: int = Query(ge=7, le=30, json_schema_extra={"enum": [7, 30]}),
    course_id: str | None = Query(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
):
    params = {"days"} | ({"course_id"} if course_id is not None else set())
    if days not in (7, 30) or not _request_keys(request, params):
        raise HTTPException(422, "Invalid statistics query")
    try:
        principal = await async_current_principal(request)
        now = datetime.now(timezone.utc)
        now = now.replace(microsecond=now.microsecond // 1000 * 1000)
        today = now.astimezone(MOSCOW_TZ).date()
        start_day = today - timedelta(days=days - 1)
        start = datetime.combine(start_day, time.min, MOSCOW_TZ).astimezone(timezone.utc)

        def capture_progress(db):
            claims = authorize_grant(db, principal, purpose="self_report", course_id=course_id, target_ref=None)
            if claims["role"] != "user":
                raise HTTPException(403, "Learner Analytics unavailable for this role")
            return claims, read_learner_completions(db, principal, course_id, start, now)

        before = await run_request_sql(request, capture_progress)
        if not getattr(request.app.state, "analytics_grant_signer", None):
            raise LearnerReadUnavailable()
        raw = await _read(request, "statistics", {"from_time": start.isoformat(), "to_time": now.isoformat()})
        projection = ProjectionReport.model_validate(raw)
        if (projection.from_time != start or projection.to_time != now
                or any(not start_day <= row.date <= today for row in projection.daily)):
            raise LearnerReadUnavailable()
        history_days = set()
        try:
            history_value = await _read(request, "history", {"start": start_day.isoformat(), "end": today.isoformat()})
            history_result = HistoryResponse.model_validate(history_value)
            history_dates = [item.date for item in history_result.days]
            _validate_days(history_dates, start_day, today)
            history_days = set(history_dates)
        except (LearnerReadUnavailable, ValidationError, ValueError):
            pass
        except HTTPException as exc:
            if exc.status_code != 503:
                raise

        current = await async_current_principal(request)
        if (current.user_id, current.account_ref) != (principal.user_id, principal.account_ref):
            raise LearnerReadUnavailable()
        after = await run_request_sql(request, capture_progress)
        if after != before:
            raise LearnerReadUnavailable()
        completion_counts = Counter(datetime.fromisoformat(item[2]).astimezone(MOSCOW_TZ).date() for item in before[1])
        measured_days = {row.date: row for row in projection.daily}

        def seconds(value):
            return value / 1000 if value else None

        output_days = []
        for offset in range(days):
            day = start_day + timedelta(days=offset)
            row = measured_days.get(day)
            output_days.append({"date": day, "visited": True if day in history_days or row and row.unique_visitors else None,
                "completed_lessons": completion_counts[day],
                "active_seconds": seconds(row.active_ms) if row else None,
                "theory_seconds": seconds(row.theory_ms) if row else None,
                "practice_seconds": seconds(row.practice_ms) if row else None})
        totals = projection.totals
        result = LearnerReport.model_validate({"schema_version": 1, "source": "analytics", "as_of": now,
            "period": {"days": days, "start": start_day, "end": today, "timezone": "Europe/Moscow"},
            "scope": {"course_id": course_id, "activity": "platform"},
            "coverage": {"visits": "partial", "time": "partial" if totals.active_ms else "unavailable", "progress": "core"},
            "summary": {"active_days_total": None, "current_streak": None, "active_days_period": None,
                "completed_lessons_period": sum(completion_counts.values()),
                "active_seconds": seconds(totals.active_ms), "theory_seconds": seconds(totals.theory_ms),
                "practice_seconds": seconds(totals.practice_ms)}, "days": output_days})
        response.headers["Cache-Control"] = "no-store"
        return result
    except (LearnerReadUnavailable, ValidationError, ValueError):
        raise _unavailable() from None
    except HTTPException as exc:
        if exc.status_code == 503:
            raise _unavailable() from None
        raise


@router.get("/activity/history", response_model=HistoryResponse, responses={401: {}, 403: {}, 503: {}})
async def history(request: Request, response: Response, start: date, end: date):
    if (not _request_keys(request, {"start", "end"}) or not 2000 <= start.year <= end.year <= 2100
            or not 0 <= (end - start).days <= 30
            or request.query_params["start"] != start.isoformat() or request.query_params["end"] != end.isoformat()):
        raise HTTPException(422, "Invalid history period")
    try:
        value = await _read(request, "history", {"start": start.isoformat(), "end": end.isoformat()})
        result = HistoryResponse.model_validate(value)
        _validate_days([item.date for item in result.days], start, end)
    except (LearnerReadUnavailable, ValidationError, ValueError):
        raise _unavailable() from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/activity/measured", response_model=MeasuredCalendarResponse, response_model_exclude_unset=True,
            responses={401: {}, 403: {}, 404: {}, 503: {}})
async def measured(
    request: Request, response: Response, year: int = Query(ge=2000, le=2100), month: int = Query(ge=1, le=12),
    course_id: str | None = Query(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
):
    params = {"year": year, "month": month}
    if course_id is not None:
        params["course_id"] = course_id
    if not _request_keys(request, set(params)):
        raise HTTPException(422, "Invalid calendar period")
    try:
        value = await _read(request, "measured", params, course_id=course_id)
        result = MeasuredCalendarResponse.model_validate(value)
        if result.course_id != course_id or ("course_id" in value) != (course_id is not None):
            raise LearnerReadUnavailable()
        _validate_days(result.days, date(year, month, 1), date(year, month, monthrange(year, month)[1]))
        if (result.current_streak == 0 and result.coverage != "complete"
                or result.streak_is_lower_bound and not result.current_streak
                or result.current_streak and result.coverage != "complete" and not result.streak_is_lower_bound):
            raise LearnerReadUnavailable()
    except (LearnerReadUnavailable, ValidationError, ValueError):
        raise _unavailable() from None
    response.headers["Cache-Control"] = "no-store"
    return result
