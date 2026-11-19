"""Bounded subject/day projection store with generation and privacy fences."""
import asyncio
from dataclasses import replace
from datetime import datetime, time as day_time, timedelta, timezone
import json
import time
from uuid import UUID, uuid4

from sqlalchemy import text

from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.modules.analytics.activity.day import MAX_BUCKETS, MAX_BYTES, MAX_ITEMS, _normalize, normalize_day
from pymentor_analytics.modules.analytics.activity.day_ports import DayClaim, DaySourceNotReady, StaleDayClaim
from pymentor_analytics.modules.analytics.activity.projection_ports import METRIC_VERSION, ProjectionLimit
from pymentor_analytics.modules.collector.ports import IntakeUnavailable


async def has_activity_day_projection_schema(session):
    row = (await session.execute(text("""SELECT
        to_regclass('analytics.activity_projection_day_sources') IS NOT NULL,
        to_regclass('analytics.activity_projection_days') IS NOT NULL,
        to_regclass('analytics.activity_projection_day_backfill') IS NOT NULL"""))).one()
    if len(set(row)) != 1:
        raise IntakeUnavailable("incomplete activity day projection schema")
    return row[0]


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise IntakeUnavailable("activity day deadline exceeded")
    return remaining


def _payload(value):
    keys = {"contributions", "presence_labels", "source_bucket_count", "source_compact_items", "source_compact_bytes"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("invalid day projection")
    for key, maximum in (("source_bucket_count", MAX_BUCKETS), ("source_compact_items", MAX_ITEMS), ("source_compact_bytes", MAX_BYTES)):
        if type(value[key]) is not int or not 0 <= value[key] <= maximum:
            raise ProjectionLimit("invalid day source bound")
    contributions, labels = value["contributions"], value["presence_labels"]
    if not isinstance(contributions, list) or not isinstance(labels, list):
        raise ValueError("invalid day projection arrays")
    normalized = _normalize([{"contributions": contributions, "presence_labels": labels,
        "compact_item_count": len(contributions) + len(labels), "compact_json_bytes": 0}],
        bucket_duration_limit=86_400_000)
    if normalized["contributions"] != contributions or normalized["presence_labels"] != labels:
        raise ValueError("noncanonical day projection")
    if len(contributions) + len(labels) > value["source_compact_items"]:
        raise ValueError("invalid source item count")
    if value["source_bucket_count"] == 0 and (contributions or labels or value["source_compact_items"] or value["source_compact_bytes"]):
        raise ValueError("invalid empty day")
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class PostgresActivityDayProjection:
    def __init__(self, database, *, expected_run_id):
        if not isinstance(expected_run_id, UUID):
            raise ValueError("valid dataset run ID required")
        self.database, self.expected_run_id = database, expected_run_id

    async def _run(self, session):
        row = (await session.execute(text("SELECT run_id,phase FROM analytics.activity_projection_backfill WHERE singleton=true FOR SHARE"))).one_or_none()
        if row is None or row.run_id != self.expected_run_id or row.phase != 'complete':
            raise IntakeUnavailable("activity dataset generation unavailable")

    async def _state(self, session):
        row = (await session.execute(text("SELECT run_id,phase,cursor_subject,cursor_bucket,cursor_metric FROM analytics.activity_projection_day_backfill WHERE singleton=true"))).one_or_none()
        if row is None or row.run_id != self.expected_run_id:
            raise IntakeUnavailable("day backfill generation unavailable")
        return row

    async def claim(self, owner, *, deadline):
        if not isinstance(owner, UUID):
            raise ValueError("invalid worker identity")
        async with asyncio.timeout(_remaining(deadline)):
            async with self.database.transaction() as session:
                await self._run(session)
                await self._state(session)
                row = (await session.execute(text("""SELECT s.subject_ref,s.local_day,s.generation,s.state,s.attempts
                  FROM analytics.activity_projection_day_sources s WHERE s.metric_version=:metric AND
                  ((s.state='dirty' AND s.next_attempt_at<=clock_timestamp() AND NOT EXISTS (
                     SELECT 1 FROM analytics.activity_projection_buckets b
                     WHERE b.subject_ref=s.subject_ref AND b.metric_version=s.metric_version
                       AND b.bucket_start >= ((s.local_day::timestamp - interval '3 hours') AT TIME ZONE 'UTC')
                       AND b.bucket_start < ((s.local_day::timestamp - interval '3 hours') AT TIME ZONE 'UTC') + interval '1 day'
                       AND b.state IN ('dirty','claimed'))) OR
                   (s.state='claimed' AND s.lease_expires_at<=clock_timestamp()))
                  ORDER BY s.next_attempt_at,s.local_day,s.subject_ref LIMIT 1 FOR UPDATE OF s SKIP LOCKED"""), {"metric": METRIC_VERSION})).one_or_none()
                if row is None:
                    return None
                args = {"subject": row.subject_ref, "day": row.local_day, "metric": METRIC_VERSION, "owner": owner,
                        "claim": uuid4(), "run": self.expected_run_id, "attempts": row.attempts + int(row.state == 'claimed')}
                if args["attempts"] >= 3:
                    await session.execute(text("""UPDATE analytics.activity_projection_day_sources SET state='quarantined',attempts=:attempts,
                      last_failure='lease_expired',owner=NULL,claim_id=NULL,lease_expires_at=NULL,claim_generation=NULL,claim_epoch=NULL,claim_run_id=NULL
                      WHERE subject_ref=:subject AND local_day=:day AND metric_version=:metric"""), args)
                    return None
                await session.execute(text("""UPDATE analytics.activity_projection_day_sources SET state='claimed',owner=:owner,claim_id=:claim,
                  claim_generation=generation,claim_epoch=NULL,claim_run_id=:run,attempts=:attempts,
                  lease_expires_at=clock_timestamp()+interval '30 seconds' WHERE subject_ref=:subject AND local_day=:day AND metric_version=:metric"""), args)
                return DayClaim(row.subject_ref, row.local_day, row.generation, None, args["claim"], owner, self.expected_run_id)

    async def _locked(self, session, claim):
        if not isinstance(claim, DayClaim) or claim.run_id != self.expected_run_id:
            raise StaleDayClaim()
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(claim.subject_ref)})
        await self._run(session)
        await self._state(session)
        args = {"subject": claim.subject_ref, "day": claim.local_day, "metric": METRIC_VERSION, "generation": claim.generation,
                "owner": claim.owner, "claim": claim.claim_id, "run": claim.run_id}
        fence = (await session.execute(text("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=:subject"), args)).one_or_none()
        if fence and fence.deleted:
            raise StaleDayClaim()
        epoch = fence.epoch if fence else 0
        row = (await session.execute(text("""SELECT generation,state,claim_generation,claim_epoch,claim_run_id,claim_id,owner,
          lease_expires_at>clock_timestamp() AS active FROM analytics.activity_projection_day_sources
          WHERE subject_ref=:subject AND local_day=:day AND metric_version=:metric FOR UPDATE"""), args)).one_or_none()
        if (row is None or row.state != 'claimed' or row.generation != claim.generation or row.claim_generation != claim.generation
                or row.claim_run_id != claim.run_id or row.claim_id != claim.claim_id or row.owner != claim.owner or not row.active
                or (row.claim_epoch is not None and row.claim_epoch != epoch)
                or (claim.subject_epoch is not None and claim.subject_epoch != epoch)):
            raise StaleDayClaim()
        return args, epoch

    async def load(self, claim, *, deadline):
        pending = False
        async with asyncio.timeout(_remaining(deadline)):
            async with self.database.transaction() as session:
                args, epoch = await self._locked(session, claim)
                lower = datetime.combine(claim.local_day, day_time.min, tzinfo=timezone(timedelta(hours=3))).astimezone(timezone.utc)
                args.update(lower=lower, upper=lower + timedelta(days=1), epoch=epoch)
                rows = (await session.execute(text("""SELECT bucket_start,state,generation,projection_generation,subject_epoch,
                  compact_item_count,compact_json_bytes FROM analytics.activity_projection_buckets
                  WHERE subject_ref=:subject AND metric_version=:metric AND bucket_start>=:lower AND bucket_start<:upper
                  ORDER BY bucket_start LIMIT 97"""), args)).all()
                if len(rows) > MAX_BUCKETS:
                    raise ProjectionLimit("day bucket limit exceeded")
                if any(r.state == 'quarantined' for r in rows):
                    raise ValueError("quarantined source bucket")
                if any(r.state != 'ready' for r in rows):
                    await session.execute(text("""UPDATE analytics.activity_projection_day_sources SET state='dirty',
                      next_attempt_at=clock_timestamp()+interval '1 second',owner=NULL,claim_id=NULL,lease_expires_at=NULL,
                      claim_generation=NULL,claim_epoch=NULL,claim_run_id=NULL WHERE subject_ref=:subject AND local_day=:day AND metric_version=:metric"""), args)
                    pending = True
                else:
                    if any(r.projection_generation != r.generation or r.subject_epoch != epoch or r.compact_item_count is None or r.compact_json_bytes is None
                           or r.compact_item_count < 0 or r.compact_json_bytes < 0 for r in rows):
                        raise ValueError("invalid ready bucket metadata")
                    if sum(r.compact_item_count for r in rows) > MAX_ITEMS or sum(r.compact_json_bytes for r in rows) > MAX_BYTES:
                        raise ProjectionLimit("day source limit exceeded")
                    payloads = (await session.execute(text("""SELECT contributions,presence_labels,compact_item_count,compact_json_bytes
                      FROM analytics.activity_projection_buckets WHERE subject_ref=:subject AND metric_version=:metric
                      AND bucket_start>=:lower AND bucket_start<:upper ORDER BY bucket_start LIMIT 97"""), args)).all()
                    if len(payloads) != len(rows):
                        raise StaleDayClaim()
                    records = tuple(dict(r._mapping) for r in payloads)
                    await session.execute(text("""UPDATE analytics.activity_projection_day_sources SET claim_epoch=:epoch
                      WHERE subject_ref=:subject AND local_day=:day AND metric_version=:metric"""), args)
            if pending:
                raise DaySourceNotReady()
            return replace(claim, subject_epoch=epoch), records

    async def commit(self, claim, projection, *, deadline):
        if claim.subject_epoch is None:
            raise StaleDayClaim()
        _remaining(deadline)
        raw = _payload(projection)
        async with asyncio.timeout(_remaining(deadline)):
            async with self.database.transaction() as session:
                args, epoch = await self._locked(session, claim)
                args.update(epoch=epoch, body=raw)
                size = (await session.execute(text("""SELECT octet_length((CAST(:body AS jsonb)->'contributions')::text)::bigint +
                  octet_length((CAST(:body AS jsonb)->'presence_labels')::text)::bigint"""), args)).scalar_one()
                if size > MAX_BYTES:
                    raise ProjectionLimit("day database representation exceeds bound")
                await session.execute(text("""INSERT INTO analytics.activity_projection_days(subject_ref,local_day,metric_version,generation,subject_epoch,run_id,
                  contributions,presence_labels,source_bucket_count,source_compact_items,source_compact_bytes)
                  VALUES(:subject,:day,:metric,:generation,:epoch,:run,CAST(:body AS jsonb)->'contributions',CAST(:body AS jsonb)->'presence_labels',
                    (CAST(:body AS jsonb)->>'source_bucket_count')::bigint,(CAST(:body AS jsonb)->>'source_compact_items')::bigint,
                    (CAST(:body AS jsonb)->>'source_compact_bytes')::bigint)
                  ON CONFLICT(subject_ref,local_day,metric_version) DO UPDATE SET generation=EXCLUDED.generation,subject_epoch=EXCLUDED.subject_epoch,
                    run_id=EXCLUDED.run_id,contributions=EXCLUDED.contributions,presence_labels=EXCLUDED.presence_labels,
                    source_bucket_count=EXCLUDED.source_bucket_count,source_compact_items=EXCLUDED.source_compact_items,source_compact_bytes=EXCLUDED.source_compact_bytes"""), args)
                result = await session.execute(text("""UPDATE analytics.activity_projection_day_sources SET state='ready',owner=NULL,claim_id=NULL,
                  lease_expires_at=NULL,claim_generation=NULL,claim_epoch=NULL,claim_run_id=NULL,attempts=0,last_failure=NULL
                  WHERE subject_ref=:subject AND local_day=:day AND metric_version=:metric AND state='claimed' AND generation=:generation
                  AND claim_generation=:generation AND claim_epoch=:epoch AND claim_run_id=:run AND claim_id=:claim AND owner=:owner
                  AND lease_expires_at>clock_timestamp() RETURNING generation"""), args)
                if result.scalar_one_or_none() is None:
                    raise StaleDayClaim()

    async def fail(self, claim, reason, *, deadline):
        if reason not in {'input_limit', 'invalid_fact', 'compute_timeout', 'transient'}:
            raise ValueError("invalid day failure")
        async with asyncio.timeout(_remaining(deadline)):
            async with self.database.transaction() as session:
                args, _ = await self._locked(session, claim)
                await session.execute(text("""UPDATE analytics.activity_projection_day_sources SET attempts=attempts+1,
                  state=CASE WHEN :reason IN ('input_limit','invalid_fact') OR attempts+1>=3 THEN 'quarantined' ELSE 'dirty' END,
                  last_failure=:reason,next_attempt_at=clock_timestamp()+((LEAST(60,power(2,LEAST(attempts,5))::int)*1000
                    + get_byte(uuid_send(:claim),0)%250)*interval '1 millisecond'),
                  owner=NULL,claim_id=NULL,lease_expires_at=NULL,claim_generation=NULL,claim_epoch=NULL,claim_run_id=NULL
                  WHERE subject_ref=:subject AND local_day=:day AND metric_version=:metric"""), {**args, "reason": reason})

    async def backfill_step(self, *, deadline):
        async with asyncio.timeout(_remaining(deadline)):
            # Initialization is separate so no transaction holds a day row while
            # acquiring numeric subject locks in the scanning transaction.
            async with self.database.transaction() as session:
                await session.execute(text("SELECT analytics.begin_activity_day_backfill(:run)"), {"run": self.expected_run_id})
            async with self.database.transaction() as session:
                state = await self._state(session)
                if state.phase == 'complete':
                    await self._run(session)
                    return 'complete'
                if state.phase == 'draining':
                    await self._run(session)
                    locked = (await session.execute(text("""SELECT run_id,phase FROM analytics.activity_projection_day_backfill
                      WHERE singleton=true FOR UPDATE SKIP LOCKED"""))).one_or_none()
                    if locked is None or locked.run_id != self.expected_run_id or locked.phase != 'draining':
                        return 'busy'
                    changed = (await session.execute(text("""UPDATE analytics.activity_projection_day_backfill SET phase='complete'
                      WHERE singleton=true AND phase='draining' AND run_id=:run
                      AND NOT EXISTS(SELECT 1 FROM analytics.activity_projection_day_sources WHERE state<>'ready') RETURNING phase"""),
                      {"run": self.expected_run_id})).scalar_one_or_none()
                    return 'complete' if changed else 'draining'
                stale = (await session.execute(text("""SELECT s.subject_ref,s.local_day FROM analytics.activity_projection_day_sources s
                  LEFT JOIN analytics.activity_projection_days d USING(subject_ref,local_day,metric_version)
                  WHERE s.state='ready' AND (d.subject_ref IS NULL OR d.run_id IS DISTINCT FROM :run OR d.generation IS DISTINCT FROM s.generation)
                  ORDER BY s.subject_ref,s.local_day LIMIT 100"""), {"run": self.expected_run_id})).all()
                if stale:
                    for key in sorted({subject_lock_key(row.subject_ref) for row in stale}):
                        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
                    await self._run(session)
                    for row in stale:
                        await session.execute(text("SELECT analytics.invalidate_stale_activity_day(:subject,:day,:run)"),
                                              {"subject": row.subject_ref, "day": row.local_day, "run": self.expected_run_id})
                    return 'scanning'
                after = " AND (subject_ref,bucket_start,metric_version)>(:subject,:bucket,:metric)" if state.cursor_subject is not None else ""
                args = {"subject": state.cursor_subject, "bucket": state.cursor_bucket, "metric": state.cursor_metric}
                rows = (await session.execute(text("""SELECT subject_ref,bucket_start,metric_version FROM analytics.activity_projection_buckets
                    WHERE true""" + after + " ORDER BY subject_ref,bucket_start,metric_version LIMIT 100"), args)).all()
                for key in sorted({subject_lock_key(row.subject_ref) for row in rows}):
                    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
                await self._run(session)
                locked = (await session.execute(text("""SELECT run_id,phase,cursor_subject,cursor_bucket,cursor_metric
                  FROM analytics.activity_projection_day_backfill WHERE singleton=true FOR UPDATE SKIP LOCKED"""))).one_or_none()
                if (locked is None or locked.run_id != state.run_id or locked.phase != 'scanning'
                        or (locked.cursor_subject, locked.cursor_bucket, locked.cursor_metric) !=
                           (state.cursor_subject, state.cursor_bucket, state.cursor_metric)):
                    return 'busy'
                for row in rows:
                    await session.execute(text("SELECT analytics.register_activity_day(:subject,:bucket,:metric,:run)"),
                        {"subject": row.subject_ref, "bucket": row.bucket_start, "metric": row.metric_version, "run": self.expected_run_id})
                if rows:
                    last = rows[-1]
                    await session.execute(text("""UPDATE analytics.activity_projection_day_backfill SET
                      cursor_subject=:subject,cursor_bucket=:bucket,cursor_metric=:metric WHERE singleton=true"""),
                      {"subject": last.subject_ref, "bucket": last.bucket_start, "metric": last.metric_version})
                if len(rows) < 100:
                    await session.execute(text("UPDATE analytics.activity_projection_day_backfill SET phase='draining' WHERE singleton=true"))
                    return 'draining'
                return 'scanning'

    async def reconciliation_step(self, *, after=None, deadline):
        """Read at most 100 bucket keys; report missing day sources and a cursor.

        A caller must run to exhaustion, stop enabling Query on any mismatch,
        and restart after an uncertain result or dataset run change.
        """
        if after is not None and (not isinstance(after, tuple) or len(after) != 3):
            raise ValueError("invalid reconciliation cursor")
        async with asyncio.timeout(_remaining(deadline)):
            async with self.database.transaction() as session:
                await self._run(session)
                state = await self._state(session)
                if state.phase != 'complete':
                    raise IntakeUnavailable("day backfill incomplete")
                predicate = "WHERE (b.subject_ref,b.bucket_start,b.metric_version)>(:subject,:bucket,:metric)" if after else ""
                args = {"subject": after[0], "bucket": after[1], "metric": after[2]} if after else {}
                rows = (await session.execute(text("""SELECT b.subject_ref,b.bucket_start,b.metric_version,
                  s.subject_ref IS NULL AS missing FROM analytics.activity_projection_buckets b
                  LEFT JOIN analytics.activity_projection_day_sources s ON s.subject_ref=b.subject_ref
                    AND s.local_day=(b.bucket_start AT TIME ZONE 'UTC' + interval '3 hours')::date AND s.metric_version=b.metric_version
                  """ + predicate + " ORDER BY b.subject_ref,b.bucket_start,b.metric_version LIMIT 100"), args)).all()
                missing = tuple((r.subject_ref, r.bucket_start, r.metric_version) for r in rows if r.missing)
                cursor = (rows[-1].subject_ref, rows[-1].bucket_start, rows[-1].metric_version) if len(rows) == 100 else None
                return cursor, missing
