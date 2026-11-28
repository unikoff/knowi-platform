"""Pure source comparison for Analytics shadow windows."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Literal

from pymentor_analytics.contracts.v2 import AcceptedEvent
from pymentor_analytics.contracts.windows_v1 import (
    MAX_CONTEXTS, ConfirmedWindow, CoverageReason, ReconcileDecision, ReconciliationInput,
    V2FactReceipt, WindowDiagnostics, WindowKey, WindowStat, WindowState,
    canonical_stats, late_deadline, split_interval, v2_context, window_bounds,
)
from pymentor_analytics.modules.analytics.activity.bucket import (
    MAX_BUCKET_FACTS, BucketOverflow, iter_winning_segments,
)
from pymentor_analytics.modules.analytics.activity.calculator import _ms

Stats = tuple[WindowStat, ...]
Outcome = Literal["matched", "missing_v3", "extra_v3", "different", "old_only", "both_lost", "unknown"]
CoverageSource = Literal["managed_manifest", "v2_continuity", "unknown"]


@dataclass(frozen=True, slots=True)
class CoverageEvidence:
    """Independent evidence for one key, supplied by the diagnostic or trusted caller."""

    manifest_expected: Stats | None = None
    v2_source_complete: bool = False
    managed_scenario: bool = False
    old_web_only: bool = False
    client_final_seq: int | None = None
    reasons: tuple[CoverageReason, ...] = ()

    def __post_init__(self) -> None:
        if self.manifest_expected is not None:
            if not self.managed_scenario:
                raise ValueError("expected values require a managed manifest")
            object.__setattr__(self, "manifest_expected", canonical_stats(self.manifest_expected))


@dataclass(frozen=True, slots=True)
class ContextDifference:
    tool_id: str
    course_id: str | None
    lesson_id: str | None
    expected_ms: int
    observed_ms: int


@dataclass(frozen=True, slots=True)
class ObservedWindow:
    stats: Stats
    stats_hash: str | None
    revision: int | None
    payload_hash: str | None
    differences: tuple[ContextDifference, ...] | None


@dataclass(frozen=True, slots=True)
class WindowComparison:
    diagnostics: WindowDiagnostics
    outcome: Outcome
    coverage_source: CoverageSource
    late_deadline: datetime
    expected_stats: Stats | None
    expected_hash: str | None
    before: ObservedWindow
    decision: ReconcileDecision

    def after(self, committed: WindowState) -> ObservedWindow:
        """Capture the adapter's committed observation without replacing the original."""
        return _observe(self.expected_stats, committed.confirmed)


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    rows: tuple[WindowComparison, ...]

    @property
    def denominator(self) -> int:
        return len(self.rows)

    @property
    def coverage(self) -> Literal["known", "unknown"]:
        if self.rows and all(row.coverage_source != "unknown" for row in self.rows):
            return "known"
        return "unknown"

    @property
    def passes(self) -> bool:
        return any(row.expected_stats for row in self.rows) and all(
            row.outcome == "matched" for row in self.rows
        )


def _digest(stats: Stats) -> str:
    body = [row.model_dump(mode="json") for row in stats]
    return sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _observe(expected: Stats | None, confirmation: ConfirmedWindow | None) -> ObservedWindow:
    stats = confirmation.stats if confirmation else ()
    differences = None
    if expected is not None:
        wanted = {row.context: row for row in expected}
        actual = {row.context: row for row in stats}
        diff = []
        for key in sorted(wanted.keys() | actual.keys()):
            expected_ms = wanted[key].active_ms if key in wanted else 0
            observed_ms = actual[key].active_ms if key in actual else 0
            if expected_ms != observed_ms:
                context = wanted.get(key) or actual[key]
                diff.append(ContextDifference(context.tool_id, context.course_id, context.lesson_id,
                                              expected_ms, observed_ms))
        differences = tuple(diff)
    return ObservedWindow(
        stats, _digest(stats) if confirmation else None,
        confirmation.revision if confirmation else None,
        confirmation.payload_hash if confirmation else None,
        differences,
    )


def _measure(
    facts: Sequence[AcceptedEvent], key: WindowKey, *, start: datetime, end: datetime,
) -> tuple[Stats, tuple[tuple[str, str], ...], tuple[V2FactReceipt, ...], bool]:
    lower, upper = _ms(start), _ms(end)
    seen = set()
    segments = []
    receipts = []
    for index, record in enumerate(facts):
        if index >= MAX_BUCKET_FACTS:
            raise BucketOverflow("reconciliation bucket fact limit exceeded")
        if record.subject_ref != key.student_id:
            raise ValueError("facts contain a different student")
        event = record.event
        left, right = max(lower, _ms(event.started_at)), min(upper, _ms(event.ended_at))
        if left >= right or event.type == "course_opened":
            continue
        identity = record.producer_namespace, event.event_id
        if identity in seen:
            raise ValueError("reconciliation facts must already be deduplicated")
        seen.add(identity)
        receipts.append(V2FactReceipt(producer_namespace=identity[0], event_id=identity[1], status="accepted"))
        segments.append((record, left, right))

    amounts = defaultdict(int)
    versions = defaultdict(set)
    for record, left, right in iter_winning_segments(segments):
        context = v2_context(record)
        assert context is not None  # Only measured activities reached the sweep.
        amounts[context] += right - left
        if record.event.type == "activity_segment":
            versions[record.event.course_id].add(record.event.catalog_version)

    stats = tuple(sorted(
        (WindowStat(tool_id=tool, course_id=course, lesson_id=lesson, active_ms=amount)
         for (tool, course, lesson), amount in amounts.items()),
        key=lambda row: row.context,
    ))
    catalog = tuple(sorted((course, min(values)) for course, values in versions.items()))
    receipts.sort(key=lambda item: (item.producer_namespace, item.event_id))
    return stats, catalog, tuple(receipts), any(len(values) > 1 for values in versions.values())


def compare_reconciliation(
    incoming: ReconciliationInput, *, cohort_t0: datetime, now: datetime, evidence: CoverageEvidence = CoverageEvidence(),
) -> WindowComparison:
    """Use one consistent accepted-fact snapshot and independent evidence for a key."""
    start, end = window_bounds(incoming.key)
    if cohort_t0.utcoffset() is None or now.utcoffset() is None or end <= cohort_t0:
        raise ValueError("aware clocks and a key in the observation period are required")
    deadline = late_deadline(incoming.key)
    v2, catalog, receipts, catalog_conflict = _measure(
        incoming.v2_facts, incoming.key, start=max(start, cohort_t0), end=end,
    )
    state = incoming.state
    confirmed = state.confirmed
    observed = confirmed.stats if confirmed else ()
    manifest_expected = evidence.manifest_expected
    blockers = {
        reason for reason in evidence.reasons
        if reason in {"v2_inflight", "v2_unconfirmed", "v2_coverage_unknown", "source_generation_changed"}
    }
    if incoming.v2_inflight:
        blockers.add("v2_inflight")
    if not evidence.v2_source_complete or now < end or (now < deadline and not evidence.managed_scenario):
        blockers.add("v2_coverage_unknown")
    elif manifest_expected is not None and manifest_expected != v2:
        blockers.update(("v2_coverage_unknown", "v2_unconfirmed"))
    if catalog_conflict:
        blockers.add("catalog_conflict")
    if len(v2) > MAX_CONTEXTS:
        blockers.add("v2_coverage_unknown")
    if evidence.old_web_only and not observed:
        blockers.add("old_only")

    covered = not blockers
    source: CoverageSource = "unknown"
    if evidence.managed_scenario and (covered or manifest_expected is not None):
        source = "managed_manifest"
    elif covered:
        source = "v2_continuity"
    reasons = set(evidence.reasons) | blockers
    reasons.update(reason for reason in state.coverage_reasons if reason in {"writer_conflict", "catalog_conflict"})
    expected = manifest_expected if manifest_expected is not None else (v2 if covered or v2 else None)
    if manifest_expected and not v2 and not observed:
        outcome: Outcome = "both_lost"
        reasons.add("absent_both")
    elif evidence.old_web_only and not observed:
        outcome = "old_only"
    elif not covered:
        outcome = "unknown"
        if observed and not v2:
            reasons.add("v3_only")
    elif not observed and v2:
        outcome = "missing_v3"
        reasons.add("v3_unconfirmed")
    elif observed and not v2:
        outcome = "extra_v3"
        reasons.add("v3_only")
    else:
        outcome = "matched" if v2 == observed else "different"

    final_seq = evidence.client_final_seq
    if final_seq is None and confirmed and confirmed.client_final:
        final_seq = confirmed.seq
    diagnostics = WindowDiagnostics(
        protocol_version=1, key=incoming.key, client_final_seq=final_seq, v3_confirmed=confirmed,
        v2_confirmed=receipts, source_generation=state.source_generation,
        reconciled_generation=state.reconciled_generation, state=state.state,
        coverage_reasons=tuple(sorted(reasons)),
    )
    decision = ReconcileDecision(
        state.source_generation, state.change_generation, v2 if covered else None, catalog if covered else (),
        () if covered else diagnostics.coverage_reasons,
    )
    return WindowComparison(
        diagnostics, outcome, source, deadline, expected, _digest(expected) if expected is not None else None,
        _observe(expected, confirmed), decision,
    )


def compare_windows(
    *, cohort_t0: datetime, now: datetime, v2_facts: Iterable[AcceptedEvent] = (),
    states: Mapping[WindowKey, WindowState] | None = None,
    evidence: Mapping[WindowKey, CoverageEvidence] | None = None,
    in_flight: Collection[WindowKey] = (),
) -> ComparisonReport:
    """Compare the union of keys from v2, v3/control and independent evidence."""
    if cohort_t0.utcoffset() is None or now.utcoffset() is None:
        raise ValueError("aware clocks required")
    states = states or {}
    evidence = evidence or {}
    facts_by_key = defaultdict(list)
    for fact in v2_facts:
        event = fact.event
        if event.type == "course_opened" or event.ended_at <= cohort_t0:
            continue
        for coordinate, _ in split_interval(max(event.started_at, cohort_t0), event.ended_at):
            key = WindowKey(student_id=fact.subject_ref, activity_date=coordinate.activity_date,
                            slot_no=coordinate.slot_no)
            facts_by_key[key].append(fact)
    keys = facts_by_key.keys() | states.keys() | evidence.keys() | set(in_flight)
    rows = []
    for key in sorted(keys, key=lambda item: (item.student_id, item.activity_date, item.slot_no)):
        if window_bounds(key)[1] <= cohort_t0:
            continue
        incoming = ReconciliationInput(key, states.get(key, WindowState()), tuple(facts_by_key[key]), key in in_flight)
        rows.append(compare_reconciliation(incoming, cohort_t0=cohort_t0, now=now,
                                           evidence=evidence.get(key, CoverageEvidence())))
    return ComparisonReport(tuple(rows))
