"""Bounded single-ledger activity buckets with lease/generation fencing."""
import asyncio
from dataclasses import replace
import json
import re
import time
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.v2 import AcceptedEvent, canonical_hash
from pymentor_analytics.modules.analytics.activity.projection_ports import (
    BUCKET_SECONDS, MAX_BUCKET_BYTES, MAX_BUCKET_FACTS, METRIC_VERSION,
    BucketClaim, ProjectionLimit, StaleBucketClaim,
)
from pymentor_analytics.modules.collector.ports import IntakeUnavailable

LEASE_SECONDS = 30
MAX_ATTEMPTS = 3
BACKFILL_PAGE = 100


async def has_activity_projection_schema(session):
    row = (await session.execute(text("""SELECT
        to_regclass('analytics.activity_projection_buckets') IS NOT NULL,
        to_regclass('analytics.activity_projection_backfill') IS NOT NULL,
        to_regprocedure('analytics.mark_activity_projection_fact(uuid,timestamp with time zone,timestamp with time zone,text)') IS NOT NULL,
        to_regprocedure('analytics.backfill_activity_projection_fact(text,uuid)') IS NOT NULL,
        to_regprocedure('analytics.activity_projection_fact_inserted()') IS NOT NULL,
        to_regprocedure('analytics.begin_activity_projection_backfill(uuid,uuid)') IS NOT NULL,
        to_regprocedure('analytics.lock_activity_projection_run(uuid)') IS NOT NULL,
        EXISTS(SELECT 1 FROM pg_catalog.pg_trigger WHERE tgrelid=to_regclass('analytics.activity_facts')
            AND tgname='activity_projection_fact_inserted' AND NOT tgisinternal AND tgenabled IN ('O','A')
            AND tgfoid=to_regprocedure('analytics.activity_projection_fact_inserted()') AND tgtype=5)"""))).one()
    if len(set(row)) != 1:
        raise IntakeUnavailable("incomplete activity projection schema")
    return row[0]


def _valid_projection(claim, value):
    keys = {"winner_intervals", "presence_intervals", "presence_points", "contributions", "presence_labels"}
    if not isinstance(value, dict) or set(value) != keys or any(not isinstance(value[k], list) for k in keys):
        raise ValueError("invalid bucket projection")
    compact_keys, edge_keys = ("contributions", "presence_labels"), ("winner_intervals", "presence_intervals", "presence_points")
    compact_count, edge_count = (sum(len(value[k]) for k in group) for group in (compact_keys, edge_keys))
    if compact_count > 30000 or edge_count > 40000:
        raise ProjectionLimit("bucket projection items exceed bound")
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode("utf-8")) > MAX_BUCKET_BYTES:
        raise ProjectionLimit("bucket projection exceeds bound")
    start = int(claim.bucket_start.timestamp()) * 1000
    end = start + BUCKET_SECONDS * 1000
    def identifier(value):
        return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is not None
    totals, labels, previous = {}, set(), start
    for item in value["winner_intervals"]:
        if (not isinstance(item, dict) or set(item) != {"start_ms", "end_ms", "course_id", "lesson_id", "section"}
                or type(item["start_ms"]) is not int or type(item["end_ms"]) is not int
                or not previous <= item["start_ms"] < item["end_ms"] <= end
                or not identifier(item["course_id"]) or not identifier(item["lesson_id"])
                or item["section"] not in {"theory", "practice"}):
            raise ValueError("invalid winner interval")
        previous = item["end_ms"]
        key = (item["course_id"], item["lesson_id"], item["section"])
        totals[key] = totals.get(key, 0) + item["end_ms"] - item["start_ms"]
    for item in value["presence_intervals"]:
        if (not isinstance(item, dict) or set(item) != {"start_ms", "end_ms", "course_id", "lesson_id", "section"}
                or type(item["start_ms"]) is not int or type(item["end_ms"]) is not int
                or not start <= item["start_ms"] < item["end_ms"] <= end
                or not identifier(item["course_id"]) or not identifier(item["lesson_id"])
                or item["section"] not in {"theory", "practice"}):
            raise ValueError("invalid presence interval")
        labels.update(((item["course_id"], None), (item["course_id"], item["lesson_id"])))
    for item in value["presence_points"]:
        if (not isinstance(item, dict) or set(item) != {"at_ms", "course_id"} or type(item["at_ms"]) is not int
                or not start <= item["at_ms"] < end or not identifier(item["course_id"])):
            raise ValueError("invalid presence point")
        labels.add((item["course_id"], None))
    reported = {}
    for item in value["contributions"]:
        if (not isinstance(item, dict) or set(item) != {"course_id", "lesson_id", "section", "active_ms"}
                or not identifier(item["course_id"]) or not identifier(item["lesson_id"])
                or item["section"] not in {"theory", "practice"} or type(item["active_ms"]) is not int or item["active_ms"] <= 0):
            raise ValueError("invalid contribution")
        key = (item["course_id"], item["lesson_id"], item["section"])
        if key in reported:
            raise ValueError("duplicate contribution")
        reported[key] = item["active_ms"]
    actual_labels = set()
    for item in value["presence_labels"]:
        if (not isinstance(item, dict) or set(item) != {"course_id", "lesson_id"} or not identifier(item["course_id"])
                or (item["lesson_id"] is not None and not identifier(item["lesson_id"]))):
            raise ValueError("invalid presence label")
        key = (item["course_id"], item["lesson_id"])
        if key in actual_labels:
            raise ValueError("duplicate presence label")
        actual_labels.add(key)
    if totals != reported or labels != actual_labels:
        raise ValueError("bucket summary mismatch")
    def bytes_for(group):
        return sum(len(json.dumps(value[key], ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                  allow_nan=False).encode("utf-8")) for key in group)
    stats = {"compact_count": compact_count, "edge_count": edge_count,
             "compact_bytes": bytes_for(compact_keys), "edge_bytes": bytes_for(edge_keys)}
    if stats["compact_bytes"] + stats["edge_bytes"] > MAX_BUCKET_BYTES:
        raise ProjectionLimit("bucket projection bytes exceed bound")
    return raw, stats


class PostgresActivityProjection:
    def __init__(self, database, *, expected_run_id):
        if not isinstance(expected_run_id, UUID):
            raise ValueError("valid dataset run ID required")
        self.database, self.expected_run_id = database, expected_run_id

    async def _run(self, session, *, lock=False, share=False):
        suffix = " FOR UPDATE SKIP LOCKED" if lock else " FOR SHARE" if share else ""
        row = (await session.execute(text("SELECT run_id,phase,cursor_namespace,cursor_event_id FROM analytics.activity_projection_backfill WHERE singleton=true" + suffix))).one_or_none()
        if row is None:
            if lock:
                return None
            raise IntakeUnavailable("activity backfill state unavailable")
        if row.run_id != self.expected_run_id:
            raise IntakeUnavailable("activity dataset generation mismatch")
        return row

    async def claim(self, owner, *, deadline):
        if not isinstance(owner, UUID):
            raise ValueError("invalid worker identity")
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise IntakeUnavailable()
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                await self._run(session, share=True)
                row = (await session.execute(text("""SELECT subject_ref,bucket_start,generation,state,attempts
                    FROM analytics.activity_projection_buckets WHERE metric_version=:metric
                    AND ((state='dirty' AND next_attempt_at<=clock_timestamp()) OR
                         (state='claimed' AND lease_expires_at<=clock_timestamp()))
                    ORDER BY next_attempt_at,bucket_start,subject_ref LIMIT 1 FOR UPDATE SKIP LOCKED"""), {"metric": METRIC_VERSION})).one_or_none()
                if row is None:
                    return None
                attempts = row.attempts + int(row.state == "claimed")
                args = {"subject": row.subject_ref, "bucket": row.bucket_start, "metric": METRIC_VERSION,
                        "attempts": attempts, "owner": owner, "claim": uuid4(), "run": self.expected_run_id}
                if attempts >= MAX_ATTEMPTS:
                    await session.execute(text("""UPDATE analytics.activity_projection_buckets SET state='quarantined',
                        attempts=:attempts,last_failure='lease_expired',owner=NULL,claim_id=NULL,lease_expires_at=NULL,
                        claim_generation=NULL,claim_epoch=NULL,claim_run_id=NULL
                        WHERE subject_ref=:subject AND bucket_start=:bucket AND metric_version=:metric"""), args)
                    return None
                await session.execute(text("""UPDATE analytics.activity_projection_buckets SET state='claimed',
                    owner=:owner,claim_id=:claim,claim_generation=generation,claim_epoch=NULL,claim_run_id=:run,
                    attempts=:attempts,lease_expires_at=clock_timestamp()+interval '30 seconds'
                    WHERE subject_ref=:subject AND bucket_start=:bucket AND metric_version=:metric"""), args)
                return BucketClaim(row.subject_ref, row.bucket_start, row.generation, None, args["claim"], owner, self.expected_run_id)

    async def _locked(self, session, claim):
        if not isinstance(claim, BucketClaim) or claim.run_id != self.expected_run_id:
            raise StaleBucketClaim()
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(claim.subject_ref)})
        await self._run(session, share=True)
        args = {"subject": claim.subject_ref, "bucket": claim.bucket_start, "metric": METRIC_VERSION,
                "owner": claim.owner, "claim": claim.claim_id, "run": claim.run_id, "generation": claim.generation}
        fence = (await session.execute(text("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=:subject"), args)).one_or_none()
        if fence and fence.deleted:
            raise StaleBucketClaim()
        epoch = fence.epoch if fence else 0
        row = (await session.execute(text("""SELECT generation,state,claim_generation,claim_epoch,claim_run_id,claim_id,owner,
                lease_expires_at>clock_timestamp() AS active FROM analytics.activity_projection_buckets
                WHERE subject_ref=:subject AND bucket_start=:bucket AND metric_version=:metric FOR UPDATE"""), args)).one_or_none()
        if (row is None or row.state != "claimed" or row.generation != claim.generation or row.claim_generation != claim.generation
                or row.claim_run_id != claim.run_id or row.claim_id != claim.claim_id or row.owner != claim.owner or not row.active
                or (row.claim_epoch is not None and row.claim_epoch != epoch)
                or (claim.subject_epoch is not None and claim.subject_epoch != epoch)):
            raise StaleBucketClaim()
        return args, epoch

    async def load(self, claim, *, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise IntakeUnavailable()
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                args, epoch = await self._locked(session, claim)
                await session.execute(text("""UPDATE analytics.activity_projection_buckets SET claim_epoch=:epoch
                    WHERE subject_ref=:subject AND bucket_start=:bucket AND metric_version=:metric"""), {**args, "epoch": epoch})
                bounds = {**args, "lower": claim.bucket_start - timedelta(seconds=60),
                          "upper": claim.bucket_start + timedelta(seconds=BUCKET_SECONDS), "limit": MAX_BUCKET_FACTS + 1}
                predicate = """ FROM analytics.activity_facts WHERE source_kind='browser_v2' AND subject_ref=:subject
                    AND started_at>=:lower AND started_at<:upper ORDER BY started_at,producer_namespace,event_id LIMIT :limit"""
                count, size = (await session.execute(text("""SELECT count(*),coalesce(sum(bytes),0) FROM (
                    SELECT octet_length(accepted_event::text) AS bytes""" + predicate + ") q"), bounds)).one()
                if count > MAX_BUCKET_FACTS or size > MAX_BUCKET_BYTES:
                    raise ProjectionLimit("bucket input exceeds bound")
                rows = (await session.execute(text("""SELECT producer_namespace,event_id,subject_ref,course_id,catalog_version,
                    lesson_id,started_at,received_at,canonical_hash,hash_version,accepted_event""" + predicate), bounds)).all()
                if len(rows) != count:
                    raise StaleBucketClaim()
                records = []
                for row in rows:
                    record = AcceptedEvent.model_validate_json(json.dumps(row.accepted_event, ensure_ascii=False))
                    event = record.event
                    if (record.subject_ref != claim.subject_ref or record.producer_namespace != row.producer_namespace
                            or event.event_id != row.event_id or event.course_id != row.course_id or event.lesson_id != row.lesson_id
                            or event.catalog_version != row.catalog_version or event.started_at != row.started_at
                            or record.received_at != row.received_at or row.hash_version != 1 or canonical_hash(record) != row.canonical_hash):
                        raise ValueError("invalid stored activity fact")
                    records.append(record)
                return replace(claim, subject_epoch=epoch), tuple(records)

    async def commit(self, claim, projection, *, deadline):
        if claim.subject_epoch is None:
            raise StaleBucketClaim()
        raw, stats = _valid_projection(claim, projection)
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise IntakeUnavailable()
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                args, epoch = await self._locked(session, claim)
                args.update(epoch=epoch, body=raw, **stats)
                # This is the same PostgreSQL JSONB text representation used by
                # the generated columns and therefore exactly matches Query caps.
                byte_count = (await session.execute(text("""SELECT
                    octet_length((CAST(:body AS jsonb)->'contributions')::text)::bigint +
                    octet_length((CAST(:body AS jsonb)->'presence_labels')::text)::bigint +
                    octet_length((CAST(:body AS jsonb)->'winner_intervals')::text)::bigint +
                    octet_length((CAST(:body AS jsonb)->'presence_intervals')::text)::bigint +
                    octet_length((CAST(:body AS jsonb)->'presence_points')::text)::bigint"""), args)).scalar_one()
                if byte_count > MAX_BUCKET_BYTES:
                    raise ProjectionLimit("bucket database representation exceeds bound")
                result = await session.execute(text("""UPDATE analytics.activity_projection_buckets SET state='ready',
                    projection_generation=generation,subject_epoch=:epoch,owner=NULL,claim_id=NULL,lease_expires_at=NULL,
                    claim_generation=NULL,claim_epoch=NULL,claim_run_id=NULL,attempts=0,last_failure=NULL,
                    winner_intervals=CAST(:body AS jsonb)->'winner_intervals',
                    presence_intervals=CAST(:body AS jsonb)->'presence_intervals',
                    presence_points=CAST(:body AS jsonb)->'presence_points',
                    contributions=CAST(:body AS jsonb)->'contributions',presence_labels=CAST(:body AS jsonb)->'presence_labels',
                    has_presence=(jsonb_array_length(CAST(:body AS jsonb)->'presence_labels')>0),
                    compact_item_count=:compact_count,edge_item_count=:edge_count
                    WHERE subject_ref=:subject AND bucket_start=:bucket AND metric_version=:metric AND state='claimed'
                      AND generation=:generation AND claim_generation=:generation AND owner=:owner AND claim_id=:claim
                      AND claim_run_id=:run AND claim_epoch=:epoch AND lease_expires_at>clock_timestamp() RETURNING generation"""), args)
                if result.scalar_one_or_none() is None:
                    raise StaleBucketClaim()

    async def fail(self, claim, reason, *, deadline):
        if reason not in {"input_limit", "invalid_fact", "compute_timeout", "transient"}:
            raise ValueError("invalid bucket failure")
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise IntakeUnavailable()
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                args, _ = await self._locked(session, claim)
                await session.execute(text("""UPDATE analytics.activity_projection_buckets SET attempts=attempts+1,
                    state=CASE WHEN :reason IN ('input_limit','invalid_fact') OR attempts+1>=:max_attempts
                      THEN 'quarantined' ELSE 'dirty' END,last_failure=:reason,
                    next_attempt_at=clock_timestamp()+((LEAST(60,power(2,LEAST(attempts,5))::int)*1000
                       + get_byte(uuid_send(:claim),0)%250)*interval '1 millisecond'),
                    owner=NULL,claim_id=NULL,lease_expires_at=NULL,claim_generation=NULL,claim_epoch=NULL,claim_run_id=NULL
                    WHERE subject_ref=:subject AND bucket_start=:bucket AND metric_version=:metric"""),
                    {**args, "reason": reason, "max_attempts": MAX_ATTEMPTS})

    async def backfill_step(self, *, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise IntakeUnavailable()
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                state = await self._run(session)
                if state.phase == "complete": return "complete"
                if state.phase == "draining":
                    locked = await self._run(session, lock=True)
                    if locked is None or locked.phase != "draining": return "busy"
                    outstanding = (await session.execute(text("SELECT EXISTS(SELECT 1 FROM analytics.activity_projection_buckets WHERE state<>'ready')"))).scalar_one()
                    if not outstanding:
                        await session.execute(text("UPDATE analytics.activity_projection_backfill SET phase='complete' WHERE singleton=true"))
                        return "complete"
                    return "draining"
                after = " AND (producer_namespace,event_id)>(:namespace,:event)" if state.cursor_namespace is not None else ""
                rows = (await session.execute(text("""SELECT producer_namespace,event_id,subject_ref FROM analytics.activity_facts
                    WHERE source_kind='browser_v2'""" + after + " ORDER BY producer_namespace,event_id LIMIT :limit"),
                    {"namespace": state.cursor_namespace, "event": state.cursor_event_id, "limit": BACKFILL_PAGE})).all()
                for key in sorted({subject_lock_key(row.subject_ref) for row in rows}):
                    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
                locked = await self._run(session, lock=True)
                if (locked is None or locked.phase != "scanning" or locked.cursor_namespace != state.cursor_namespace
                        or locked.cursor_event_id != state.cursor_event_id):
                    return "busy"
                last, capacity_full = None, False
                for row in rows:
                    try:
                        # Preserve the earlier page prefix if this fact exhausts
                        # capacity, including rollback of its first bucket.
                        async with session.begin_nested():
                            await session.execute(text("SELECT analytics.backfill_activity_projection_fact(:namespace,:event)"),
                                                  {"namespace": row.producer_namespace, "event": row.event_id})
                    except DBAPIError as exc:
                        if getattr(exc.orig, "sqlstate", None) != "PCA01":
                            raise
                        capacity_full = True
                        break
                    last = row
                if last is not None:
                    await session.execute(text("""UPDATE analytics.activity_projection_backfill SET cursor_namespace=:namespace,
                        cursor_event_id=:event WHERE singleton=true"""), {"namespace": last.producer_namespace, "event": last.event_id})
                if not capacity_full and len(rows) < BACKFILL_PAGE:
                    await session.execute(text("UPDATE analytics.activity_projection_backfill SET phase='draining' WHERE singleton=true"))
                    return "draining"
                return "scanning" if last is not None else "busy"

    async def retry_quarantined(self, subject, start, generation, *, deadline):
        """Explicit bounded replay of one unchanged quarantined bucket."""
        if not isinstance(subject, UUID) or type(generation) is not int or generation < 1:
            raise ValueError("invalid bucket replay")
        remaining = deadline - time.monotonic()
        if remaining <= 0: raise IntakeUnavailable()
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
                await self._run(session, share=True)
                fence = (await session.execute(text("SELECT deleted FROM analytics.subject_fences WHERE subject_ref=:subject"),
                                               {"subject": subject})).scalar_one_or_none()
                if fence is True:
                    raise StaleBucketClaim()
                result = await session.execute(text("""UPDATE analytics.activity_projection_buckets SET state='dirty',attempts=0,
                    next_attempt_at=clock_timestamp(),last_failure=NULL WHERE subject_ref=:subject AND bucket_start=:bucket
                      AND metric_version=:metric AND generation=:generation AND state='quarantined' RETURNING generation"""),
                    {"subject": subject, "bucket": start, "metric": METRIC_VERSION, "generation": generation})
                return result.scalar_one_or_none() is not None


async def begin_activity_projection_backfill(database, *, expected_run_id, new_run_id, deadline):
    """Guarded owner-only compare-and-swap; never called by a runtime worker."""
    if (expected_run_id is not None and not isinstance(expected_run_id, UUID)) or not isinstance(new_run_id, UUID):
        raise ValueError("invalid dataset generation")
    remaining = deadline - time.monotonic()
    if remaining <= 0: raise IntakeUnavailable()
    async with asyncio.timeout(remaining):
        async with database.transaction() as session:
            identity = (await session.execute(text("SELECT current_database() AS name,current_user AS role"))).one()
            if (not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", identity.name)
                    or identity.role != identity.name + "_migrate"):
                raise ValueError("migration role required")
            await session.execute(text("SELECT analytics.begin_activity_projection_backfill(:expected,:new)"),
                                  {"expected": expected_run_id, "new": new_run_id})
