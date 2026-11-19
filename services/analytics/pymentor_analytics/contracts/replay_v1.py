"""Durable same-handler-version synthetic subject rebuild contract."""
from dataclasses import dataclass
from uuid import UUID

MAX_REPLAY_PAGE = 100
MAX_REPLAY_ENQUEUE = 16


@dataclass(frozen=True)
class ReplayHandle:
    consumer: str
    version: int
    subject_ref: UUID
    request_id: UUID
    generation: int
    subject_epoch: int


@dataclass(frozen=True)
class ReplayState:
    handle: ReplayHandle
    phase: str
    cursor_namespace: str | None
    cursor_event_id: UUID | None
    blocked: bool = False


class ReplayForbidden(Exception):
    """Subject lifecycle is tombstoned."""


class ReplayStale(Exception):
    """Handle is superseded or the subject epoch changed."""


class ReplayBusy(Exception):
    """Another replay generation is already active for this partition."""
