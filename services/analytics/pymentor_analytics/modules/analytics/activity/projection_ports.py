"""One durable bucket per subject and metric version; no course ownership."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pymentor_analytics.contracts.v2 import AcceptedEvent

BUCKET_SECONDS = 900
METRIC_VERSION = "activity-v1"
MAX_BUCKET_FACTS = 10000
MAX_BUCKET_BYTES = 8 * 1024 * 1024


class ProjectionLimit(Exception):
    """The bucket is unavailable; never publish a truncated normalization."""


class StaleBucketClaim(Exception):
    """Generation, owner, lease or privacy epoch no longer matches."""


@dataclass(frozen=True, slots=True)
class BucketClaim:
    subject_ref: UUID
    bucket_start: datetime
    generation: int
    subject_epoch: int | None
    claim_id: UUID
    owner: UUID
    run_id: UUID


class BucketStore(Protocol):
    async def claim(self, owner: UUID, *, deadline: float) -> BucketClaim | None: ...
    async def load(self, claim: BucketClaim, *, deadline: float) -> tuple[BucketClaim, tuple[AcceptedEvent, ...]]: ...
    async def commit(self, claim: BucketClaim, projection: dict, *, deadline: float) -> None: ...
    async def fail(self, claim: BucketClaim, reason: str, *, deadline: float) -> None: ...
    async def backfill_step(self, *, deadline: float) -> str: ...
