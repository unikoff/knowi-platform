"""Activity windows wire models and storage transaction contract.

A verified grant is supplied by the runtime verifier. The transition functions
are pure: their caller holds the lifecycle and window locks and owns the commit.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Any, Literal, Protocol, get_args
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from .metadata_v1 import FUTURE_SKEW, MAX_LATENESS
from .v2 import AcceptedEvent, AwareTime, Contract, Identifier, ToolPayload

WINDOW_MS = 900_000
MOSCOW = ZoneInfo("Europe/Moscow")
INITIAL_EPOCH = 1
MAX_SAFE_INTEGER = (1 << 53) - 1
MAX_REVISION = (1 << 63) - 1
MAX_KEYS = 16
MAX_CONTEXTS = 32
MAX_SNAPSHOT_BYTES = 16 * 1024
MAX_BATCH_BYTES = 64 * 1024
MAX_GRANT_SECONDS = 300
MAX_COURSES = 64
MAX_TOOL_SCOPES = 80
ACCEPT_WINDOW_AGE = timedelta(hours=24)
OUTBOX_TTL = ACCEPT_WINDOW_AGE
RECONCILIATION_RECEIPT_TTL = MAX_LATENESS + ACCEPT_WINDOW_AGE
RETENTION_DAYS = 90
OUTBOX_MAX_KEYS = 96
OUTBOX_MAX_BYTES = 512 * 1024
MAX_RETRY_ATTEMPTS = 8
REQUEST_TIMEOUT_SECONDS = 10
SUBJECT_BATCHES_PER_MINUTE = 12
SEND_INTERVAL_SECONDS = 60
HEARTBEAT_SECONDS = 5
LEASE_SECONDS = 15
MAX_CLOCK_GAP_MS = 20_000
FLAG_GENERATION = "VITE_ANALYTICS_WINDOWS_ENABLED"
FLAG_INTAKE = "ANALYTICS_WINDOWS_INTAKE_ENABLED"
FLAG_REPAIR = "ANALYTICS_WINDOWS_RECONCILIATION_ENABLED"
FLAG_COHORT = "ANALYTICS_WINDOWS_COHORT"
FLAG_T0 = "ANALYTICS_WINDOWS_T0"

Slot = Annotated[int, Field(ge=1, le=96)]
PositiveSafeInt = Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)]
Revision = Annotated[int, Field(ge=1, le=MAX_REVISION)]
Generation = Annotated[int, Field(ge=0, le=MAX_SAFE_INTEGER)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ScopeToolIds = frozenset(get_args(ToolPayload.model_fields["tool_id"].annotation))
State = Literal["accumulating", "pending_reconciliation", "verified", "repaired", "incomplete_coverage"]
CoverageReason = Literal[
    "absent_both", "old_only", "v3_only", "v2_inflight", "v2_coverage_unknown",
    "client_final_missing", "v2_unconfirmed", "v3_unconfirmed", "clock_untrusted",
    "lease_lost", "writer_conflict", "catalog_conflict", "storage_denied",
    "queue_overflow", "retry_exhausted", "expired", "account_change",
    "unsupported", "epoch_rebase_required", "v3_disabled", "source_generation_changed",
]
AckStatus = Literal["committed", "duplicate", "stale", "conflict", "pending", "rejected"]
AckCode = Literal[
    "committed", "same_payload", "stale_seq", "stale_epoch", "finalized",
    "payload_conflict", "non_monotonic", "ownership_conflict", "invalid_snapshot",
    "subject_forbidden", "writer_forbidden", "course_forbidden", "tool_forbidden",
    "invalid_active_ms", "window_budget_exceeded", "catalog_conflict",
    "too_large", "future_clock", "too_late", "privacy_forbidden",
]
ErrorCode = Literal[
    "invalid_request", "unsupported_version", "unauthorized", "forbidden",
    "too_large", "rate_limited", "capability_off", "unavailable", "privacy_unavailable",
]

_ACK_CODES = {
    "committed": {"committed"},
    "duplicate": {"same_payload"},
    "stale": {"stale_seq", "stale_epoch", "finalized"},
    "conflict": {"payload_conflict", "non_monotonic", "catalog_conflict"},
    "pending": {"ownership_conflict"},
    "rejected": {
        "invalid_snapshot", "subject_forbidden", "writer_forbidden", "course_forbidden",
        "tool_forbidden", "invalid_active_ms", "window_budget_exceeded", "catalog_conflict",
        "too_large", "future_clock", "too_late", "privacy_forbidden",
    },
}


class Versioned(Contract):
    protocol_version: Literal[1]

    @field_validator("protocol_version", mode="before")
    @classmethod
    def exact_protocol(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("protocol version must be an integer")
        return value


class Writer(Contract):
    writer_id: UUID
    writer_generation: PositiveSafeInt


class WindowCoordinate(Contract):
    activity_date: Annotated[date, Field(ge=date(2015, 1, 1), le=date(2100, 12, 31))]
    slot_no: Slot


class WindowKey(WindowCoordinate):
    student_id: UUID


class SnapshotIdentity(WindowCoordinate, Writer):
    server_epoch: PositiveSafeInt
    seq: PositiveSafeInt


class WindowStat(Contract):
    tool_id: Identifier
    course_id: Identifier | None
    lesson_id: Identifier | None
    active_ms: Annotated[int, Field(ge=1, le=WINDOW_MS)]

    @model_validator(mode="after")
    def valid_context(self) -> WindowStat:
        if self.tool_id not in ScopeToolIds or self.course_id == "platform-tools":
            raise ValueError("unsupported activity context")
        if self.lesson_id is not None and (
            self.course_id is None or self.tool_id not in {"theory", "practice"}
        ):
            raise ValueError("lesson activity requires an educational context")
        return self

    @property
    def context(self) -> tuple[str, str, str]:
        return self.tool_id, self.course_id or "", self.lesson_id or ""


WindowStats = Annotated[tuple[WindowStat, ...], Field(max_length=MAX_CONTEXTS)]


def canonical_stats(stats: Sequence[WindowStat]) -> tuple[WindowStat, ...]:
    """Validate and order stats. Empty is valid only for a repair result."""
    if len(stats) > MAX_CONTEXTS or len({row.context for row in stats}) != len(stats):
        raise PydanticCustomError("invalid_snapshot", "invalid number of unique contexts")
    if sum(row.active_ms for row in stats) > WINDOW_MS:
        raise PydanticCustomError("window_budget_exceeded", "window exceeds 900000 ms")
    return tuple(sorted(stats, key=lambda row: row.context))


class WindowSnapshot(SnapshotIdentity):
    stats: Annotated[tuple[WindowStat, ...], Field(min_length=1, max_length=MAX_CONTEXTS)]
    catalog_versions: Annotated[dict[Identifier, Identifier], Field(max_length=MAX_CONTEXTS)]
    client_final: bool

    @model_validator(mode="after")
    def valid_stats(self) -> WindowSnapshot:
        canonical_stats(self.stats)
        educational_courses = {row.course_id for row in self.stats if row.lesson_id is not None}
        if self.catalog_versions.keys() != educational_courses:
            raise PydanticCustomError("catalog_conflict", "versions must cover exactly the educational courses")
        return self


class WindowBatch(Versioned):
    windows: Annotated[tuple[WindowSnapshot, ...], Field(min_length=1, max_length=MAX_KEYS)]

    @model_validator(mode="after")
    def one_snapshot_per_key(self) -> WindowBatch:
        keys = {(window.activity_date, window.slot_no) for window in self.windows}
        if len(keys) != len(self.windows):
            raise ValueError("duplicate window key")
        return self


class WindowWireBatch(Versioned):
    """Transport validates the envelope before validating correlated items independently."""

    windows: Annotated[tuple[dict[str, Any], ...], Field(min_length=1, max_length=MAX_KEYS)]


class ToolScope(Contract):
    tool_id: Identifier
    course_id: Identifier | None

    @model_validator(mode="after")
    def valid_scope(self) -> ToolScope:
        if self.tool_id not in ScopeToolIds or self.course_id == "platform-tools":
            raise ValueError("unsupported tool scope")
        return self


class WindowGrantRequest(Versioned, Writer):
    pass


class WindowGrantClaims(Versioned, Writer):
    grant_version: Literal[1]
    iss: Identifier
    aud: Literal["analytics-windows"]
    sub: UUID
    iat: Generation
    nbf: Generation
    exp: Generation
    scope: Literal["windows:collect"]
    course_ids: Annotated[tuple[Identifier, ...], Field(max_length=MAX_COURSES)]
    tool_scopes: Annotated[tuple[ToolScope, ...], Field(max_length=MAX_TOOL_SCOPES)]

    @field_validator("grant_version", mode="before")
    @classmethod
    def exact_grant_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("grant version must be an integer")
        return value

    @model_validator(mode="after")
    def valid_grant(self) -> WindowGrantClaims:
        if not self.iat <= self.nbf < self.exp <= self.iat + MAX_GRANT_SECONDS:
            raise ValueError("invalid grant lifetime")
        if (len(set(self.course_ids)) != len(self.course_ids) or "platform-tools" in self.course_ids
                or len(set(self.tool_scopes)) != len(self.tool_scopes)):
            raise ValueError("duplicate or synthetic scope")
        if any(row.course_id is not None and row.course_id not in self.course_ids for row in self.tool_scopes):
            raise ValueError("course tools require granted course scope")
        return self


class WindowGrantResponse(Versioned, Writer):
    grant: Annotated[str, Field(min_length=1, max_length=16 * 1024)]
    subject_ref: UUID
    server_time: AwareTime
    expires_at: AwareTime


class ConfirmedWindow(Contract):
    """Last durable state. Repair may fence an epoch before a writer can resume."""

    writer_id: UUID | None
    writer_generation: PositiveSafeInt | None
    server_epoch: PositiveSafeInt
    seq: PositiveSafeInt | None
    payload_hash: Hash | None
    revision: Revision
    stats: WindowStats
    client_final: bool
    rebase_at: AwareTime | None

    @model_validator(mode="after")
    def valid_confirmation(self) -> ConfirmedWindow:
        if (self.writer_id is None) != (self.writer_generation is None):
            raise ValueError("incomplete writer identity")
        if (self.seq is None) != (self.payload_hash is None):
            raise ValueError("incomplete receipt identity")
        if self.seq is not None and (self.writer_id is None or not self.stats):
            raise ValueError("receipt requires writer and nonempty snapshot")
        if self.stats != canonical_stats(self.stats):
            raise ValueError("confirmed stats must be canonical")
        return self


class WindowItemAck(SnapshotIdentity):
    index: Annotated[int, Field(ge=0, lt=MAX_KEYS)]
    payload_hash: Hash | None
    status: AckStatus
    code: AckCode
    confirmed: ConfirmedWindow | None

    @model_validator(mode="after")
    def valid_ack(self) -> WindowItemAck:
        if self.code not in _ACK_CODES[self.status]:
            raise ValueError("status and code disagree")
        if self.status != "rejected" and self.payload_hash is None:
            raise ValueError("validated snapshot receipt requires a payload hash")
        if self.status in {"committed", "duplicate"} and not confirmation_matches(self):
            raise ValueError("success ACK must confirm the exact snapshot")
        return self


class WindowBatchAck(Versioned):
    items: Annotated[tuple[WindowItemAck, ...], Field(min_length=1, max_length=MAX_KEYS)]

    @model_validator(mode="after")
    def contiguous_acknowledgements(self) -> WindowBatchAck:
        if any(item.index != index for index, item in enumerate(self.items)):
            raise ValueError("ACK indices must follow the request item order")
        return self


class WindowError(Contract):
    code: ErrorCode
    message: Annotated[str, Field(max_length=256)]
    correlation_id: UUID


class V2FactReceipt(Contract):
    producer_namespace: Identifier
    event_id: UUID
    status: Literal["accepted", "duplicate"]


class WindowDiagnostics(Versioned):
    key: WindowKey
    client_final_seq: PositiveSafeInt | None
    v3_confirmed: ConfirmedWindow | None
    v2_confirmed: tuple[V2FactReceipt, ...]
    source_generation: Generation
    reconciled_generation: Generation | None
    state: State
    coverage_reasons: tuple[CoverageReason, ...]

def snapshot_digest(snapshot: WindowSnapshot) -> str:
    data = snapshot.model_dump(mode="json")
    data["stats"] = [row.model_dump(mode="json") for row in canonical_stats(snapshot.stats)]
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def snapshot_rejection(error: ValidationError, raw_snapshot: Mapping[str, Any]) -> AckCode:
    if "subject_ref" in raw_snapshot or "student_id" in raw_snapshot:
        return "subject_forbidden"
    for issue in error.errors(include_input=False):
        if issue["type"] in {"window_budget_exceeded", "catalog_conflict"}:
            return issue["type"]
        if issue["loc"] and issue["loc"][-1] == "active_ms":
            return "invalid_active_ms"
    return "invalid_snapshot"


@dataclass(frozen=True, slots=True)
class PublishedCourse:
    version: str
    lesson_ids: frozenset[str]


def authorize_snapshot(
    snapshot: WindowSnapshot,
    grant: WindowGrantClaims,
    catalog: Mapping[str, PublishedCourse],
) -> AckCode | None:
    """Validate the entire window against already verified, current authorization."""
    if (snapshot.writer_id, snapshot.writer_generation) != (grant.writer_id, grant.writer_generation):
        return "writer_forbidden"
    if any(row.course_id is not None and row.course_id not in grant.course_ids for row in snapshot.stats):
        return "course_forbidden"
    granted_tools = {(scope.tool_id, scope.course_id) for scope in grant.tool_scopes}
    for row in snapshot.stats:
        if row.lesson_id is None:
            if (row.tool_id, row.course_id) not in granted_tools:
                return "tool_forbidden"
            continue
        course = catalog.get(row.course_id)
        if course is None or course.version != snapshot.catalog_versions[row.course_id] or row.lesson_id not in course.lesson_ids:
            return "catalog_conflict"
    return None


def window_bounds(coordinate: WindowCoordinate) -> tuple[datetime, datetime]:
    start = datetime.combine(coordinate.activity_date, time.min, MOSCOW)
    start += timedelta(milliseconds=WINDOW_MS * (coordinate.slot_no - 1))
    start = start.astimezone(timezone.utc)
    return start, start + timedelta(milliseconds=WINDOW_MS)


def split_interval(start: datetime, end: datetime) -> tuple[tuple[WindowCoordinate, int], ...]:
    """Split a trusted projected interval into whole milliseconds on the MSK grid."""
    if start.utcoffset() is None or end.utcoffset() is None or start > end:
        raise ValueError("ordered aware interval required")
    start = start.astimezone(MOSCOW)
    end = end.astimezone(MOSCOW)
    start = start.replace(microsecond=start.microsecond // 1000 * 1000)
    end = end.replace(microsecond=end.microsecond // 1000 * 1000)
    rows = []
    while start < end:
        slot = start.hour * 4 + start.minute // 15 + 1
        boundary = start.replace(minute=(start.minute // 15) * 15, second=0, microsecond=0)
        stop = min(boundary + timedelta(milliseconds=WINDOW_MS), end)
        elapsed_ms = (stop - start) // timedelta(milliseconds=1)
        rows.append((WindowCoordinate(activity_date=start.date(), slot_no=slot), elapsed_ms))
        start = stop
    return tuple(rows)


def validate_window_age(coordinate: WindowCoordinate, received_at: datetime) -> AckCode | None:
    if received_at.utcoffset() is None:
        raise ValueError("trusted aware clock required")
    start, end = window_bounds(coordinate)
    if start > received_at + FUTURE_SKEW:
        return "future_clock"
    if received_at > end + ACCEPT_WINDOW_AGE:
        return "too_late"
    return None


def v2_context(record: AcceptedEvent) -> tuple[str, str | None, str | None] | None:
    event = record.event
    if event.type == "activity_segment":
        return event.section, event.course_id, event.lesson_id
    if event.type == "tool_segment":
        return event.payload.tool_id, None if event.course_id == "platform-tools" else event.course_id, None
    return None


def confirmation_matches(ack: WindowItemAck) -> bool:
    confirmed = ack.confirmed
    return confirmed is not None and (
        ack.writer_id, ack.writer_generation, ack.server_epoch, ack.seq, ack.payload_hash,
    ) == (
        confirmed.writer_id, confirmed.writer_generation, confirmed.server_epoch,
        confirmed.seq, confirmed.payload_hash,
    )


def can_remove_confirmed_snapshot(
    snapshot: WindowSnapshot, ack: WindowItemAck, *, owner: UUID, authenticated_subject: UUID, expected_index: int = 0,
) -> bool:
    """Only an exact durable receipt under the same lifecycle confirms this body."""
    if owner != authenticated_subject or ack.index != expected_index or ack.status not in {"committed", "duplicate"}:
        return False
    return confirmation_matches(ack) and (
        snapshot.activity_date, snapshot.slot_no, snapshot.writer_id,
        snapshot.writer_generation, snapshot.server_epoch, snapshot.seq, snapshot_digest(snapshot),
    ) == (
        ack.activity_date, ack.slot_no, ack.writer_id, ack.writer_generation, ack.server_epoch,
        ack.seq, ack.payload_hash,
    )


@dataclass(frozen=True, slots=True)
class WindowState:
    confirmed: ConfirmedWindow | None = None
    catalog_versions: tuple[tuple[str, str], ...] = ()
    state: State = "pending_reconciliation"
    source_generation: int = 0
    change_generation: int = 0
    reconciled_generation: int | None = None
    coverage_reasons: tuple[CoverageReason, ...] = ()


@dataclass(frozen=True, slots=True)
class Transition:
    state: WindowState
    status: AckStatus
    code: AckCode


def receive_snapshot(current: WindowState, snapshot: WindowSnapshot) -> Transition:
    """Return the next state and ACK outcome, scoped to one already authorized key."""
    previous = current.confirmed
    epoch = previous.server_epoch if previous else INITIAL_EPOCH
    if snapshot.server_epoch != epoch:
        return Transition(current, "stale", "stale_epoch")
    digest = snapshot_digest(snapshot)
    stats = canonical_stats(snapshot.stats)
    versions = tuple(sorted(snapshot.catalog_versions.items()))
    if previous:
        if (snapshot.writer_id, snapshot.writer_generation) != (previous.writer_id, previous.writer_generation):
            reasons = current.coverage_reasons
            if "writer_conflict" not in reasons:
                current = replace(current, state="pending_reconciliation",
                                  change_generation=current.change_generation + 1,
                                  coverage_reasons=(*reasons, "writer_conflict"))
            return Transition(current, "pending", "ownership_conflict")
        if previous.seq is not None:
            if snapshot.seq < previous.seq:
                return Transition(current, "stale", "stale_seq")
            if snapshot.seq == previous.seq:
                if digest == previous.payload_hash:
                    return Transition(current, "duplicate", "same_payload")
                return Transition(current, "conflict", "payload_conflict")
            if previous.client_final:
                return Transition(current, "stale", "finalized")
        if any(snapshot.catalog_versions.get(course) != version for course, version in current.catalog_versions):
            return Transition(current, "conflict", "catalog_conflict")
        amounts = {row.context: row.active_ms for row in stats}
        if any(amounts.get(row.context, 0) < row.active_ms for row in previous.stats):
            return Transition(current, "conflict", "non_monotonic")
    changed = previous is None or previous.stats != stats
    revision = 1 if previous is None else previous.revision + changed
    confirmed = ConfirmedWindow(
        writer_id=snapshot.writer_id, writer_generation=snapshot.writer_generation,
        server_epoch=epoch, seq=snapshot.seq, payload_hash=digest, revision=revision, stats=stats,
        client_final=snapshot.client_final, rebase_at=previous.rebase_at if previous else None,
    )
    phase = "pending_reconciliation" if snapshot.client_final or current.coverage_reasons else "accumulating"
    return Transition(replace(current, confirmed=confirmed, catalog_versions=versions, state=phase,
                              change_generation=current.change_generation + 1), "committed", "committed")


def build_ack(index: int, snapshot: WindowSnapshot, transition: Transition, *, include_confirmed: bool = True) -> WindowItemAck:
    identity = snapshot.model_dump(include=set(SnapshotIdentity.model_fields))
    return WindowItemAck(**identity, index=index, payload_hash=snapshot_digest(snapshot),
                         status=transition.status, code=transition.code,
                         confirmed=transition.state.confirmed if include_confirmed else None)


def record_v2_commit(current: WindowState) -> WindowState:
    """Call for each intersected key, in the same transaction as new v2 facts."""
    return replace(current, source_generation=current.source_generation + 1,
                   change_generation=current.change_generation + 1, state="pending_reconciliation")


def late_deadline(coordinate: WindowCoordinate) -> datetime:
    """Latest admission time including a v2 segment ending after this window."""
    return window_bounds(coordinate)[1] + timedelta(minutes=1) + MAX_LATENESS


@dataclass(frozen=True, slots=True)
class ReconcileDecision:
    source_generation: int
    change_generation: int
    expected_stats: tuple[WindowStat, ...] | None
    catalog_versions: tuple[tuple[str, str], ...] = ()
    coverage_reasons: tuple[CoverageReason, ...] = ()


def apply_reconciliation(
    current: WindowState, decision: ReconcileDecision, *, rebase_at: datetime,
) -> WindowState | None:
    """Apply a comparison/repair only to the exact snapshot used by the worker."""
    if (current.source_generation, current.change_generation) != (
        decision.source_generation, decision.change_generation,
    ):
        return None
    if decision.expected_stats is None:
        if not decision.coverage_reasons:
            raise ValueError("incomplete coverage requires explicit reasons")
        return replace(current, state="incomplete_coverage", coverage_reasons=decision.coverage_reasons,
                       change_generation=current.change_generation + 1, reconciled_generation=None)
    if decision.coverage_reasons:
        raise ValueError("covered repair cannot carry coverage failures")
    stats = canonical_stats(decision.expected_stats)
    previous = current.confirmed
    repaired = stats != (previous.stats if previous else ())
    confirmed = previous
    if repaired:
        confirmed = ConfirmedWindow(
            writer_id=previous.writer_id if previous else None,
            writer_generation=previous.writer_generation if previous else None,
            server_epoch=previous.server_epoch + 1 if previous else INITIAL_EPOCH + 1,
            seq=None, payload_hash=None, revision=previous.revision + 1 if previous else 1,
            stats=stats, client_final=False, rebase_at=rebase_at,
        )
    return replace(current, confirmed=confirmed, state="repaired" if repaired else "verified",
                   catalog_versions=decision.catalog_versions if repaired else current.catalog_versions,
                   reconciled_generation=decision.source_generation, coverage_reasons=(),
                   change_generation=current.change_generation + 1)


def handoff_writer(current: WindowState, writer: Writer, *, rebase_at: datetime) -> WindowState:
    """Fence a former owner after covered reconciliation, preserving the confirmed base."""
    previous = current.confirmed
    if previous is None or current.state not in {"verified", "repaired"}:
        raise ValueError("confirmed covered state required for handoff")
    confirmed = ConfirmedWindow(
        writer_id=writer.writer_id, writer_generation=writer.writer_generation,
        server_epoch=previous.server_epoch + 1, seq=None, payload_hash=None,
        revision=previous.revision + 1, stats=previous.stats,
        client_final=False, rebase_at=rebase_at,
    )
    return replace(current, confirmed=confirmed, state="pending_reconciliation",
                   change_generation=current.change_generation + 1)


class WindowWritePort(Protocol):
    async def write_window(
        self, key: WindowKey, snapshot: WindowSnapshot, *, received_at: datetime,
    ) -> Transition:
        """Return only after commit. The adapter enforces the lifecycle fence."""
        ...


@dataclass(frozen=True, slots=True)
class ReconciliationInput:
    key: WindowKey
    state: WindowState
    v2_facts: tuple[AcceptedEvent, ...]
    v2_inflight: bool


class WindowReconciliationPort(Protocol):
    async def read_reconciliation(self, key: WindowKey) -> ReconciliationInput:
        """Read one consistent, across-course snapshot of source facts and state."""
        ...

    async def changed_windows(self, *, limit: int) -> Sequence[WindowKey]:
        """Read durable dirty keys in bounded order; failed processing leaves work discoverable."""
        ...

    async def reconcile_window(
        self, key: WindowKey, decision: ReconcileDecision, *, rebase_at: datetime,
    ) -> WindowState | None:
        """Revalidate fence and CAS then commit atomically; None denotes lost CAS."""
        ...


class WindowV2ChangedPort(Protocol):
    async def mark_v2_changed(self, transaction: Any, keys: Set[WindowKey], *, received_at: datetime) -> None:
        """Record dirty generations in the owning v2 transaction, without committing."""
        ...
