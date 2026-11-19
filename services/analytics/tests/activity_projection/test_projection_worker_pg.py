"""Prepared destructive-only-to-sentinel-DB tests; opt-in after root GO."""
import asyncio
import importlib.util
import os
import time
from datetime import date, datetime, time as daytime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pymentor_analytics.adapters.postgres.activity import ActivityStorage
from pymentor_analytics.adapters.postgres.activity_projection import PostgresActivityProjection
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.core_transfer import CheckedDeletionStatus, PostgresCoreTransfer
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.privacy_control_v1 import JournalEntry, deletion_hash
from pymentor_analytics.contracts.v2 import DeletionFence
from pymentor_analytics.app.config import Settings
from pymentor_analytics.modules.analytics.activity.bucket import normalize_subject_bucket
from pymentor_analytics.modules.analytics.activity.projection_ports import StaleBucketClaim
from pymentor_analytics.modules.collector.ports import IntakeUnavailable
from pymentor_analytics.modules.collector.ports import IntakeForbidden

pytestmark = pytest.mark.skipif(os.environ.get("ANALYTICS_RUN_ACTIVITY_PROJECTION_SYNTHETIC") != "1",
                                reason="separate disposable a010 PostgreSQL GO required")
spec = importlib.util.spec_from_file_location("projection_worker_events", Path(__file__).parents[1] / "reference/test_activity_v1.py")
events = importlib.util.module_from_spec(spec)
spec.loader.exec_module(events)
core_spec = importlib.util.spec_from_file_location("projection_core_contract", Path(__file__).resolve().parents[3] / "core-api/pymentor_core/modules/analytics_delivery/contracts.py")
core = importlib.util.module_from_spec(core_spec)
core_spec.loader.exec_module(core)
helpers_spec = importlib.util.spec_from_file_location("projection_deletion_helpers", Path(__file__).parents[1] / "core_transfer/test_deletion_status.py")
helpers = importlib.util.module_from_spec(helpers_spec)
helpers_spec.loader.exec_module(helpers)


def test_trigger_duplicate_late_claim_replay_and_restore_generation(activity_projection_target):
    _, _, urls, run_id, _ = activity_projection_target
    async def run():
        writer, processor, other = [Database(Settings(database_url=urls[role], pool_size=2, pool_overflow=0,
                                                       db_timeout_seconds=5)) for role in ("collector", "processor", "processor")]
        store = PostgresActivityProjection(processor, expected_run_id=run_id)
        other_store = PostgresActivityProjection(other, expected_run_id=run_id)
        deadline = lambda: time.monotonic() + 5
        start = events.BASE + timedelta(minutes=14, seconds=50)
        first = events.event(start, start + timedelta(seconds=20), number=1)
        later = events.event(start + timedelta(seconds=1), start + timedelta(seconds=16), number=2, course="other")
        try:
            async with writer.transaction() as session:
                assert await ActivityStorage(session).insert(first) == "inserted"
                assert await ActivityStorage(session).insert(first) == "duplicate"
            async with processor.transaction() as session:
                assert (await session.execute(text("SELECT count(*),max(generation) FROM analytics.activity_projection_buckets"))).one() == (2, 1)
            claims = await asyncio.gather(store.claim(uuid4(), deadline=deadline()), other_store.claim(uuid4(), deadline=deadline()))
            assert all(claims) and claims[0].bucket_start != claims[1].bucket_start
            claim, records = await store.load(claims[0], deadline=deadline())
            old = normalize_subject_bucket(records, subject_ref=claim.subject_ref, bucket_start=claim.bucket_start)
            async with writer.transaction() as session:
                assert await ActivityStorage(session).insert(later) == "inserted"
            with pytest.raises(StaleBucketClaim):
                await store.commit(claim, old, deadline=deadline())
            newer = await other_store.claim(uuid4(), deadline=deadline())
            assert newer and newer.generation == 2
            loaded, records = await other_store.load(newer, deadline=deadline())
            await other_store.commit(loaded, normalize_subject_bucket(records, subject_ref=loaded.subject_ref,
                                       bucket_start=loaded.bucket_start), deadline=deadline())
            wrong_run = PostgresActivityProjection(processor, expected_run_id=uuid4())
            with pytest.raises(IntakeUnavailable): await wrong_run.claim(uuid4(), deadline=deadline())
            # The processor cannot alter the externally controlled dataset token.
            with pytest.raises(DBAPIError):
                async with processor.transaction() as session:
                    await session.execute(text("UPDATE analytics.activity_projection_backfill SET run_id=:run"), {"run": uuid4()})
        finally:
            for database in (writer, processor, other): await database.close()
    asyncio.run(run())


def test_legacy_browser_days_moscow_dedup_dirty_and_deletion(activity_projection_target):
    _, _, urls, run_id, _ = activity_projection_target
    async def run():
        collector, processor, query, privacy = [Database(Settings(database_url=urls[role], pool_size=2, pool_overflow=0,
                                                                   db_timeout_seconds=5)) for role in ("collector", "processor", "query", "privacy")]
        async def guard(_session): pass  # This fixture is not an external authority integration test.
        query.transaction_guard = guard
        worker = PostgresActivityProjection(processor, expected_run_id=run_id)
        reader = PostgresCoreTransfer(query, browser_run_id=run_id)
        deadline = lambda: time.monotonic() + 5
        legacy = core.activity(events.SUBJECT, date(2026, 1, 1), None, revision=1)
        before_midnight = events.BASE + timedelta(hours=20, minutes=59, seconds=50)
        crossing = events.event(before_midnight, before_midnight + timedelta(seconds=20), number=5)
        try:
            assert await PostgresCoreTransfer(collector).accept("core", legacy, core.fingerprint(legacy), deadline=deadline()) == "accepted"
            assert await PostgresCoreTransfer(processor).project_one(deadline=deadline())
            async with collector.transaction() as session:
                assert await ActivityStorage(session).insert(crossing) == "inserted"
            with pytest.raises(IntakeUnavailable):
                await reader.read_subject("core", events.SUBJECT, date(2026, 1, 2), deadline=deadline())
            for _ in range(20):
                phase = await worker.backfill_step(deadline=deadline())
                claim = await worker.claim(uuid4(), deadline=deadline())
                if claim:
                    claim, records = await worker.load(claim, deadline=deadline())
                    await worker.commit(claim, normalize_subject_bucket(records, subject_ref=claim.subject_ref,
                                        bucket_start=claim.bucket_start), deadline=deadline())
                elif phase == "complete": break
            else: raise AssertionError("projection did not drain")
            visits, progress, coverage, browser_as_of = await reader.read_subject("core", events.SUBJECT, date(2026, 1, 2), deadline=deadline())
            assert {row["activity_date"]: row["visited_at"] for row in visits} == {"2026-01-01": None, "2026-01-02": None}
            assert progress == [] and coverage is None and browser_as_of
            admin, _, _ = await reader.read_admin("core", [events.SUBJECT], date(2026, 1, 2), deadline=deadline())
            assert {(v["subject_ref"], v["activity_date"]) for v in admin} == {(str(events.SUBJECT), "2026-01-01"), (str(events.SUBJECT), "2026-01-02")}
            async with collector.transaction() as session:
                await ActivityStorage(session).insert(events.event(before_midnight, before_midnight + timedelta(seconds=10), number=6))
            with pytest.raises(IntakeUnavailable):
                await reader.read_subject("core", events.SUBJECT, date(2026, 1, 2), deadline=deadline())
            async with privacy.transaction() as session:
                await session.execute(text("INSERT INTO analytics.subject_fences(subject_ref,epoch,deleted,control_id) VALUES (:subject,1,true,:control)"),
                                      {"subject": events.SUBJECT, "control": uuid4()})
            with pytest.raises(IntakeForbidden):
                await reader.read_subject("core", events.SUBJECT, date(2026, 1, 2), deadline=deadline())
        finally:
            for database in (collector, processor, query, privacy): await database.close()
    asyncio.run(run())


def test_backfill_capacity_commits_prefix_and_makes_progress(activity_projection_target):
    _, _, urls, run_id, _ = activity_projection_target
    async def run():
        collector, processor, migrator = [Database(Settings(database_url=urls[role], pool_size=2, pool_overflow=0,
                                                               db_timeout_seconds=5)) for role in ("collector", "processor", "migrate")]
        store = PostgresActivityProjection(processor, expected_run_id=run_id)
        deadline = lambda: time.monotonic() + 5
        try:
            async with collector.transaction() as session:
                for i in range(3):
                    when = events.BASE + timedelta(minutes=15*i)
                    assert await ActivityStorage(session).insert(events.event(when, when, number=100+i, opened=True)) == "inserted"
            async with migrator.transaction() as session:
                # Simulate historical pre-trigger raw facts in this disposable DB only.
                await session.execute(text("DELETE FROM analytics.activity_projection_buckets"))
                await session.execute(text("UPDATE analytics.admission_budget SET max_outstanding_jobs=2 WHERE singleton=true"))
            assert await store.backfill_step(deadline=deadline()) == "scanning"
            async with processor.transaction() as session:
                assert (await session.execute(text("SELECT count(*) FROM analytics.activity_projection_buckets"))).scalar_one() == 2
            for _ in range(30):
                phase = await store.backfill_step(deadline=deadline())
                claim = await store.claim(uuid4(), deadline=deadline())
                if claim:
                    claim, records = await store.load(claim, deadline=deadline())
                    await store.commit(claim, normalize_subject_bucket(records, subject_ref=claim.subject_ref,
                                       bucket_start=claim.bucket_start), deadline=deadline())
                elif phase == "complete": break
            else: raise AssertionError("capacity-constrained scan failed to progress")
            async with processor.transaction() as session:
                assert (await session.execute(text("SELECT count(*) FROM analytics.activity_projection_buckets WHERE state='ready'"))).scalar_one() == 3
        finally:
            for database in (collector, processor, migrator): await database.close()
    asyncio.run(run())


def test_waiting_worker_observes_committed_delete_before_loading(activity_projection_target):
    _, _, urls, run_id, _ = activity_projection_target
    async def run():
        writer, processor, privacy = [Database(Settings(database_url=urls[role], pool_size=2, pool_overflow=0,
                                                        db_timeout_seconds=5)) for role in ("collector", "processor", "privacy")]
        store = PostgresActivityProjection(processor, expected_run_id=run_id)
        deadline = lambda: time.monotonic() + 5
        try:
            async with writer.transaction() as session:
                await ActivityStorage(session).insert(events.event(events.BASE, events.BASE + timedelta(seconds=10)))
            claim = await store.claim(uuid4(), deadline=deadline())
            async with privacy.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(events.SUBJECT)})
                await session.execute(text("INSERT INTO analytics.subject_fences(subject_ref,epoch,deleted,control_id) VALUES (:subject,1,true,:control)"),
                                      {"subject": events.SUBJECT, "control": uuid4()})
                pending = asyncio.create_task(store.load(claim, deadline=deadline()))
                await asyncio.sleep(.1)
                assert not pending.done()
            with pytest.raises(StaleBucketClaim): await pending
            async with processor.transaction() as session:
                row = (await session.execute(text("SELECT projection_generation FROM analytics.activity_projection_buckets"))).scalar_one()
                assert row is None
        finally:
            for database in (writer, processor, privacy): await database.close()
    asyncio.run(run())


def test_verified_deletion_waits_for_all_bucket_pages(activity_projection_target):
    _, _, urls, run_id, _ = activity_projection_target
    async def run():
        collector, privacy, status = [Database(Settings(database_url=urls[role], pool_size=2, pool_overflow=0,
                                                       db_timeout_seconds=5)) for role in ("collector", "privacy", "transfer_status")]
        now, control_id = datetime.now(timezone.utc), uuid4()
        control = DeletionFence(schema_version=2, kind="deletion_fence", control_id=control_id,
                                subject_ref=events.SUBJECT, epoch=1, occurred_at=now)
        entry = JournalEntry(seq=1, producer="core", control=control, received_at=now, payload_hash=deletion_hash("core", control))
        checker = CheckedDeletionStatus(status, helpers.signed_client(entry), producer_by_namespace={"core": "core"})
        store = PostgresPrivacyStore(privacy)
        try:
            async with collector.transaction() as session:
                for i in range(3):
                    when = events.BASE + timedelta(minutes=15*i)
                    await ActivityStorage(session).insert(events.event(when, when, number=300+i, opened=True))
            first = await store.apply("test-authority", entry, limit=1)
            assert not first.complete
            assert await checker.status("core", control_id, events.SUBJECT, 1, deadline=time.monotonic()+4) == "pending"
            for _ in range(5):
                progress = await store.apply("test-authority", entry, limit=1)
                if progress.complete: break
            assert progress.complete and progress.applied_through == 1
            assert await checker.status("core", control_id, events.SUBJECT, 1, deadline=time.monotonic()+4) == "accepted"
            async with status.transaction() as session:
                count = (await session.execute(text("SELECT count(subject_ref) FROM analytics.activity_projection_buckets WHERE subject_ref=:subject"),
                                               {"subject": events.SUBJECT})).scalar_one()
                assert count == 0
            with pytest.raises(DBAPIError):
                async with collector.transaction() as session:
                    await ActivityStorage(session).insert(events.event(events.BASE, events.BASE, number=400, opened=True))
        finally:
            for database in (collector, privacy, status): await database.close()
    asyncio.run(run())


def test_restore_generation_blocks_old_workers_until_new_scan_finishes(activity_projection_target):
    _, _, urls, run_id, _ = activity_projection_target
    async def run():
        writer, processor, reader, migrator = [Database(Settings(database_url=urls[role], pool_size=2, pool_overflow=0,
                                                                 db_timeout_seconds=5)) for role in ("collector", "processor", "query", "migrate")]
        original = PostgresActivityProjection(processor, expected_run_id=run_id)
        new_id = uuid4()
        replacement = PostgresActivityProjection(processor, expected_run_id=new_id)
        deadline = lambda: time.monotonic()+5
        async def drain(store):
            for _ in range(20):
                phase = await store.backfill_step(deadline=deadline())
                claim = await store.claim(uuid4(), deadline=deadline())
                if claim:
                    claim, records = await store.load(claim, deadline=deadline())
                    await store.commit(claim, normalize_subject_bucket(records, subject_ref=claim.subject_ref,
                                       bucket_start=claim.bucket_start), deadline=deadline())
                elif phase == "complete": return
            raise AssertionError("bounded rebuild did not complete")
        try:
            async with writer.transaction() as session:
                await ActivityStorage(session).insert(events.event(events.BASE, events.BASE+timedelta(seconds=10)))
            await drain(original)
            async with reader.transaction() as session:
                assert (await session.execute(text("SELECT analytics.lock_activity_projection_run(:run)"), {"run": run_id})).scalar_one() is True
            async with migrator.transaction() as session:
                await session.execute(text("SELECT analytics.begin_activity_projection_backfill(:old,:new)"), {"old": run_id, "new": new_id})
            with pytest.raises(IntakeUnavailable): await original.claim(uuid4(), deadline=deadline())
            async with reader.transaction() as session:
                assert (await session.execute(text("SELECT analytics.lock_activity_projection_run(:run)"), {"run": new_id})).scalar_one() is False
            await drain(replacement)
            async with reader.transaction() as session:
                assert (await session.execute(text("SELECT analytics.lock_activity_projection_run(:run)"), {"run": new_id})).scalar_one() is True
                assert (await session.execute(text("SELECT analytics.lock_activity_projection_run(:run)"), {"run": run_id})).scalar_one() is False
        finally:
            for database in (writer, processor, reader, migrator): await database.close()
    asyncio.run(run())


def test_future_browser_presence_is_not_included_before_snapshot(activity_projection_target):
    _, _, urls, run_id, _ = activity_projection_target
    async def run():
        writer, processor, query = [Database(Settings(database_url=urls[role], pool_size=2, pool_overflow=0,
                                                       db_timeout_seconds=5)) for role in ("collector", "processor", "query")]
        async def guard(_session): pass
        query.transaction_guard = guard
        worker = PostgresActivityProjection(processor, expected_run_id=run_id)
        reader = PostgresCoreTransfer(query, browser_run_id=run_id)
        now = datetime.now(timezone.utc)
        moscow_today = now.astimezone(ZoneInfo("Europe/Moscow")).date()
        midnight = datetime.combine(moscow_today+timedelta(days=1), daytime.min, ZoneInfo("Europe/Moscow")).astimezone(timezone.utc)
        future_point, future_segment, future_midnight = uuid4(), uuid4(), uuid4()
        deadline = lambda: time.monotonic()+5
        try:
            async with writer.transaction() as session:
                await ActivityStorage(session).insert(events.event(now+timedelta(seconds=20), now+timedelta(seconds=20),
                                                      number=501, subject=future_point, opened=True))
                await ActivityStorage(session).insert(events.event(now+timedelta(seconds=20), now+timedelta(seconds=21),
                                                      number=502, subject=future_segment))
                await ActivityStorage(session).insert(events.event(midnight, midnight, number=503, subject=future_midnight, opened=True))
                await ActivityStorage(session).insert(events.event(midnight, midnight+timedelta(seconds=1), number=504, subject=future_midnight))
            for _ in range(30):
                phase = await worker.backfill_step(deadline=deadline())
                claim = await worker.claim(uuid4(), deadline=deadline())
                if claim:
                    claim, records = await worker.load(claim, deadline=deadline())
                    await worker.commit(claim, normalize_subject_bucket(records, subject_ref=claim.subject_ref,
                                        bucket_start=claim.bucket_start), deadline=deadline())
                elif phase == "complete": break
            else: raise AssertionError("bounded scan did not complete")
            for subject in (future_point, future_segment, future_midnight):
                visits, _, _, _ = await reader.read_subject("core", subject, moscow_today, visit_through=moscow_today+timedelta(days=1), deadline=deadline())
                assert visits == []
        finally:
            for database in (writer, processor, query): await database.close()
    asyncio.run(run())


def test_database_derives_projection_sizes_and_rejects_forged_metadata(activity_projection_target):
    _, _, urls, run_id, _ = activity_projection_target
    async def run():
        writer, processor, migrator = [Database(Settings(database_url=urls[role], pool_size=2, pool_overflow=0,
                                                          db_timeout_seconds=5)) for role in ("collector", "processor", "migrate")]
        store = PostgresActivityProjection(processor, expected_run_id=run_id)
        deadline = lambda: time.monotonic()+5
        try:
            async with writer.transaction() as session:
                await ActivityStorage(session).insert(events.event(events.BASE, events.BASE+timedelta(seconds=10)))
            claim = await store.claim(uuid4(), deadline=deadline())
            claim, records = await store.load(claim, deadline=deadline())
            await store.commit(claim, normalize_subject_bucket(records, subject_ref=claim.subject_ref,
                               bucket_start=claim.bucket_start), deadline=deadline())
            async with migrator.transaction() as session:
                valid = (await session.execute(text("""SELECT compact_json_bytes=octet_length(contributions::text)+octet_length(presence_labels::text),
                    edge_json_bytes=octet_length(winner_intervals::text)+octet_length(presence_intervals::text)+octet_length(presence_points::text)
                    FROM analytics.activity_projection_buckets"""))).one()
                assert valid == (True, True)
            with pytest.raises(DBAPIError):
                async with migrator.transaction() as session:
                    await session.execute(text("UPDATE analytics.activity_projection_buckets SET edge_json_bytes=0"))
            with pytest.raises(DBAPIError):
                async with processor.transaction() as session:
                    await session.execute(text("UPDATE analytics.activity_projection_buckets SET compact_json_bytes=0"))
        finally:
            for database in (writer, processor, migrator): await database.close()
    asyncio.run(run())
