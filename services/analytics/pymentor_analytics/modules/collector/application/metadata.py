"""Server-owned browser metadata and new-event clock policy.

No request headers or body identity enter this interface. Check clock policy only
for a new identity, after durable duplicate/conflict resolution.
"""
from datetime import datetime, timezone
from collections.abc import Callable

from pymentor_analytics.contracts.v2 import AcceptedEvent, BrowserEvent
from pymentor_analytics.modules.collector.application.admission import BrowserIdentity
from pymentor_analytics.contracts.metadata_v1 import (
    FUTURE_SKEW, MAX_LATENESS, MetadataRejected, validate_new_record,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_record(identity: BrowserIdentity, event: BrowserEvent, *,
                 clock: Callable[[], datetime] = _utc_now) -> AcceptedEvent:
    """Create a candidate without new-event policy, for dedup reconciliation."""
    received = clock()
    if received.tzinfo is None or received.utcoffset() is None:
        raise ValueError("server clock must be timezone-aware")
    return AcceptedEvent(producer_namespace=identity.producer_namespace,
                         subject_ref=identity.subject_ref, source="browser",
                         event=event, received_at=received.astimezone(timezone.utc))


def make_record(identity: BrowserIdentity, event: BrowserEvent, *,
                clock: Callable[[], datetime] = _utc_now) -> AcceptedEvent:
    """Convenience for known-new events; unsafe before duplicate resolution."""
    record = build_record(identity, event, clock=clock)
    validate_new_record(record)
    return record
