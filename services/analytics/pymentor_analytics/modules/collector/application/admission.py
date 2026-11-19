"""Admission of a verified user grant; no storage or ACK occurs here."""
from dataclasses import dataclass
import time
from uuid import UUID

from pymentor_analytics.contracts.auth_v1 import AuthorizationError, SubjectFence, VerifiedGrant, require_live_subject


@dataclass(frozen=True, slots=True)
class BrowserIdentity:
    subject_ref: UUID
    producer_namespace: str
    source: str = "browser"


async def authorize_collection(grant: VerifiedGrant, *, course_id: str, fence: SubjectFence) -> BrowserIdentity:
    if not isinstance(grant, VerifiedGrant) or time.time() >= grant.expires_at or grant.scope != "collect" or grant.role != "user" or course_id not in grant.course_ids:
        raise AuthorizationError()
    await require_live_subject(fence, grant.sub)
    if time.time() >= grant.expires_at:
        raise AuthorizationError()
    return BrowserIdentity(subject_ref=grant.sub, producer_namespace=f"browser:{grant.sub}")
