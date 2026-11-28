"""Real PostgreSQL rehearsal of CORP-608 on its own loopback cluster."""
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import UUID, uuid4, uuid5

import psycopg
from psycopg import sql
import pytest
from pydantic import SecretStr

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.core_transfer import PostgresCoreTransfer
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
from pymentor_analytics.adapters.postgres.student_windows import PostgresStudentWindows
from pymentor_analytics.adapters.postgres.student_windows_backfill import (
    BackfillError, LiveProof, StudentWindowsBackfill, live_digest, post_t0_source_digest, source_window,
)
from pymentor_analytics.contracts.core_transfer_v1 import NAMESPACE, canonical, fingerprint
from pymentor_analytics.modules.analytics.activity.student_windows_reconcile import CoverageEvidence
from pymentor_analytics.modules.analytics.activity.student_windows_worker import WindowReconcileWorker
from pymentor_analytics.adapters.postgres.student_windows_backfill_verify import observe_core, verify
from pymentor_analytics.adapters.postgres.student_windows_backfill_coverage import CORE_COLUMNS, CoreTarget, CoverageWitness
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.v2 import AcceptedEvent, BrowserEvent, EmptyPayload, ToolPayload, canonical_hash
from pymentor_analytics.contracts.windows_v1 import WindowKey, WindowStat
from tools import bootstrap_postgres
from pymentor_analytics.app.backfill_windows_cli import apply

from .test_storage_pg import AUTHORITY, deletion, live_fixture_guard, snapshot_input

UTC = timezone.utc
DAY = datetime(2026, 11, 1, 21, tzinfo=UTC)
T0 = datetime(2026, 11, 4, 21, 0, 30, tzinfo=UTC)
SUBJECT = UUID("3b120001-1111-4333-8888-000000000001")
TOOL_SUBJECT = UUID("3b120001-1111-4333-8888-000000000002")
INSERT = """INSERT INTO analytics.activity_facts
    (producer_namespace,event_id,subject_ref,course_id,catalog_version,lesson_id,started_at,
     received_at,canonical_hash,accepted_event,source_kind)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING event_id"""


@pytest.fixture
def rehearsal(monkeypatch):
    control_path = os.environ.get("ANALYTICS_608_CLUSTER_CONTROL")
    if not control_path:
        pytest.skip("explicit CORP-608 disposable cluster control is required")
    control = json.loads(Path(control_path).read_text(encoding="utf-8"))
    assert control["port"] == 62409 and control["name"] == "corp608-m-rehearsal-" + control["run"]
    assert UUID(hex=control["run"]).hex == control["run"]
    inspect = subprocess.run(["docker", "inspect", control["id"]], capture_output=True, check=True, text=True, timeout=10)
    container, = json.loads(inspect.stdout)
    assert container["Name"] == "/" + control["name"]
    assert container["Config"]["Labels"]["pymentor.corp608"] == control["run"]
    ports = container["NetworkSettings"]["Ports"]["5432/tcp"]
    assert ports == [{"HostIp": "127.0.0.1", "HostPort": "62409"}]
    data = dict(password=control["password"], user="postgres", host="127.0.0.1", port=62409)

    def connect(target_data, dbname=None, *, benchmark=False):
        assert target_data is data and not benchmark
        assert dbname is None or bootstrap_postgres.PATTERN.fullmatch(dbname) or dbname.startswith("core_608_")
        return psycopg.connect(**data, dbname=dbname or "postgres", autocommit=True, connect_timeout=3)

    monkeypatch.setattr(bootstrap_postgres, "connect", connect)

    def create(revision):
        name, token, urls = bootstrap_postgres.create(data, revision=revision)
        databases.append((name, token))
        urls = {key: url.replace(":57392/", ":62409/") for key, url in urls.items()}
        upgrade(rehearsal_settings(urls["migrate"]), target_revision=revision)
        with connect(data, name) as connection:
            assert connection.execute("SELECT version_num FROM public.alembic_version").fetchall() == [(revision,)]
            assert connection.execute("SELECT token FROM public.analytics_test_sentinel").fetchall() == [(token,)]
        return name, token, urls

    databases = []
    core = "core_608_" + uuid4().hex
    with connect(data) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(core)))
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(
            sql.Identifier(core + "_reader"), sql.Literal(data["password"])))
    try:
        with connect(data, core) as connection:
            connection.execute("""CREATE TABLE user_activity_days (
                id int PRIMARY KEY,user_id int,activity_date date,visited_at timestamptz);
                CREATE TABLE user_lesson_progress (
                id int PRIMARY KEY,user_id int,course_id text,lesson_id text,completed_at timestamptz);
                CREATE TABLE user_task_progress (
                id int PRIMARY KEY,user_id int,course_id text,task_id text,task_revision text,completed_at timestamptz);
                CREATE TABLE account_lifecycles (user_id int PRIMARY KEY,lifecycle_id uuid NOT NULL);
                INSERT INTO account_lifecycles VALUES (101,'3b120001-1111-4333-8888-000000000002');
                INSERT INTO user_activity_days VALUES (1,102,'2026-11-01','2026-11-01T00:00:00Z');
                INSERT INTO user_lesson_progress VALUES (1,101,'archive','lesson-old','2026-11-01T21:00:00Z');
                INSERT INTO user_task_progress VALUES (1,101,'archive','task-old','rev7','2026-11-01T02:00:00Z')""")
            for table, columns in CORE_COLUMNS.items():
                connection.execute(sql.SQL("GRANT SELECT ({}) ON public.{} TO {}").format(
                    sql.SQL(",").join(map(sql.Identifier, columns)), sql.Identifier(table), sql.Identifier(core + "_reader")))
        yield control, data, connect, create, core
    finally:
        for name, token in reversed(databases):
            bootstrap_postgres.cleanup(data, name, token, revision="a016")
        with connect(data) as connection:
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(core)))
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(core + "_reader")))


def core_target(data, connect, core):
    with connect(data, core) as connection:
        address, port, owner = connection.execute("""SELECT host(inet_server_addr()),inet_server_port(),
            (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database())""").fetchone()
    return CoreTarget(host="127.0.0.1", port=62409, server_port=port, server_addresses=(address,),
                      database=core, owner=owner, reader=core + "_reader", standby=False)


def browser(subject, event_id, *, lesson, start, seconds, kind="activity_segment", namespace="corp608-browser", course="archive"):
    end = start + timedelta(seconds=seconds)
    event = BrowserEvent(
        schema_version=2, event_id=UUID(int=event_id), type=kind,
        course_id="platform-tools" if kind == "tool_segment" else course,
        catalog_version="platform-tools-v1" if kind == "tool_segment" else "catalog-v17", lesson_id=lesson, section="theory" if lesson else None,
        session_id=uuid4(), started_at=start, ended_at=end, last_action_at=start,
        visible=True, focused=True, active_ms=seconds * 1000,
        payload=ToolPayload(tool_id="account-statistics") if kind == "tool_segment" else EmptyPayload(),
    )
    return AcceptedEvent(producer_namespace=namespace, subject_ref=subject, source="browser", event=event, received_at=end)


def insert(connection, row):
    event = row.event
    return connection.execute(INSERT, (
        row.producer_namespace, event.event_id, row.subject_ref, event.course_id, event.catalog_version,
        event.lesson_id, event.started_at, row.received_at, canonical_hash(row),
        psycopg.types.json.Jsonb(row.model_dump(mode="json")),
        "browser_tool_v1" if event.type == "tool_segment" else "browser_v2",
    )).fetchone()


async def core_source(url, *, revision=1):
    database = db(url)
    try:
        body = dict(schema_version=1, kind="lesson_progress", revision=revision, subject_ref=str(TOOL_SUBJECT),
                    payload=dict(course_id="archive", lesson_id="lesson-old", completed_at=DAY.isoformat()))
        event = dict(event_id=str(uuid5(NAMESPACE, canonical(body))), **body)
        assert await PostgresCoreTransfer(database).accept(
            "corp608-core", event, fingerprint(event), deadline=time.monotonic() + 6) == "accepted"
        return event
    finally:
        await database.close()


def rehearsal_settings(url):
    # The dedicated fixture already verified its Docker ownership, bound loopback
    # port and database sentinel; this does not widen the application URL guard.
    assert "127.0.0.1:62409/analytics_test_" in url
    return Settings.model_construct(database_url=SecretStr(url), db_timeout_seconds=8)


def db(url, *, guarded=True):
    database = Database(rehearsal_settings(url))
    if guarded:
        database.transaction_guard = database.transaction_exit_guard = live_fixture_guard
    return database


def privacy_open(connection):
    connection.execute("INSERT INTO analytics.privacy_state(authority_id,applied_through) VALUES (%s,0)", (AUTHORITY,))


def business(connection):
    return connection.execute("""SELECT student_id,activity_date,slot_no,stats,revision
        FROM analytics.student_activity_windows ORDER BY student_id,activity_date,slot_no""").fetchall()


def canonical_rows(rows):
    return [[str(subject), str(day), slot, stats, rev] for subject, day, slot, stats, rev in rows]


def stat(tool, course, lesson, amount):
    return dict(tool_id=tool, course_id=course, lesson_id=lesson, active_ms=amount)


def crash_child(url, run_id, t0, key, *, before=False):
    body = """
import asyncio, json, os
from datetime import datetime
from uuid import UUID
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.student_windows_backfill import StudentWindowsBackfill
from pymentor_analytics.app.config import Settings
from sqlalchemy import text
from pydantic import SecretStr

async def main():
    database = Database(Settings.model_construct(database_url=SecretStr(os.environ["ANALYTICS_608_CHILD_URL"])))
    async def guard(session):
        assert (await session.execute(text("SELECT applied_through FROM analytics.privacy_state WHERE singleton"))).one() is not None
    database.transaction_guard = database.transaction_exit_guard = guard
    backfill = StudentWindowsBackfill(database, run_id=UUID(os.environ["ANALYTICS_608_CHILD_RUN"]),
                                     t0=datetime.fromisoformat(os.environ["ANALYTICS_608_CHILD_T0"]))
    raw = json.loads(os.environ["ANALYTICS_608_CHILD_KEY"])
    coords = {"student_id": UUID(raw["student_id"]), "activity_date": datetime.fromisoformat(raw["activity_date"]).date(),
              "slot_no": raw["slot_no"]}
    if os.environ["ANALYTICS_608_CHILD_BEFORE"] == "1":
        original = backfill._outcome
        async def abort(*args):
            await original(*args)
            os._exit(72)
        backfill._outcome = abort
    assert await backfill.apply_key(coords) == "historical"
    os._exit(73)

asyncio.run(main())
"""
    return subprocess.run([sys.executable, "-B", "-c", body], text=True, capture_output=True, timeout=20,
                          env={**os.environ, "ANALYTICS_608_CHILD_URL": url, "ANALYTICS_608_CHILD_RUN": str(run_id),
                                "ANALYTICS_608_CHILD_T0": t0.isoformat(), "ANALYTICS_608_CHILD_KEY": json.dumps(key, default=str),
                               "ANALYTICS_608_CHILD_BEFORE": "1" if before else "0"})


def test_snapshot_late_commit_race_and_exact_retry(rehearsal):
    control, data, connect, create, core = rehearsal
    name, token, urls = create("a015")
    a = browser(SUBJECT, 100, lesson="a", start=DAY - timedelta(seconds=20), seconds=40)
    smaller = browser(SUBJECT, 200, lesson="b-loser", start=DAY - timedelta(seconds=10), seconds=20, course="active")
    larger = browser(SUBJECT, 300, lesson="b-winner", start=DAY - timedelta(seconds=10), seconds=20, course="active")
    namespace_loser = browser(SUBJECT, 300, lesson="b-namespace-loser", start=DAY - timedelta(seconds=10),
                              seconds=20, namespace="corp608-a", course="active")
    edge = browser(SUBJECT, 400, lesson="edge", start=DAY - timedelta(days=1, minutes=1), seconds=60)
    tool = browser(TOOL_SUBJECT, 500, lesson=None, start=DAY - timedelta(days=2), seconds=60, kind="tool_segment")
    visit = browser(TOOL_SUBJECT, 600, lesson=None, start=DAY - timedelta(days=1), seconds=0, kind="course_opened")
    late = browser(SUBJECT, 10, lesson="c", start=DAY + timedelta(seconds=5), seconds=5)
    late = late.model_copy(update={"received_at": DAY + timedelta(seconds=10)})
    run_id = uuid4()
    with connect(data, name) as connection:
        privacy_open(connection)
        for row in (a, smaller, larger, namespace_loser, tool, visit):
            assert insert(connection, row)
        assert insert(connection, larger) is None
    asyncio.run(core_source(urls["collector"]))

    async def a015_probe():
        reader = db(urls["processor"])
        try:
            store = PostgresStudentWindows(reader, cohort={SUBJECT}, observation_start=T0)
            assert await store.changed_windows(limit=1, now=datetime.now(UTC)) == ()
        finally:
            await reader.close()

    asyncio.run(a015_probe())
    upgrade(rehearsal_settings(urls["migrate"]), target_revision="a016")
    with psycopg.connect(urls["collector"], autocommit=True) as connection:
        assert insert(connection, edge)
    core_uri = f"postgresql://{core}_reader:{data['password']}@127.0.0.1:62409/{core}"
    separate = observe_core(psycopg, core_uri, core_target(data, connect, core))

    async def scenario():
        processor = db(urls["processor"])
        backfill = StudentWindowsBackfill(processor, run_id=run_id, t0=T0)
        try:
            oldest = browser(TOOL_SUBJECT, 7, lesson=None, start=tool.event.started_at, seconds=60, kind="tool_segment")
            with psycopg.connect(urls["collector"], autocommit=True) as opened_before_snapshot:
                opened_before_snapshot.execute("BEGIN")
                assert insert(opened_before_snapshot, oldest)
                initial_plan = asyncio.create_task(backfill.plan())
                await asyncio.sleep(.15)
                snapshot_waited = not initial_plan.done()
                opened_before_snapshot.execute("COMMIT")
                checkpoint = await initial_plan
                assert snapshot_waited and checkpoint["snapshot_position"] == 2
            verify_before = await verify(backfill, core_sources=separate, require_witness=False)
            assert verify_before["status"] == "STOP" and verify_before["windows"]["classes"] == {"missing": 4}
            assert await backfill.scan(3) == 3
            scanned = await backfill.plan()
            assert (scanned["scan_namespace"], scanned["scan_event"]) > (late.producer_namespace, late.event.event_id)
            assert (await apply(backfill, size=2, limit=100))["checkpoint"]["scan_complete"]
            first = await verify(backfill, core_sources=separate, require_witness=False)
            assert first["status"] == "PASS", first["stop_reasons"]
            with connect(data, name) as connection:
                original = business(connection)
            assert [row[3] for row in original] == [
                [stat("theory", "archive", "edge", 60_000)],
                [stat("theory", "active", "b-winner", 10_000), stat("theory", "archive", "a", 10_000)],
                [stat("theory", "active", "b-winner", 10_000), stat("theory", "archive", "a", 10_000)],
                [stat("account-statistics", None, None, 60_000)],
            ]
            # An older Collector transaction started before the cut and has
            # allocated a number. A later transaction can commit out of order.
            with psycopg.connect(urls["collector"], autocommit=True) as held, (
                    psycopg.connect(urls["collector"], autocommit=True)) as concurrent:
                held.execute("BEGIN")
                assert insert(held, late)
                roll = browser(TOOL_SUBJECT, 12, lesson=None, start=tool.event.started_at, seconds=60, kind="tool_segment")
                concurrent.execute("SET statement_timeout='3s'")
                concurrent.execute("BEGIN")
                assert insert(concurrent, roll)
                concurrent.execute("ROLLBACK")
                follower = browser(TOOL_SUBJECT, 13, lesson=None, start=tool.event.started_at, seconds=60, kind="tool_segment")
                assert insert(concurrent, follower)
                barrier = asyncio.create_task(backfill.barrier())
                await asyncio.sleep(.15)
                blocked = not barrier.done()
                held.execute("COMMIT")
                accepted = await barrier
                assert blocked and accepted["accepted_position"] == checkpoint["snapshot_position"] + 3

            assert await backfill.catch_up(10) == 2
            pending = await backfill.candidates(10)
            coordinate = next(key for key in pending if key["slot_no"] == 1)
            before_commit = await asyncio.to_thread(crash_child, urls["processor"], run_id, T0, coordinate, before=True)
            assert before_commit.returncode == 72, before_commit.stderr
            with connect(data, name) as connection:
                assert business(connection) == original
            lost_reply = await asyncio.to_thread(crash_child, urls["processor"], run_id, T0, coordinate)
            assert lost_reply.returncode == 73, lost_reply.stderr
            assert lost_reply.stdout == ""
            confirmed = await backfill.plan()
            assert confirmed["applied_position"] == accepted["accepted_position"]
            with connect(data, name) as connection:
                after_child = business(connection)
            assert after_child[2][3] == [
                stat("theory", "active", "b-winner", 5_000), stat("theory", "archive", "a", 10_000),
                stat("theory", "archive", "c", 5_000)]
            assert after_child[2][4] == original[2][4] + 1
            await backfill.barrier(close=True)
            await apply(backfill, size=2, limit=100)
            latest = await verify(backfill, core_sources=separate, require_witness=False)
            assert latest["status"] == "PASS", latest["stop_reasons"]
            assert latest["checkpoint"]["final_position"] == latest["checkpoint"]["accepted_position"] == latest["checkpoint"]["applied_position"]
            await apply(backfill, size=1, limit=100)
            await core_source(urls["collector"], revision=2)
            await apply(backfill, size=2, limit=100)
            later_core = await verify(backfill, core_sources=separate, require_witness=False)
            assert later_core["status"] == "PASS"
            assert later_core["checkpoint"]["final_position"] == latest["checkpoint"]["final_position"]
            assert later_core["checkpoint"]["applied_position"] > later_core["checkpoint"]["final_position"]
            with connect(data, name) as connection:
                assert business(connection) == after_child
            core = latest["source_classes"]
            assert core["legacy_course_visit_zero_ms"]["count"] == 1
            with connect(data, name) as connection:
                with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                    insert(connection, browser(TOOL_SUBJECT, 14, lesson=None, start=DAY, seconds=0, kind="course_opened"))
            assert core["trusted_lesson_progress"]["count"] == 1
            assert latest["direct_core_sources"]["user_activity_days"]["count"] == 1
            assert latest["windows"]["actual"]["keys"]["sha256"] == latest["windows"]["expected"]["keys"]["sha256"]
            print(json.dumps(dict(scenario="snapshot-catchup-replay", status="PASS", postgres="16",
                                  observed=dict(first=first, before=verify_before, final=latest),
                                  from_revision="a015", a015_worker_bounded_scan="PASS", snapshot_waited=snapshot_waited, cutoff_waited=blocked, crashed_before_commit=before_commit.returncode, crashed_after_commit=lost_reply.returncode,
                                  before_rows=canonical_rows(original), after_rows=canonical_rows(after_child),
                                  sentinel_sha256=sha256(token.encode()).hexdigest()), default=str))
        finally:
            await processor.close()

    asyncio.run(scenario())


def test_cutover_live_worker_late_source_and_privacy(rehearsal):
    _, data, connect, create, core = rehearsal
    name, token, urls = create("a016")
    slot_start = T0.replace(second=0)
    key = WindowKey(student_id=SUBJECT, activity_date=(slot_start + timedelta(hours=3)).date(), slot_no=1)
    run_id = uuid4()
    pre = browser(SUBJECT, 100, lesson="pre", start=T0 - timedelta(seconds=20), seconds=20)
    post = browser(SUBJECT, 200, lesson="live", start=T0, seconds=10)
    pre_late = browser(SUBJECT, 10, lesson="late-pre", start=T0 - timedelta(seconds=5), seconds=5)
    post_later = browser(SUBJECT, 300, lesson="live", start=T0 + timedelta(seconds=10), seconds=5)
    before_purge = browser(TOOL_SUBJECT, 900, lesson="purge-first", start=DAY, seconds=10)
    with connect(data, name) as connection:
        privacy_open(connection)
    with psycopg.connect(urls["collector"], autocommit=True) as connection:
        for row in (pre, post, before_purge):
            assert insert(connection, row)
    core_uri = f"postgresql://{core}_reader:{data['password']}@127.0.0.1:62409/{core}"
    separate = observe_core(psycopg, core_uri, core_target(data, connect, core))

    async def scenario():
        processor = db(urls["processor"])
        collector = db(urls["collector"])
        privacy = db(urls["privacy"], guarded=False)
        purge = PostgresPrivacyStore(privacy)
        backfill = StudentWindowsBackfill(processor, run_id=run_id, t0=T0)
        writer = PostgresStudentWindows(collector, cohort={SUBJECT}, observation_start=T0)
        view = PostgresStudentWindows(processor, cohort={SUBJECT}, observation_start=T0)
        initial = snapshot_input(SUBJECT, T0 + timedelta(seconds=10), seq=1, active_ms=10_000, client_final=False)[1]
        snap = initial.model_copy(update={
            "activity_date": key.activity_date, "slot_no": key.slot_no,
            "stats": (WindowStat(**stat("theory", "archive", "live", 10_000)),),
            "catalog_versions": {"archive": "catalog-v17"},
        })
        now = datetime.now(UTC)
        try:
            first = await writer.write_window(key, snap, received_at=T0 + timedelta(seconds=10))
            assert first.status == "committed"
            await backfill.plan()
            while await backfill.scan(1):
                pass
            queued = next(row for row in await backfill.candidates(10) if row["student_id"] == TOOL_SUBJECT)
            gone = await purge.apply(AUTHORITY, deletion(TOOL_SUBJECT), limit=1)
            for _ in range(10):
                if gone.complete:
                    break
                raise AssertionError("single-fact purge did not settle in its tested schedule")
            assert await backfill.apply_key(queued) == "purged"
            coords = key.model_dump()
            async with processor.transaction() as session:
                old_proof = LiveProof(
                    (WindowStat(**stat("theory", "archive", "live", 10_000)),
                     WindowStat(**stat("theory", "archive", "pre", 20_000))),
                    live_digest(first.state.confirmed), first.state.source_generation, first.state.change_generation,
                    post_t0_source_digest(await source_window(session, key), key, T0),
                )
            stale = StudentWindowsBackfill(processor, run_id=run_id, t0=T0, proof={key: old_proof})
            snap2 = snap.model_copy(update={
                "seq": 2, "stats": (WindowStat(**stat("theory", "archive", "live", 15_000)),),
            })
            second = await writer.write_window(key, snap2, received_at=T0 + timedelta(seconds=15))
            assert second.state.confirmed.revision == 2
            assert await stale.apply_key(coords) == "live_overlap_unknown"
            with connect(data, name) as connection:
                assert business(connection)[0][3] == [stat("theory", "archive", "live", 15_000)]
            with psycopg.connect(urls["collector"], autocommit=True) as connection:
                assert insert(connection, post_later)
            await apply(backfill, size=10, limit=50)
            read = await view.read_reconciliation(key)
            async with processor.transaction() as session:
                proof = LiveProof(
                    (WindowStat(**stat("theory", "archive", "live", 15_000)),
                     WindowStat(**stat("theory", "archive", "pre", 20_000))),
                    live_digest(read.state.confirmed), read.state.source_generation, read.state.change_generation,
                    post_t0_source_digest(await source_window(session, key), key, T0),
                )
            trusted = StudentWindowsBackfill(processor, run_id=run_id, t0=T0, proof={key: proof})
            applied = await apply(trusted, size=10, limit=50)
            assert applied["work"]["covered_live"] == 1
            with connect(data, name) as connection:
                before = business(connection)
                assert before[0][3] == [stat("theory", "archive", "live", 15_000),
                                        stat("theory", "archive", "pre", 20_000)]
                assert connection.execute("SELECT history_backfill_owned FROM analytics.student_activity_window_control WHERE student_id=%s",
                                          (SUBJECT,)).fetchone() == (True,)
            with psycopg.connect(urls["collector"], autocommit=True) as connection:
                assert insert(connection, pre_late)
            async with collector.transaction() as session:
                await writer.mark_v2_changed(session, {key}, received_at=now)
            live_worker = WindowReconcileWorker(view, cohort_t0=T0)
            proof_for_live = CoverageEvidence(v2_source_complete=True)
            scan = await live_worker.run_once(repair=True, now=now, evidence={key: proof_for_live})
            selected_before_marker = await live_worker.run_key(key, now=now, evidence=proof_for_live, repair=True)
            assert not scan.windows and selected_before_marker.status != "repaired"
            with connect(data, name) as connection:
                assert business(connection) == before
            caught = await apply(backfill, size=10, limit=50)
            assert caught["work"]["covered_live"] == 1
            with connect(data, name) as connection:
                combined = business(connection)
            assert combined[0][3] == [stat("theory", "archive", "late-pre", 5_000),
                                      stat("theory", "archive", "live", 15_000),
                                      stat("theory", "archive", "pre", 15_000)]
            assert combined[0][4] == before[0][4] + 1
            final = await verify(backfill, core_sources=separate, require_witness=False)
            assert final["status"] == "PASS", final["stop_reasons"]
            old_write = await writer.write_window(key, snap2, received_at=now)
            assert old_write.code == "too_late"
            removed_entry = deletion(SUBJECT).model_copy(update={"seq": 2})
            for _ in range(15):
                deleted = await purge.apply(AUTHORITY, removed_entry, limit=1)
                if deleted.complete:
                    break
            else:
                raise AssertionError("bounded history purge did not finish")
            await apply(backfill, size=10, limit=50)
            with connect(data, name) as connection:
                assert business(connection) == []
                leftover = connection.execute("""SELECT
                    (SELECT count(*) FROM analytics.student_activity_window_control),
                    (SELECT count(*) FROM analytics.window_backfill_work),
                    (SELECT count(*) FROM analytics.window_backfill_scan_cursor),
                    (SELECT count(*) FROM analytics.window_backfill_journal WHERE subject_ref IS NOT NULL)
                    """).fetchone()
                assert leftover == (0, 0, 0, 0)
            print(json.dumps(dict(scenario="live-worker-boundary-privacy", status="PASS", after=final,
                                  first_purge="before_CAS", second_purge="after_commit", residue=leftover,
                                  stale_proof="live_overlap_unknown", worker_status=selected_before_marker.status,
                                  before=canonical_rows(before), combined=canonical_rows(combined),
                                  sentinel_sha256=sha256(token.encode()).hexdigest()), default=str))
        finally:
            await processor.close()
            await collector.close()
            await privacy.close()

    asyncio.run(scenario())


def test_archive_current_privacy_and_packaged_cli(rehearsal, tmp_path):
    import base64
    import socket
    from urllib.parse import unquote, urlsplit

    import uvicorn
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
    from pymentor_analytics.adapters.privacy_authority.journal import Journal
    from pymentor_analytics.adapters.privacy_authority.server import create_authority_app
    from pymentor_analytics.app.privacy import PrivacyRuntime
    from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable

    control, data, connect, create, core = rehearsal
    name, _, urls = create("a016")
    target, sentinel, restored = create("a016")
    run_id = uuid4()
    private, issuer, command = (Ed25519PrivateKey.generate() for _ in range(3))

    def pem(key):
        return key.public_key().public_bytes(serialization.Encoding.PEM,
                                             serialization.PublicFormat.SubjectPublicKeyInfo)

    secret = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
    journal = Journal.initialize(tmp_path / "independent-authority.sqlite", AUTHORITY)
    authority_app = create_authority_app(
        journal, issuer=AUTHORITY, kid="fixture", signing_key=private,
        producer_issuer="core", producer_public_keys={"issuer": pem(issuer)},
        allowed_producers={"core"}, producer_command_public_keys={"core": pem(command)},
        reader_secret=secret,
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(20)
    port = listener.getsockname()[1]
    authority = dict(url=f"http://127.0.0.1:{port}", authority_id=AUTHORITY, issuer=AUTHORITY,
                     public_keys={"fixture": pem(private).decode("ascii")}, reader_secret=secret, fixture_mode=True)
    authority_json = json.dumps(authority)
    server = uvicorn.Server(uvicorn.Config(authority_app, log_config=None, access_log=False))

    def pg(tool, database, url, args, content=None, *, restore=False):
        profile = urlsplit(url)
        command_line = ["docker", "exec", "-i", "-e", "PGPASSWORD"]
        opts = dict(PGPASSWORD=unquote(profile.password))
        if restore:
            command_line.extend(("-e", "PGOPTIONS"))
            opts["PGOPTIONS"] = "-c analytics.window_backfill_restore=on"
        result = subprocess.run(command_line + [
            control["id"], tool, "--host", "127.0.0.1", "--username", profile.username,
            "--dbname", database, *args], input=content, capture_output=True,
            env={**os.environ, **opts}, timeout=25)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        return result.stdout

    survivor_fact = browser(TOOL_SUBJECT, 200, lesson="survivor", start=DAY, seconds=20)

    async def scenario():
        service = asyncio.create_task(server.serve(sockets=[listener]))
        async with asyncio.timeout(8):
            while not server.started:
                await asyncio.sleep(.05)
        source, source_privacy, target_db, target_privacy = (
            db(url, guarded=False) for url in (urls["processor"], urls["privacy"], restored["processor"], restored["privacy"])
        )
        permit = False
        config = AuthorityConfig.from_json(authority_json)
        runtime = PrivacyRuntime(AuthorityClient(config), source)
        src_purge = PrivacyRuntime(AuthorityClient(config), source_privacy, store=PostgresPrivacyStore(source_privacy))
        gate = PrivacyRuntime(AuthorityClient(config), target_db, recovery_permit=lambda: permit)
        target_purge = PrivacyRuntime(AuthorityClient(config), target_privacy, store=PostgresPrivacyStore(target_privacy))
        source.transaction_guard = source.transaction_exit_guard = runtime.check_in_transaction
        target_db.transaction_guard = target_db.transaction_exit_guard = gate.check_in_transaction
        try:
            await src_purge.tick()
            await runtime.tick()
            assert await runtime.is_ready()
            with psycopg.connect(urls["collector"], autocommit=True) as connection:
                assert insert(connection, browser(SUBJECT, 100, lesson="purged", start=DAY, seconds=10))
                assert insert(connection, survivor_fact)
            backfill = StudentWindowsBackfill(source, run_id=run_id, t0=T0)
            await backfill.plan()
            await apply(backfill, size=10, limit=50)
            with connect(data, name) as connection:
                archived_rows = business(connection)
            assert len(archived_rows) == 2
            tables = ["activity_facts", "student_activity_window_control", "student_activity_windows",
                      "window_backfill_gate", "window_backfill_run", "window_backfill_journal",
                      "window_backfill_journal_position_seq", "window_backfill_work", "window_backfill_scan_cursor",
                      "privacy_state", "subject_fences", "privacy_receipts"]
            dump_options = ["--format=custom", "--data-only", "--no-owner", "--no-acl"]
            dump_options.extend(f"--table=analytics.{table}" for table in tables)
            archive = await asyncio.to_thread(pg, "pg_dump", name, urls["migrate"], dump_options)
            assert archive.startswith(b"PGDMP")
            erased = deletion(SUBJECT)
            assert journal.append(erased.producer, erased.control) == ("accepted", 1)
            await src_purge.tick()
            with psycopg.connect(restored["migrate"], autocommit=True) as connection:
                assert connection.execute("SELECT token FROM public.analytics_test_sentinel").fetchall() == [(sentinel,)]
                assert connection.execute("SELECT count(*) FROM analytics.window_backfill_gate").fetchone() == (1,)
                connection.execute("DELETE FROM analytics.window_backfill_gate WHERE singleton")
            await asyncio.to_thread(
                pg, "pg_restore", target, restored["migrate"],
                ["--exit-on-error", "--single-transaction", "--data-only", "--no-owner", "--no-acl"],
                archive, restore=True,
            )
            with connect(data, target) as connection:
                assert business(connection) == archived_rows
            await gate.tick()
            assert not await gate.is_ready()
            copy = StudentWindowsBackfill(target_db, run_id=run_id, t0=T0)
            with pytest.raises(PrivacyUnavailable):
                await copy.plan()
            await target_purge.tick()
            await gate.tick()
            assert not await gate.is_ready()
            with pytest.raises(PrivacyUnavailable):
                await copy.plan()
            permit = True
            await gate.tick()
            assert await gate.is_ready()
            await apply(copy, size=10, limit=50)
            with connect(data, target) as connection:
                survivors = business(connection)
                assert survivors == [row for row in archived_rows if row[0] != SUBJECT]
                counts = connection.execute("""SELECT
                    (SELECT count(*) FROM analytics.window_backfill_work WHERE student_id=%s),
                    (SELECT count(*) FROM analytics.window_backfill_journal WHERE subject_ref=%s),
                    (SELECT count(*) FROM analytics.student_activity_window_control WHERE student_id=%s),
                    (SELECT applied_through FROM analytics.privacy_state WHERE singleton)""", (SUBJECT,)*3).fetchone()
                assert counts == (0, 0, 0, 1)

            # Exercise the installed module through the same JSON CLI that ships
            # in the release image, with a live signed Authority on loopback.
            core_uri = f"postgresql://{core}_reader:{data['password']}@127.0.0.1:62409/{core}"
            execute = [sys.executable, "-B", "-m", "pymentor_analytics.app.backfill_windows_cli",
                       "--target", "local", "--database", target, "--sentinel-sha256", sha256(sentinel.encode()).hexdigest(),
                       "--run-id", str(run_id), "--t0", T0.isoformat(), "--mode", "verify"]
            target_core = core_target(data, connect, core)
            witness_file = tmp_path / "coverage-witness.json"
            witness_file.write_text(json.dumps(independent_witness(run_id, target_core, [survivor_fact], credits=independent_credits(data, connect, target))),
                                    encoding="utf-8")
            execute.extend(("--coverage", str(witness_file)))
            done = await asyncio.to_thread(subprocess.run, execute, timeout=25, capture_output=True, text=True,
                env={**os.environ, "ANALYTICS_DATABASE_URL": restored["processor"],
                     "ANALYTICS_BACKFILL_INSPECT_URL": restored["migrate"], "ANALYTICS_PRIVACY_AUTHORITY_JSON": authority_json,
                     "ANALYTICS_BACKFILL_CORE_READ_URL": core_uri})
            assert done.returncode == 0, done.stdout or done.stderr
            final = json.loads(done.stdout)
            permit = False
            with pytest.raises(PrivacyUnavailable):
                await copy.plan()
            print(json.dumps(dict(scenario="a016-archive-live-authority-packaged-cli", status="PASS", a016_revision=True,
                                  archive_sha256=sha256(archive).hexdigest(), rows_before=len(archived_rows),
                                  rows_restored=len(survivors), privacy_checked="signed_independent_authority",
                                  access_before_barrier="denied", access_before_permit="denied",
                                  access_after_revocation="denied", archive_existed=True, receipt=final,
                                  old_lifecycle_counts=counts, actual_core_cli_database_scope="test-read-only"), default=str))
        finally:
            for current in (runtime, src_purge, gate, target_purge):
                await current.stop()
            for database in (source, source_privacy, target_db, target_privacy):
                await database.close()
            server.should_exit = True
            await service

    asyncio.run(scenario())


def witness_measure(rows, *, mapped=None):
    digest = sha256()
    for row in rows:
        encoded = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        digest.update(sha256(encoded.encode()).digest())
    result = dict(count=len(rows), active_ms=0, sha256=digest.hexdigest())
    if mapped is not None:
        result.update(mapped=mapped, unmapped=len(rows) - mapped)
    return result


def independent_credits(data, connect, name):
    # An independent SQL connection sees the dirty/ready states created by
    # the existing v2 projection triggers. The witness must include them.
    fields = {
        "activity_credit_windows": "bucket_start",
        "activity_credit_days": "local_day",
    }
    with connect(data, name) as connection:
        return {table: witness_measure([row[0] for row in connection.execute(f"""SELECT row_to_json(prior)
            FROM (SELECT subject_ref,{key},metric_version,credit_version,generation,state,subject_epoch,run_id,credits
                  FROM analytics.{table}) prior ORDER BY subject_ref,{key},metric_version,credit_version""")])
            for table, key in fields.items()}


def independent_witness(run_id, target, events, transfers=(), *, credits):
    from datetime import date

    # These are the values seeded by the fixture, in the independent witness
    # protocol order. No call to the transfer's readers, SQL or oracle is made.
    core = {
        "user_activity_days": witness_measure([(102, date(2026, 11, 1), datetime(2026, 11, 1, 0, tzinfo=UTC), None)], mapped=0),
        "user_lesson_progress": witness_measure([(101, "archive", "lesson-old", DAY, TOOL_SUBJECT)], mapped=1),
        "user_task_progress": witness_measure([(101, "archive", "task-old", "rev7",
                                                datetime(2026, 11, 1, 2, tzinfo=UTC), TOOL_SUBJECT)], mapped=1),
    }
    measured = [(item.subject_ref, item.producer_namespace, item.event.event_id, "browser_v2", item.event.type,
                 False, (item.event.started_at + timedelta(hours=3)).date(), canonical_hash(item))
                for item in sorted(events, key=lambda item: (item.producer_namespace, item.event.event_id))]
    analytics = {**credits, "measured": witness_measure(measured)}
    if transfers:
        rows = [(UUID(item["subject_ref"]), "corp608-core", UUID(item["event_id"]), "core_transfer_v1",
                 "lesson_progress", True,
                 (datetime.fromisoformat(item["payload"]["completed_at"]) + timedelta(hours=3)).date(),
                 fingerprint(item)) for item in sorted(transfers, key=lambda event: event["event_id"])]
        analytics["trusted_lesson_progress"] = witness_measure(rows)
    return dict(version=1, run_id=str(run_id), t0=T0.isoformat(), evidence_sha256="1" * 64,
                core_target=target.model_dump(mode="json"), core_sources=core, analytics_sources=analytics)


def test_independent_core_scope_witness_and_writer_corruption(rehearsal, monkeypatch):
    from functools import partial
    from pymentor_analytics.adapters.postgres import student_windows_backfill as publisher

    _, data, connect, create, core = rehearsal
    name, _, urls = create("a016")
    scoped = core_target(data, connect, core)
    core_uri = f"postgresql://{core}_reader:{data['password']}@127.0.0.1:62409/{core}"
    run_id = uuid4()
    old_event = browser(SUBJECT, 33, lesson="check", start=DAY, seconds=10)
    late_event = browser(SUBJECT, 34, lesson="check", start=DAY + timedelta(seconds=10), seconds=5)
    with connect(data, name) as connection:
        privacy_open(connection)
    with psycopg.connect(urls["collector"], autocommit=True) as connection:
        assert insert(connection, old_event)
    transfer = asyncio.run(core_source(urls["collector"]))
    fixed = partial(observe_core, psycopg, core_uri, scoped)

    async def scenario():
        processor = db(urls["processor"])
        backfill = StudentWindowsBackfill(processor, run_id=run_id, t0=T0)
        try:
            await backfill.plan()
            normal = publisher._normalization

            def broken(facts, key):
                amounts, catalogs = normal(facts, key)
                return tuple(item.model_copy(update={"active_ms": item.active_ms - 1}) for item in amounts), catalogs

            monkeypatch.setattr(publisher, "_normalization", broken)
            await apply(backfill, size=10, limit=100)
            declared = independent_witness(run_id, scoped, [old_event], [transfer], credits=independent_credits(data, connect, name))
            witness = CoverageWitness.model_validate_json(json.dumps(declared))
            red = await verify(backfill, core_reader=fixed, witness=witness)
            assert red["stop_reasons"] == ["window_union_mismatch"], json.dumps(red["coverage"])
            assert red["windows"]["classes"] == {"different": 1}
            assert red["windows"]["actual"]["keys"]["active_ms"] == 9_999
            assert red["windows"]["expected"]["keys"]["active_ms"] == 10_000
            unobserved = await verify(backfill, core_sources={})
            assert "direct_core_sources_unobserved_or_unverified" in unobserved["stop_reasons"]
            assert "independent_coverage_witness_missing" in unobserved["stop_reasons"]
            monkeypatch.setattr(publisher, "_normalization", normal)
            with psycopg.connect(urls["collector"], autocommit=True) as connection:
                assert insert(connection, late_event)
            await apply(backfill, size=10, limit=100)
            right = independent_witness(run_id, scoped, [old_event, late_event], [transfer], credits=independent_credits(data, connect, name))
            expected = CoverageWitness.model_validate_json(json.dumps(right))
            green = await verify(backfill, core_reader=fixed, witness=expected)
            assert green["status"] == "PASS", green["stop_reasons"]
            assert green["windows"]["actual"]["keys"]["active_ms"] == 15_000
            assert green["direct_core_sources"]["transfer_comparison"] == {
                "direct_unmapped": 1, "matched": 1, "direct_only": 1, "analytics_only": 0,
            }
            with connect(data, core) as connection:
                connection.execute("UPDATE public.user_lesson_progress SET completed_at=completed_at+interval '1 second'")
            disappeared = await verify(backfill, core_reader=fixed, witness=expected)
            assert disappeared["status"] == "STOP"
            assert "confirmed_core_sources_disagree" in disappeared["stop_reasons"]
            assert disappeared["coverage"]["differences"]["core"] == ["user_lesson_progress"]
            with pytest.raises(BackfillError, match="actual Core database"):
                observe_core(psycopg, core_uri, scoped.model_copy(update={"server_addresses": ("127.0.0.2",)}))
            wrong = core_uri.replace("/" + core, "/postgres")
            with pytest.raises(BackfillError, match="independently pinned"):
                observe_core(psycopg, wrong, scoped)
            with connect(data, core) as connection:
                connection.execute(sql.SQL("GRANT SELECT ON public.user_activity_days TO {}").format(
                    sql.Identifier(scoped.reader)))
            with pytest.raises(BackfillError, match="scoped read-only"):
                observe_core(psycopg, core_uri, scoped)
            print(json.dumps(dict(scenario="strict-source-and-independent-oracle", status="PASS",
                                  wrong_endpoint_and_scope="STOP", broad_grant="STOP", forged_core_observation="STOP",
                                  publisher_divergence=red["windows"], successful_comparison=green["direct_core_sources"],
                                  stale_values=disappeared["coverage"], final=green), default=str))
        finally:
            await processor.close()

    asyncio.run(scenario())
