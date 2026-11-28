"""Live a015 reconciliation and CLI evidence on disposable synthetic databases."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.intake import PostgresIntake
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
from pymentor_analytics.adapters.postgres.student_windows import PostgresStudentWindows
from pymentor_analytics.adapters.postgres.student_windows_reconcile import PostgresWindowDiagnosticStore
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.student_windows_runtime import StudentWindowsReconciliationRuntime
from pymentor_analytics.contracts.windows_v1 import WindowSnapshot, WindowStat, late_deadline, window_bounds
from pymentor_analytics.modules.analytics.activity.student_windows_evidence import load_trusted_evidence
from pymentor_analytics.modules.analytics.activity.student_windows_reconcile import CoverageEvidence, compare_reconciliation
from pymentor_analytics.modules.analytics.activity.student_windows_worker import WindowReconcileWorker, WorkerLimits

from .test_reconcile import NOW, SOURCES, T0, V2_COMPLETE, _fact, _key
from .test_storage_pg import (
    AUTHORITY, deletion, live_fixture_guard, snapshot_input, student_windows_target, upgrade_windows,
)
from tools.bootstrap_postgres import connect


@pytest.fixture
def reconcile_target(student_windows_target):
    upgrade_windows(student_windows_target)

    async def initialize_privacy():
        database = Database(Settings(database_url=student_windows_target[2]["privacy"]))
        try:
            assert (await PostgresPrivacyStore(database).cursor(AUTHORITY)).applied_through == 0
        finally:
            await database.close()
    asyncio.run(initialize_privacy())
    return student_windows_target


@asynccontextmanager
async def stack(target, *, key, processor=PostgresWindowDiagnosticStore, cohort_t0=T0, marker=True):
    data, name, urls, run_id = target
    collector, processor_db, privacy_db = (
        Database(Settings(database_url=urls[role], db_timeout_seconds=10))
        for role in ("collector", "processor", "privacy")
    )
    try:
        privacy = PostgresPrivacyStore(privacy_db)
        assert (await privacy.cursor(AUTHORITY)).applied_through == 0
        collector.transaction_guard = processor_db.transaction_guard = live_fixture_guard
        writer = PostgresStudentWindows(collector, cohort={key.student_id}, observation_start=cohort_t0)
        store = processor(processor_db, cohort={key.student_id}, observation_start=cohort_t0)
        intake = PostgresIntake(collector, windows_changed=writer if marker else None)
        yield collector, writer, store, intake, privacy
    finally:
        for database in (collector, processor_db, privacy_db):
            await database.close()


def _sample():
    raw = SOURCES["coverage_cases"][0]
    return _key(raw["student_id"], {"activity_date": "2026-11-27", "slot_no": 41}), raw["v2"][0]


def _snapshot(key, *, ms=20000, section="theory", lesson="71"):
    return WindowSnapshot(
        activity_date=key.activity_date, slot_no=key.slot_no, writer_id=uuid4(), writer_generation=1,
        server_epoch=1, seq=4, stats=(WindowStat(tool_id=section, course_id="backend", lesson_id=lesson, active_ms=ms),),
        catalog_versions={"backend": "v1"}, client_final=True,
    )


async def _seed(writer, intake, key, literal=None, *, ms=20000, section="theory", lesson="71"):
    if literal is not None:
        assert await intake.accept(_fact(str(key.student_id), literal), deadline=time.monotonic() + 5) == "accepted"
    snap = _snapshot(key, ms=ms, section=section, lesson=lesson)
    result = await writer.write_window(key, snap, received_at=window_bounds(key)[1] + timedelta(minutes=1))
    assert result.status == "committed" and result.state.confirmed.revision == 1
    return snap


def _persisted(target, key):
    data, name, _, _ = target
    with connect(data, name) as db:
        return db.execute(
            """SELECT b.stats,b.revision,c.server_epoch,c.phase,c.source_generation,c.change_generation,
                      c.reconciled_generation,c.recheck_at,c.reconciliation_evidence
               FROM analytics.student_activity_window_control c
               LEFT JOIN analytics.student_activity_windows b USING (student_id,activity_date,slot_no)
               WHERE c.student_id=%s AND c.activity_date=%s AND c.slot_no=%s""",
            (key.student_id, key.activity_date, key.slot_no),
        ).fetchone()


def _cli(target, key, *extra, repair=False, bad_sentinel=False):
    data, name, urls, _ = target
    with connect(data, name) as db:
        token = db.execute("SELECT token FROM public.analytics_test_sentinel").fetchone()[0]
    sentinel = "0" * 64 if bad_sentinel else sha256(token.encode()).hexdigest()
    default = Path(__file__).resolve().parents[2] / ".local/postgres-connection.json"
    config_path = os.environ.get("ANALYTICS_STUDENT_WINDOWS_PG_CONFIG", str(default))
    command = [
        sys.executable, "-B", "tools/check_student_windows.py", "--pg-config", config_path, "--target", name,
        "--sentinel-sha256", sentinel, "--student-id", str(key.student_id), "--cohort-t0", T0.isoformat(),
        "--through", NOW.isoformat(), *extra,
    ]
    if repair:
        command.append("--repair")
    response = subprocess.run(
        command, env={**os.environ, "ANALYTICS_STUDENT_WINDOWS_PROCESSOR_URL": urls["processor"]},
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30, check=False,
    )
    assert response.returncode == (2 if bad_sentinel else 0), response.stderr
    return json.loads(response.stdout)


def _worker(store, *, cohort_t0=T0):
    return WindowReconcileWorker(store, cohort_t0=cohort_t0, limits=WorkerLimits(retry_delay_seconds=0))


def test_v3_only_is_unknown_in_read_only_and_repair_cli(reconcile_target):
    raw = SOURCES["coverage_cases"][1]
    key = _key(raw["student_id"], raw["v3"][0])

    async def seed():
        async with stack(reconcile_target, key=key) as (_, writer, _, intake, _):
            await _seed(writer, intake, key, section="practice", lesson="72")
    asyncio.run(seed())
    initial = _persisted(reconcile_target, key)

    assert _cli(reconcile_target, key, repair=True, bad_sentinel=True)["status"] == "unavailable"
    read_only = _cli(reconcile_target, key)
    assert _persisted(reconcile_target, key) == initial
    repaired = _cli(reconcile_target, key, repair=True)
    for report in (read_only, repaired):
        assert (report["status"], report["denominator"], report["v3_only"]) == ("unknown", 1, 1)
        (row,) = report["rows"]
        assert row["outcome"] == "unknown" and row["before"]["revision"] == 1
        assert row["before"]["stats"][0]["active_ms"] == 20000 and row["repairable"] is False
    actual = _persisted(reconcile_target, key)
    assert actual[:3] == initial[:3] and actual[3] == "incomplete_coverage" and actual[-1] is None
    print(f"606-Б U: unknown, {actual[0][0]['active_ms']} ms, revision {actual[1]}, sentinel rejection passed")


class HoldLateCommit(PostgresIntake):
    def __init__(self, database, *, windows_changed):
        super().__init__(database, windows_changed=windows_changed)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def _before_commit(self):
        self.entered.set()
        await asyncio.wait_for(self.release.wait(), timeout=5)


def test_day_six_commit_reopens_verified_and_inflight_blocks_maturity(reconcile_target):
    key, literal = _sample()
    day_six = T0 + timedelta(days=6, hours=1)
    deadline = late_deadline(key)

    async def scenario():
        async with stack(reconcile_target, key=key) as (collector, writer, store, intake, _):
            await _seed(writer, intake, key, literal, ms=10000)
            processed = await _worker(store).run_once(now=NOW, repair=True, evidence={key: V2_COMPLETE})
            assert processed.windows[0].after.revision == 1
            verified = await store.read_reconciliation(key)
            assert verified.state.state == "verified"
            assert (verified.state.source_generation, verified.state.change_generation) == (1, 3)
            stale = compare_reconciliation(verified, cohort_t0=T0, now=day_six, evidence=V2_COMPLETE)

            late = _fact(str(key.student_id), {
                **literal, "event_id": "60000000-0000-4000-8000-000000000015",
                "started_at": "2026-11-27T10:01:00+03:00", "ended_at": "2026-11-27T10:01:20+03:00",
            }).model_copy(update={"received_at": day_six})
            held = HoldLateCommit(collector, windows_changed=writer)
            upload = asyncio.create_task(held.accept(late, deadline=time.monotonic() + 7))
            try:
                await asyncio.wait_for(held.entered.wait(), timeout=5)
                assert (await store.read_reconciliation(key)).v2_inflight
                blocked = await _worker(store).run_key(
                    key, now=deadline, repair=True, evidence=CoverageEvidence(v2_source_complete=True),
                )
                assert blocked.status == "deferred" and blocked.after is None
                assert _persisted(reconcile_target, key)[1:5] == (1, 1, "verified", 1)
            finally:
                held.release.set()
                assert await upload == "accepted"

            assert await store.changed_windows(limit=10, now=deadline) == (key,)
            dirty = (await store.read_reconciliation(key)).state
            assert (dirty.source_generation, dirty.change_generation) == (2, 4)
            assert await store.reconcile_window(key, stale.decision, rebase_at=deadline) is None
            report = await _worker(store).run_once(
                now=deadline, repair=True, evidence={key: CoverageEvidence(v2_source_complete=True)},
            )
            (row,) = report.windows
            assert row.after.revision == 2 and row.after.stats[0].active_ms == 30000
            assert row.comparison.decision.source_generation == 2
    asyncio.run(scenario())

    actual = _persisted(reconcile_target, key)
    assert actual[0][0]["active_ms"] == 30000 and actual[1:7] == (2, 2, "repaired", 2, 5, 2)
    print(f"606-В late received={day_six.isoformat()} mature={deadline.isoformat()} 10000 -> 30000 ms, rev 1 -> 2, old CAS rejected")


class SimulatedStop(RuntimeError):
    """Injected abrupt shutdown around the real processor transaction."""


class StopBeforeCommit(PostgresWindowDiagnosticStore):
    async def _save(self, session, key, previous, state):
        await super()._save(session, key, previous, state)
        if state.state == "repaired":
            raise SimulatedStop("before commit")


class StopAfterCommit(PostgresWindowDiagnosticStore):
    async def reconcile_window(self, key, decision, **kwargs):
        committed = await super().reconcile_window(key, decision, **kwargs)
        if committed is not None and committed.state == "repaired":
            raise SimulatedStop("after commit")
        return committed


@pytest.mark.parametrize("processor", (StopBeforeCommit, StopAfterCommit))
def test_processor_restart_preserves_one_repair_and_origin_evidence(reconcile_target, processor):
    key, literal = _sample()

    async def scenario():
        async with stack(reconcile_target, key=key, processor=processor) as (_, writer, fault, intake, _):
            snap = await _seed(writer, intake, key, literal)
            with pytest.raises(SimulatedStop):
                await _worker(fault).run_once(now=NOW, repair=True, evidence={key: V2_COMPLETE})
            seen = _persisted(reconcile_target, key)
            if processor is StopBeforeCommit:
                assert seen[1:4] == (1, 1, "pending_reconciliation") and seen[-1] is None
            else:
                assert seen[1:4] == (2, 2, "repaired") and seen[-1]["before"]["revision"] == 1

            restarted = PostgresWindowDiagnosticStore(fault.database, cohort={key.student_id}, observation_start=T0)
            result = await _worker(restarted).run_once(now=NOW, repair=True, evidence={key: V2_COMPLETE})
            assert len(result.windows) == (1 if processor is StopBeforeCommit else 0)
            for seq in (4, 100):
                replay = await writer.write_window(key, snap.model_copy(update={"seq": seq}),
                                                   received_at=window_bounds(key)[1] + timedelta(minutes=2))
                assert replay.code == "stale_epoch" and replay.state.confirmed.revision == 2
    asyncio.run(scenario())

    actual = _persisted(reconcile_target, key)
    assert actual[0][0]["active_ms"] == 10000 and actual[1:4] == (2, 2, "repaired")
    exported = _cli(reconcile_target, key)["rows"][0]["durable_repair"]
    assert exported == actual[-1]
    assert exported["before"]["context_diff"] == [
        {"tool_id": "theory", "course_id": "backend", "lesson_id": "71", "expected_ms": 10000, "observed_ms": 20000},
    ]
    assert exported["before"]["revision"] == 1 and exported["after"]["revision"] == 2
    assert exported["after"]["context_diff"] == []
    print(f"606-Г {processor.__name__}: rev 2, epoch 2, 10000 ms; durable before 20000 ms rev 1, replay seq 4/100 fenced")


def test_confirmed_zero_removes_business_row_and_preserves_epoch_fence(reconcile_target):
    key, _ = _sample()
    evidence = CoverageEvidence(manifest_expected=(), v2_source_complete=True, managed_scenario=True)

    async def scenario():
        async with stack(reconcile_target, key=key) as (_, writer, store, intake, _):
            snap = await _seed(writer, intake, key)
            (row,) = (await _worker(store).run_once(now=NOW, repair=True, evidence={key: evidence})).windows
            assert not row.after.stats and row.after.revision == 2
            for seq in (4, 100):
                result = await writer.write_window(key, snap.model_copy(update={"seq": seq}),
                                                   received_at=window_bounds(key)[1] + timedelta(minutes=2))
                assert result.code == "stale_epoch"
    asyncio.run(scenario())
    actual = _persisted(reconcile_target, key)
    assert actual[:4] == (None, None, 2, "repaired") and actual[-1]["after"]["revision"] == 2
    print("606-Г zero: business absent, epoch 2, repaired revision 2, old replay rejected")


class WaitForPurge(PostgresWindowDiagnosticStore):
    async def reconcile_window(self, key, decision, **kwargs):
        self.planned.set()
        await asyncio.wait_for(self.resume.wait(), timeout=5)
        return await super().reconcile_window(key, decision, **kwargs)


def test_real_purge_wins_after_calculation_before_cas(reconcile_target):
    key, literal = _sample()

    async def scenario():
        async with stack(reconcile_target, key=key, processor=WaitForPurge) as (_, writer, store, intake, privacy):
            snap = await _seed(writer, intake, key, literal)
            store.planned, store.resume = asyncio.Event(), asyncio.Event()
            work = asyncio.create_task(_worker(store).run_once(now=NOW, repair=True, evidence={key: V2_COMPLETE}))
            try:
                await asyncio.wait_for(store.planned.wait(), timeout=5)
                gone = await privacy.apply(AUTHORITY, deletion(key.student_id))
                assert gone.complete
            finally:
                store.resume.set()
                (row,) = (await work).windows
            assert row.status == "fenced" and row.after is None
            replay = await writer.write_window(key, snap, received_at=window_bounds(key)[1] + timedelta(minutes=2))
            assert replay.code == "privacy_forbidden"
    asyncio.run(scenario())

    assert _persisted(reconcile_target, key) is None
    data, name, _, _ = reconcile_target
    with connect(data, name) as db:
        for relation, field in (
            ("student_activity_windows", "student_id"), ("student_activity_window_control", "student_id"),
            ("activity_facts", "subject_ref"), ("processing_jobs", "subject_ref"),
        ):
            present = db.execute(f"SELECT count(*) FROM analytics.{relation} WHERE {field}=%s", (key.student_id,)).fetchone()
            assert present == (0,)
        assert db.execute("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=%s",
                          (key.student_id,)).fetchone() == (1, True)
    print("606-Д: zero business/control/facts/jobs, lifecycle fence epoch 1, replay denied")


def test_cli_manifest_only_window_and_empty_population(reconcile_target, tmp_path):
    raw = SOURCES["coverage_cases"][2]
    key = _key(raw["student_id"], {"activity_date": "2026-11-27", "slot_no": 41})
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({
        "protocol_version": 1,
        "windows": [{"key": key.model_dump(mode="json"), "v2_source_complete": False, "expected_stats": [
            {"tool_id": "theory", "course_id": "backend", "lesson_id": "71", "active_ms": 10000},
        ]}],
    }), encoding="utf-8")
    empty = _cli(reconcile_target, key)
    observed = _cli(reconcile_target, key, "--manifest", str(path))
    assert (empty["status"], empty["denominator"], empty["mismatch_before"]) == ("unknown", 0, 0)
    assert (observed["status"], observed["denominator"], observed["mismatch_before"]) == ("mismatch", 1, 1)
    (row,) = observed["rows"]
    assert row["outcome"] == "both_lost" and row["repairable"] is False
    assert row["expected_stats"][0]["active_ms"] == 10000 and row["before"]["stats"] == []
    assert _persisted(reconcile_target, key) is None
    print("606-Е: with manifest denominator=1 mismatch=1 absent_both; without denominator=0 coverage unknown")


def test_runtime_hook_default_off_then_ready_runs_unknown_and_stops(reconcile_target):
    now = datetime.now(timezone.utc)
    key, snap = snapshot_input(uuid4(), now)
    cohort_t0 = window_bounds(key)[0]

    class Gate:
        opened = False

        async def is_ready(self):
            return self.opened

    async def scenario():
        async with stack(reconcile_target, key=key, cohort_t0=cohort_t0) as (_, writer, store, _, _):
            assert (await writer.write_window(key, snap, received_at=now)).status == "committed"
            gate = Gate()
            worker = _worker(store, cohort_t0=cohort_t0)
            disabled = StudentWindowsReconciliationRuntime(worker=worker, readiness=gate)
            disabled.start()
            await disabled.stop()
            assert disabled.metrics.keys == 0

            runtime = StudentWindowsReconciliationRuntime(worker=worker, readiness=gate, enabled=True, poll_seconds=0.1)
            runtime.start()
            try:
                await asyncio.sleep(0.12)
                assert runtime.metrics.keys == 0
                gate.opened = True
                async with asyncio.timeout(5):
                    while runtime.metrics.keys == 0:
                        await asyncio.sleep(0.01)
                assert runtime.metrics.coverage_unknown == 1 and runtime.metrics.repairs == 0
            finally:
                await runtime.stop()
            assert runtime._task is None
    asyncio.run(scenario())

    actual = _persisted(reconcile_target, key)
    assert actual[1:4] == (1, 1, "incomplete_coverage") and actual[7] == late_deadline(key)
    print("606.C hook: off and closed idle; open processes one unknown, rev 1 unchanged; shutdown complete")


def test_cli_finds_v2_only_midnight_tail_without_dirty_control(reconcile_target, tmp_path):
    raw = SOURCES["window_cases"][1]
    key = _key(raw["student_id"], {"activity_date": "2026-11-27", "slot_no": 96})
    next_key = _key(raw["student_id"], {"activity_date": "2026-11-28", "slot_no": 1})
    evidence_path = tmp_path / "old-client-manifest.json"
    evidence_path.write_text(json.dumps({
        "protocol_version": 1, "windows": [
            {"key": item.model_dump(mode="json"), "old_web_only": True} for item in (key, next_key)
        ],
    }), encoding="utf-8")

    async def seed():
        async with stack(reconcile_target, key=key, marker=False) as (_, _, _, intake, _):
            assert await intake.accept(_fact(raw["student_id"], raw["v2"][0]), deadline=time.monotonic() + 5) == "accepted"
    asyncio.run(seed())

    plain = _cli(reconcile_target, key)
    assert plain["denominator"] == 2 and plain["unknown"] == 2
    assert [row["key"] for row in plain["rows"]] == [item.model_dump(mode="json") for item in (key, next_key)]
    assert [row["expected_stats"][0]["active_ms"] for row in plain["rows"]] == [10000, 20000]
    old = _cli(reconcile_target, key, "--manifest", str(evidence_path))
    assert old["old_only"] == 2 and old["status"] == "unknown"
    limited = _cli(reconcile_target, key, "--limit", "1")
    assert limited["truncated"] and limited["denominator"] == 1 and limited["status"] == "unknown"
    assert _persisted(reconcile_target, key) is None and _persisted(reconcile_target, next_key) is None
    print("606.C v2-only SQL scan: 27/96 10000 ms and 28/1 20000 ms, denominator 2 without control; old-only and truncation visible")


class LoseCommittedReply(PostgresWindowDiagnosticStore):
    commit_calls = 0

    async def reconcile_window(self, key, decision, **kwargs):
        self.commit_calls += 1
        result = await super().reconcile_window(key, decision, **kwargs)
        if result is not None:
            raise TimeoutError("synthetic loss after the processor transaction committed")
        return result


def test_lost_processor_ack_rechecks_existing_commit_without_second_cas(reconcile_target):
    key, literal = _sample()

    async def scenario():
        async with stack(reconcile_target, key=key, processor=LoseCommittedReply) as (_, writer, store, intake, _):
            await _seed(writer, intake, key, literal)
            (row,) = (await _worker(store).run_once(now=NOW, repair=True, evidence={key: V2_COMPLETE})).windows
            assert row.status == "recovered" and row.attempts == 2 and store.commit_calls == 1
            assert row.comparison.before.revision == 1 and row.after.revision == 2
    asyncio.run(scenario())
    actual = _persisted(reconcile_target, key)
    assert actual[1:4] == (2, 2, "repaired") and actual[-1]["after"]["revision"] == 2
    print("606.C lost processor ACK: second read, one CAS, rev 2")


def test_runtime_managed_proof_waits_for_v2_and_then_repairs(reconcile_target, tmp_path):
    now = datetime.now(timezone.utc)
    key, _ = snapshot_input(uuid4(), now)
    cohort_t0 = window_bounds(key)[0]
    path = tmp_path / "runtime-proof.json"

    def write_manifest(*, t0=cohort_t0, proof_key=key, complete=True):
        path.write_text(json.dumps({
            "protocol_version": 1, "cohort_t0": t0.isoformat(), "windows": [{
                "key": proof_key.model_dump(mode="json"), "v2_source_complete": complete,
                "expected_stats": [
                    {"tool_id": "theory", "course_id": "backend", "lesson_id": "71", "active_ms": 30000},
                ],
            }],
        }), encoding="utf-8")

    options = dict(cohort={key.student_id}, cohort_t0=cohort_t0)
    write_manifest(t0=cohort_t0 - timedelta(minutes=15))
    with pytest.raises(ValueError, match="configured T0"):
        load_trusted_evidence(path, **options)
    write_manifest(proof_key=key.model_copy(update={"student_id": uuid4()}))
    with pytest.raises(ValueError, match="cohort"):
        load_trusted_evidence(path, **options)
    write_manifest(complete=False)
    with pytest.raises(ValueError, match="confirmed v2"):
        load_trusted_evidence(path, **options)

    write_manifest()
    evidence = load_trusted_evidence(path, **options)

    class Ready:
        async def is_ready(self):
            return True

    async def scenario():
        async with stack(reconcile_target, key=key, cohort_t0=cohort_t0) as (_, writer, store, intake, _):
            start = cohort_t0 + timedelta(seconds=1)
            literal = {
                "event_id": str(uuid4()), "started_at": start.isoformat(),
                "ended_at": (start + timedelta(seconds=10)).isoformat(),
                "course_id": "backend", "lesson_id": "71", "section": "theory",
            }
            await _seed(writer, intake, key, literal)
            runtime = StudentWindowsReconciliationRuntime(
                worker=_worker(store, cohort_t0=cohort_t0), readiness=Ready(), enabled=True, poll_seconds=0.1,
                trusted_evidence=evidence,
            )
            runtime.start()
            try:
                async with asyncio.timeout(5):
                    while runtime.metrics.coverage_unknown == 0:
                        await asyncio.sleep(0.01)
                current = _persisted(reconcile_target, key)
                assert current[0][0]["active_ms"] == 20000 and current[1:4] == (1, 1, "incomplete_coverage")

                later = start + timedelta(minutes=1)
                next_fact = _fact(str(key.student_id), {
                    **literal, "event_id": str(uuid4()), "started_at": later.isoformat(),
                    "ended_at": (later + timedelta(seconds=20)).isoformat(),
                }).model_copy(update={"received_at": now})
                assert await intake.accept(next_fact, deadline=time.monotonic() + 5) == "accepted"

                async with asyncio.timeout(5):
                    while runtime.metrics.repairs == 0:
                        await asyncio.sleep(0.01)
                assert runtime.metrics.mismatch_before == 1 and runtime.metrics.mismatch_after == 0
            finally:
                await runtime.stop()
    asyncio.run(scenario())

    actual = _persisted(reconcile_target, key)
    assert actual[0][0]["active_ms"] == 30000 and actual[1:4] == (2, 2, "repaired")
    assert actual[-1]["coverage_source"] == "managed_manifest"
    assert actual[-1]["before"]["revision"] == 1 and actual[-1]["after"]["revision"] == 2
    print("606.C trusted runtime proof: wrong T0, outsider and incomplete rejected; 20000 rev 1 held until source reached 30000, then rev 2")
