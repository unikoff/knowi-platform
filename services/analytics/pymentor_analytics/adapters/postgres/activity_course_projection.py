"""Discovery of the optional, additive course materialization schema."""
from sqlalchemy import text
import asyncio
import time
from uuid import UUID
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.modules.collector.ports import IntakeUnavailable


async def has_activity_course_projection_schema(session):
    row = (await session.execute(text("""SELECT
        to_regclass('analytics.activity_course_buckets') IS NOT NULL,
        to_regclass('analytics.activity_course_days') IS NOT NULL"""))).one()
    if row[0] != row[1]:
        raise IntakeUnavailable("incomplete course projection schema")
    return row[0]


async def backfill_course_projection_step(database, *, run_id, deadline):
    """Publish one existing ready source under its ordinary subject fence.

    Call repeatedly until 'complete'. This does not mark sources dirty or change
    measured values. A concurrently invalidated source is left to its worker.
    """
    if not isinstance(run_id, UUID):
        raise ValueError("valid run ID required")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise IntakeUnavailable()
    async with asyncio.timeout(remaining):
        async with database.transaction() as session:
            state = (await session.execute(text("""SELECT run_id,phase FROM analytics.activity_projection_backfill
                WHERE singleton=true FOR SHARE"""))).one_or_none()
            if state is None or state.run_id != run_id or state.phase != 'complete':
                raise IntakeUnavailable("activity dataset not ready")
            bucket = (await session.execute(text("""SELECT subject_ref,bucket_start,metric_version
                FROM analytics.activity_projection_buckets WHERE state='ready'
                AND (course_projection_generation IS DISTINCT FROM generation OR course_projection_run_id IS DISTINCT FROM :run)
                ORDER BY subject_ref,bucket_start,metric_version LIMIT 1"""), {"run": run_id})).one_or_none()
            if bucket:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(bucket.subject_ref)})
                await session.execute(text("""UPDATE analytics.activity_projection_buckets SET projection_generation=projection_generation
                    WHERE subject_ref=:subject AND bucket_start=:bucket AND metric_version=:metric AND state='ready'
                    AND NOT EXISTS(SELECT 1 FROM analytics.subject_fences WHERE subject_ref=:subject AND deleted)"""),
                    {"subject": bucket.subject_ref, "bucket": bucket.bucket_start, "metric": bucket.metric_version})
                return "bucket"
            day = (await session.execute(text("""SELECT d.subject_ref,d.local_day,d.metric_version
                FROM analytics.activity_projection_days d JOIN analytics.activity_projection_day_sources s
                USING(subject_ref,local_day,metric_version)
                WHERE s.state='ready' AND d.generation=s.generation AND d.run_id=:run
                AND (d.course_projection_generation IS DISTINCT FROM d.generation OR d.course_projection_run_id IS DISTINCT FROM :run)
                ORDER BY d.subject_ref,d.local_day,d.metric_version LIMIT 1"""), {"run": run_id})).one_or_none()
            if day:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(day.subject_ref)})
                await session.execute(text("""UPDATE analytics.activity_projection_days d SET generation=d.generation
                    FROM analytics.activity_projection_day_sources s
                    WHERE d.subject_ref=:subject AND d.local_day=:day AND d.metric_version=:metric
                    AND (s.subject_ref,s.local_day,s.metric_version)=(d.subject_ref,d.local_day,d.metric_version)
                    AND s.state='ready' AND d.generation=s.generation AND d.run_id=:run
                    AND NOT EXISTS(SELECT 1 FROM analytics.subject_fences WHERE subject_ref=:subject AND deleted)"""),
                    {"subject": day.subject_ref, "day": day.local_day, "metric": day.metric_version, "run": run_id})
                return "day"
            pending = (await session.execute(text("""SELECT
                EXISTS(SELECT 1 FROM analytics.activity_projection_buckets WHERE state<>'ready') OR
                EXISTS(SELECT 1 FROM analytics.activity_projection_day_sources WHERE state<>'ready') OR
                NOT EXISTS(SELECT 1 FROM analytics.activity_projection_day_backfill
                           WHERE singleton=true AND run_id=:run AND phase='complete')"""), {"run": run_id})).scalar_one()
            return "draining" if pending else "complete"
