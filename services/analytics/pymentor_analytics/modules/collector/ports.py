"""Collector storage contract; an insert is provisional until transaction commit."""
from typing import Literal, Protocol
from pymentor_analytics.contracts.v2 import AcceptedEvent


class ActivityStorage(Protocol):
    async def insert(self, record: AcceptedEvent) -> Literal["inserted", "duplicate", "conflict"]: ...


class IntakeUnavailable(Exception):
    """No durable result is available; a timeout is an unknown outcome."""


class IntakeForbidden(Exception):
    """Subject lifecycle cannot be accepted under the transactional fence."""


class IntakeOverloaded(Exception):
    def __init__(self, retry_after: int = 1):
        self.retry_after = max(1, min(int(retry_after), 60))
        super().__init__("intake overloaded")


class IntakePort(Protocol):
    """Commit-owned boundary: success/duplicate only after durable transaction.

    The implementation atomically checks the subject fence and commits the
    immutable fact, dedup identity/hash and required jobs. ``deadline`` is an
    absolute monotonic timestamp. Cancellation/timeout has unknown outcome;
    reconcile/retry with the same namespace, event_id and payload. No exactly
    once transport claim is implied.
    """
    async def accept(self, record: AcceptedEvent, *, deadline: float) -> Literal["accepted", "duplicate", "conflict"]: ...


class CatalogPort(Protocol):
    """Trusted projection checks product course/version/lesson identity."""
    async def known(self, *, course_id: str, catalog_version: str, lesson_id: str | None, deadline: float) -> bool: ...
