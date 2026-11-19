"""Read learner history and measured dates through Core's session."""
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, Security
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from pymentor_core.modules.analytics_delivery.errors import LearnerReadUnavailable
from pymentor_core.app.async_request import run_request_sql
from pymentor_core.modules.analytics_delivery.grants import authorize_grant
from pymentor_core.modules.analytics_delivery.source_watermark import capture
from pymentor_core.modules.identity.api.sessions import async_current_principal, session_cookie_scheme

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
