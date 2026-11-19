"""Bounded transactional boundary credit over canonical measured buckets."""
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import time
from uuid import UUID

from sqlalchemy import text
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.modules.analytics.activity.boundary_credit import WINDOW_MS, window_boundary_credit
from pymentor_analytics.modules.collector.ports import IntakeUnavailable

VERSION = "boundary-v1"


async def boundary_credit_step(database, *, run_id, deadline):
    """Reconcile at most one window or day, keeping subject and run fenced."""
    if not isinstance(run_id, UUID):
        raise ValueError("valid run ID required")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise IntakeUnavailable()
    async with asyncio.timeout(remaining):
        async with database.transaction() as session:
            # Discover without row locks: all actual work locks the subject first.
            candidate = (await session.execute(text("""SELECT b.subject_ref,b.bucket_start,b.metric_version
              FROM analytics.activity_credit_windows c JOIN analytics.activity_projection_buckets b
                USING(subject_ref,bucket_start,metric_version)
              LEFT JOIN analytics.subject_fences f ON f.subject_ref=b.subject_ref
              WHERE c.state='dirty' AND c.credit_version='boundary-v1' AND NOT COALESCE(f.deleted,false)
                AND NOT EXISTS(SELECT 1 FROM analytics.activity_projection_buckets n
                  WHERE n.subject_ref=b.subject_ref AND n.metric_version=b.metric_version
                    AND n.bucket_start BETWEEN b.bucket_start-interval '30 minutes' AND b.bucket_start+interval '15 minutes'
                    AND (n.state<>'ready' OR n.projection_generation IS DISTINCT FROM n.generation
                         OR n.subject_epoch IS DISTINCT FROM COALESCE(f.epoch,0)))
              ORDER BY b.subject_ref,b.bucket_start,b.metric_version LIMIT 1"""))).one_or_none()
            if candidate is None:
                # Existing a010 datasets predate the queue; discover them in
                # bounded steps without treating missing rows as zero credit.
                candidate = (await session.execute(text("""SELECT b.subject_ref,b.bucket_start,b.metric_version
                  FROM analytics.activity_projection_buckets b LEFT JOIN analytics.activity_credit_windows c ON
                    (c.subject_ref,c.bucket_start,c.metric_version,c.credit_version)=
                    (b.subject_ref,b.bucket_start,b.metric_version,'boundary-v1')
                  LEFT JOIN analytics.subject_fences f ON f.subject_ref=b.subject_ref
                  WHERE NOT COALESCE(f.deleted,false)
                    AND (c.subject_ref IS NULL OR c.run_id IS DISTINCT FROM :run) AND
                    NOT EXISTS(SELECT 1 FROM analytics.activity_projection_buckets n
                      WHERE n.subject_ref=b.subject_ref AND n.metric_version=b.metric_version
                        AND n.bucket_start BETWEEN b.bucket_start-interval '30 minutes' AND b.bucket_start+interval '15 minutes'
                        AND (n.state<>'ready' OR n.projection_generation IS DISTINCT FROM n.generation
                             OR n.subject_epoch IS DISTINCT FROM COALESCE(f.epoch,0)))
                  ORDER BY b.subject_ref,b.bucket_start,b.metric_version LIMIT 1"""), {"run": run_id})).one_or_none()
            if candidate:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(candidate.subject_ref)})
                state = (await session.execute(text("""SELECT run_id,phase FROM analytics.activity_projection_backfill
                    WHERE singleton=true FOR SHARE"""))).one_or_none()
                if state is None or state.run_id != run_id or state.phase != 'complete':
                    raise IntakeUnavailable("activity dataset not ready")
                args = {"subject": candidate.subject_ref, "bucket": candidate.bucket_start,
                        "metric": candidate.metric_version, "run": run_id}
                fence = (await session.execute(text("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=:subject"), args)).one_or_none()
                if fence and fence.deleted:
                    return "retry"
                epoch = fence.epoch if fence else 0
                already_ready = (await session.execute(text("""SELECT EXISTS(
                    SELECT 1 FROM analytics.activity_credit_windows WHERE subject_ref=:subject AND bucket_start=:bucket
                      AND metric_version=:metric AND credit_version='boundary-v1' AND state='ready'
                      AND run_id=:run AND subject_epoch=:epoch)"""), {**args, "epoch": epoch})).scalar_one()
                if already_ready:
                    return "retry"
                rows = (await session.execute(text("""SELECT bucket_start,state,generation,projection_generation,subject_epoch,winner_intervals
                    FROM analytics.activity_projection_buckets WHERE subject_ref=:subject AND metric_version=:metric
                      AND bucket_start BETWEEN CAST(:bucket AS timestamptz)-interval '30 minutes'
                                           AND CAST(:bucket AS timestamptz)+interval '15 minutes'
                    ORDER BY bucket_start"""), args)).all()
                by_start = {row.bucket_start: row for row in rows}
                if candidate.bucket_start not in by_start:
                    return "retry"
                snapshots = {}
                start_ms = (candidate.bucket_start - datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(milliseconds=1)
                for offset in (-2, -1, 0, 1):
                    row = by_start.get(candidate.bucket_start + timedelta(minutes=15 * offset))
                    if row is None:
                        value = {"state": "absent"}
                    elif row.state != 'ready' or row.projection_generation != row.generation or row.subject_epoch != epoch:
                        return "retry"
                    else:
                        value = {"state": "ready", "winners": row.winner_intervals}
                    snapshots[start_ms + offset * WINDOW_MS] = value
                result = window_boundary_credit(start_ms, snapshots)
                args.update(epoch=epoch, credits=json.dumps(result["boundary_credit"], separators=(",", ":")))
                await session.execute(text("""INSERT INTO analytics.activity_credit_windows
                    (subject_ref,bucket_start,metric_version,credit_version,generation,state,subject_epoch,run_id,credits)
                    VALUES(:subject,:bucket,:metric,'boundary-v1',1,'ready',:epoch,:run,CAST(:credits AS jsonb))
                    ON CONFLICT(subject_ref,bucket_start,metric_version,credit_version) DO UPDATE SET
                    state='ready',subject_epoch=:epoch,run_id=:run,credits=CAST(:credits AS jsonb)"""), args)
                await session.execute(text("""INSERT INTO analytics.activity_credit_days
                    (subject_ref,local_day,metric_version,credit_version,generation,state)
                    SELECT subject_ref,local_day,metric_version,'boundary-v1',1,'dirty'
                    FROM analytics.activity_projection_day_sources WHERE subject_ref=:subject AND metric_version=:metric
                      AND local_day=(CAST(:bucket AS timestamptz) AT TIME ZONE 'UTC'+interval '3 hours')::date
                    ON CONFLICT(subject_ref,local_day,metric_version,credit_version) DO UPDATE SET
                      generation=analytics.activity_credit_days.generation+1,state='dirty'"""), args)
                return "window"
            day = (await session.execute(text("""SELECT d.subject_ref,d.local_day,d.metric_version
              FROM analytics.activity_projection_day_sources d LEFT JOIN analytics.activity_credit_days c ON
                (c.subject_ref,c.local_day,c.metric_version,c.credit_version)=
                (d.subject_ref,d.local_day,d.metric_version,'boundary-v1')
              JOIN analytics.activity_projection_days p ON
                (p.subject_ref,p.local_day,p.metric_version)=(d.subject_ref,d.local_day,d.metric_version)
              LEFT JOIN analytics.subject_fences f ON f.subject_ref=d.subject_ref
              WHERE d.state='ready' AND NOT COALESCE(f.deleted,false)
                AND p.generation=d.generation AND p.run_id=:run AND p.subject_epoch=COALESCE(f.epoch,0)
                AND (c.subject_ref IS NULL OR c.state='dirty' OR c.run_id IS DISTINCT FROM :run)
              AND NOT EXISTS(SELECT 1 FROM analytics.activity_projection_buckets b
                LEFT JOIN analytics.activity_credit_windows w ON
                  (w.subject_ref,w.bucket_start,w.metric_version,w.credit_version)=
                  (b.subject_ref,b.bucket_start,b.metric_version,'boundary-v1')
                WHERE b.subject_ref=d.subject_ref AND b.metric_version=d.metric_version
                  AND b.bucket_start >= (CAST(d.local_day AS timestamp)-interval '3 hours') AT TIME ZONE 'UTC'
                  AND b.bucket_start < (CAST(d.local_day+1 AS timestamp)-interval '3 hours') AT TIME ZONE 'UTC'
                  AND (b.state<>'ready' OR b.projection_generation IS DISTINCT FROM b.generation
                       OR b.subject_epoch IS DISTINCT FROM COALESCE(f.epoch,0)
                       OR w.state IS DISTINCT FROM 'ready' OR w.run_id IS DISTINCT FROM :run
                       OR w.subject_epoch IS DISTINCT FROM COALESCE(f.epoch,0)))
              ORDER BY d.subject_ref,d.local_day,d.metric_version LIMIT 1"""), {"run": run_id})).one_or_none()
            if not day:
                return "idle"
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(day.subject_ref)})
            state = (await session.execute(text("SELECT run_id,phase FROM analytics.activity_projection_backfill WHERE singleton=true FOR SHARE"))).one_or_none()
            if state is None or state.run_id != run_id or state.phase != 'complete':
                raise IntakeUnavailable("activity dataset not ready")
            args = {"subject": day.subject_ref, "day": day.local_day, "metric": day.metric_version, "run": run_id}
            fence = (await session.execute(text("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=:subject"), args)).one_or_none()
            if fence and fence.deleted:
                return "retry"
            epoch = fence.epoch if fence else 0
            already_ready = (await session.execute(text("""SELECT EXISTS(
                SELECT 1 FROM analytics.activity_credit_days WHERE subject_ref=:subject AND local_day=:day
                  AND metric_version=:metric AND credit_version='boundary-v1' AND state='ready'
                  AND run_id=:run AND subject_epoch=:epoch)"""), {**args, "epoch": epoch})).scalar_one()
            if already_ready:
                return "retry"
            source = (await session.execute(text("""SELECT s.state,d.generation=s.generation AS current,d.run_id,d.subject_epoch
              FROM analytics.activity_projection_day_sources s LEFT JOIN analytics.activity_projection_days d
              USING(subject_ref,local_day,metric_version) WHERE s.subject_ref=:subject AND s.local_day=:day AND s.metric_version=:metric"""), args)).one_or_none()
            if not source or source.state != 'ready' or not source.current or source.run_id != run_id or source.subject_epoch != epoch:
                return "retry"
            rows = (await session.execute(text("""SELECT b.state,b.generation,b.projection_generation,b.subject_epoch,
                c.state AS credit_state,c.run_id,c.subject_epoch AS credit_epoch,c.credits
              FROM analytics.activity_projection_buckets b LEFT JOIN analytics.activity_credit_windows c ON
                (c.subject_ref,c.bucket_start,c.metric_version,c.credit_version)=
                (b.subject_ref,b.bucket_start,b.metric_version,'boundary-v1')
              WHERE b.subject_ref=:subject AND b.metric_version=:metric AND
                b.bucket_start>=(CAST(CAST(:day AS date) AS timestamp) - interval '3 hours') AT TIME ZONE 'UTC' AND
                b.bucket_start<(CAST(CAST(:day AS date)+1 AS timestamp) - interval '3 hours') AT TIME ZONE 'UTC'"""), args)).all()
            if any(r.state != 'ready' or r.generation != r.projection_generation or r.subject_epoch != epoch
                   or r.credit_state != 'ready' or r.run_id != run_id or r.credit_epoch != epoch for r in rows):
                return "retry"
            totals = defaultdict(int)
            for row in rows:
                for item in row.credits:
                    totals[(item["course_id"], item["lesson_id"], item["section"])] += item["active_ms"]
            credits = [{"course_id": c, "lesson_id": l, "section": s, "active_ms": n}
                       for (c, l, s), n in sorted(totals.items())]
            args.update(epoch=epoch, credits=json.dumps(credits, separators=(",", ":")))
            await session.execute(text("""INSERT INTO analytics.activity_credit_days
                (subject_ref,local_day,metric_version,credit_version,generation,state,subject_epoch,run_id,credits)
                VALUES(:subject,:day,:metric,'boundary-v1',1,'ready',:epoch,:run,CAST(:credits AS jsonb))
                ON CONFLICT(subject_ref,local_day,metric_version,credit_version) DO UPDATE SET
                  state='ready',subject_epoch=:epoch,run_id=:run,credits=CAST(:credits AS jsonb)"""), args)
            return "day"
