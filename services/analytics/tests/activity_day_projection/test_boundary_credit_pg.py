"""Disposable PostgreSQL tests for persisted, generation-fenced boundary credits."""
import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from pymentor_analytics.adapters.postgres.activity_boundary_credit import boundary_credit_step
from pymentor_analytics.adapters.postgres.activity_day_projection import PostgresActivityDayProjection
from pymentor_analytics.adapters.postgres.activity_projection import PostgresActivityProjection
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.intake import PostgresIntake
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.modules.collector.ports import IntakeUnavailable

from test_day_reports_pg import _drain_day, g, helpers


def test_boundary_credit_rebuild_midnight_and_invalidation(activity_day_projection_target):
    _, _, urls, run_id, _ = activity_day_projection_target
    upgrade(Settings(database_url=urls["migrate"]), target_revision="a013")
    engine = create_engine(urls["migrate"].replace("postgresql://", "postgresql+psycopg://", 1))
    subject = uuid4()
    boundary = datetime(2026, 1, 1, 21, tzinfo=timezone.utc)
    try:
        with engine.begin() as connection:
            connection.execute(text("UPDATE analytics.activity_projection_backfill SET phase='complete'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
            for start, end, bucket in ((boundary-timedelta(seconds=20), boundary, boundary-timedelta(minutes=15)),
                                       (boundary, boundary+timedelta(seconds=20), boundary)):
                left, right = int(start.timestamp()*1000), int(end.timestamp()*1000)
                winners = [{"start_ms": left, "end_ms": right, "course_id": "python", "lesson_id": "one", "section": "theory"}]
                parts = [{"course_id": "python", "lesson_id": "one", "section": "theory", "active_ms": 20000}]
                args = {"subject": subject, "bucket": bucket, "w": json.dumps(winners), "c": json.dumps(parts)}
                connection.execute(text("""INSERT INTO analytics.activity_projection_buckets
                    (subject_ref,bucket_start,metric_version,generation,state) VALUES(:subject,:bucket,'activity-v1',1,'dirty')"""), args)
                connection.execute(text("""UPDATE analytics.activity_projection_buckets SET state='ready',projection_generation=1,subject_epoch=0,
                    winner_intervals=CAST(:w AS jsonb),presence_intervals='[]',presence_points='[]',contributions=CAST(:c AS jsonb),
                    presence_labels='[]',compact_item_count=1,edge_item_count=1,has_presence=false
                    WHERE subject_ref=:subject AND bucket_start=:bucket"""), args)
            connection.execute(text("UPDATE analytics.activity_projection_day_sources SET state='ready' WHERE subject_ref=:subject"), {"subject": subject})
            connection.execute(text("""INSERT INTO analytics.activity_projection_days
                (subject_ref,local_day,metric_version,generation,subject_epoch,run_id,contributions,presence_labels,
                 source_bucket_count,source_compact_items,source_compact_bytes)
                SELECT subject_ref,local_day,metric_version,generation,0,:run,'[]','[]',1,0,4
                FROM analytics.activity_projection_day_sources WHERE subject_ref=:subject"""), {"subject": subject, "run": run_id})
        async def run():
            db = Database(Settings(database_url=urls["processor"], pool_size=2, pool_overflow=0, db_timeout_seconds=5))
            try:
                steps = [await boundary_credit_step(db, run_id=run_id, deadline=time.monotonic()+5) for _ in range(5)]
                assert steps == ["window", "window", "day", "day", "idle"]
            finally:
                await db.close()
        asyncio.run(run())
        with engine.begin() as connection:
            windows = connection.execute(text("SELECT credits FROM analytics.activity_credit_windows ORDER BY bucket_start")).scalars().all()
            assert [x[0]["active_ms"] for x in windows] == [40000, 40000]
            days = connection.execute(text("SELECT credits FROM analytics.activity_credit_days ORDER BY local_day")).scalars().all()
            assert [x[0]["active_ms"] for x in days] == [40000, 40000]
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
            connection.execute(text("""UPDATE analytics.activity_projection_buckets SET state='dirty',generation=generation+1
                WHERE subject_ref=:subject AND bucket_start=:bucket"""), {"subject": subject, "bucket": boundary})
            assert connection.execute(text("SELECT count(*) FROM analytics.activity_credit_windows WHERE state='dirty'")).scalar_one() == 2
            assert connection.execute(text("SELECT count(*) FROM analytics.activity_credit_days WHERE state='dirty'")).scalar_one() == 2
            # A correction opens a gap at midnight. The previous bonus must
            # disappear from both windows and both days on re-publication.
            start_ms = int((boundary + timedelta(seconds=20)).timestamp() * 1000)
            corrected = [{"start_ms": start_ms, "end_ms": start_ms + 20_000,
                          "course_id": "python", "lesson_id": "one", "section": "theory"}]
            connection.execute(text("""UPDATE analytics.activity_projection_buckets SET state='ready',projection_generation=generation,
                winner_intervals=CAST(:winner AS jsonb) WHERE subject_ref=:subject AND bucket_start=:bucket"""),
                              {"subject": subject, "bucket": boundary, "winner": json.dumps(corrected)})
            connection.execute(text("UPDATE analytics.activity_projection_day_sources SET state='ready' WHERE subject_ref=:subject"),
                              {"subject": subject})
            connection.execute(text("""UPDATE analytics.activity_projection_days p SET generation=s.generation
                FROM analytics.activity_projection_day_sources s
                WHERE (p.subject_ref,p.local_day,p.metric_version)=(s.subject_ref,s.local_day,s.metric_version)
                  AND p.subject_ref=:subject"""), {"subject": subject})
        asyncio.run(run())
        with engine.begin() as connection:
            assert connection.execute(text("SELECT credits FROM analytics.activity_credit_windows ORDER BY bucket_start")).scalars().all() == [[], []]
            assert connection.execute(text("SELECT credits FROM analytics.activity_credit_days ORDER BY local_day")).scalars().all() == [[], []]
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
            connection.execute(text("DELETE FROM analytics.activity_projection_buckets WHERE subject_ref=:subject"), {"subject": subject})
            connection.execute(text("DELETE FROM analytics.activity_projection_day_sources WHERE subject_ref=:subject"), {"subject": subject})
            assert connection.execute(text("SELECT count(*) FROM analytics.activity_credit_windows")).scalar_one() == 0
            assert connection.execute(text("SELECT count(*) FROM analytics.activity_credit_days")).scalar_one() == 0
    finally:
        engine.dispose()


def test_boundary_credit_after_intake_retry_matches_uninterrupted_processing(activity_day_projection_target):
    _, _, urls, run_id, _ = activity_day_projection_target
    upgrade(Settings(database_url=urls["migrate"]), target_revision="a013")
    boundary = datetime(2026, 1, 1, 21, tzinfo=timezone.utc)
    unrelated = boundary + timedelta(days=3)
    control, recovered = uuid4(), uuid4()

    class BeforeCommitFailure(PostgresIntake):
        async def _before_commit(self):
            raise IntakeUnavailable("controlled rollback")

    class AfterCommitFailure(PostgresIntake):
        async def _after_commit(self):
            raise IntakeUnavailable("controlled lost ACK")

    async def run():
        collector, processor, observer = [
            Database(Settings(database_url=urls[role], db_timeout_seconds=5))
            for role in ("collector", "processor", "migrate")
        ]
        intake = PostgresIntake(collector)
        buckets = PostgresActivityProjection(processor, expected_run_id=run_id)
        days = PostgresActivityDayProjection(processor, expected_run_id=run_id)

        async def accept(store, event):
            return await store.accept(event, deadline=time.monotonic() + 5)

        async def publish():
            await helpers.drain(buckets)
            await _drain_day(days)
            for _ in range(24):
                if await boundary_credit_step(processor, run_id=run_id, deadline=time.monotonic() + 5) == "idle":
                    return
            pytest.fail("boundary credits did not drain")

        async def snapshot(subject):
            async with observer.transaction() as session:
                return (await session.execute(text("""
                    SELECT d.local_day, d.generation AS day_generation, d.contributions,
                           c.generation AS credit_generation, c.state, c.credits
                    FROM analytics.activity_projection_days d
                    JOIN analytics.activity_credit_days c USING (subject_ref, local_day, metric_version)
                    WHERE d.subject_ref=:subject ORDER BY d.local_day
                """), {"subject": subject})).all()

        async def untouched_window():
            async with observer.transaction() as session:
                return (await session.execute(text("""
                    SELECT b.generation, b.contributions, c.generation, c.state, c.credits
                    FROM analytics.activity_projection_buckets b
                    JOIN analytics.activity_credit_windows c USING (subject_ref, bucket_start, metric_version)
                    WHERE b.subject_ref=:subject AND b.bucket_start=:start
                """), {"subject": recovered, "start": unrelated})).one()

        try:
            for offset, subject in enumerate((control, recovered)):
                opening = g.event(boundary - timedelta(seconds=20), boundary, number=101 + offset, subject=subject)
                other_day = g.event(unrelated, unrelated + timedelta(seconds=10), number=201 + offset, subject=subject)
                assert await accept(intake, opening) == await accept(intake, other_day) == "accepted"
            await publish()
            before = await snapshot(recovered)
            window_before = await untouched_window()
            assert len(before) == 2 and all(row.day_generation >= 1 and row.state == "ready" for row in before)

            clean_event = g.event(boundary, boundary + timedelta(seconds=20), number=301, subject=control)
            retried_event = g.event(boundary, boundary + timedelta(seconds=20), number=302, subject=recovered)
            assert await accept(intake, clean_event) == "accepted"
            with pytest.raises(IntakeUnavailable, match="controlled rollback"):
                await accept(BeforeCommitFailure(collector), retried_event)
            assert await snapshot(recovered) == before
            with pytest.raises(IntakeUnavailable, match="controlled lost ACK"):
                await accept(AfterCommitFailure(collector), retried_event)
            assert await accept(intake, retried_event) == "duplicate"

            await publish()
            clean_rows, recovered_rows = await snapshot(control), await snapshot(recovered)
            assert len(clean_rows) == len(recovered_rows) == 3
            assert [(row[0], row[2], row[5]) for row in clean_rows] == [
                (row[0], row[2], row[5]) for row in recovered_rows
            ]
            assert [row.credits[0]["active_ms"] for row in recovered_rows[:2]] == [40_000, 40_000]
            assert recovered_rows[-1] == before[-1]
            assert await untouched_window() == window_before
            assert await accept(intake, retried_event) == "duplicate"
            await publish()
            assert await snapshot(recovered) == recovered_rows
        finally:
            await observer.close()
            await processor.close()
            await collector.close()

    asyncio.run(run())
