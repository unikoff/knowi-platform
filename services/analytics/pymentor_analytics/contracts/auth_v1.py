"""Verified identities and privacy ports shared without a concrete auth adapter."""
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

MAX_TOKEN_BYTES = 16 * 1024


class AuthenticationError(Exception):
    """Safe error with no credential, decoded claims or key material."""
    def __init__(self):
        super().__init__("invalid credential")


@dataclass(frozen=True, slots=True)
class VerifiedGrant:
    """Value returned only after the configured verifier validates a grant."""
    sub: UUID
    role: str
    scope: str
    course_ids: tuple[str, ...]
    subject_refs: tuple[UUID, ...]
    global_read: bool
    expires_at: int
    legacy_admin_roster_v1: tuple[tuple[UUID, int, bool], ...] | None = None
    course_completion_roster_v1: tuple[str, int | None, int | None, tuple[tuple[UUID, int, bool], ...]] | None = None


@dataclass(frozen=True, slots=True)
class VerifiedProducer:
    sub: str
    scope: str
    expires_at: int


class SubjectFence(Protocol):
    async def permits(self, subject_ref: UUID) -> bool: ...


class AuthorizationError(Exception):
    def __init__(self):
        super().__init__("access denied")


class FenceUnavailable(Exception):
    def __init__(self):
        super().__init__("privacy fence unavailable")


async def require_live_subject(fence: SubjectFence, subject_ref: UUID) -> None:
    try:
        allowed = await fence.permits(subject_ref)
    except Exception:
        raise FenceUnavailable() from None
    if allowed is not True:
        raise AuthorizationError()
