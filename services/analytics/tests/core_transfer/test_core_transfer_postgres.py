"""Explicit opt-in only: generated, sentinel-guarded synthetic DB, never retained data."""
import asyncio
import importlib.util
import json
import os
import secrets
import time
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4, uuid5

import pytest
from psycopg import sql
from sqlalchemy import create_engine

from pymentor_analytics.adapters.postgres.core_transfer import PostgresCoreTransfer
from pymentor_analytics.adapters.postgres.core_snapshot_proof import PostgresSnapshotProof
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.intake import PostgresIntake
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
from pymentor_analytics.adapters.postgres.processor import PostgresProcessor, PostgresSyntheticCounts
from pymentor_analytics.adapters.postgres.quality import PostgresReconciler
from pymentor_analytics.adapters.postgres.replay import PostgresReplay
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.processing_v1 import synthetic_delta
from pymentor_analytics.contracts.core_snapshot_proof_v1 import NAMESPACE, DOMAIN, active_line, proof_fingerprint
from pymentor_analytics.contracts.core_transfer_v1 import canonical, entity_key
from pymentor_analytics.contracts.privacy_control_v1 import JournalEntry, deletion_hash
from pymentor_analytics.contracts.v2 import AcceptedEvent, DeletionFence, parse_browser_item
from pymentor_analytics.modules.collector.ports import IntakeForbidden, IntakeUnavailable
from tools.bootstrap_postgres import cleanup, config, connect, create

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("core_transfer_for_pg_test", ROOT.parent / "core-api/pymentor_core/modules/analytics_delivery/contracts.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


@pytest.fixture
def target():
    if os.environ.get("ANALYTICS_RUN_CORE_TRANSFER_SYNTHETIC") != "1":
        pytest.skip("explicit Core transfer synthetic DB opt-in required")
    data = config(ROOT / ".local/postgres-connection.json")
    name, token, urls = create(data)
    status_role = name + "_transfer_status"
    status_created = False
    try:
        password = secrets.token_urlsafe(32)
        with connect(data) as connection:
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS")
                               .format(sql.Identifier(status_role), sql.Literal(password)))
            status_created = True
            connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(name), sql.Identifier(status_role)))
        urls["transfer_status"] = f"postgresql://{status_role}:{quote(password, safe='')}@127.0.0.1:57392/{name}"
        upgrade(Settings(database_url=urls["migrate"]), target_revision="a007")
        yield data, name, urls
    finally:
        cleanup(data, name, token)
        if status_created:
            with connect(data) as connection:
                connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(status_role)))


@pytest.fixture
def proof_target(target):
    _, _, urls = target
    upgrade(Settings(database_url=urls["migrate"]), target_revision="a009")
    return target


def test_atomic_dedup_revision_and_fence(target):
    data, name, urls = target
    subject = uuid4()
    zero = core.activity(subject, date(2026, 11, 16), None, revision=0)
    live = core.activity(subject, date(2026, 11, 16), datetime(2026, 11, 16, tzinfo=timezone.utc), revision=2)
    conflicting = core.activity(subject, date(2026, 11, 16), None, revision=2)
    async def run():
        databases = {role: Database(Settings(database_url=urls[role], pool_size=2, pool_overflow=0, db_timeout_seconds=3))
                     for role in ("collector", "processor", "query")}
        store = PostgresCoreTransfer(databases["collector"])
        processor = PostgresCoreTransfer(databases["processor"])
        reader = PostgresCoreTransfer(databases["query"])
        deadline = lambda: time.monotonic() + 3
        try:
            assert await asyncio.gather(*(store.accept("core", live, core.fingerprint(live), deadline=deadline()) for _ in range(2))) == ["accepted", "accepted"]
            assert await store.accept("core", conflicting, core.fingerprint(conflicting), deadline=deadline()) == "conflict"
            assert await store.accept("core", zero, core.fingerprint(zero), deadline=deadline()) == "accepted"
            assert await processor.project_one(deadline=deadline())
            assert await processor.project_one(deadline=deadline())
            visits, progress = await reader.read_subject("core", subject, date(2026, 11, 17), deadline=deadline())
            assert visits == [{"activity_date": "2026-11-16", "visited_at": "2026-11-16T00:00:00+00:00"}]
            assert progress == []
            assert await reader.read_admin("core", [subject], date(2026, 11, 17), deadline=deadline()) == [
                {"subject_ref": str(subject), "activity_date": "2026-11-16"}]
            with connect(data, name) as conn:
                assert conn.execute("SELECT revision,event_id FROM analytics.core_transfer_projection WHERE subject_ref=%s", (subject,)).fetchone() == (2, UUID(live["event_id"]))
                assert conn.execute("SELECT count(*) FROM analytics.processing_receipts WHERE subject_ref=%s", (subject,)).fetchone()[0] == 2
                conn.execute("INSERT INTO analytics.subject_fences(subject_ref,epoch,deleted,control_id) VALUES(%s,1,true,%s)", (subject, uuid4()))
            with pytest.raises(IntakeForbidden):
                await store.accept("core", zero, core.fingerprint(zero), deadline=deadline())
        finally:
            for db in databases.values(): await db.close()
    asyncio.run(run())


def test_transaction_rollback_and_post_commit_reconciliation(target):
    data, name, urls = target
    subject = uuid4()
    event = core.activity(subject, date(2026, 11, 16), None, revision=1)

    class FailInside(PostgresCoreTransfer):
        async def _after_fact(self): raise RuntimeError("synthetic rollback")

    class LostReply(PostgresCoreTransfer):
        async def _after_commit(self): raise TimeoutError()

    async def run():
        db = Database(Settings(database_url=urls["collector"], db_timeout_seconds=3))
        processor = Database(Settings(database_url=urls["processor"], db_timeout_seconds=3))
        deadline = lambda: time.monotonic() + 3
        try:
            with pytest.raises(RuntimeError):
                await FailInside(db).accept("core", event, core.fingerprint(event), deadline=deadline())
            with connect(data, name) as conn:
                assert conn.execute("SELECT count(*) FROM analytics.activity_facts WHERE subject_ref=%s", (subject,)).fetchone()[0] == 0
            with pytest.raises(TimeoutError):
                await LostReply(db).accept("core", event, core.fingerprint(event), deadline=deadline())
            assert await PostgresCoreTransfer(db).receipt("core", UUID(event["event_id"]), core.fingerprint(event), deadline=deadline()) == "accepted"
            assert await PostgresCoreTransfer(processor).project_one(deadline=deadline())
            with connect(data, name) as conn:
                conn.execute("DELETE FROM analytics.processing_jobs WHERE subject_ref=%s AND status='completed'", (subject,))
            assert await PostgresCoreTransfer(db).receipt("core", UUID(event["event_id"]), core.fingerprint(event), deadline=deadline()) == "accepted"
            assert await PostgresCoreTransfer(db).accept("core", event, core.fingerprint(event), deadline=deadline()) == "accepted"
            with connect(data, name) as conn:
                conn.execute("DELETE FROM analytics.activity_facts WHERE subject_ref=%s", (subject,))
                for table in ("core_transfer_entities", "core_transfer_projection", "processing_jobs", "processing_receipts"):
                    assert conn.execute("SELECT count(*) FROM analytics." + table + " WHERE subject_ref=%s", (subject,)).fetchone()[0] == 0
        finally:
            await db.close()
            await processor.close()
    asyncio.run(run())


def test_runtime_roles_are_scoped(target):
    data, name, urls = target
    with connect(data, name) as connection:
        status = name + "_transfer_status"
        actual = connection.execute("""SELECT
            has_column_privilege(%s,'analytics.activity_facts','subject_ref','SELECT'),
            has_column_privilege(%s,'analytics.activity_facts','accepted_event','SELECT'),
            has_table_privilege(%s,'analytics.activity_facts','INSERT'),
            has_table_privilege(%s,'analytics.core_transfer_projection','DELETE')""", (status, status, status, status)).fetchone()
        assert actual == (True, False, False, False)


def test_mixed_source_replay_and_quality_do_not_parse_core_as_v2(target):
    _, _, urls = target
    subject = uuid4()
    async def run():
        collector, processor = (Database(Settings(database_url=urls[role], db_timeout_seconds=3)) for role in ("collector", "processor"))
        try:
            raw = json.loads((ROOT / "contracts/fixtures/browser-batch.json").read_text(encoding="utf-8"))["events"][0]
            now = datetime.now(timezone.utc)
            raw.update(event_id=str(uuid4()), started_at=now.isoformat(), ended_at=now.isoformat(), last_action_at=now.isoformat())
            browser = AcceptedEvent(producer_namespace="browser-test", subject_ref=subject, source="browser",
                                    event=parse_browser_item(json.dumps(raw).encode()), received_at=now)
            transfer = core.activity(subject, date(2026, 11, 16), None, revision=1)
            assert await PostgresIntake(collector).accept(browser, deadline=time.monotonic() + 3) == "accepted"
            assert await PostgresCoreTransfer(collector).accept("core", transfer, core.fingerprint(transfer), deadline=time.monotonic() + 3) == "accepted"
            worker = PostgresProcessor(processor)
            batch = await worker.claim("synthetic.activity", 1, uuid4(), 20, 30)
            assert batch and len(batch.facts) == 1 and batch.facts[0].record.event.event_id == browser.event.event_id
            await worker.commit(batch, synthetic_delta(batch.facts))
            assert (await PostgresReconciler(processor).check("synthetic.activity", 1, subject)).status == "ok"
            replay = PostgresReplay(processor)
            state = await replay.begin("synthetic.activity", 1, subject, uuid4())
            for _ in range(20):
                if state.phase == "drain": break
                state = await replay.step(state.handle)
            assert state.phase == "drain"
            batch = await worker.claim("synthetic.activity", 1, uuid4(), 20, 30)
            assert batch and len(batch.facts) == 1
            await worker.commit(batch, synthetic_delta(batch.facts))
            assert (await replay.finish(state.handle)).phase == "complete"
            assert (await PostgresReconciler(processor).check("synthetic.activity", 1, subject)).status == "ok"
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_delete_race_and_checked_authority_status(target):
    from pymentor_analytics.adapters.postgres.core_transfer import CheckedDeletionStatus
    spec = importlib.util.spec_from_file_location("core_deletion_status_helpers", ROOT / "tests/core_transfer/test_deletion_status.py")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    _, _, urls = target
    subject = uuid4()
    now = datetime.now(timezone.utc)
    control = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(), subject_ref=subject, epoch=1, occurred_at=now)
    entry = JournalEntry(seq=1, producer="core", control=control, received_at=now, payload_hash=deletion_hash("core", control))
    async def run():
        collector, privacy, statusdb = (Database(Settings(database_url=urls[role], db_timeout_seconds=3))
                                        for role in ("collector", "privacy", "transfer_status"))
        try:
            transfer = core.activity(subject, date(2026, 11, 16), None, revision=1)
            writer = PostgresCoreTransfer(collector)
            checker = CheckedDeletionStatus(statusdb, helpers.signed_client(entry), producer_by_namespace={"core": "core"})
            assert await checker.status("core", control.control_id, subject, 1, deadline=time.monotonic() + 3) == "pending"
            results = await asyncio.gather(writer.accept("core", transfer, core.fingerprint(transfer), deadline=time.monotonic() + 3),
                PostgresPrivacyStore(privacy).apply("test-authority", entry), return_exceptions=True)
            assert results[0] == "accepted" or isinstance(results[0], IntakeForbidden)
            assert results[1].complete
            assert await checker.status("core", control.control_id, subject, 1, deadline=time.monotonic() + 3) == "accepted"
            with pytest.raises(IntakeForbidden):
                await writer.accept("core", transfer, core.fingerprint(transfer), deadline=time.monotonic() + 3)
        finally:
            await collector.close(); await privacy.close(); await statusdb.close()
    asyncio.run(run())


def test_admin_and_synthetic_lock_order_and_deleted_roster(target):
    data, name, urls = target
    actor = UUID("00000000-0000-0000-0000-000000000001")
    subject = UUID("00000000-0000-0000-0000-000000000004")

    async def run():
        query = Database(Settings(database_url=urls["query"], pool_size=2, pool_overflow=0, db_timeout_seconds=3))
        try:
            reader, counts = PostgresCoreTransfer(query), PostgresSyntheticCounts(query)
            results = await asyncio.wait_for(asyncio.gather(
                reader.read_admin("core", [subject], date(2026, 11, 17), actor=actor, deadline=time.monotonic() + 3),
                counts.read_counts("synthetic.activity", 1, actor, subject, ("backend",))), timeout=3)
            assert results == [[], None]
            with connect(data, name) as conn:
                conn.execute("INSERT INTO analytics.subject_fences(subject_ref,epoch,deleted,control_id) VALUES(%s,1,true,%s)", (subject, uuid4()))
            with pytest.raises(IntakeUnavailable):
                await reader.read_admin("core", [subject], date(2026, 11, 17), actor=actor, deadline=time.monotonic() + 3)
        finally:
            await query.close()
    asyncio.run(run())


def test_snapshot_proof_receipts_late_fact_invalidation_and_newer_projection(proof_target):
    data, name, urls = proof_target
    with connect(data, name) as conn:
        collector = name + "_collector"
        privileges = conn.execute("""SELECT
            has_column_privilege(%s,'analytics.core_snapshot_proofs','status','UPDATE'),
            has_column_privilege(%s,'analytics.core_snapshot_proofs','status','INSERT'),
            has_column_privilege(%s,'analytics.core_snapshot_proofs','proof_id','INSERT'),
            has_function_privilege(%s,'analytics.invalidate_core_snapshot_proofs(text,uuid,bigint)','EXECUTE')""",
            (collector, collector, collector, collector)).fetchone()
        assert privileges == (False, False, True, False)
    subject = uuid4()
    first = core.activity(subject, date(2026, 11, 16), None, revision=0)
    second = core.progress(subject, "backend", "lesson", datetime(2026, 11, 16, tzinfo=timezone.utc), revision=1)
    digest = sha256(DOMAIN)
    for event in sorted((first, second), key=entity_key): digest.update(active_line(event))
    body = {"schema_version": 1, "subject_ref": str(subject), "scope": "subject_all_v1", "cut_revision": 1,
            "entity_count": 2, "entities_digest": digest.hexdigest(), "created_at": datetime.now(timezone.utc).isoformat()}
    proof = {"proof_id": str(uuid5(NAMESPACE, canonical(body))), **body}

    async def run():
        dbs = {role: Database(Settings(database_url=urls[role], pool_size=2, pool_overflow=0, db_timeout_seconds=5))
               for role in ("collector", "processor", "query", "privacy")}
        try:
            writer, processor, reader = (PostgresCoreTransfer(dbs[role]) for role in ("collector", "processor", "query"))
            intake, verifier = PostgresSnapshotProof(dbs["collector"]), PostgresSnapshotProof(dbs["processor"])
            deadline = lambda: time.monotonic() + 5
            assert await intake.accept("core", proof, proof_fingerprint(proof), deadline=deadline()) == "accepted"
            assert (await intake.receipt("core", UUID(proof["proof_id"]), proof_fingerprint(proof), deadline=deadline()))["coverage"] == "pending"
            assert await verifier.project_one(deadline=deadline())
            for event in (first, second):
                assert await writer.accept("core", event, core.fingerprint(event), deadline=deadline()) == "accepted"
                assert await processor.project_one(deadline=deadline())
            assert await verifier.project_one(deadline=deadline())
            assert (await intake.receipt("core", UUID(proof["proof_id"]), proof_fingerprint(proof), deadline=deadline()))["coverage"] == "verified"
            _, _, coverage = await reader.read_subject("core", subject, date(2026, 11, 17), deadline=deadline(), require_coverage=True)
            assert coverage["coverage_through_revision"] == 1
            async def privacy_guard(session):
                pass  # Synthetic database only; production uses the external privacy barrier.
            dbs["query"].transaction_guard = privacy_guard
            history = await reader.read_history("core", subject, date(2026, 11, 16), date(2026, 11, 17), deadline=deadline())
            assert history["days"] == [{"date": "2026-11-16", "theory_ms": 3600000, "practice_ms": 3600000,
                                        "provenance": "core_legacy_conditional"}]
            assert history["coverage"]["coverage_through_revision"] == 1
            admin_visits, admin_coverage = await reader.read_admin("core", [subject], date(2026, 11, 17),
                deadline=deadline(), required_revisions={subject: 1})
            assert len(admin_visits) == 1 and admin_coverage[0]["coverage_through_revision"] == 1
            with pytest.raises(IntakeUnavailable):
                await reader.read_admin("core", [subject], date(2026, 11, 17), deadline=deadline(), required_revisions={subject: 0})
            newer = core.activity(subject, date(2026, 11, 17), None, revision=2)
            assert await writer.accept("core", newer, core.fingerprint(newer), deadline=deadline()) == "accepted"
            assert await processor.project_one(deadline=deadline())
            with pytest.raises(IntakeUnavailable):
                await reader.read_subject("core", subject, date(2026, 11, 17), deadline=deadline(), require_coverage=True)
            with pytest.raises(IntakeUnavailable):
                await reader.read_history("core", subject, date(2026, 11, 16), date(2026, 11, 17), deadline=deadline())
            late = core.activity(subject, date(2026, 11, 15), None, revision=0)
            assert await writer.accept("core", late, core.fingerprint(late), deadline=deadline()) == "accepted"
            assert (await intake.receipt("core", UUID(proof["proof_id"]), proof_fingerprint(proof), deadline=deadline()))["coverage"] == "pending"
            now = datetime.now(timezone.utc)
            control = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(), subject_ref=subject, epoch=1, occurred_at=now)
            entry = JournalEntry(seq=1, producer="core", control=control, received_at=now, payload_hash=deletion_hash("core", control))
            assert (await PostgresPrivacyStore(dbs["privacy"]).apply("test-authority", entry)).complete
            with pytest.raises(IntakeForbidden):
                await reader.read_history("core", subject, date(2026, 11, 16), date(2026, 11, 17), deadline=deadline())
            assert (await intake.receipt("core", UUID(proof["proof_id"]), proof_fingerprint(proof), deadline=deadline()))["status"] == "absent"
        finally:
            for db in dbs.values(): await db.close()
    asyncio.run(run())


def test_task_progress_projection_and_v1_v2_proof_scopes(proof_target, monkeypatch):
    from pymentor_analytics.contracts.core_snapshot_proof_v1 import validate_proof

    monkeypatch.syspath_prepend(str(ROOT.parent / "core-api"))
    from pymentor_core.modules.analytics_delivery.snapshot_contract import make_proof

    data, name, urls = proof_target
    subject = uuid4()
    now = datetime.now(timezone.utc)
    day = core.activity(subject, date(2026, 11, 16), None, revision=0)
    lesson = core.progress(subject, "backend", "same-id", now, revision=1)
    task_v1 = core.task_progress(subject, "backend", "same-id", "r1", now, revision=2)
    task_v2 = core.task_progress(subject, "backend", "same-id", "r2", now, revision=3)
    compat_proof = make_proof(subject, 3, [day, lesson], now)
    full_proof = make_proof(subject, 4, [day, lesson, task_v2], now, tasks=True)

    async def run():
        collector, processor, query = (Database(Settings(database_url=urls[role], pool_size=1, pool_overflow=0))
                                       for role in ("collector", "processor", "query"))
        try:
            writer = PostgresCoreTransfer(collector)
            projector = PostgresCoreTransfer(processor)
            proof_writer, proof_worker = PostgresSnapshotProof(collector), PostgresSnapshotProof(processor)
            deadline = lambda: time.monotonic() + 5
            for event in (task_v2, day, lesson, task_v1, task_v2):
                assert await writer.accept("core", event, core.fingerprint(event), deadline=deadline()) == "accepted"
            for _ in range(4):
                assert await projector.project_one(deadline=deadline())
            assert not await projector.project_one(deadline=deadline())
            for proof in (compat_proof, full_proof):
                hash_value = proof_fingerprint(proof)
                assert validate_proof(proof, hash_value) == proof
                assert await proof_writer.accept("core", proof, hash_value, deadline=deadline()) == "accepted"
                assert await proof_worker.project_one(deadline=deadline())
                assert await proof_writer.receipt("core", UUID(proof["proof_id"]), hash_value, deadline=deadline()) == {
                    "status": "accepted", "coverage": "verified"}
            with connect(data, name) as conn:
                rows = conn.execute("""SELECT f.accepted_event->>'kind',f.accepted_event->'payload'->>'task_revision',p.revision
                    FROM analytics.core_transfer_projection p JOIN analytics.activity_facts f
                    USING (producer_namespace,event_id,subject_ref) WHERE p.subject_ref=%s ORDER BY p.revision""",
                    (subject,)).fetchall()
                assert rows == [("legacy_activity_day", None, 0), ("lesson_progress", None, 1), ("task_progress", "r2", 3)]
            async def synthetic_guard(session):
                pass

            query.transaction_guard = synthetic_guard
            reader = PostgresCoreTransfer(query)
            actor = uuid4()
            summary = await reader.read_completion_summary("core", {subject: 4}, actor, now, deadline=deadline())
            assert summary["lessons_completed_7d"] == 1
            assert summary["weekly_by_subject"] == {subject: 1}
            assert sum(day["lessons_completed"] for day in summary["daily_lessons_completed"]) == 1
            with pytest.raises(IntakeUnavailable):
                await reader.read_completion_summary("core", {subject: 3}, actor, now, deadline=deadline())
        finally:
            await collector.close()
            await processor.close()
            await query.close()

    asyncio.run(run())
