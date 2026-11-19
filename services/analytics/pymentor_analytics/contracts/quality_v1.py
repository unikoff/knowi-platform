"""Internal synthetic reconciliation and fixed aggregate diagnostic DTOs."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class Coverage:
    facts: int
    receipts: int
    pending: int
    claimed: int
    quarantined: int
    missing_jobs: int
    invalid_facts: int


@dataclass(frozen=True)
class QualityReport:
    status: Literal["no_data", "ok", "zero", "stale", "mismatch", "deleted", "unavailable"]
    consumer: str
    version: int
    projection_version: str
    scan_complete: bool
    coverage: Coverage | None
    data_through: datetime | None
    expected_count: int | None
    actual_count: int | None
    reason: str
    external_coverage: Literal["unknown"] = "unknown"
    checkpoint_kind: Literal["per_fact_receipts"] = "per_fact_receipts"


@dataclass(frozen=True)
class JobCount:
    consumer: str
    version: int
    status: Literal["pending", "claimed", "completed", "quarantined"]
    count: int


@dataclass(frozen=True)
class DiagnosticSnapshot:
    stored_facts: int
    jobs: tuple[JobCount, ...]
    unknown_consumer_jobs: int
    unknown_status_jobs: int
    oldest_pending_seconds: int | None
    retrying_jobs: int
    quarantined_jobs: int
    active_replays: int
    database_bytes: int
    database_connections: int
    privacy_applied_through: int | None
    database_waiting_locks: int | None = None


class DiagnosticUnavailable(Exception):
    """Aggregate diagnostic read failed or exceeded its bounded deadline."""
