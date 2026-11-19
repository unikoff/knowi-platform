"""Isolated a011 to a012 migration, materialization and privacy proof."""
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from pymentor_analytics.adapters.postgres.activity_course_projection import backfill_course_projection_step
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.privacy_control_v1 import JournalEntry, deletion_hash
from pymentor_analytics.contracts.v2 import DeletionFence


def test_course_backfill_empty_markers_privacy_and_prior_schema(activity_day_projection_target):
    _, _, urls, run_id, _ = activity_day_projection_target
    subject, empty = uuid4(), uuid4()
    bucket = datetime(2026, 1, 1, 21, tzinfo=timezone.utc)
    intervals = [{"start_ms": 1767301200000, "end_ms": 1767301202000,
                  "course_id": "python", "lesson_id": "l1", "section": "theory"}]
    contributions = [{"course_id": "python", "lesson_id": "l1", "section": "theory", "active_ms": 2000}]
    engine = create_engine(urls["migrate"].replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.begin() as connection:
            connection.execute(text("UPDATE analytics.activity_projection_backfill SET phase='complete'"))
            connection.execute(text("UPDATE analytics.activity_projection_day_backfill SET phase='complete',run_id=:run"), {"run": run_id})
            for who, winners, parts in ((subject, intervals, contributions), (empty, [], [])):
                connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(who)})
                connection.execute(text("""INSERT INTO analytics.activity_projection_buckets
                    (subject_ref,bucket_start,metric_version,generation,state) VALUES(:subject,:bucket,'activity-v1',1,'dirty')"""),
                    {"subject": who, "bucket": bucket})
                connection.execute(text("""UPDATE analytics.activity_projection_buckets SET state='ready',projection_generation=1,subject_epoch=0,
                    winner_intervals=CAST(:w AS jsonb),presence_intervals='[]',presence_points='[]',contributions=CAST(:c AS jsonb),
                    presence_labels='[]',compact_item_count=:n,edge_item_count=:n,has_presence=false WHERE subject_ref=:subject"""),
                    {"subject": who, "w": json.dumps(winners), "c": json.dumps(parts), "n": len(parts)})
                connection.execute(text("UPDATE analytics.activity_projection_day_sources SET state='ready' WHERE subject_ref=:subject"), {"subject": who})
                connection.execute(text("""INSERT INTO analytics.activity_projection_days
                    (subject_ref,local_day,metric_version,generation,subject_epoch,run_id,contributions,presence_labels,
                     source_bucket_count,source_compact_items,source_compact_bytes)
                    SELECT subject_ref,local_day,metric_version,generation,0,:run,CAST(:c AS jsonb),'[]',1,:n,0
                    FROM analytics.activity_projection_day_sources WHERE subject_ref=:subject"""),
                    {"subject": who, "run": run_id, "c": json.dumps(parts), "n": len(parts)})
        upgrade(Settings(database_url=urls["migrate"]), target_revision="a012")
        with engine.begin() as connection:
            assert connection.execute(text("SELECT count(*) FROM analytics.activity_course_buckets")).scalar_one() == 0
            assert connection.execute(text("SELECT count(*) FROM analytics.activity_projection_buckets WHERE course_projection_generation IS NULL")).scalar_one() == 2
        async def backfill():
            db = Database(Settings(database_url=urls["processor"], pool_size=2, pool_overflow=0, db_timeout_seconds=5))
            try:
                assert await backfill_course_projection_step(db, run_id=run_id, deadline=time.monotonic()+5) == "bucket"
                assert await backfill_course_projection_step(db, run_id=run_id, deadline=time.monotonic()+5) == "bucket"
                assert await backfill_course_projection_step(db, run_id=run_id, deadline=time.monotonic()+5) == "day"
                assert await backfill_course_projection_step(db, run_id=run_id, deadline=time.monotonic()+5) == "day"
                assert await backfill_course_projection_step(db, run_id=run_id, deadline=time.monotonic()+5) == "complete"
            finally:
                await db.close()
        asyncio.run(backfill())
        with engine.begin() as connection:
            row = connection.execute(text("SELECT course_id,theory_ms,practice_ms,total_ms,winner_intervals FROM analytics.activity_course_buckets")).one()
            assert row == ("python", 2000, 0, 2000, intervals)
            assert connection.execute(text("""SELECT count(*) FROM analytics.activity_projection_buckets
                WHERE course_projection_generation=generation AND course_projection_run_id=:run"""), {"run": run_id}).scalar_one() == 2
            assert connection.execute(text("SELECT course_id,theory_ms,practice_ms,total_ms FROM analytics.activity_course_days")).one() == ("python", 2000, 0, 2000)
            assert connection.execute(text("""SELECT count(*) FROM analytics.activity_projection_days
                WHERE course_projection_generation=generation AND course_projection_run_id=:run"""), {"run": run_id}).scalar_one() == 2
            # Privacy role has access to both derived tables and can remove rows.
        async def purge():
            db = Database(Settings(database_url=urls["privacy"], pool_size=2, pool_overflow=0, db_timeout_seconds=5))
            now = datetime.now(timezone.utc)
            control = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(),
                                    subject_ref=subject, epoch=1, occurred_at=now)
            item = JournalEntry(seq=1, producer="core", control=control, received_at=now,
                                payload_hash=deletion_hash("core", control))
            try:
                result = await PostgresPrivacyStore(db).apply("course-test-authority", item, limit=1)
                assert result.complete
            finally:
                await db.close()
        asyncio.run(purge())
        with engine.begin() as connection:
            for table in ("activity_course_buckets", "activity_course_days", "activity_projection_buckets", "activity_projection_days"):
                assert connection.execute(text(f"SELECT count(*) FROM analytics.{table} WHERE subject_ref=:subject"), {"subject": subject}).scalar_one() == 0
            assert connection.execute(text("SELECT count(*) FROM analytics.activity_projection_buckets WHERE subject_ref=:subject"), {"subject": empty}).scalar_one() == 1
        with engine.begin() as connection:
            config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
            config.attributes["connection"] = connection
            command.downgrade(config, "a011")
            assert connection.execute(text("SELECT to_regclass('analytics.activity_course_buckets')")).scalar_one() is None
            assert connection.execute(text("SELECT count(*) FROM analytics.activity_projection_buckets WHERE subject_ref=:subject"), {"subject": empty}).scalar_one() == 1
    finally:
        engine.dispose()


def test_legacy_privacy_source_deletes_cascade_course_rows(activity_day_projection_target):
    _, _, urls, run_id, _ = activity_day_projection_target
    upgrade(Settings(database_url=urls["migrate"]), target_revision="a012")
    subject = uuid4()
    bucket = datetime(2026, 1, 1, tzinfo=timezone.utc)
    owner = create_engine(urls["migrate"].replace("postgresql://", "postgresql+psycopg://", 1))
    privacy = create_engine(urls["privacy"].replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with owner.begin() as connection:
            connection.execute(text("""INSERT INTO analytics.activity_projection_buckets
                (subject_ref,bucket_start,metric_version,generation,state) VALUES(:subject,:bucket,'activity-v1',1,'dirty')"""),
                {"subject": subject, "bucket": bucket})
            connection.execute(text("""INSERT INTO analytics.activity_projection_days
                (subject_ref,local_day,metric_version,generation,subject_epoch,run_id,contributions,presence_labels,
                 source_bucket_count,source_compact_items,source_compact_bytes)
                SELECT subject_ref,local_day,metric_version,generation,0,:run,'[]','[]',1,0,4
                FROM analytics.activity_projection_day_sources WHERE subject_ref=:subject"""), {"subject": subject, "run": run_id})
            connection.execute(text("""INSERT INTO analytics.activity_course_buckets
                VALUES(:subject,:bucket,'activity-v1','c',1,0,:run,'[{}]','[{}]',1,0,1)"""),
                {"subject": subject, "bucket": bucket, "run": run_id})
            connection.execute(text("""INSERT INTO analytics.activity_course_days
                SELECT subject_ref,local_day,metric_version,'c',generation,0,:run,1,0,1
                FROM analytics.activity_projection_days WHERE subject_ref=:subject"""), {"subject": subject, "run": run_id})
        with privacy.begin() as connection:
            connection.execute(text("""INSERT INTO analytics.subject_fences(subject_ref,epoch,deleted,control_id)
                VALUES(:subject,1,true,:control)"""), {"subject": subject, "control": uuid4()})
            connection.execute(text("DELETE FROM analytics.activity_projection_buckets WHERE subject_ref=:subject"), {"subject": subject})
            connection.execute(text("DELETE FROM analytics.activity_projection_days WHERE subject_ref=:subject"), {"subject": subject})
        with owner.begin() as connection:
            assert connection.execute(text("SELECT count(*) FROM analytics.activity_course_buckets")).scalar_one() == 0
            assert connection.execute(text("SELECT count(*) FROM analytics.activity_course_days")).scalar_one() == 0
    finally:
        privacy.dispose()
        owner.dispose()
