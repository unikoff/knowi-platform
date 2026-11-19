"""Opt-in grant endpoint; claims are always derived from current Core state."""
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, HTTPException, Request, Response, Security
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymentor_core.app.async_request import request_principal, run_request_sql, statistics_selection_revision
from pymentor_core.modules.identity.api.sessions import session_cookie_scheme
from pymentor_core.modules.analytics_delivery.grants import authorize_grant, authorize_student_statistics_grant

router = APIRouter(dependencies=[Security(session_cookie_scheme)])


class GrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course_id: str | None = Field(default=None, min_length=1, max_length=100)
    subject_ref: str | None = Field(default=None, min_length=36, max_length=36)
    user_id: int | None = Field(default=None, gt=0, strict=True)

    @model_validator(mode="after")
    def one_target(self):
        if self.subject_ref is not None and self.user_id is not None:
            raise ValueError("Specify only one target")
        return self


class StudentStatisticsGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    student_statistics: Literal[True]
    course_id: str = Field(min_length=1, max_length=100)
    user_id: int = Field(gt=0, strict=True)
    expected_target_ref: UUID
    expected_actor_ref: UUID

    @field_validator("student_statistics", mode="before")
    @classmethod
    def require_opt_in(cls, value):
        if value is not True:
            raise ValueError("Statistics opt-in must be true")
        return value


class GrantResponse(BaseModel):
    grant: str
    subject_ref: str
    target_subject_ref: str | None = None
    expires_at: datetime
    server_time: datetime


def _issue_grant(signer, claims, purpose):
    target_subject_ref = claims.pop("target_subject_ref", None)
    issued = int(datetime.now(timezone.utc).timestamp())
    token = signer.grant(**claims, now=issued)
    server_time = datetime.fromtimestamp(issued, timezone.utc)
    return {"grant": token, "subject_ref": claims["subject"], "target_subject_ref": target_subject_ref,
            "server_time": server_time, "expires_at": server_time + timedelta(seconds=300 if purpose == "collect" else 60)}


async def _grant(
    request: Request, payload: GrantRequest | StudentStatisticsGrantRequest, response: Response, purpose: str,
):
    principal = await request_principal(request)
    signer = request.app.state.analytics_grant_signer
    if isinstance(payload, StudentStatisticsGrantRequest):
        if principal.account_ref != str(payload.expected_actor_ref):
            raise HTTPException(401, "Account lifecycle changed")
        target_ref = str(payload.expected_target_ref)
        selection_revision = await statistics_selection_revision(
            request, principal.account_ref, payload.course_id, target_ref,
        )

        def authorize_and_issue(db):
            claims = authorize_student_statistics_grant(
                db, principal, course_id=payload.course_id, user_id=payload.user_id,
                target_ref=target_ref, selection_revision=selection_revision,
            )
            return _issue_grant(signer, claims, purpose)

        # Sign while the actor, course authority and target remain locked.
        result = await run_request_sql(request, authorize_and_issue)
    else:
        if purpose == "collect" and payload.user_id is not None:
            raise HTTPException(422, "Target is not accepted for collection")
        claims = await run_request_sql(request, lambda db: authorize_grant(db, principal,
            purpose=purpose, course_id=payload.course_id, target_ref=payload.subject_ref, target_user_id=payload.user_id))
        result = _issue_grant(signer, claims, purpose)
        if (purpose == 'report' and payload.course_id is not None
                and payload.subject_ref is None and payload.user_id is None
                and claims.get('role') == 'user' and claims.get('scope') == 'report:read'
                and claims.get('subject') == principal.account_ref):
            from pymentor_core.modules.identity.cache_session_marker import marker_after_authentication
            marker = marker_after_authentication(request)
            if marker is not None:
                response.headers['X-Calendar-Session-Marker'] = marker
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return result


@router.post("/analytics/grants/collect", response_model=GrantResponse, response_model_exclude_none=True)
async def collect(request: Request, payload: GrantRequest, response: Response):
    return await _grant(request, payload, response, "collect")


@router.post("/analytics/grants/report", response_model=GrantResponse, response_model_exclude_none=True)
async def report(request: Request, payload: GrantRequest | StudentStatisticsGrantRequest, response: Response):
    return await _grant(request, payload, response, "report")
