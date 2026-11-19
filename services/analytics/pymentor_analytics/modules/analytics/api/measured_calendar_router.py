"""Dedicated self-only measured calendar; no legacy presence fallback."""
import asyncio
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from pymentor_analytics.adapters.auth.http import extract_bearer
from pymentor_analytics.adapters.http.errors import error_response
from pymentor_analytics.contracts.auth_v1 import AuthenticationError, AuthorizationError
from pymentor_analytics.contracts.v2 import Identifier
from pymentor_analytics.modules.analytics.activity.measured_calendar import read_measured_calendar

router = APIRouter()


class MeasuredCalendar(BaseModel):
    days: list[date]
    current_streak: int | None
    streak_is_lower_bound: bool
    coverage: Literal["unknown", "partial", "complete"]
    as_of: datetime | None
    course_id: Identifier | None = None


@router.get("/api/analytics/reports/measured-calendar", response_model=MeasuredCalendar, response_model_exclude_unset=True,
            openapi_extra={"security": [{"AnalyticsGrant": []}]})
async def measured_calendar(request: Request, year: int, month: int, course_id: Identifier | None = None):
    state = request.app.state
    if not getattr(state, "trust_manifest_validated", False):
        return error_response(503, "unavailable", "Report unavailable")
    try:
        verifier = getattr(state, "grant_verifier", None)
        if verifier is None:
            raise AuthenticationError()
        grant = verifier.verify(extract_bearer(request.scope["headers"]))
    except AuthenticationError:
        return error_response(401, "unauthorized", "Invalid credential", {"WWW-Authenticate": "Bearer"})
    if grant.scope != "report:read" or grant.role != "user" or grant.global_read or grant.subject_refs or not grant.course_ids:
        return error_response(403, "forbidden", "Access denied")
    keys = {"year", "month"} if course_id is None else {"year", "month", "course_id"}
    params = list(request.query_params.multi_items())
    if (len(params) != len(keys) or {key for key, _ in params} != keys
            or not 2000 <= year <= 2100 or not 1 <= month <= 12):
        return error_response(400, "invalid_request", "Invalid report query")
    if course_id is not None and (course_id not in grant.course_ids or course_id == "platform-tools"):
        return error_response(403, "forbidden", "Access denied")
    store, probe = getattr(state, "activity_reports", None), getattr(state, "probe", None)
    if store is None or probe is None or not getattr(store, "day_projection_enabled", False):
        return error_response(503, "unavailable", "Report unavailable")
    try:
        timeout = state.settings.db_timeout_seconds
        if not await asyncio.wait_for(probe.is_ready(), timeout=timeout):
            return error_response(503, "unavailable", "Report unavailable")
        return await asyncio.wait_for(read_measured_calendar(store, grant, year, month,
                                      now=datetime.now(timezone.utc), course_id=course_id), timeout=timeout)
    except AuthorizationError:
        return error_response(403, "forbidden", "Access denied")
    except Exception:
        return error_response(503, "unavailable", "Report unavailable")
