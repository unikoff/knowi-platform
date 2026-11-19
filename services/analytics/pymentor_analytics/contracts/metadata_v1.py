"""Public new-fact clock policy; apply after duplicate/conflict reconciliation."""
from datetime import timedelta
from typing import Literal

from .v2 import AcceptedEvent

FUTURE_SKEW = timedelta(seconds=120)
MAX_LATENESS = timedelta(days=7)


class MetadataRejected(ValueError):
    def __init__(self, reason: Literal["future_clock", "too_late"]):
        self.reason = reason
        super().__init__(reason)


def validate_new_record(record: AcceptedEvent) -> None:
    if record.event.ended_at > record.received_at + FUTURE_SKEW:
        raise MetadataRejected("future_clock")
    if record.received_at > record.event.ended_at + MAX_LATENESS:
        raise MetadataRejected("too_late")
