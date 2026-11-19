"""Resolve authorized report scope from a verified, unexpired grant."""
from dataclasses import dataclass
import time
from uuid import UUID
from pymentor_analytics.contracts.auth_v1 import AuthorizationError, VerifiedGrant
from pymentor_analytics.contracts.reports_v1 import ActivityLifetimeQuery, ActivityQuery

@dataclass(frozen=True)
class ReportScope:
    actor: UUID
    subjects: tuple[UUID, ...] | None  # None means global admin discovery
    courses: tuple[str, ...] | None  # None means global admin, never client authority
    course_id: str | None
    selected_subject: UUID | None

def resolve_scope(grant: VerifiedGrant, query: ActivityQuery | ActivityLifetimeQuery) -> ReportScope:
    if not isinstance(grant, VerifiedGrant) or not isinstance(grant.sub, UUID) or time.time() >= grant.expires_at:
        raise AuthorizationError()
    if grant.course_completion_roster_v1 is not None:
        raise AuthorizationError()
    if grant.scope == "report:student":
        if (grant.role not in {"leader", "admin"} or grant.global_read or grant.legacy_admin_roster_v1 is not None
                or query.course_id is None or query.subject_ref is None
                or grant.course_ids != (query.course_id,) or grant.subject_refs != (query.subject_ref,)):
            raise AuthorizationError()
        return ReportScope(grant.sub, grant.subject_refs, grant.course_ids, query.course_id, query.subject_ref)
    if grant.scope != "report:read":
        raise AuthorizationError()
    if isinstance(query, ActivityLifetimeQuery) and (grant.role != "user" or query.subject_ref is not None):
        raise AuthorizationError()
    if grant.role == "user" and not grant.global_read and grant.course_ids and not grant.subject_refs:
        if query.subject_ref is not None and query.subject_ref != grant.sub:
            raise AuthorizationError()
        subjects = (grant.sub,)
        courses = grant.course_ids
    elif grant.role == "leader" and not grant.global_read and len(grant.course_ids) == 1:
        if query.subject_ref is not None and query.subject_ref not in grant.subject_refs:
            raise AuthorizationError()
        subjects = (query.subject_ref,) if query.subject_ref is not None else grant.subject_refs
        courses = grant.course_ids
    elif grant.role == "admin" and grant.global_read and not grant.course_ids and not grant.subject_refs:
        subjects = (query.subject_ref,) if query.subject_ref is not None else None
        courses = None
    else:
        raise AuthorizationError()
    if query.course_id is not None:
        if courses is not None and query.course_id not in courses:
            raise AuthorizationError()
        courses = (query.course_id,)
    course_id = query.course_id
    if grant.role == "leader" and course_id is None:
        course_id = courses[0]
    return ReportScope(grant.sub, subjects, courses, course_id, query.subject_ref)
