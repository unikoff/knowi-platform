"""Independent evidence and source handling for the CORP-606 comparison."""

import asyncio
from dataclasses import replace
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from pymentor_analytics.contracts.v2 import AcceptedEvent
from pymentor_analytics.contracts.windows_v1 import (
    ConfirmedWindow, ReconciliationInput, WindowKey, WindowStat, WindowState, apply_reconciliation,
    WindowSnapshot, late_deadline, receive_snapshot, record_v2_commit, window_bounds,
)
from pymentor_analytics.modules.analytics.activity.student_windows_reconcile import (
    CoverageEvidence, compare_reconciliation, compare_windows,
)

from pymentor_analytics.modules.analytics.activity.student_windows_worker import WindowReconcileWorker, WorkerLimits
from pymentor_analytics.modules.collector.ports import IntakeForbidden

FIXTURES = Path(__file__).with_name("fixtures")
SOURCES = json.loads((FIXTURES / "corp_606_inputs.json").read_text(encoding="utf-8"))
ORACLE = json.loads((FIXTURES / "corp_606_expected.json").read_text(encoding="utf-8"))
T0 = datetime.fromisoformat("2026-11-27T10:00:00+03:00")
NOW = datetime.fromisoformat("2026-11-29T11:00:00+03:00")
V2_COMPLETE = CoverageEvidence(v2_source_complete=True, managed_scenario=True)


def _case_pairs(group):
    assert SOURCES["fixture_revision"] == ORACLE["fixture_revision"]
    assert [case["id"] for case in SOURCES[group]] == [case["id"] for case in ORACLE[group]]
    return [
        pytest.param(source, expected, id=source["id"])
        for source, expected in zip(SOURCES[group], ORACLE[group], strict=True)
    ]


def _key(student, row):
    return WindowKey(student_id=UUID(student), activity_date=date.fromisoformat(row["activity_date"]),
                     slot_no=row["slot_no"])


def _stats(rows):
    return tuple(WindowStat(**row) for row in rows)


def _fact(student, raw, *, namespace="oracle-606", catalog=None):
    start = datetime.fromisoformat(raw["started_at"])
    end = datetime.fromisoformat(raw["ended_at"])
    kind = raw.get("type", "activity_segment")
    event = {
        "schema_version": 2, "event_id": UUID(raw["event_id"]), "type": kind,
        "course_id": raw["course_id"], "lesson_id": raw["lesson_id"], "section": raw["section"],
        "catalog_version": catalog or ("platform-tools-v1" if kind == "tool_segment" else "v1"),
        "session_id": UUID(student), "started_at": start, "ended_at": end, "last_action_at": start,
        "visible": True, "focused": True, "active_ms": (end - start) // timedelta(milliseconds=1),
        "payload": {"tool_id": raw["tool_id"]} if kind == "tool_segment" else {},
    }
    return AcceptedEvent(producer_namespace=namespace, subject_ref=UUID(student),
                         source="browser", event=event, received_at=end)


def _confirmation(student, raw):
    return ConfirmedWindow(
        writer_id=UUID(student), writer_generation=1, server_epoch=1, seq=4,
        payload_hash="a" * 64, revision=raw["revision"], stats=_stats(raw["stats"]),
        client_final=True, rebase_at=None,
    )


def _manifest_entries(manifest, student):
    if not manifest:
        return {}
    start = datetime.fromisoformat(manifest["range_start"])
    end = datetime.fromisoformat(manifest["range_end"])
    stats_by_key = {}
    stamp = start
    while stamp < end:
        slot = stamp.hour * 4 + stamp.minute // 15 + 1
        stats_by_key[_key(student, {"activity_date": stamp.date().isoformat(), "slot_no": slot})] = []
        stamp += timedelta(minutes=15)
    for fact in manifest["activities"]:
        left = datetime.fromisoformat(fact["started_at"])
        right = datetime.fromisoformat(fact["ended_at"])
        key = _key(student, {"activity_date": left.date().isoformat(), "slot_no": left.hour * 4 + left.minute // 15 + 1})
        assert start <= left < right <= window_bounds(key)[1]
        stats_by_key[key].append(WindowStat(
            tool_id=fact["tool_id"], course_id=fact["course_id"], lesson_id=fact["lesson_id"],
            active_ms=(right - left) // timedelta(milliseconds=1),
        ))
    assert manifest["complete"]
    return {
        key: CoverageEvidence(manifest_expected=tuple(stats), v2_source_complete=manifest["v2_source_complete"],
                              managed_scenario=True)
        for key, stats in stats_by_key.items()
    }


@pytest.mark.parametrize("source,oracle", _case_pairs("window_cases"))
def test_independent_window_values(source, oracle):
    student = source["student_id"]
    facts = [_fact(student, row) for row in source["v2"]]
    evidence = {_key(student, row): V2_COMPLETE for row in oracle["rows"]}
    report = compare_windows(cohort_t0=T0, now=NOW, v2_facts=reversed(facts), evidence=evidence)

    assert report.denominator == len(oracle["rows"])
    for actual, expected in zip(report.rows, oracle["rows"], strict=True):
        assert actual.diagnostics.key == _key(student, expected)
        assert actual.expected_stats == actual.decision.expected_stats == _stats(expected["stats"])
        assert actual.outcome == "missing_v3"
        assert actual.before.stats == ()
        assert actual.coverage_source == "managed_manifest"


@pytest.mark.parametrize("source,oracle", _case_pairs("coverage_cases"))
def test_independent_coverage_cases(source, oracle):
    student = source["student_id"]
    states = {_key(student, row): WindowState(confirmed=_confirmation(student, row)) for row in source["v3"]}
    facts = [_fact(student, row) for row in source["v2"]]
    evidence = _manifest_entries(source["manifest"], student)
    if source["client_mode"] == "old_web":
        evidence[_key(student, {"activity_date": "2026-11-27", "slot_no": 41})] = CoverageEvidence(old_web_only=True)
    if source.get("known_delivery_gap") == "v2_memory_queue_lost_on_reload":
        evidence = {key: CoverageEvidence(reasons=("v2_unconfirmed",)) for key in states}

    report = compare_windows(cohort_t0=datetime.fromisoformat(source["cohort_t0"]), now=NOW,
                             v2_facts=facts, states=states, evidence=evidence)

    assert report.denominator == oracle["denominator"]
    assert report.passes is oracle["empty_population_pass"]
    assert report.coverage == oracle["unseen_coverage"]
    for actual, expected in zip(report.rows, oracle["rows"], strict=True):
        key = _key(student, expected)
        assert actual.diagnostics.key == key
        assert actual.outcome == expected["outcome"]
        assert actual.expected_stats == (None if expected["expected_stats"] is None else _stats(expected["expected_stats"]))
        code = {"M": "v3_unconfirmed", "L": "v3_only", "U": "v2_coverage_unknown", "D": "absent_both", "O": "old_only"}
        assert code[expected["label"]] in actual.diagnostics.coverage_reasons
        committed = apply_reconciliation(states.get(key, WindowState()), actual.decision, rebase_at=NOW)
        assert committed is not None
        after = actual.after(committed)
        if expected["label"] == "U":
            assert actual.decision.expected_stats is None
            assert after.stats == _stats(expected["preserved_observed_stats"])
            assert after.revision == expected["preserved_observed_revision"]
        elif expected["label"] in {"M", "L"}:
            assert len(actual.before.differences) == 1
            difference = actual.before.differences[0]
            assert (difference.expected_ms, difference.observed_ms) == ((10000, 0) if expected["label"] == "M" else (0, 20000))
            assert after.differences == ()
            assert after.stats_hash == actual.expected_hash
            assert after.revision == (1 if expected["label"] == "M" else 2)
            assert actual.before.revision == (None if expected["label"] == "M" else 1)
        else:
            assert actual.decision.expected_stats is None
            assert committed.confirmed is None


def test_tool_competes_for_the_same_budget_and_namespace_breaks_tie():
    source = SOURCES["window_cases"][-1]
    student = source["student_id"]
    tool = source["v2"][0]
    lesson = {
        **tool, "type": "activity_segment", "course_id": "backend", "lesson_id": "71", "section": "theory",
        "tool_id": None, "ended_at": "2026-11-27T10:00:10+03:00",
    }
    facts = [_fact(student, tool, namespace="a"), _fact(student, lesson, namespace="z")]
    key = _key(student, {"activity_date": "2026-11-27", "slot_no": 41})
    observation = compare_reconciliation(ReconciliationInput(key, WindowState(), tuple(facts), False),
                                         cohort_t0=T0, now=NOW, evidence=V2_COMPLETE)
    assert observation.expected_stats == _stats([
        {"tool_id": "catalog", "course_id": None, "lesson_id": None, "active_ms": 20000},
        {"tool_id": "theory", "course_id": "backend", "lesson_id": "71", "active_ms": 10000},
    ])
    assert observation.decision.catalog_versions == (("backend", "v1"),)
    assert len(observation.diagnostics.v2_confirmed) == 2


def test_opt_in_clips_first_window_and_keeps_control_only_key():
    source = SOURCES["window_cases"][0]
    student = source["student_id"]
    fact = _fact(student, source["v2"][0])
    key_60, key_61, key_62, key_63 = (
        _key(student, {"activity_date": "2026-11-27", "slot_no": number}) for number in range(60, 64)
    )
    cohort_t0 = datetime.fromisoformat("2026-11-27T15:14:57+03:00")
    report = compare_windows(cohort_t0=cohort_t0, now=NOW, v2_facts=(fact,),
                             states={key_60: WindowState(), key_63: WindowState()},
                             evidence={key_61: V2_COMPLETE, key_62: V2_COMPLETE})

    assert [row.diagnostics.key for row in report.rows] == [key_61, key_62, key_63]
    assert [row.expected_stats[0].active_ms for row in report.rows[:2]] == [3000, 20000]
    assert report.rows[2].outcome == "unknown"
    assert report.rows[2].decision.expected_stats is None


def test_maturity_requires_continuity_and_excludes_an_uncommitted_fact():
    source = SOURCES["coverage_cases"][0]
    student = source["student_id"]
    fact = _fact(student, source["v2"][0])
    key = _key(student, {"activity_date": "2026-11-27", "slot_no": 41})
    deadline = late_deadline(key)
    assert deadline == datetime.fromisoformat("2026-12-04T10:16:00+03:00")
    base = ReconciliationInput(key, WindowState(source_generation=5, change_generation=7), (fact,), False)
    continuity = CoverageEvidence(v2_source_complete=True)

    assert compare_reconciliation(base, cohort_t0=T0, now=deadline).outcome == "unknown"
    before = compare_reconciliation(base, cohort_t0=T0, now=deadline - timedelta(seconds=1), evidence=continuity)
    assert before.decision.expected_stats is None
    active = ReconciliationInput(key, base.state, base.v2_facts, True)
    blocked = compare_reconciliation(active, cohort_t0=T0, now=deadline, evidence=continuity)
    assert blocked.decision.expected_stats is None
    assert "v2_inflight" in blocked.diagnostics.coverage_reasons
    ready = compare_reconciliation(base, cohort_t0=T0, now=deadline, evidence=continuity)
    assert ready.coverage_source == "v2_continuity"
    assert ready.decision.expected_stats[0].active_ms == 10000
    assert (ready.decision.source_generation, ready.decision.change_generation) == (5, 7)


def test_conflicting_catalog_versions_keep_an_unrepairable_expected_observation():
    source = SOURCES["window_cases"][2]
    records = (
        _fact(source["student_id"], source["v2"][0]),
        _fact(source["student_id"], source["v2"][3], catalog="v2"),
    )
    key = _key(source["student_id"], {"activity_date": "2026-11-27", "slot_no": 41})
    report = compare_reconciliation(ReconciliationInput(key, WindowState(), records, False),
                                    cohort_t0=T0, now=NOW, evidence=V2_COMPLETE)
    assert report.expected_stats[0].active_ms == 20000
    assert report.outcome == "unknown"
    assert report.decision.expected_stats is None
    assert "catalog_conflict" in report.diagnostics.coverage_reasons


def test_a_matching_observed_window_is_a_pass_before_repair():
    source = SOURCES["coverage_cases"][0]
    student = source["student_id"]
    key = _key(student, {"activity_date": "2026-11-27", "slot_no": 41})
    stats = [{"tool_id": "theory", "course_id": "backend", "lesson_id": "71", "active_ms": 10000}]
    state = WindowState(confirmed=_confirmation(student, {"stats": stats, "revision": 4}))
    report = compare_windows(cohort_t0=T0, now=NOW, states={key: state}, evidence={key: V2_COMPLETE},
                             v2_facts=(_fact(student, source["v2"][0]),))

    assert report.passes
    actual = report.rows[0]
    assert actual.before.stats_hash == actual.expected_hash
    assert actual.before.differences == ()
    assert actual.diagnostics.client_final_seq == 4
    committed = apply_reconciliation(state, actual.decision, rebase_at=NOW)
    assert committed.state == "verified"
    assert actual.after(committed).revision == actual.before.revision == 4


class SimulatedCrash(RuntimeError):
    """Process stopped while its in-memory cursor and observation were disposable."""


class ModelWindowStore:
    def __init__(self, states, v2_facts):
        self.states = states
        self.v2_facts = v2_facts
        self.recheck_at = {}
        self.evidence = {}
        self.fenced = set()
        self.inflight = set()
        self.before_cas = None
        self.fault = None
        self.commit_calls = 0
        self.applied = 0

    @staticmethod
    def order(key):
        return key.activity_date, key.slot_no, key.student_id

    async def changed_windows(self, *, limit, now=None, after=None):
        keys = []
        for key, state in self.states.items():
            due = self.recheck_at.get(key)
            if after is not None and self.order(key) <= self.order(after):
                continue
            if now < window_bounds(key)[1]:
                continue
            if state.state in {"accumulating", "pending_reconciliation"} or (
                state.state == "incomplete_coverage" and due is not None and due <= now
            ):
                keys.append(key)
        return sorted(keys, key=self.order)[:limit]

    async def read_reconciliation(self, key):
        if key in self.fenced:
            raise IntakeForbidden("deleted model lifecycle")
        return ReconciliationInput(key, self.states.get(key, WindowState()),
                                   self.v2_facts.get(key, ()), key in self.inflight)

    async def reconcile_window(self, key, decision, *, rebase_at, evidence=None, recheck_at=None):
        self.commit_calls += 1
        fault, self.fault = self.fault, None
        if fault == "crash_before":
            raise SimulatedCrash("before commit")
        if fault == "timeout_before":
            raise TimeoutError("request lost before commit")
        if fault == "stall_before":
            await asyncio.Future()
        if self.before_cas:
            hook, self.before_cas = self.before_cas, None
            hook(key)
        if key in self.fenced:
            raise IntakeForbidden("deleted model lifecycle")
        current = self.states.get(key, WindowState())
        state = apply_reconciliation(current, decision, rebase_at=rebase_at)
        if state is None:
            return None
        self.states[key] = state
        self.recheck_at[key] = recheck_at
        if evidence is not None:
            self.evidence[key] = evidence
        self.applied += 1
        if fault == "crash_after":
            raise SimulatedCrash("after commit")
        if fault == "timeout_after":
            raise TimeoutError("reply lost after commit")
        return state

    def commit_v2(self, key, fact):
        self.states[key] = record_v2_commit(self.states[key])
        self.v2_facts[key] = (*self.v2_facts[key], fact)

    def purge(self, key):
        self.fenced.add(key)
        self.states.pop(key, None)
        self.v2_facts.pop(key, None)
        self.recheck_at.pop(key, None)
        self.evidence.pop(key, None)


def _dirty_sample(*, observed_ms=20000):
    source = SOURCES["coverage_cases"][0]
    key = _key(source["student_id"], {"activity_date": "2026-11-27", "slot_no": 41})
    confirmed = _confirmation(source["student_id"], {
        "revision": 1,
        "stats": [{"tool_id": "theory", "course_id": "backend", "lesson_id": "71", "active_ms": observed_ms}],
    })
    return key, ModelWindowStore(
        {key: WindowState(confirmed=confirmed, source_generation=1, change_generation=2)},
        {key: (_fact(source["student_id"], source["v2"][0]),)},
    )


def _worker(store, **options):
    return WindowReconcileWorker(store, cohort_t0=T0, limits=WorkerLimits(retry_delay_seconds=0, **options))


def _snapshot(key, *, seq, epoch):
    return WindowSnapshot(
        activity_date=key.activity_date, slot_no=key.slot_no, writer_id=key.student_id, writer_generation=1,
        server_epoch=epoch, seq=seq, client_final=True, catalog_versions={"backend": "v1"},
        stats=(WindowStat(tool_id="theory", course_id="backend", lesson_id="71", active_ms=20000),),
    )


@pytest.mark.parametrize("fault", ("crash_before", "crash_after"))
def test_process_stops_on_each_side_of_commit_and_restarts(fault):
    async def scenario():
        key, store = _dirty_sample()
        initial = store.states[key]
        store.fault = fault
        with pytest.raises(SimulatedCrash):
            await _worker(store).run_once(now=NOW, repair=True, evidence={key: V2_COMPLETE})

        if fault == "crash_before":
            assert store.states[key] == initial and await store.changed_windows(limit=1, now=NOW) == [key]
            assert store.evidence == {}
        else:
            assert store.states[key].confirmed.revision == 2
            assert await store.changed_windows(limit=1, now=NOW) == []

        await _worker(store).run_once(now=NOW, repair=True, evidence={key: V2_COMPLETE})
        assert store.applied == 1
        saved = store.states[key]
        assert saved.state == "repaired" and saved.confirmed.revision == saved.confirmed.server_epoch == 2
        assert saved.confirmed.stats[0].active_ms == 10000
        assert store.evidence[key]["before"]["revision"] == 1
        assert store.evidence[key]["before"]["context_diff"] == [
            {"tool_id": "theory", "course_id": "backend", "lesson_id": "71", "expected_ms": 10000, "observed_ms": 20000},
        ]
        assert store.evidence[key]["after"]["revision"] == 2
        assert store.evidence[key]["after"]["context_diff"] == []
        for seq in (4, 100):
            replay = receive_snapshot(saved, _snapshot(key, seq=seq, epoch=1))
            assert replay.code == "stale_epoch" and replay.state == saved
    asyncio.run(scenario())


@pytest.mark.parametrize(("fault", "status", "calls"), [
    ("timeout_before", "committed", 2),
    ("timeout_after", "recovered", 1),
])
def test_lost_ack_reconciles_state_before_retry(fault, status, calls):
    async def scenario():
        key, store = _dirty_sample()
        store.fault = fault
        result = await _worker(store).run_once(now=NOW, repair=True, evidence={key: V2_COMPLETE})

        (row,) = result.windows
        assert row.status == status and row.attempts == 2
        assert row.comparison.before.revision == 1
        assert row.after.revision == 2
        assert store.applied == 1 and store.commit_calls == calls
        assert store.evidence[key]["before"]["context_diff"][0]["observed_ms"] == 20000
    asyncio.run(scenario())


@pytest.mark.parametrize("active_writer", (False, True))
def test_late_v2_on_day_six_reopens_verified_key_and_never_commits_stale_cut(active_writer):
    async def scenario():
        key, store = _dirty_sample(observed_ms=10000)
        early = await _worker(store).run_once(now=NOW, repair=True, evidence={key: V2_COMPLETE})
        assert early.windows[0].after.revision == 1 and store.states[key].state == "verified"
        date_six = T0 + timedelta(days=6, hours=1)
        original = SOURCES["coverage_cases"][0]["v2"][0]
        initial = _fact(str(key.student_id), {
            **original, "event_id": "60000000-0000-4000-8000-000000000015",
            "started_at": "2026-11-27T10:01:00+03:00", "ended_at": "2026-11-27T10:01:20+03:00",
        }).model_copy(update={"received_at": date_six})
        if active_writer:
            store.inflight.add(key)
            read = await _worker(store).run_key(key, now=date_six, repair=True, evidence=V2_COMPLETE)
            assert read.status == "deferred" and store.states[key].confirmed.revision == 1
            store.inflight.remove(key)
            store.commit_v2(key, initial)
            row = (await _worker(store).run_once(now=date_six, repair=True, evidence={key: V2_COMPLETE})).windows[0]
            assert row.attempts == 1
        else:
            store.before_cas = lambda row_key: store.commit_v2(row_key, initial)
            row = await _worker(store).run_key(key, now=date_six, repair=True, evidence=V2_COMPLETE)
            assert row.status == "committed" and row.attempts == 2
        assert row.comparison.decision.source_generation == 2
        assert row.after.stats[0].active_ms == 30000
        assert row.after.revision == 2
        assert store.applied == 2
        assert store.evidence[key]["before"]["revision"] == 1
    asyncio.run(scenario())


def test_new_writer_generation_supersedes_stale_read_and_keeps_both_sources_if_evidence_is_unknown():
    async def scenario():
        key, store = _dirty_sample()
        initial = store.states[key]
        facts = store.v2_facts[key]

        def new_epoch(row_key):
            old = store.states[row_key]
            confirmed = old.confirmed.model_copy(update={
                "writer_generation": 2, "server_epoch": 2, "revision": 2, "seq": None, "payload_hash": None,
            })
            store.states[row_key] = replace(old, confirmed=confirmed, change_generation=old.change_generation + 1)

        store.before_cas = new_epoch
        (row,) = (await _worker(store).run_once(now=NOW, repair=True)).windows
        assert row.status == "committed" and row.attempts == 2
        assert row.comparison.outcome == "unknown" and row.comparison.decision.expected_stats is None
        assert store.v2_facts[key] == facts
        assert store.states[key].confirmed.stats == initial.confirmed.stats
        assert store.states[key].confirmed.revision == store.states[key].confirmed.server_epoch == 2
        assert store.evidence == {}
        assert store.states[key].state == "incomplete_coverage"
    asyncio.run(scenario())


def test_purge_wins_when_lifecycle_is_deleted_between_read_and_cas():
    async def scenario():
        key, store = _dirty_sample()
        store.before_cas = store.purge
        (row,) = (await _worker(store).run_once(now=NOW, repair=True, evidence={key: V2_COMPLETE})).windows
        assert row.status == "fenced" and row.after is None
        assert store.applied == 0
        assert not store.states and not store.v2_facts and not store.evidence and not store.recheck_at
        assert key in store.fenced
        assert (await _worker(store).run_key(key, now=NOW, repair=True, evidence=V2_COMPLETE)).status == "fenced"
    asyncio.run(scenario())


def test_zero_result_removes_business_content_and_fences_old_snapshot():
    async def scenario():
        key, store = _dirty_sample()
        store.v2_facts[key] = ()
        complete_zero = CoverageEvidence(manifest_expected=(), v2_source_complete=True, managed_scenario=True)
        (row,) = (await _worker(store).run_once(now=NOW, repair=True, evidence={key: complete_zero})).windows
        assert row.comparison.outcome == "extra_v3"
        assert row.after.stats == () and row.after.revision == 2
        zero_fence = store.states[key]
        assert zero_fence.confirmed.server_epoch == 2 and zero_fence.confirmed.seq is None
        for seq in (4, 100):
            replay = receive_snapshot(zero_fence, _snapshot(key, seq=seq, epoch=1))
            assert replay.code == "stale_epoch" and replay.state == zero_fence
    asyncio.run(scenario())


@pytest.mark.parametrize("continuity_on_recheck", (False, True))
def test_early_unknown_durable_recheck_wakes_once_and_still_requires_evidence(continuity_on_recheck):
    async def scenario():
        key, store = _dirty_sample()
        deadline = late_deadline(key)
        initial = store.states[key].confirmed
        (first,) = (await _worker(store).run_once(repair=True, now=NOW)).windows
        assert first.comparison.outcome == "unknown"
        assert store.recheck_at[key] == deadline
        assert store.states[key].confirmed == initial
        empty = await _worker(store).run_once(repair=True, now=deadline - timedelta(seconds=1))
        assert empty.windows == ()

        evidence = {key: CoverageEvidence(v2_source_complete=True)} if continuity_on_recheck else {}
        (second,) = (await _worker(store).run_once(repair=True, now=deadline, evidence=evidence)).windows
        assert store.recheck_at[key] is None
        assert second.comparison.outcome == ("different" if continuity_on_recheck else "unknown")
        assert second.after.revision == (2 if continuity_on_recheck else 1)
        assert (await _worker(store).run_once(repair=True, now=deadline)).windows == ()
    asyncio.run(scenario())


def test_read_only_and_cursor_prevent_a_deferred_prefix_from_blocking_the_page():
    async def scenario():
        key, store = _dirty_sample()
        next_key = key.model_copy(update={"slot_no": 42})
        store.states[next_key] = WindowState()
        store.inflight.add(key)
        worker = _worker(store, batch_limit=1, max_attempts=1)

        (first,) = (await worker.run_once(now=NOW)).windows
        (second,) = (await worker.run_once(now=NOW)).windows
        assert (first.key, first.status) == (key, "deferred")
        assert (second.key, second.status) == (next_key, "read_only")
        assert store.commit_calls == 0
        assert (await worker.run_once(now=NOW)).windows == ()
        assert (await worker.run_once(now=NOW)).windows[0].key == key
    asyncio.run(scenario())


def test_batch_deadline_does_not_ack_or_drop_a_blocked_cas():
    async def scenario():
        key, store = _dirty_sample()
        next_key = key.model_copy(update={"slot_no": 42})
        store.states[next_key] = WindowState()
        store.fault = "stall_before"
        worker = _worker(store, timeout_seconds=0.02)
        result = await worker.run_once(now=NOW, repair=True, evidence={key: V2_COMPLETE})
        assert result.timed_out and store.applied == 0
        assert store.states[key].confirmed.revision == 1
        (next_row,) = (await worker.run_once(now=NOW)).windows
        assert next_row.key == next_key
        (row,) = (await worker.run_once(now=NOW, repair=True, evidence={key: V2_COMPLETE})).windows[:1]
        assert row.key == key and row.after.revision == 2
        assert store.evidence[key]["after"]["revision"] == 2
    asyncio.run(scenario())
