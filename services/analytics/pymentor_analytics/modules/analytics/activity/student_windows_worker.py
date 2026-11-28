"""Bounded reconcile worker for the dirty window queue."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from random import uniform
from typing import Literal, Protocol

from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.windows_v1 import (
    ReconcileDecision, WindowKey, WindowReconciliationPort, WindowState,
    apply_reconciliation, late_deadline, window_bounds,
)
from pymentor_analytics.modules.analytics.activity.bucket import BucketOverflow
from pymentor_analytics.modules.analytics.activity.student_windows_reconcile import (
    CoverageEvidence, ObservedWindow, WindowComparison, compare_reconciliation,
)
from pymentor_analytics.modules.collector.ports import IntakeForbidden, IntakeUnavailable

WorkerStatus = Literal[
    "read_only", "committed", "recovered", "deferred", "superseded", "fenced", "unavailable", "overflow",
]
_TRANSIENT = (ConnectionError, TimeoutError, IntakeUnavailable, PrivacyUnavailable)


class WorkerStore(WindowReconciliationPort, Protocol):
    """Dirty keys are durable, and a write claims its exact generations in one CAS."""

    async def changed_windows(
        self, *, limit: int, now: datetime | None = None, after: WindowKey | None = None,
    ) -> Sequence[WindowKey]: ...

    async def reconcile_window(
        self, key: WindowKey, decision: ReconcileDecision, *, rebase_at: datetime,
        evidence: Mapping[str, object] | None = None, recheck_at: datetime | None = None,
    ) -> WindowState | None: ...


@dataclass(frozen=True, slots=True)
class WorkedWindow:
    key: WindowKey
    status: WorkerStatus
    attempts: int
    age_ms: int
    comparison: WindowComparison | None = None
    after: ObservedWindow | None = None


@dataclass(frozen=True, slots=True)
class WorkerBatch:
    windows: tuple[WorkedWindow, ...]
    timed_out: bool = False
    queue_unavailable: bool = False


@dataclass(frozen=True, slots=True)
class WorkerLimits:
    batch_limit: int = 32
    max_attempts: int = 3
    timeout_seconds: float = 15.0
    retry_delay_seconds: float = 0.05

    def __post_init__(self) -> None:
        if type(self.batch_limit) is not int or not 1 <= self.batch_limit <= 1000:
            raise ValueError("batch limit must be from 1 through 1000")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 8:
            raise ValueError("attempts must be from 1 through 8")
        if not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout must be positive and finite")
        if not 0 <= self.retry_delay_seconds <= 1:
            raise ValueError("initial retry delay must be between 0 and 1 second")


def _observed_document(observed: ObservedWindow) -> dict[str, object]:
    return {
        "revision": observed.revision, "stats_hash": observed.stats_hash, "payload_hash": observed.payload_hash,
        "context_diff": None if observed.differences is None else [asdict(item) for item in observed.differences],
    }


def _repair_evidence(
    comparison: WindowComparison, state: WindowState, *, now: datetime,
) -> dict[str, object] | None:
    if not comparison.before.differences or comparison.decision.expected_stats is None:
        return None
    return {
        "version": 1, "recorded_at": now.isoformat(), "outcome": comparison.outcome,
        "reasons": list(comparison.diagnostics.coverage_reasons), "coverage_source": comparison.coverage_source,
        "source_generation": comparison.decision.source_generation,
        "change_generation": comparison.decision.change_generation, "expected_hash": comparison.expected_hash,
        "before": _observed_document(comparison.before), "after": _observed_document(comparison.after(state)),
    }


class WindowReconcileWorker:
    def __init__(
        self, store: WorkerStore, *, cohort_t0: datetime, limits: WorkerLimits = WorkerLimits(),
    ) -> None:
        if cohort_t0.utcoffset() is None:
            raise ValueError("aware cohort start required")
        self.store = store
        self.cohort_t0 = cohort_t0
        self.limits = limits
        self._after: WindowKey | None = None

    async def run_once(
        self, *, repair: bool = False, evidence: Mapping[WindowKey, CoverageEvidence] | None = None,
        now: datetime | None = None,
    ) -> WorkerBatch:
        """Scan one bounded page. A timed-out or cancelled key stays durable in the store."""
        at = now if now is not None else datetime.now(timezone.utc)
        if at.utcoffset() is None:
            raise ValueError("aware reconciliation clock required")
        rows: list[WorkedWindow] = []
        observations = evidence or {}
        try:
            async with asyncio.timeout(self.limits.timeout_seconds):
                try:
                    page = await self.store.changed_windows(limit=self.limits.batch_limit, now=at, after=self._after)
                except _TRANSIENT:
                    return WorkerBatch((), queue_unavailable=True)
                for key in page:
                    self._after = key
                    row = await self._work(key, now=at, repair=repair,
                                           evidence=observations.get(key, CoverageEvidence()))
                    rows.append(row)
                if len(page) < self.limits.batch_limit:
                    self._after = None
        except TimeoutError:
            return WorkerBatch(tuple(rows), timed_out=True)
        return WorkerBatch(tuple(rows))

    async def run_key(
        self, key: WindowKey, *, now: datetime, evidence: CoverageEvidence = CoverageEvidence(), repair: bool = False,
    ) -> WorkedWindow:
        """Read or reconcile an explicitly selected diagnostic key, even if retired."""
        if now.utcoffset() is None:
            raise ValueError("aware reconciliation clock required")
        try:
            async with asyncio.timeout(self.limits.timeout_seconds):
                return await self._work(key, now=now, repair=repair, evidence=evidence)
        except TimeoutError:
            return WorkedWindow(key, "unavailable", 0, self._age(key, now))

    @staticmethod
    def _age(key: WindowKey, now: datetime) -> int:
        return max(0, (now - window_bounds(key)[1]) // timedelta(milliseconds=1))

    async def _work(self, key: WindowKey, *, now: datetime, repair: bool, evidence: CoverageEvidence) -> WorkedWindow:
        age = self._age(key, now)
        status: WorkerStatus = "deferred"
        comparison: WindowComparison | None = None
        possible_commit: tuple[WindowComparison | None, WindowState] | None = None
        for attempt in range(1, self.limits.max_attempts + 1):
            if attempt > 1 and self.limits.retry_delay_seconds:
                ceiling = min(1.0, self.limits.retry_delay_seconds * 2 ** (attempt - 2))
                await asyncio.sleep(uniform(ceiling / 2, ceiling))
            try:
                incoming = await self.store.read_reconciliation(key)
            except IntakeForbidden:
                return WorkedWindow(key, "fenced", attempt, age, comparison)
            except _TRANSIENT:
                status = "unavailable"
                continue
            if incoming.v2_inflight or (repair and now < window_bounds(key)[1]):
                status = "deferred"
                continue
            if possible_commit is not None and incoming.state == possible_commit[1]:
                original = possible_commit[0]
                return WorkedWindow(key, "recovered", attempt, age, original,
                                    original.after(incoming.state) if original else None)

            try:
                comparison = compare_reconciliation(incoming, cohort_t0=self.cohort_t0, now=now, evidence=evidence)
                decision = comparison.decision
            except BucketOverflow:
                comparison = None
                decision = ReconcileDecision(
                    incoming.state.source_generation, incoming.state.change_generation, None,
                    coverage_reasons=("v2_coverage_unknown",),
                )
            if not repair:
                return WorkedWindow(key, "read_only" if comparison else "overflow", attempt, age, comparison)

            next_check = None
            if decision.expected_stats is None and now < late_deadline(key):
                next_check = late_deadline(key)
            projected = apply_reconciliation(incoming.state, decision, rebase_at=now)
            assert projected is not None  # The decision was derived from this exact in-memory state.
            durable_evidence = _repair_evidence(comparison, projected, now=now) if comparison else None
            try:
                committed = await self.store.reconcile_window(
                    key, decision, rebase_at=now, evidence=durable_evidence, recheck_at=next_check,
                )
            except IntakeForbidden:
                return WorkedWindow(key, "fenced", attempt, age, comparison)
            except _TRANSIENT:
                # A lost commit reply has an unknown outcome until the next read.
                possible_commit = comparison, projected
                status = "unavailable"
                continue
            if committed is not None:
                return WorkedWindow(key, "committed" if comparison else "overflow", attempt, age, comparison,
                                    comparison.after(committed) if comparison else None)
            status = "superseded"
        return WorkedWindow(key, status, self.limits.max_attempts, age, comparison)
