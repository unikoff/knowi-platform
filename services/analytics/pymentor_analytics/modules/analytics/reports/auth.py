"""Scope reports to a verified grant and an explicit student/course selector."""
from uuid import UUID
import time
from pydantic import TypeAdapter, ValidationError

from pymentor_analytics.contracts.auth_v1 import AuthorizationError, SubjectFence, VerifiedGrant, require_live_subject
from pymentor_analytics.contracts.v2 import Identifier


async def authorize_report(grant: VerifiedGrant, *, course_id: str, subject_ref: UUID,
                           fence: SubjectFence) -> None:
    if not isinstance(grant, VerifiedGrant) or time.time() >= grant.expires_at or grant.scope != "report:read" or not course_id or not isinstance(subject_ref, UUID):
        raise AuthorizationError()
    if grant.course_completion_roster_v1 is not None:
        raise AuthorizationError()
    try:
        TypeAdapter(Identifier).validate_python(course_id, strict=True)
    except ValidationError:
        raise AuthorizationError() from None
    if grant.role == "admin":
        allowed = grant.global_read
    elif grant.role == "user":
        allowed = course_id in grant.course_ids and subject_ref == grant.sub
    elif grant.role == "leader":
        allowed = course_id in grant.course_ids and subject_ref in grant.subject_refs
    else:
        allowed = False
    if not allowed:
        raise AuthorizationError()
    # Check the actor as well as the selected student; do not reveal target
    # fence state until the signed scope has authorized that target.
    await require_live_subject(fence, grant.sub)
    if subject_ref != grant.sub:
        await require_live_subject(fence, subject_ref)
    if time.time() >= grant.expires_at:
        raise AuthorizationError()
