"""Bounded synthetic processor DTOs; counts are not learner activity metrics."""
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import UUID

from .v2 import AcceptedEvent, canonical_hash

MAX_BATCH = 100
MAX_FACT_BYTES = 8192
MAX_COURSES = 1000
MAX_LEASE_SECONDS = 60


@dataclass(frozen=True)
class ClaimedFact:
    record: AcceptedEvent
    already_applied: bool


@dataclass(frozen=True)
class ClaimedBatch:
    consumer: str
    version: int
    subject_ref: UUID
    subject_epoch: int
    worker: UUID
    generation: int
    batch_id: UUID
    facts: tuple[ClaimedFact, ...]


@dataclass(frozen=True)
class SyntheticDelta:
    course_counts: dict[str, int]
    fingerprint_xor: str


@dataclass(frozen=True)
class SyntheticCountRow:
    course_id: str
    event_count: int


@dataclass(frozen=True)
class SyntheticCountSnapshot:
    subject_ref: UUID
    consumer: str
    version: int
    rows: tuple[SyntheticCountRow, ...]
    as_of: datetime | None


class ProjectionUnavailable(Exception):
    """Projection is stale relative to the subject fence and cannot be served."""


class StaleClaim(Exception):
    """Lease, subject epoch, claim membership or commit already changed."""


def synthetic_delta(facts: tuple[ClaimedFact, ...]) -> SyntheticDelta:
    """Version 1 count and order-independent fingerprint of unapplied facts."""
    courses: dict[str, int] = {}
    fingerprint = 0
    for fact in facts:
        if fact.already_applied:
            continue
        record = fact.record
        course = record.event.course_id
        courses[course] = courses.get(course, 0) + 1
        namespace = record.producer_namespace.encode("utf-8")
        encoded = len(namespace).to_bytes(4, "big") + namespace + record.event.event_id.bytes + canonical_hash(record).encode("ascii")
        fingerprint ^= int.from_bytes(sha256(encoded).digest(), "big")
    return SyntheticDelta(courses, f"{fingerprint:064x}")
