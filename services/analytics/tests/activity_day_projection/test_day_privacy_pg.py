"""Opt-in disposable PostgreSQL integration coverage; never runs without the fixture gate."""
import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import time
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.activity_day_projection import PostgresActivityDayProjection
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.privacy_control_v1 import JournalEntry, deletion_hash
from pymentor_analytics.contracts.v2 import DeletionFence
from pymentor_analytics.modules.analytics.activity.day_ports import StaleDayClaim
from pymentor_analytics.modules.collector.ports import IntakeUnavailable
from tools.bootstrap_postgres import connect


# The shared fixture creates and removes only its sentinel-guarded disposable DB.
_path = Path(__file__).resolve().parents[1] / "activity_projection/conftest.py"
_spec = importlib.util.spec_from_file_location("day_privacy_pg_fixture", _path)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
activity_projection_target = _fixture.activity_projection_target


def make_entry(subject):
    now = datetime.now(timezone.utc)
    control = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(),
                            subject_ref=subject, epoch=1, occurred_at=now)
    return JournalEntry(seq=1, producer="core", control=control, received_at=now,
                        payload_hash=deletion_hash("core", control))


def test_real_pg_purge_pages_trigger_and_new_lifecycle(activity_projection_target):
    data, name, urls, run_id, _ = activity_projection_target
    upgrade(Settings(database_url=urls["migrate"]), target_revision="a011")
    old, new = uuid4(), uuid4()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with connect(data, name) as connection:
        connection.execute("UPDATE analytics.activity_projection_backfill SET phase='complete' WHERE singleton=true")
        connection.execute("SELECT analytics.begin_activity_day_backfill(%s)", (run_id,))
        for subject, count in ((old, 4), (new, 1)):
            for index in range(count):
                timestamp = now + timedelta(days=index + (100 if subject == new else 0))
                connection.execute("""INSERT INTO analytics.activity_projection_buckets
                    (subject_ref,bucket_start,generation,state) VALUES (%s,%s,1,'dirty')""", (subject, timestamp))
                connection.execute("""INSERT INTO analytics.activity_projection_days
                    (subject_ref,local_day,metric_version,generation,subject_epoch,run_id,contributions,presence_labels,
                     source_bucket_count,source_compact_items,source_compact_bytes)
                    SELECT subject_ref,local_day,metric_version,generation,0,%s,'[]','[]',1,0,4
                    FROM analytics.activity_projection_day_sources WHERE subject_ref=%s AND local_day=%s""",
                                   (run_id, subject, timestamp.date()))
    item = make_entry(old)
    class LostReply(PostgresPrivacyStore):
        async def _after_commit(self): raise RuntimeError("lost reply")
    async def scenario():
        db = Database(Settings(database_url=urls["privacy"], pool_size=2, pool_overflow=0, db_timeout_seconds=5))
        migrator = Database(Settings(database_url=urls["migrate"], pool_size=2, pool_overflow=0, db_timeout_seconds=5))
        processor = Database(Settings(database_url=urls["processor"], pool_size=2, pool_overflow=0, db_timeout_seconds=5))
        try:
            worker = PostgresActivityDayProjection(processor, expected_run_id=run_id)
            old_claim = await worker.claim(uuid4(), deadline=time.monotonic() + 5)
            assert old_claim and old_claim.subject_ref == old
            with pytest.raises(RuntimeError, match="lost reply"):
                await LostReply(db).apply("day-privacy-authority", item, limit=1)
            with pytest.raises(StaleDayClaim):
                await worker.load(old_claim, deadline=time.monotonic() + 5)
            with connect(data, name) as connection:
                assert connection.execute("SELECT deleted FROM analytics.subject_fences WHERE subject_ref=%s", (old,)).fetchone() == (True,)
                assert connection.execute("SELECT applied_through FROM analytics.privacy_state").fetchone() == (0,)
                for table in ("activity_projection_buckets", "activity_projection_days", "activity_projection_day_sources"):
                    assert connection.execute(f"SELECT count(*) FROM analytics.{table} WHERE subject_ref=%s", (old,)).fetchone() == (3,)
            store = PostgresPrivacyStore(db)
            for _ in range(5):
                result = await store.apply("day-privacy-authority", item, limit=1)
                if result.complete:
                    break
            assert result.complete and result.applied_through == 1
            assert (await store.apply("day-privacy-authority", item, limit=1)).complete
            with connect(data, name) as connection:
                for table in ("activity_projection_buckets", "activity_projection_days", "activity_projection_day_sources"):
                    assert connection.execute(f"SELECT count(*) FROM analytics.{table} WHERE subject_ref=%s", (old,)).fetchone() == (0,)
                    assert connection.execute(f"SELECT count(*) FROM analytics.{table} WHERE subject_ref=%s", (new,)).fetchone() == (1,)
            with pytest.raises(DBAPIError):
                async with migrator.transaction() as session:
                    from sqlalchemy import text
                    await session.execute(text("""INSERT INTO analytics.activity_projection_buckets
                        (subject_ref,bucket_start,generation,state) VALUES (:subject,:bucket,1,'dirty')"""),
                                          {"subject": old, "bucket": now})
        finally:
            await db.close()
            await migrator.close()
            await processor.close()
    asyncio.run(scenario())


def test_real_pg_a010_and_partial_day_schema_fail_closed(activity_projection_target):
    data, name, urls, _, _ = activity_projection_target
    db = Database(Settings(database_url=urls["privacy"], pool_size=2, pool_overflow=0, db_timeout_seconds=5))
    old, partial = make_entry(uuid4()), make_entry(uuid4())
    partial = partial.model_copy(update={"seq": 2})
    async def scenario():
        try:
            store = PostgresPrivacyStore(db)
            assert (await store.apply("day-privacy-authority", old)).complete
            # Synthetic partial schema deliberately tests the deployment guard.
            with connect(data, name) as connection:
                connection.execute("CREATE TABLE analytics.activity_projection_day_sources(subject_ref uuid)")
            with pytest.raises(IntakeUnavailable):
                await store.apply("day-privacy-authority", partial)
            with connect(data, name) as connection:
                assert connection.execute("SELECT applied_through FROM analytics.privacy_state").fetchone() == (1,)
                assert connection.execute("SELECT count(*) FROM analytics.subject_fences WHERE subject_ref=%s", (partial.control.subject_ref,)).fetchone() == (0,)
        finally:
            await db.close()
    asyncio.run(scenario())
