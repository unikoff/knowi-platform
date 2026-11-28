"""Opt-in storage and privacy regressions on disposable PostgreSQL."""
import asyncio
import base64
import json
import os
import time
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pymentor_analytics.adapters.postgres.activity_reports import PostgresActivityReport
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.intake import PostgresIntake
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.adapters.postgres import privacy as privacy_adapter
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore, PostgresSubjectFence
from pymentor_analytics.adapters.postgres.student_windows import PostgresStudentWindows, has_student_windows_schema
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
from pymentor_analytics.adapters.privacy_authority.journal import Journal
from pymentor_analytics.adapters.privacy_authority.server import create_authority_app
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.privacy import PrivacyRuntime
from pymentor_analytics.contracts.auth_v1 import VerifiedGrant
from pymentor_analytics.contracts.privacy_control_v1 import JournalEntry, deletion_hash
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.v2 import AcceptedEvent, BrowserEvent, DeletionFence, EmptyPayload
from pymentor_analytics.contracts.windows_v1 import (
    ReconcileDecision, WindowKey, WindowSnapshot, WindowStat, build_ack, split_interval, window_bounds,
)
from pymentor_analytics.modules.analytics.api.activity_router import router
from pymentor_analytics.modules.collector.ports import IntakeForbidden
from tools.bootstrap_postgres import cleanup, config, connect, create, roles_for_revision

ROOT = Path(__file__).resolve().parents[2]
BASE_REVISION = "a014"
COURSE = "backend"
LESSON = "storage-baseline"
AUTHORITY = "corp-603-baseline"


@pytest.fixture
def student_windows_target():
    if os.environ.get("ANALYTICS_RUN_STUDENT_WINDOWS_SYNTHETIC") != "1":
        pytest.skip("explicit disposable student windows PostgreSQL opt-in required")
    config_path = Path(os.environ.get(
        "ANALYTICS_STUDENT_WINDOWS_PG_CONFIG", ROOT / ".local/postgres-connection.json"
    ))
    data = config(config_path)
    name, sentinel, urls = create(data, revision=BASE_REVISION)
    run_id = uuid4()
    try:
        # Record target ownership before the first step that could be interrupted.
        with connect(data, name) as connection:
            owner = connection.execute(
                "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()"
            ).fetchone()
            assert owner == (name + "_migrate",)
            assert connection.execute("SELECT token FROM public.analytics_test_sentinel").fetchall() == [(sentinel,)]
            version = connection.execute("SHOW server_version").fetchone()[0]
        sentinel_digest = sha256(sentinel.encode()).hexdigest()
        print(f"603-run={run_id} target={name} sentinel_sha256={sentinel_digest} postgres={version}", flush=True)
        upgrade(Settings(database_url=urls["migrate"]), target_revision=BASE_REVISION)
        with connect(data, name) as connection:
            assert connection.execute("SELECT version_num FROM public.alembic_version").fetchall() == [(BASE_REVISION,)]
        yield data, name, urls, run_id
    finally:
        cleanup(data, name, sentinel, revision=BASE_REVISION)
        with connect(data) as connection:
            assert connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone() is None
            roles = [f"{name}_{role}" for role in roles_for_revision(BASE_REVISION)]
            assert connection.execute("SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (roles,)).fetchone() is None
        print(f"603-run={run_id} target={name} cleaned=yes roles_removed={len(roles)}")


def v2_fact(subject, start):
    end = start + timedelta(seconds=10)
    event = BrowserEvent(
        schema_version=2, event_id=uuid4(), type="activity_segment",
        course_id=COURSE, catalog_version="v1", lesson_id=LESSON, section="theory",
        session_id=uuid4(), started_at=start, ended_at=end, last_action_at=start,
        visible=True, focused=True, active_ms=10_000, payload=EmptyPayload(),
    )
    return AcceptedEvent(
        producer_namespace="corp-603-browser", subject_ref=subject,
        source="browser", event=event, received_at=end,
    )


def deletion(subject):
    at = datetime.now(timezone.utc)
    control = DeletionFence(
        schema_version=2, kind="deletion_fence", control_id=uuid4(),
        subject_ref=subject, epoch=1, occurred_at=at,
    )
    return JournalEntry(
        seq=1, producer="core", control=control, received_at=at,
        payload_hash=deletion_hash("core", control),
    )


async def read_v2_report(reader, fact):
    # Exercise the existing HTTP router, Query role, fence and calculation with
    # local readiness and grant seams. This suite tests persisted data and purge.
    async def synthetic_ready(*_):
        return True

    reader.transaction_guard = synthetic_ready
    grant = VerifiedGrant(fact.subject_ref, "user", "report:read", (COURSE,), (), False, int(time.time()) + 60)
    app = FastAPI()
    app.include_router(router)
    app.state.trust_manifest_validated = True
    app.state.grant_verifier = SimpleNamespace(verify=lambda _: grant)
    app.state.probe = SimpleNamespace(is_ready=synthetic_ready)
    app.state.settings = SimpleNamespace(db_timeout_seconds=5)
    app.state.activity_reports = PostgresActivityReport(reader, PostgresSubjectFence(reader))
    event = fact.event
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/analytics/reports/activity",
            headers={"Authorization": "Bearer fixture"},
            params={
                "course_id": COURSE, "lesson_id": LESSON, "from_time": event.started_at.isoformat(),
                "to_time": event.ended_at.isoformat(),
            },
        )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body.pop("as_of") is not None
    return body


def fact_values(data, name, subject):
    with connect(data, name) as connection:
        return connection.execute(
            """SELECT source_kind, course_id, lesson_id, accepted_event #>> '{event,section}',
                      (accepted_event #>> '{event,active_ms}')::int
               FROM analytics.activity_facts WHERE subject_ref=%s""",
            (subject,),
        ).fetchall()


def test_a014_privacy_baseline(student_windows_target):
    data, name, urls, run_id = student_windows_target
    previous_day = datetime.now(timezone.utc) - timedelta(days=1)
    start = previous_day.replace(hour=7, minute=0, second=0, microsecond=0)
    old, control = (v2_fact(uuid4(), start) for _ in range(2))
    entry = deletion(old.subject_ref)
    millis = [("browser_v2", COURSE, LESSON, "theory", 10_000)]

    async def scenario():
        writer, reader, privacy = (Database(Settings(database_url=urls[role]))
                                   for role in ("collector", "query", "privacy"))
        try:
            intake = PostgresIntake(writer)
            for fact in (old, control):
                assert await intake.accept(fact, deadline=time.monotonic() + 4) == "accepted"
                assert fact_values(data, name, fact.subject_ref) == millis

            report = await read_v2_report(reader, old)
            totals = {"unique_visitors": 1, "active_ms": 10_000, "theory_ms": 10_000, "practice_ms": 0}
            assert report == {
                "schema_version": 1, "status": "ok", "quality": "unknown", "lag_seconds": None,
                "timezone": "Europe/Moscow", "metric_version": "activity-v1", "course_id": COURSE,
                "from_time": start.isoformat().replace("+00:00", "Z"),
                "to_time": old.event.ended_at.isoformat().replace("+00:00", "Z"),
                "totals": totals, "daily": [{"date": start.astimezone(ZoneInfo("Europe/Moscow")).date().isoformat(),
                                            **totals}],
                "trusted_progress": None,
            }

            store = PostgresPrivacyStore(privacy)
            assert (await store.cursor(AUTHORITY)).applied_through == 0
            first = await store.apply(AUTHORITY, entry)
            repeated = await store.apply(AUTHORITY, entry)
            assert first.complete and repeated.complete
            assert first.applied_through == repeated.applied_through == 1
            assert (await store.cursor(AUTHORITY)).applied_through == 1
            assert first.removed_facts == 1 and repeated.removed_facts == 0
            with pytest.raises(IntakeForbidden):
                await intake.accept(old, deadline=time.monotonic() + 4)
            return report
        finally:
            for database in (writer, reader, privacy):
                await database.close()

    report = asyncio.run(scenario())
    assert fact_values(data, name, old.subject_ref) == []
    assert fact_values(data, name, control.subject_ref) == millis
    with connect(data, name) as connection:
        assert connection.execute("SELECT version_num FROM public.alembic_version").fetchall() == [(BASE_REVISION,)]
        assert connection.execute("SELECT applied_through FROM analytics.privacy_state").fetchall() == [(1,)]
        assert connection.execute(
            "SELECT epoch, deleted, control_id FROM analytics.subject_fences WHERE subject_ref=%s",
            (old.subject_ref,),
        ).fetchall() == [(1, True, entry.control.control_id)]
        assert connection.execute(
            "SELECT seq, phase, control_id FROM analytics.privacy_receipts"
        ).fetchall() == [(1, "complete", entry.control.control_id)]
        assert connection.execute("SELECT to_regclass('analytics.student_activity_windows')").fetchone() == (None,)
    print(json.dumps({
        "run_id": str(run_id), "revision": BASE_REVISION, "report": report, "v2_fact_ms_before": 10_000,
        "privacy": {"cursor_before": 0, "cursor_after_repeat": 1, "fence_epoch": 1, "receipt_count": 1,
                    "deleted_facts": 0, "control_facts": 1, "late_replay": "forbidden"},
    }, sort_keys=True))


def upgrade_windows(target):
    _, _, urls, _ = target
    upgrade(Settings(database_url=urls["migrate"]), target_revision="a015")


def window_rows(data, name, subject):
    with connect(data, name) as connection:
        return connection.execute(
            """SELECT slot_no,stats,revision FROM analytics.student_activity_windows
               WHERE student_id=%s ORDER BY activity_date,slot_no""", (subject,),
        ).fetchall()


def snapshot_input(subject, now, *, seq=4, active_ms=480_000, client_final=True):
    start = now - timedelta(minutes=20)
    coordinate = split_interval(start, start + timedelta(milliseconds=1))[0][0]
    key = WindowKey(student_id=subject, **coordinate.model_dump())
    snapshot = WindowSnapshot(
        **coordinate.model_dump(), writer_id=uuid4(), writer_generation=1, server_epoch=1, seq=seq,
        stats=(WindowStat(tool_id="theory", course_id=COURSE, lesson_id=LESSON, active_ms=active_ms),),
        catalog_versions={COURSE: "v1"}, client_final=client_final,
    )
    return key, snapshot


async def live_fixture_guard(session):
    """Synthetic applied prefix seam used by the real role-separated PG transactions."""
    cursor = (await session.execute(text("""SELECT authority_id,applied_through
        FROM analytics.privacy_state WHERE singleton=true"""))).one_or_none()
    if cursor is None or cursor.authority_id != AUTHORITY:
        raise PrivacyUnavailable()


def test_a014_upgrade_compatible(student_windows_target):
    data, name, urls, run_id = student_windows_target
    at = (datetime.now(timezone.utc) - timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
    first = v2_fact(uuid4(), at)
    second = v2_fact(first.subject_ref, at + timedelta(minutes=15))

    async def query_with_baseline_client(seed):
        writer, reader = (Database(Settings(database_url=urls[role])) for role in ("collector", "query"))
        try:
            if seed is not None:
                assert await PostgresIntake(writer).accept(seed, deadline=time.monotonic() + 4) == "accepted"
            return await read_v2_report(reader, first)
        finally:
            await writer.close()
            await reader.close()

    before = asyncio.run(query_with_baseline_client(first))
    upgrade_windows(student_windows_target)
    upgrade_windows(student_windows_target)
    assert asyncio.run(query_with_baseline_client(second)) == before
    with connect(data, name) as conn:
        assert conn.execute("SELECT version_num FROM public.alembic_version").fetchall() == [("a015",)]
        expected = {"student_activity_windows", "student_activity_window_control"}
        actual = conn.execute("""SELECT tablename FROM pg_tables WHERE schemaname='analytics'
            AND tablename LIKE 'student_activity_window%'""").fetchall()
        assert {name for name, in actual} == expected and len(actual) == len(expected)
        assert conn.execute("SELECT count(*) FROM analytics.student_activity_window_control").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM analytics.student_activity_windows").fetchone() == (0,)
    assert fact_values(data, name, first.subject_ref) == [("browser_v2", COURSE, LESSON, "theory", 10_000)] * 2
    print(json.dumps({"run_id": str(run_id), "revision": "a015", "same_v2_report": before,
                      "v2_facts_ms": [10000, 10000], "empty_v3": True, "upgrade_runs": 2}, sort_keys=True))


def test_store_privacy_smoke(student_windows_target, monkeypatch):
    data, name, urls, run_id = student_windows_target
    upgrade_windows(student_windows_target)
    subject = uuid4()
    now = datetime.now(timezone.utc)
    key, snapshot = snapshot_input(subject, now)
    entry = deletion(subject)

    async def scenario():
        collector, privacy = (Database(Settings(database_url=urls[role])) for role in ("collector", "privacy"))
        try:
            writer = PostgresStudentWindows(collector, cohort={subject})
            with pytest.raises(PrivacyUnavailable):
                await writer.write_window(key, snapshot, received_at=now)
            collector.transaction_guard = live_fixture_guard
            with pytest.raises(PrivacyUnavailable):
                await writer.write_window(key, snapshot, received_at=now)
            store = PostgresPrivacyStore(privacy)
            assert (await store.cursor(AUTHORITY)).applied_through == 0
            async with privacy.transaction() as session:
                assert await has_student_windows_schema(session)
            first = await writer.write_window(key, snapshot, received_at=now)
            ack = build_ack(0, snapshot, first)
            assert ack.status == "committed" and ack.confirmed.revision > 0
            assert window_rows(data, name, subject) == [
                (key.slot_no, [item.model_dump() for item in snapshot.stats], ack.confirmed.revision)
            ]
            duplicate = build_ack(0, snapshot, await writer.write_window(key, snapshot, received_at=now))
            assert duplicate.status == "duplicate" and duplicate.confirmed == ack.confirmed

            with pytest.raises(DBAPIError, match="row-level security"):
                async with collector.transaction() as session:
                    await session.execute(text("""UPDATE analytics.student_activity_window_control
                        SET phase='repaired' WHERE student_id=:subject"""), {"subject": subject})
            with pytest.raises(DBAPIError, match="permission denied"):
                async with collector.transaction() as session:
                    await session.execute(text("""UPDATE analytics.student_activity_window_control
                        SET reconciliation_evidence='{}' WHERE student_id=:subject"""), {"subject": subject})
            with pytest.raises(DBAPIError, match="check constraint"):
                async with collector.transaction() as session:
                    await session.execute(text("""UPDATE analytics.student_activity_windows SET stats='[]'
                        WHERE student_id=:subject"""), {"subject": subject})

            async def a014_privacy_discovery(_):
                return False

            # Models the exact a014 path through apply: that binary has no
            # discovery or deletes for the added tables. The real trigger rejects its receipt.
            with monkeypatch.context() as isolated:
                isolated.setattr(privacy_adapter, "has_student_windows_schema", a014_privacy_discovery)
                with pytest.raises(DBAPIError, match="student window purge required"):
                    await store.apply(AUTHORITY, entry)
            assert (await store.cursor(AUTHORITY)).applied_through == 0
            assert lifecycle_counts(data, name, subject) == (1, 1, 0, 0, 0)

            assert (await store.apply(AUTHORITY, entry)).applied_through == 1
            assert (await store.apply(AUTHORITY, entry)).complete
            replay = await writer.write_window(key, snapshot, received_at=now)
            assert replay.status == "rejected" and replay.code == "privacy_forbidden"
            return ack, duplicate
        finally:
            await collector.close()
            await privacy.close()

    ack, duplicate = asyncio.run(scenario())
    assert window_rows(data, name, subject) == []
    with connect(data, name) as conn:
        assert conn.execute("SELECT count(*) FROM analytics.student_activity_window_control WHERE student_id=%s",
                            (subject,)).fetchone() == (0,)
        assert conn.execute("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=%s",
                            (subject,)).fetchall() == [(1, True)]
        assert conn.execute("SELECT applied_through FROM analytics.privacy_state").fetchall() == [(1,)]
    print(json.dumps({"run_id": str(run_id), "student_id": str(subject), "ack": ack.model_dump(mode="json"),
                      "duplicate": duplicate.status, "collector_repair": "denied", "old_purge_cursor": 0,
                      "purge_cursor": 1, "fence_epoch": 1, "rows_after_purge": [0, 0],
                      "late_replay": "privacy_forbidden"}, sort_keys=True))


def test_changed_v2_cohort_and_recheck_smoke(student_windows_target):
    data, name, urls, run_id = student_windows_target
    upgrade_windows(student_windows_target)
    subject, outsider = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    key, _ = snapshot_input(subject, now)
    at, _ = window_bounds(key)
    fact = v2_fact(subject, at + timedelta(seconds=11))

    async def scenario():
        collector, worker, privacy = (
            Database(Settings(database_url=urls[role])) for role in ("collector", "processor", "privacy")
        )
        try:
            assert (await PostgresPrivacyStore(privacy).cursor(AUTHORITY)).applied_through == 0
            collector.transaction_guard = worker.transaction_guard = live_fixture_guard
            marker = PostgresStudentWindows(collector, cohort={subject}, observation_start=at)
            reconciler = PostgresStudentWindows(worker, cohort={subject}, observation_start=at)
            intake = PostgresIntake(collector, windows_changed=marker)
            # Collection flags can be off while the previously enrolled cohort
            # retains source dirtiness from the ordinary v2 path.
            outside = v2_fact(outsider, at + timedelta(seconds=11))
            before_observation = v2_fact(subject, at - timedelta(minutes=3))
            for record in (outside, before_observation, fact):
                assert await intake.accept(record, deadline=time.monotonic() + 4) == "accepted"
            assert await intake.accept(fact, deadline=time.monotonic() + 4) == "duplicate"
            assert await reconciler.changed_windows(limit=10, now=now) == (key,)
            found = await reconciler.read_reconciliation(key)
            assert not found.v2_inflight and found.v2_facts == (fact,)
            assert (found.state.source_generation, found.state.change_generation) == (1, 1)
            assert await reconciler.reconcile_window(
                key, ReconcileDecision(1, 0, None, coverage_reasons=("v2_coverage_unknown",)), rebase_at=now,
            ) is None
            recheck = now + timedelta(minutes=1)
            delayed = await reconciler.reconcile_window(
                key, ReconcileDecision(1, 1, None, coverage_reasons=("v2_coverage_unknown",)),
                rebase_at=now, recheck_at=recheck,
            )
            assert delayed.state == "incomplete_coverage"
            assert await reconciler.changed_windows(limit=10, now=now) == ()
            assert await reconciler.changed_windows(limit=10, now=recheck) == (key,)
            assert await reconciler.changed_windows(limit=10, now=recheck, after=key) == ()

            stats = (WindowStat(tool_id="theory", course_id=COURSE, lesson_id=LESSON, active_ms=10_000),)
            repair = await reconciler.reconcile_window(
                key, ReconcileDecision(1, delayed.change_generation, stats, ((COURSE, "v1"),)),
                rebase_at=recheck, evidence={"expected_ms": 10_000},
            )
            assert repair.state == "repaired" and repair.confirmed.revision == 1
            assert await reconciler.changed_windows(limit=10, now=recheck) == ()
        finally:
            for database in (collector, worker, privacy):
                await database.close()

    asyncio.run(scenario())
    with connect(data, name) as conn:
        state = conn.execute("""SELECT student_id,phase,source_generation,reconciled_generation,
            recheck_at,reconciliation_evidence FROM analytics.student_activity_window_control""").fetchall()
        assert state == [(subject, "repaired", 1, 1, None, {"expected_ms": 10_000})]
        assert conn.execute("SELECT count(*) FROM analytics.activity_facts").fetchall() == [(3,)]
    assert window_rows(data, name, subject) == [
        (key.slot_no, [{"tool_id": "theory", "course_id": COURSE, "lesson_id": LESSON, "active_ms": 10_000}], 1)
    ]
    print(json.dumps({"run_id": str(run_id), "source_generation": 1, "old_and_out_of_cohort_changed": 0,
                      "lost_CAS": "rejected", "recheck": "one due", "v2_facts": 3, "repair_ms": 10_000},
                     sort_keys=True))


class LostReply(RuntimeError):
    """The PostgreSQL transaction returned before the caller lost its reply."""


class LockSchedule:
    def __init__(self):
        self.first_ready = asyncio.Event()
        self.release_first = asyncio.Event()
        self.second_entered = asyncio.Event()
        self.second_pid = None

    async def hold(self):
        self.first_ready.set()
        await asyncio.wait_for(self.release_first.wait(), timeout=4)

    async def announce(self, session):
        self.second_pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        self.second_entered.set()

    async def confirm_wait(self, data, name):
        await asyncio.wait_for(self.second_entered.wait(), timeout=2)
        with connect(data, name) as conn:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                waiting = conn.execute("""SELECT count(*) FROM pg_locks
                    WHERE pid=%s AND locktype='advisory' AND NOT granted""", (self.second_pid,)).fetchone()
                if waiting == (1,):
                    return
                await asyncio.sleep(0.02)
        raise AssertionError("second transaction never waited on the subject lock")


def with_amount(snapshot, seq, active_ms):
    stats = (snapshot.stats[0].model_copy(update={"active_ms": active_ms}),)
    return snapshot.model_copy(update={"seq": seq, "stats": stats, "client_final": False})


@pytest.mark.parametrize("seq_first", [2, 3], ids=["seq2-first", "seq3-first"])
def test_concurrent_writes_lost_ack(student_windows_target, seq_first):
    data, name, urls, run_id = student_windows_target
    upgrade_windows(student_windows_target)
    subject = uuid4()
    now = datetime.now(timezone.utc)
    key, initial = snapshot_input(subject, now, seq=1, active_ms=120_000, client_final=False)
    seq2 = with_amount(initial, 2, 240_000)
    seq3 = with_amount(initial, 3, 480_000)
    expected_revision = 3 if seq_first == 2 else 2

    async def scenario():
        db1, db2 = (Database(Settings(database_url=urls["collector"])) for _ in range(2))
        privacy = Database(Settings(database_url=urls["privacy"]))
        schedule = LockSchedule()

        class FirstWriter(PostgresStudentWindows):
            async def _save(self, session, key, previous, state):
                await super()._save(session, key, previous, state)
                await schedule.hold()

        class SecondWriter(PostgresStudentWindows):
            async def _exclusive(self, session, key):
                await schedule.announce(session)
                await super()._exclusive(session, key)

        async def deliver(writer, snapshot):
            if snapshot.seq != 3:
                return await writer.write_window(key, snapshot, received_at=now)
            with pytest.raises(LostReply):
                result = await writer.write_window(key, snapshot, received_at=now)
                assert result.status == "committed"
                raise LostReply("the seq 3 reply was lost after commit")

        try:
            assert (await PostgresPrivacyStore(privacy).cursor(AUTHORITY)).applied_through == 0
            db1.transaction_guard = db2.transaction_guard = live_fixture_guard
            seed = PostgresStudentWindows(db1, cohort={subject})
            assert (await seed.write_window(key, initial, received_at=now)).state.confirmed.revision == 1
            first = FirstWriter(db1, cohort={subject})
            second = SecondWriter(db2, cohort={subject})
            bodies = (seq2, seq3) if seq_first == 2 else (seq3, seq2)

            async with asyncio.TaskGroup() as group:
                one = group.create_task(deliver(first, bodies[0]))
                await asyncio.wait_for(schedule.first_ready.wait(), timeout=3)
                two = group.create_task(deliver(second, bodies[1]))
                await schedule.confirm_wait(data, name)
                assert window_rows(data, name, subject) == [
                    (key.slot_no, [row.model_dump() for row in initial.stats], 1)
                ]
                schedule.release_first.set()
            seq2_result = one.result() if seq_first == 2 else two.result()
            assert seq2_result.status == ("committed" if seq_first == 2 else "stale")
            assert window_rows(data, name, subject) == [
                (key.slot_no, [row.model_dump() for row in seq3.stats], expected_revision)
            ]
            replay = await seed.write_window(key, seq3, received_at=now)
            ack = build_ack(0, seq3, replay)
            assert ack.status == "duplicate" and ack.confirmed.revision == expected_revision
            return ack.confirmed.payload_hash
        finally:
            for db in (db1, db2, privacy):
                await db.close()

    digest = asyncio.run(scenario())
    with connect(data, name) as conn:
        assert conn.execute("""SELECT seq,payload_hash,revision FROM analytics.student_activity_window_control
            WHERE student_id=%s""", (subject,)).fetchall() == [(3, digest, expected_revision)]
    print(json.dumps({"run_id": str(run_id), "scenario": "603-Б", "first_seq": seq_first,
                      "database_lock_observed": True, "revision_before": 1, "revision_final": expected_revision,
                      "seq_final": 3, "active_ms_final": 480_000, "final_hash": digest,
                      "repeated_reply": "duplicate"}, sort_keys=True))


@pytest.mark.parametrize("operation", ["write", "repair"])
@pytest.mark.parametrize("purge_first", [False, True], ids=["mutation-first", "purge-first"])
def test_write_and_repair_race_with_purge(student_windows_target, operation, purge_first):
    data, name, urls, run_id = student_windows_target
    upgrade_windows(student_windows_target)
    subject, control = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    key, initial = snapshot_input(subject, now, seq=1, active_ms=120_000, client_final=False)
    another_key, another_snapshot = snapshot_input(subject, now - timedelta(minutes=15))
    control_key, control_snapshot = snapshot_input(control, now)
    next_snapshot = with_amount(initial, 2, 240_000)
    entry = deletion(subject)

    async def scenario():
        collector, processor, privacy = (
            Database(Settings(database_url=urls[role])) for role in ("collector", "processor", "privacy")
        )
        schedule = LockSchedule()

        class RacingStore(PostgresStudentWindows):
            async def _save(self, session, key, previous, state):
                await super()._save(session, key, previous, state)
                if not purge_first:
                    await schedule.hold()

            async def _exclusive(self, session, key):
                if purge_first:
                    await schedule.announce(session)
                await super()._exclusive(session, key)

        class RacingPrivacy(PostgresPrivacyStore):
            async def _locked_state(self, session, authority_id):
                if not purge_first:
                    await schedule.announce(session)
                return await super()._locked_state(session, authority_id)

            async def _before_commit(self):
                if purge_first:
                    await schedule.hold()

            async def _after_commit(self):
                raise LostReply("privacy page reply lost after fence commit")

        try:
            store = PostgresPrivacyStore(privacy)
            await store.cursor(AUTHORITY)
            collector.transaction_guard = processor.transaction_guard = live_fixture_guard
            writer = PostgresStudentWindows(collector, cohort={subject, control})
            reconciler = PostgresStudentWindows(processor, cohort={subject})
            racing = RacingStore(collector if operation == "write" else processor, cohort={subject})
            for target, body in ((key, initial), (another_key, another_snapshot), (control_key, control_snapshot)):
                assert (await writer.write_window(target, body, received_at=now)).status == "committed"
            observed = (await reconciler.read_reconciliation(key)).state
            decision = ReconcileDecision(
                observed.source_generation, observed.change_generation, next_snapshot.stats, ((COURSE, "v1"),),
            )

            async def mutate():
                if operation == "write":
                    result = await racing.write_window(key, next_snapshot, received_at=now)
                    assert result.code == ("privacy_forbidden" if purge_first else "committed")
                elif purge_first:
                    with pytest.raises(IntakeForbidden):
                        await racing.reconcile_window(key, decision, rebase_at=now)
                else:
                    result = await racing.reconcile_window(key, decision, rebase_at=now)
                    assert result.state == "repaired" and result.confirmed.revision == 2

            async def purge():
                with pytest.raises(LostReply):
                    await RacingPrivacy(privacy).apply(AUTHORITY, entry, limit=1)

            first, second = (purge, mutate) if purge_first else (mutate, purge)
            async with asyncio.TaskGroup() as group:
                group.create_task(first())
                await asyncio.wait_for(schedule.first_ready.wait(), timeout=3)
                group.create_task(second())
                await schedule.confirm_wait(data, name)
                assert lifecycle_counts(data, name, subject) == (2, 2, 0, 0, 0)
                schedule.release_first.set()

            with connect(data, name) as conn:
                assert conn.execute("SELECT applied_through FROM analytics.privacy_state").fetchall() == [(0,)]
                assert conn.execute("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=%s",
                                    (subject,)).fetchall() == [(1, True)]
                assert conn.execute("SELECT phase FROM analytics.privacy_receipts").fetchall() == [("purging",)]
            assert lifecycle_counts(data, name, subject) == (1, 1, 1, 1, 0)
            assert (await store.apply(AUTHORITY, entry, limit=1)).applied_through == 1
            assert (await store.apply(AUTHORITY, entry, limit=1)).complete
            assert (await writer.write_window(key, next_snapshot, received_at=now)).code == "privacy_forbidden"
            with pytest.raises(IntakeForbidden):
                await reconciler.reconcile_window(key, decision, rebase_at=now)
        finally:
            for db in (collector, processor, privacy):
                await db.close()

    asyncio.run(scenario())
    with connect(data, name) as conn:
        assert conn.execute("""SELECT student_id,count(*) FROM analytics.student_activity_window_control
            GROUP BY student_id""").fetchall() == [(control, 1)]
        assert conn.execute("SELECT applied_through FROM analytics.privacy_state").fetchall() == [(1,)]
        assert conn.execute("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=%s",
                            (subject,)).fetchall() == [(1, True)]
    assert not window_rows(data, name, subject)
    assert len(window_rows(data, name, control)) == 1
    print(json.dumps({"run_id": str(run_id), "scenario": "603-В", "operation": operation,
                      "first_to_commit": "purge" if purge_first else operation, "database_lock_observed": True,
                      "before": [2, 2], "after_first_purge_page": {"business_rows": 1, "control_rows": 1,
                                                 "cursor": 0, "fence": True},
                      "after": [0, 0], "cursor": 1, "outside_lifecycle_rows": [1, 1],
                      "late_write_and_repair": "denied"}, sort_keys=True))


def test_changed_key_commit_and_rollback(student_windows_target):
    data, name, urls, run_id = student_windows_target
    upgrade_windows(student_windows_target)
    subject = uuid4()
    start = datetime(2026, 11, 27, 23, 59, 50, tzinfo=ZoneInfo("Europe/Moscow"))
    end = start + timedelta(seconds=30)
    seed = v2_fact(subject, start)
    event = seed.event.model_copy(update={"ended_at": end, "active_ms": 30_000})
    fact = seed.model_copy(update={"event": event, "received_at": end + timedelta(days=6)})
    expected = [(datetime(2026, 11, 27).date(), 96, 1, 1), (datetime(2026, 11, 28).date(), 1, 1, 1)]

    def count_rows():
        with connect(data, name) as conn:
            marks = conn.execute("""SELECT activity_date,slot_no,source_generation,change_generation
                FROM analytics.student_activity_window_control ORDER BY activity_date,slot_no""").fetchall()
            return marks, fact_values(data, name, subject)

    class FailAfterMarker(PostgresIntake):
        async def _after_fact(self):
            raise RuntimeError("rollback after the changed-key hook")

    async def scenario():
        collector, privacy = (Database(Settings(database_url=urls[role])) for role in ("collector", "privacy"))
        try:
            await PostgresPrivacyStore(privacy).cursor(AUTHORITY)
            collector.transaction_guard = live_fixture_guard
            marker = PostgresStudentWindows(collector, cohort={subject})
            with pytest.raises(RuntimeError, match="rollback after the changed-key hook"):
                await FailAfterMarker(collector, windows_changed=marker).accept(fact, deadline=time.monotonic() + 4)
            assert count_rows() == ([], [])
            intake = PostgresIntake(collector, windows_changed=marker)
            assert await intake.accept(fact, deadline=time.monotonic() + 4) == "accepted"
            committed = count_rows()
            assert committed == (expected, [("browser_v2", COURSE, LESSON, "theory", 30_000)])
            assert await intake.accept(fact, deadline=time.monotonic() + 4) == "duplicate"
            assert count_rows() == committed
        finally:
            await collector.close()
            await privacy.close()

    asyncio.run(scenario())
    print(json.dumps({"run_id": str(run_id), "scenario": "603-Г", "rolled_back": [0, 0], "lateness_days": 6,
                      "commit_and_replay_generation": [[day.isoformat(), slot, source, change]
                                                        for day, slot, source, change in expected],
                      "committed_fact_ms": 30_000}, sort_keys=True))


def docker_pg_archive(tool, data, name, args, *, content=None):
    container = os.environ.get("ANALYTICS_SYNTHETIC_PG_CONTAINER_ID")
    if container is None or not re.fullmatch(r"[0-9a-f]{64}", container):
        raise ValueError("exact synthetic PostgreSQL container ID required")
    if not re.fullmatch(r"analytics_test_[0-9a-f]{32}", name):
        raise ValueError("expected a disposable PostgreSQL target")
    env = {**os.environ, "PGPASSWORD": data["password"]}
    return subprocess.run(
        ["docker", "exec", "-i", "--env", "PGPASSWORD", container, tool,
         "--host", "127.0.0.1", "--username", data["user"], "--dbname", name, *args],
        input=content, capture_output=True, env=env, check=True, timeout=45,
    ).stdout


def lifecycle_counts(data, name, subject):
    with connect(data, name) as conn:
        return conn.execute("""SELECT
            (SELECT count(*) FROM analytics.student_activity_windows WHERE student_id=%s),
            (SELECT count(*) FROM analytics.student_activity_window_control WHERE student_id=%s),
            (SELECT count(*) FROM analytics.privacy_receipts WHERE subject_ref=%s),
            (SELECT count(*) FROM analytics.subject_fences WHERE subject_ref=%s),
            (SELECT applied_through FROM analytics.privacy_state WHERE singleton=true)
        """, (subject,) * 4).fetchone()


@pytest.mark.skipif(os.environ.get("ANALYTICS_RUN_STUDENT_WINDOWS_ARCHIVE") != "1",
                    reason="explicit custom-format PostgreSQL archive opt-in required")
def test_restore_uses_current_deletion_journal(student_windows_target, tmp_path):
    data, source_name, source_urls, run_id = student_windows_target
    upgrade_windows(student_windows_target)
    target_name, target_sentinel, target_urls = create(data, revision="a015")
    subject, replacement = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    key, snapshot = snapshot_input(subject, now)
    replacement_key, new_snapshot = snapshot_input(replacement, now, seq=1, active_ms=30_000)
    journal_path = tmp_path / "independent-authority.sqlite"
    entry = deletion(subject)
    authority_key, issuer_key, command_key = (Ed25519PrivateKey.generate() for _ in range(3))
    secret = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")

    def public_pem(key):
        return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    try:
        upgrade(Settings(database_url=target_urls["migrate"]), target_revision="a015")
        with connect(data, target_name) as conn:
            assert conn.execute(
                "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()"
            ).fetchone() == (target_name + "_migrate",)
            assert conn.execute("SELECT token FROM public.analytics_test_sentinel").fetchall() == [(target_sentinel,)]
        assert lifecycle_counts(data, target_name, subject) == (0, 0, 0, 0, None)
        journal = Journal.initialize(journal_path, AUTHORITY)
        authority_app = create_authority_app(
            journal, issuer=AUTHORITY, kid="fixture", signing_key=authority_key,
            producer_issuer="core", producer_public_keys={"issuer": public_pem(issuer_key)},
            allowed_producers={"core"}, producer_command_public_keys={"core": public_pem(command_key)},
            reader_secret=secret,
        )
        authority_config = AuthorityConfig.from_json(json.dumps({
            "url": "http://127.0.0.1:12345", "authority_id": AUTHORITY, "issuer": AUTHORITY,
            "public_keys": {"fixture": public_pem(authority_key).decode("ascii")},
            "reader_secret": secret, "fixture_mode": True,
        }))

        async def scenario():
            source_writer, source_privacy, target_writer, target_privacy = (
                Database(Settings(database_url=url)) for url in (
                    source_urls["collector"], source_urls["privacy"], target_urls["collector"], target_urls["privacy"],
                )
            )
            try:
                async with authority_app.router.lifespan_context(authority_app):
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=authority_app),
                                                 base_url=authority_config.url, timeout=2) as http:
                        async def transport(path, body, *, credential=None):
                            reply = await http.post(path, content=body, headers={
                                "Authorization": f"Bearer {credential or secret}",
                                "Content-Type": "application/json",
                            })
                            assert reply.status_code == 200
                            return reply.content

                        def runtime(db, *, privacy=False):
                            client = AuthorityClient(authority_config)
                            client._http = transport
                            store = PostgresPrivacyStore(db) if privacy else None
                            return PrivacyRuntime(client, db, store=store)

                        source_gate, source_purge = runtime(source_writer), runtime(source_privacy, privacy=True)
                        target_gate, target_purge = runtime(target_writer), runtime(target_privacy, privacy=True)
                        source_writer.transaction_guard = source_gate.check_in_transaction
                        target_writer.transaction_guard = target_gate.check_in_transaction

                        await source_purge.tick()
                        await source_gate.tick()
                        assert await source_gate.is_ready()
                        writer = PostgresStudentWindows(source_writer, cohort={subject})
                        result = await writer.write_window(key, snapshot, received_at=now)
                        assert result.status == "committed" and result.state.confirmed.revision == 1
                        assert lifecycle_counts(data, source_name, subject) == (1, 1, 0, 0, 0)
                        sources = [
                            "analytics.student_activity_window_control", "analytics.student_activity_windows",
                            "analytics.privacy_receipts", "analytics.privacy_state", "analytics.subject_fences",
                        ]
                        options = ["--format=custom", "--data-only", "--no-owner", "--no-acl"]
                        options.extend(f"--table={table}" for table in sources)
                        archive = await asyncio.to_thread(docker_pg_archive, "pg_dump", data, source_name, options)
                        assert archive.startswith(b"PGDMP")

                        assert journal.append(entry.producer, entry.control) == ("accepted", 1)
                        reopened_journal = Journal(journal_path, AUTHORITY)
                        high, entries = reopened_journal.snapshot(after=0)
                        assert high == 1 and len(entries) == 1 and entries[0].control == entry.control
                        await source_purge.tick()
                        assert lifecycle_counts(data, source_name, subject) == (0, 0, 1, 1, 1)

                        await asyncio.to_thread(
                            docker_pg_archive, "pg_restore", data, target_name,
                            ["--exit-on-error", "--single-transaction", "--data-only", "--no-owner", "--no-acl"],
                            content=archive,
                        )
                        assert lifecycle_counts(data, target_name, subject) == (1, 1, 0, 0, 0)
                        assert window_rows(data, target_name, subject) == [
                            (key.slot_no, [row.model_dump() for row in snapshot.stats], 1)
                        ]
                        with connect(data, target_name) as conn:
                            assert conn.execute("SELECT token FROM public.analytics_test_sentinel").fetchall() == [(target_sentinel,)]
                            assert conn.execute("SELECT version_num FROM public.alembic_version").fetchall() == [("a015",)]
                            assert conn.execute("""SELECT seq,payload_hash FROM analytics.student_activity_window_control
                                WHERE student_id=%s""", (subject,)).fetchall() == [(4, result.state.confirmed.payload_hash)]

                        restored = PostgresStudentWindows(target_writer, cohort={subject, replacement})
                        assert not await target_gate.is_ready()
                        await target_gate.tick()
                        assert not await target_gate.is_ready()
                        with pytest.raises(PrivacyUnavailable):
                            await restored.write_window(key, snapshot, received_at=now)
                        assert lifecycle_counts(data, target_name, subject) == (1, 1, 0, 0, 0)
                        await target_purge.tick()
                        await target_gate.tick()
                        assert await target_gate.is_ready()
                        assert lifecycle_counts(data, target_name, subject) == (0, 0, 1, 1, 1)
                        assert (await restored.write_window(key, snapshot, received_at=now)).code == "privacy_forbidden"
                        new = await restored.write_window(replacement_key, new_snapshot, received_at=now)
                        assert new.status == "committed" and new.state.confirmed.revision == 1
                        assert window_rows(data, target_name, replacement) == [
                            (replacement_key.slot_no, [row.model_dump() for row in new_snapshot.stats], 1)
                        ]
                        with connect(data, target_name) as conn:
                            assert conn.execute("""SELECT subject_ref,epoch,deleted,control_id
                                FROM analytics.subject_fences""").fetchall() == [
                                (subject, 1, True, entry.control.control_id)
                            ]
                        return sha256(archive).hexdigest(), len(archive)
            finally:
                for db in (source_writer, source_privacy, target_writer, target_privacy):
                    await db.close()

        archive_sha, archive_bytes = asyncio.run(scenario())
        print(json.dumps({
            "run_id": str(run_id), "scenario": "603-Д", "source": source_name, "target": target_name,
            "archive_sha256": archive_sha, "archive_bytes": archive_bytes, "restored_business_control": [1, 1],
            "before_current_journal": "privacy_unavailable", "after_current_journal": [0, 0],
            "cursor": 1, "fence_epoch": 1, "replay": "privacy_forbidden", "replacement_active_ms": 30_000,
        }, sort_keys=True))
    finally:
        cleanup(data, target_name, target_sentinel, revision="a015")
        with connect(data) as conn:
            roles = [f"{target_name}_{name}" for name in roles_for_revision("a015")]
            assert conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (target_name,)).fetchone() is None
            assert conn.execute("SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (roles,)).fetchone() is None
        print(f"603-archive-target={target_name} cleaned=yes roles_removed={len(roles)}")
