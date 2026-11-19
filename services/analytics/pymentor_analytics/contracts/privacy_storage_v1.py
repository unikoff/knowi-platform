"""Local applied-prefix and bounded deletion storage contracts.

Journal authentication, freshness and stream continuity belong to the caller.
"""
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pymentor_analytics.contracts.privacy_control_v1 import JournalEntry

MAX_DELETE_PAGE = 100


@dataclass(frozen=True)
class AppliedCursor:
    authority_id: str
    applied_through: int


@dataclass(frozen=True)
class DeletionProgress:
    sequence: int
    subject_ref: UUID
    epoch: int
    applied_through: int
    complete: bool
    removed_facts: int = 0
    removed_results: int = 0
    removed_leases: int = 0
    removed_replays: int = 0
    removed_windows: int = 0


class PrivacyAuthorityMismatch(Exception):
    """Database is bound to another journal authority."""


class PrivacySequenceGap(Exception):
    """Entry is not the next continuous journal sequence."""


class PrivacyConflict(Exception):
    """A sequence or control identity conflicts with its durable receipt."""


class PrivacyIntegrityError(Exception):
    """Existing fence or local receipt cannot safely be reconciled."""


class PrivacyStore(Protocol):
    async def cursor(self, authority_id: str) -> AppliedCursor: ...
    async def apply(self, authority_id: str, entry: JournalEntry, limit: int = MAX_DELETE_PAGE) -> DeletionProgress: ...
