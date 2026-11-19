"""Actual local reconciliation; no browser-facing authorization claim."""
import asyncio
import importlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.intake import PostgresIntake
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
from pymentor_analytics.adapters.postgres.processor import PostgresProcessor
from pymentor_analytics.adapters.postgres.quality import PostgresDiagnostics, PostgresReconciler
from pymentor_analytics.adapters.postgres.replay import PostgresReplay
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.privacy_control_v1 import JournalEntry, deletion_hash
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.processing_v1 import synthetic_delta
from pymentor_analytics.contracts.v2 import AcceptedEvent, DeletionFence, parse_browser_item
from tools.bootstrap_postgres import cleanup, config, connect, create

ROOT = Path(__file__).resolve().parents[2]
CONSUMER, OTHER = "synthetic.activity", "synthetic.independent"


@pytest.fixture
def target():
    if os.environ.get("ANALYTICS_RUN_SYNTHETIC_QUALITY") != "1":
        pytest.skip("explicit synthetic quality runner required")
    data = config(ROOT / ".local" / "postgres-connection.json")
    name, token, urls = create(data)
    try:
        upgrade(Settings(database_url=urls["migrate"]), target_revision="a006")
        yield data, name, urls
    finally:
        cleanup(data, name, token)


def db(url, timeout=5):
    return Database(Settings(database_url=url, pool_size=2, pool_overflow=0, db_timeout_seconds=timeout))


def event(subject, course="a"):
    raw = json.loads((ROOT / "contracts/fixtures/browser-batch.json").read_text(encoding="utf-8"))["events"][0]
    now = datetime.now(timezone.utc)
    raw.update(event_id=str(uuid4()), course_id=course, started_at=now.isoformat(), ended_at=now.isoformat(), last_action_at=now.isoformat())
    return AcceptedEvent(producer_namespace="synthetic", subject_ref=subject, source="browser",
                         event=parse_browser_item(json.dumps(raw).encode()), received_at=now)


def deletion(subject):
    now = datetime.now(timezone.utc)
    control = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(), subject_ref=subject,
                            epoch=1, occurred_at=now)
    return JournalEntry(seq=1, producer="synthetic-producer", control=control, received_at=now,
                        payload_hash=deletion_hash("synthetic-producer", control))


async def seed(writer, *items):
    for item in items: assert await writer.accept(item, deadline=time.monotonic() + 4) == "accepted"


async def drain(worker, consumer=CONSUMER):
    for _ in range(100):
        batch = await worker.claim(consumer, 1, uuid4(), 100, 30)
        if batch is None: return
        await worker.commit(batch, synthetic_delta(batch.facts))
    raise AssertionError("bounded test drain exceeded")


def test_no_data_stale_ok_zero_and_deleted_are_distinct(target):
    _, _, urls = target
    async def run():
        collector, processor, privacy = (db(urls[r]) for r in ("collector", "processor", "privacy"))
        subject, missing = uuid4(), uuid4()
        try:
            reconciler, worker = PostgresReconciler(processor), PostgresProcessor(processor)
            empty = await reconciler.check(CONSUMER, 1, missing, course_id="a")
            assert empty.status == "no_data" and empty.scan_complete and empty.data_through is None
            item = event(subject, "a")
            await seed(PostgresIntake(collector), item)
            stale = await reconciler.check(CONSUMER, 1, subject, course_id="a")
            assert stale.status == "stale" and stale.coverage.pending == 1 and stale.expected_count == 1
            await drain(worker)
            good = await reconciler.check(CONSUMER, 1, subject, course_id="a")
            assert good.status == "ok" and good.expected_count == good.actual_count == 1
            assert good.coverage.facts == good.coverage.receipts == 1 and good.data_through == item.received_at
            assert good.checkpoint_kind == "per_fact_receipts"
            zero = await reconciler.check(CONSUMER, 1, subject, course_id="b")
            assert zero.status == "zero" and zero.expected_count == zero.actual_count == 0
            assert zero.external_coverage == "unknown" and zero.data_through is None
            control = PostgresPrivacyStore(privacy)
            assert (await control.apply("synthetic-authority-1", deletion(subject))).complete
            deleted = await reconciler.check(CONSUMER, 1, subject, course_id="a")
            assert deleted.status == "deleted" and not deleted.scan_complete
            assert deleted.coverage is None and deleted.data_through is None and deleted.actual_count is None
        finally:
            for x in (collector, processor, privacy): await x.close()
    asyncio.run(run())


def test_compacted_completed_job_keeps_receipt_coverage(target):
    data, name, urls = target
    async def run():
        collector, processor = db(urls["collector"]), db(urls["processor"])
        subject = uuid4()
        try:
            item = event(subject)
            await seed(PostgresIntake(collector), item)
            await drain(PostgresProcessor(processor))
            with connect(data, name) as connection:
                connection.execute("DELETE FROM analytics.processing_jobs WHERE consumer=%s AND event_id=%s", (CONSUMER, item.event.event_id))
            report = await PostgresReconciler(processor).check(CONSUMER, 1, subject)
            assert report.status == "ok" and report.coverage.receipts == 1
            assert report.coverage.missing_jobs == 0 and report.actual_count == 1
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_scan_timeout_returns_unavailable_without_partial_results(target):
    _, _, urls = target
    async def run():
        processor = db(urls["processor"], timeout=0.1)
        class Slow(PostgresReconciler):
            async def _after_subject_lock(self):
                await asyncio.sleep(1)
        try:
            report = await Slow(processor).check(CONSUMER, 1, uuid4())
            assert report.status == "unavailable" and not report.scan_complete
            assert report.coverage is None and report.expected_count is None and report.actual_count is None
        finally:
            await processor.close()
    asyncio.run(run())


def test_corruption_mismatch_real_replay_rebuilds_same_projection(target):
    data, name, urls = target
    async def run():
        collector, processor = db(urls["collector"]), db(urls["processor"])
        subject = uuid4()
        try:
            writer, worker = PostgresIntake(collector), PostgresProcessor(processor)
            await seed(writer, event(subject, "a"), event(subject, "a"), event(subject, "b"))
            await drain(worker)
            reconciler, replay = PostgresReconciler(processor), PostgresReplay(processor)
            assert (await reconciler.check(CONSUMER, 1, subject)).status == "ok"
            with connect(data, name) as connection:
                connection.execute("UPDATE analytics.processing_results SET courses=%s::jsonb WHERE consumer=%s AND subject_ref=%s", (json.dumps({"a": 999}), CONSUMER, subject))
            assert (await reconciler.check(CONSUMER, 1, subject)).status == "mismatch"
            state = await replay.begin(CONSUMER, 1, subject, uuid4())
            assert (await reconciler.check(CONSUMER, 1, subject)).status == "stale"
            for _ in range(30):
                if state.phase == "drain": break
                state = await replay.step(state.handle, 2)
                await drain(worker)
            assert state.phase == "drain"
            await drain(worker)
            assert (await replay.finish(state.handle)).phase == "complete"
            report = await reconciler.check(CONSUMER, 1, subject)
            assert report.status == "ok" and report.expected_count == report.actual_count == 3
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_invalid_fact_and_ack_without_receipt_are_mismatch(target):
    data, name, urls = target
    async def run():
        collector, processor = db(urls["collector"]), db(urls["processor"])
        first, second = uuid4(), uuid4()
        try:
            one, two = event(first), event(second)
            await seed(PostgresIntake(collector), one, two)
            await drain(PostgresProcessor(processor))
            with connect(data, name) as connection:
                connection.execute("UPDATE analytics.activity_facts SET canonical_hash=%s WHERE event_id=%s", ("0" * 64, one.event.event_id))
                connection.execute("DELETE FROM analytics.processing_receipts WHERE consumer=%s AND event_id=%s", (CONSUMER, two.event.event_id))
            reconciler = PostgresReconciler(processor)
            invalid = await reconciler.check(CONSUMER, 1, first)
            assert invalid.coverage.invalid_facts == 1 and invalid.expected_count is None
            assert invalid.status == "mismatch" and invalid.scan_complete
            acknowledged = await reconciler.check(CONSUMER, 1, second)
            assert acknowledged.status == "mismatch" and acknowledged.scan_complete
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_full_keyset_scan_over_100_facts(target):
    data, name, urls = target
    async def run():
        collector, processor = db(urls["collector"], timeout=15), db(urls["processor"], timeout=15)
        subject = uuid4()
        try:
            with connect(data, name) as connection:
                connection.execute("UPDATE analytics.admission_budget SET subject_per_minute=200")
            await seed(PostgresIntake(collector), *(event(subject, "a") for _ in range(101)))
            await drain(PostgresProcessor(processor))
            result = await PostgresReconciler(processor).check(CONSUMER, 1, subject)
            assert result.status == "ok" and result.scan_complete
            assert result.coverage.facts == result.coverage.receipts == result.expected_count == result.actual_count == 101
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_diagnostics_are_fixed_aggregate_and_work_when_privacy_closed(target):
    data, name, urls = target
    async def run():
        collector, processor = db(urls["collector"]), db(urls["processor"])
        subject = uuid4()
        async def closed(session): raise PrivacyUnavailable()
        try:
            initial = await PostgresDiagnostics(processor).read()
            assert initial.stored_facts == 0 and initial.oldest_pending_seconds is None
            item = event(subject)
            await seed(PostgresIntake(collector), item)
            with connect(data, name) as connection:
                connection.execute("""INSERT INTO analytics.processing_jobs
                    (producer_namespace,event_id,consumer,consumer_version,subject_ref,status)
                    VALUES (%s,%s,'untrusted-label',7,%s,'odd-state')""", (item.producer_namespace, item.event.event_id, subject))
            processor.transaction_guard = closed
            report = await PostgresReconciler(processor).check(CONSUMER, 1, subject)
            assert report.status == "unavailable" and not report.scan_complete and report.coverage is None
            calls = 0
            async def closes_before_commit(session):
                nonlocal calls
                calls += 1
                if calls == 2: raise PrivacyUnavailable()
            processor.transaction_guard = closes_before_commit
            report = await PostgresReconciler(processor).check(CONSUMER, 1, subject)
            assert calls == 2 and report.status == "unavailable" and report.coverage is None
            processor.transaction_guard = closed
            snapshot = await PostgresDiagnostics(processor).read()
            assert snapshot.stored_facts == 1 and len(snapshot.jobs) == 8
            assert snapshot.unknown_consumer_jobs == 1 and snapshot.unknown_status_jobs == 1
            assert snapshot.oldest_pending_seconds is not None and snapshot.oldest_pending_seconds >= 0
            assert snapshot.database_bytes > 0 and snapshot.database_connections >= 1
            assert "untrusted-label" not in repr(snapshot) and str(subject) not in repr(snapshot)
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_reconciler_holds_subject_read_lock_until_snapshot_materialized(target):
    _, _, urls = target
    async def run():
        collector, processor, privacy = (db(urls[r]) for r in ("collector", "processor", "privacy"))
        subject, reached, release = uuid4(), asyncio.Event(), asyncio.Event()
        checking = deleting = None
        class Gated(PostgresReconciler):
            async def _after_subject_lock(self):
                reached.set()
                await release.wait()
        try:
            await seed(PostgresIntake(collector), event(subject))
            await drain(PostgresProcessor(processor))
            checking = asyncio.create_task(Gated(processor).check(CONSUMER, 1, subject))
            await asyncio.wait_for(reached.wait(), 2)
            deleting = asyncio.create_task(PostgresPrivacyStore(privacy).apply("synthetic-authority-1", deletion(subject)))
            diagnostics = PostgresDiagnostics(processor)
            async def wait_for_lock():
                while True:
                    snapshot = await diagnostics.read()
                    if snapshot.database_waiting_locks >= 1:
                        return
                    assert not deleting.done(), "deletion ended before an expected lock wait"
                    await asyncio.sleep(.02)
            await asyncio.wait_for(wait_for_lock(), 2)
            assert not deleting.done()
            release.set()
            assert (await asyncio.wait_for(checking, 2)).status == "ok"
            assert (await asyncio.wait_for(deleting, 2)).complete
            assert (await diagnostics.read()).database_waiting_locks == 0
            assert (await PostgresReconciler(processor).check(CONSUMER, 1, subject)).status == "deleted"
        finally:
            release.set()
            tasks = [task for task in (checking, deleting) if task is not None]
            for task in tasks:
                if not task.done(): task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for x in (collector, processor, privacy): await x.close()
    asyncio.run(run())


def test_operator_cli_checks_then_requires_explicit_single_repair(target, tmp_path, monkeypatch, capsys):
    data, name, urls = target
    subject, request = uuid4(), uuid4()
    async def setup():
        collector, processor = db(urls["collector"]), db(urls["processor"])
        try:
            await seed(PostgresIntake(collector), event(subject))
            await drain(PostgresProcessor(processor))
        finally:
            await collector.close(); await processor.close()
    asyncio.run(setup())
    with connect(data, name) as connection:
        token = connection.execute("SELECT token FROM public.analytics_test_sentinel").fetchone()[0]
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    cli = importlib.import_module("reconcile_synthetic")
    record = {"synthetic_only": True, "do_not_drop": True, "project": cli.runtime.PROJECT,
              "state": "ready", "database": name, "nonce": token, "urls": urls}
    monkeypatch.setattr(cli.runtime, "read_record", lambda: record)
    monkeypatch.setattr(cli.runtime, "LOCK", tmp_path / "quality-operation.lock")
    retirements = []
    monkeypatch.setattr(cli.run_local, "verify_replay_retirement", lambda value: retirements.append(value["database"]))
    base = ["--consumer", CONSUMER, "--subject-ref", str(subject)]
    assert cli.main(base) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] == "ok"
    assert str(subject) not in captured.out and token not in captured.out and not retirements
    with connect(data, name) as connection:
        assert connection.execute("SELECT count(*) FROM analytics.processing_replays").fetchone() == (0,)
    assert cli.main(base + ["--repair", "begin", "--request-id", str(request)]) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["phase"] == "cleanup" and retirements == [name]
    assert str(subject) not in captured.out and str(request) not in captured.out and token not in captured.out
    assert cli.main(base + ["--repair", "step", "--request-id", str(request),
                           "--generation", str(result["generation"]), "--subject-epoch", str(result["subject_epoch"]),
                           "--limit", "1"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["phase"] in {"cleanup", "scan"}
    assert str(subject) not in captured.out and str(request) not in captured.out
    assert cli.main(base + ["--request-id", str(request)]) == 2
    captured = capsys.readouterr()
    assert str(subject) not in captured.err and str(request) not in captured.err and token not in captured.err
