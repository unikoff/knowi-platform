"""Contracts for one durable subject by fixed UTC+03 day projection."""
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID


class StaleDayClaim(Exception):
    """The lease, generation, run or privacy epoch is no longer valid."""


class DaySourceNotReady(Exception):
    """The bucket worker has not finished; the day was deferred without penalty."""


@dataclass(frozen=True, slots=True)
class DayClaim:
    subject_ref: UUID
    local_day: date
    generation: int
    subject_epoch: int | None
    claim_id: UUID
    owner: UUID
    run_id: UUID


class DayStore(Protocol):
    async def claim(self, owner: UUID, *, deadline: float) -> DayClaim | None: ...
    async def load(self, claim: DayClaim, *, deadline: float) -> tuple[DayClaim, tuple[dict, ...]]: ...
    async def commit(self, claim: DayClaim, projection: dict, *, deadline: float) -> None: ...
    async def fail(self, claim: DayClaim, reason: str, *, deadline: float) -> None: ...
    async def backfill_step(self, *, deadline: float) -> str: ...
