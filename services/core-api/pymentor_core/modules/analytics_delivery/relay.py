"""One bounded relay step. Network and transaction lifetime are supplied externally."""
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import Protocol


class Receipt(Enum):
    ACCEPTED = "accepted"
    ABSENT = "absent"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


class Transport(Protocol):
    async def lookup(self, event_id: str, fingerprint: str, deadline_seconds: float) -> Receipt: ...
    async def send(self, event: dict, deadline_seconds: float) -> Receipt: ...


@dataclass(frozen=True)
class Decision:
    action: str  # ack, retry, reconcile, quarantine


async def deliver(transport: Transport, event: dict, fingerprint: str, *,
                  uncertain: bool, deadline_seconds: float, clock=monotonic) -> Decision:
    """After an unknown result, query the original ID before any resend.

    A timeout is represented by UNKNOWN by the transport. Exceptions and cancellation
    propagate; callers must durably record uncertainty before any external send.
    """
    if deadline_seconds <= 0:
        raise ValueError("deadline must be positive")
    if event.get("kind") == "subject_deleted":
        # Deletion must use the independent authority journal and an applied
        # purge receipt, never the ordinary transfer projection endpoint.
        return Decision("reconcile")
    deadline = clock() + deadline_seconds
    if uncertain:
        receipt = await transport.lookup(event["event_id"], fingerprint, max(0, deadline - clock()))
        if receipt is Receipt.ACCEPTED:
            return Decision("ack")
        if receipt is Receipt.CONFLICT:
            return Decision("quarantine")
        if receipt is not Receipt.ABSENT:
            return Decision("reconcile")
    remaining = deadline - clock()
    if remaining <= 0:
        return Decision("retry")
    receipt = await transport.send(event, remaining)
    return Decision({Receipt.ACCEPTED: "ack", Receipt.ABSENT: "retry",
                     Receipt.UNKNOWN: "reconcile", Receipt.CONFLICT: "quarantine"}[receipt])


def retry_delay(attempt: int, jitter: float, *, base_seconds: int = 1, cap_seconds: int = 300) -> int:
    """Bounded exponential full jitter, with supplied random fraction in [0,1]."""
    if not 1 <= attempt <= 100 or not 0 <= jitter <= 1 or not 1 <= base_seconds <= cap_seconds <= 3600:
        raise ValueError("invalid retry bounds")
    return int(min(cap_seconds, base_seconds * (2 ** min(attempt - 1, 20))) * jitter)
