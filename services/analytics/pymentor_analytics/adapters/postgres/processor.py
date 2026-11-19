"""Fenced synthetic work processing; no runtime loop or product metrics."""
import asyncio
import json
import re
from uuid import UUID, uuid4

from sqlalchemy import text

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.core_transfer import has_core_schema
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.processing_v1 import (
    MAX_BATCH, MAX_COURSES, MAX_FACT_BYTES, MAX_LEASE_SECONDS,
    ClaimedBatch, ClaimedFact, ProjectionUnavailable, StaleClaim, SyntheticCountRow,
    SyntheticCountSnapshot, SyntheticDelta, synthetic_delta,
)
from pymentor_analytics.contracts.v2 import AcceptedEvent, canonical_hash
from pymentor_analytics.contracts.work_v1 import SYNTHETIC_SUBSCRIPTIONS


def _bounds(consumer, version, worker, limit, lease_seconds):
    if (type(version) is not int or not any((s.consumer, s.version) == (consumer, version) for s in SYNTHETIC_SUBSCRIPTIONS)
            or not isinstance(worker, UUID) or type(limit) is not int or not 1 <= limit <= MAX_BATCH
            or type(lease_seconds) is not int or not 1 <= lease_seconds <= MAX_LEASE_SECONDS):
        raise ValueError("invalid processor subscription or bounds")


class PostgresProcessor:
    def __init__(self, database: Database):
        self.database = database
        # Local scheduling hints only, never processing checkpoints.
        self._cursors = {}
        self._cursor_locks = {}

    async def _before_commit(self):
        """Private controlled failure seam, never a public endpoint."""

    async def _after_commit(self):
        """Private controlled lost-reply seam."""

    async def claim(self, consumer: str, version: int, worker: UUID, limit: int, lease_seconds: int) -> ClaimedBatch | None:
        _bounds(consumer, version, worker, limit, lease_seconds)
        async with asyncio.timeout(self.database.timeout):
            lock = self._cursor_locks.setdefault((consumer, version), asyncio.Lock())
            async with lock:
                return await self._claim(consumer, version, worker, limit, lease_seconds)

    async def _claim(self, consumer: str, version: int, worker: UUID, limit: int, lease_seconds: int) -> ClaimedBatch | None:
        args = {"consumer": consumer, "version": version}
        cursor_key = (consumer, version)
        cursor = self._cursors.get(cursor_key)
        after = " HAVING (min(j.created_at),j.subject_ref) > (:after_time,:after_subject)"
        # Candidate discovery deliberately holds neither partition nor subject lock.
        async with self.database.transaction() as session:
            source_filter = " AND f.source_kind='browser_v2'" if await has_core_schema(session) else ""
            query = """
                SELECT j.subject_ref, min(j.created_at) AS oldest
                FROM analytics.processing_jobs j
                JOIN analytics.activity_facts f ON f.producer_namespace=j.producer_namespace
                  AND f.event_id=j.event_id AND f.subject_ref=j.subject_ref
                LEFT JOIN analytics.processing_leases l ON l.consumer=j.consumer
                   AND l.consumer_version=j.consumer_version AND l.subject_ref=j.subject_ref
                WHERE j.consumer=:consumer AND j.consumer_version=:version
                  AND ((j.status='pending' AND j.next_attempt_at<=clock_timestamp()) OR j.status='claimed')
                  AND (l.owner IS NULL OR l.expires_at<=clock_timestamp())
                  AND NOT EXISTS (SELECT 1 FROM analytics.subject_fences s
                                  WHERE s.subject_ref=j.subject_ref AND s.deleted)
                  AND NOT EXISTS (SELECT 1 FROM analytics.processing_replays rp
                    WHERE rp.consumer=j.consumer AND rp.consumer_version=j.consumer_version
                      AND rp.subject_ref=j.subject_ref AND rp.phase='cleanup')
            """ + source_filter + " GROUP BY j.subject_ref "
            params = {**args, "after_time": cursor[0], "after_subject": cursor[1]} if cursor else args
            candidates = (await session.execute(text(query + (after if cursor else "") + " ORDER BY oldest,j.subject_ref LIMIT 32"), params)).all()
            if not candidates and cursor:
                self._cursors.pop(cursor_key, None)
                candidates = (await session.execute(text(query + " ORDER BY oldest,j.subject_ref LIMIT 32"), args)).all()
        if not candidates:
            self._cursors.pop(cursor_key, None)
            return None
        for candidate in candidates:
            self._cursors[cursor_key] = (candidate.oldest, candidate.subject_ref)
            subject = candidate.subject_ref
            async with self.database.transaction() as session:
                source_filter = " AND f.source_kind='browser_v2'" if await has_core_schema(session) else ""
                locked = (await session.execute(text("SELECT pg_try_advisory_xact_lock(:key)"),
                                                {"key": subject_lock_key(subject)})).scalar_one()
                if not locked:
                    continue
                fence = (await session.execute(text("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=:subject"),
                                               {"subject": subject})).one_or_none()
                if fence and fence.deleted:
                    continue
                epoch = fence.epoch if fence else 0
                params = {**args, "subject": subject}
                cleaning = (await session.execute(text("""SELECT 1 FROM analytics.processing_replays
                    WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject AND phase='cleanup'"""), params)).first()
                if cleaning:
                    continue
                await session.execute(text("""
                    INSERT INTO analytics.processing_leases(consumer,consumer_version,subject_ref,generation,owner,expires_at,claim_id,subject_epoch)
                    VALUES (:consumer,:version,:subject,1,NULL,clock_timestamp(),NULL,:epoch)
                    ON CONFLICT (consumer,consumer_version,subject_ref) DO NOTHING
                """), {**params, "epoch": epoch})
                lease = (await session.execute(text("""
                    SELECT generation,owner,claim_id,live_streak,expires_at>clock_timestamp() AS active
                    FROM analytics.processing_leases WHERE consumer=:consumer AND consumer_version=:version
                      AND subject_ref=:subject FOR UPDATE SKIP LOCKED
                """), params)).one_or_none()
                if lease is None or (lease.owner is not None and lease.active):
                    continue
                if lease.owner is not None:
                    # A crashed or expired owner consumes one bounded attempt;
                    # work remains unacknowledged and is retried after backoff.
                    await session.execute(text("""UPDATE analytics.processing_jobs SET attempts=attempts+1,
                        status=CASE WHEN attempts+1>=3 THEN 'quarantined' ELSE 'pending' END,
                        next_attempt_at=clock_timestamp()+((LEAST(60,power(2,LEAST(attempts,5))::int)*1000
                          +get_byte(uuid_send(event_id),0)%250)*interval '1 millisecond'),
                        last_failure='lease_expired',claim_id=NULL,failed_replay_id=(SELECT request_id
                            FROM analytics.processing_replays WHERE consumer=:consumer AND consumer_version=:version
                              AND subject_ref=:subject AND phase<>'complete')
                        WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject
                          AND claim_id=:old_claim AND status='claimed'"""), {**params, "old_claim": lease.claim_id})
                    await session.execute(text("""UPDATE analytics.processing_leases
                        SET owner=NULL,expires_at=clock_timestamp(),claim_id=NULL
                        WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject"""), params)
                    continue
                rows = (await session.execute(text("""
                    SELECT j.producer_namespace,j.event_id,j.work_class,octet_length(f.accepted_event::text) AS bytes
                    FROM analytics.processing_jobs j JOIN analytics.activity_facts f
                      ON f.producer_namespace=j.producer_namespace AND f.event_id=j.event_id
                    WHERE j.consumer=:consumer AND j.consumer_version=:version AND j.subject_ref=:subject
                      AND ((j.status='pending' AND j.next_attempt_at<=clock_timestamp()) OR j.status='claimed')
                """ + source_filter + """ ORDER BY CASE WHEN :replay_first THEN (j.work_class <> 'replay')
                                  ELSE (j.work_class <> 'live') END,
                             j.created_at,j.producer_namespace,j.event_id LIMIT :limit FOR UPDATE OF j SKIP LOCKED
                """), {**params, "limit": limit, "replay_first": lease.live_streak >= 3})).all()
                usable = []
                for row in rows:
                    key = {**params, "producer": row.producer_namespace, "event": row.event_id}
                    if row.bytes > MAX_FACT_BYTES:
                        await session.execute(text("""UPDATE analytics.processing_jobs SET status='quarantined',
                            last_failure='oversized_fact',claim_id=NULL,failed_replay_id=(SELECT request_id FROM analytics.processing_replays
                              WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject AND phase<>'complete')
                            WHERE consumer=:consumer AND consumer_version=:version
                            AND producer_namespace=:producer AND event_id=:event"""), key)
                    else:
                        usable.append((key, row.work_class))
                if not usable:
                    continue
                batch_id = uuid4()
                generation = lease.generation + 1
                facts = []
                chosen_classes = []
                for key, work_class in usable:
                    row = (await session.execute(text("""
                        SELECT f.accepted_event,f.canonical_hash,
                          EXISTS(SELECT 1 FROM analytics.processing_receipts r WHERE r.consumer=:consumer
                            AND r.consumer_version=:version AND r.producer_namespace=f.producer_namespace
                            AND r.event_id=f.event_id AND r.subject_ref=f.subject_ref) AS applied
                        FROM analytics.activity_facts f WHERE f.producer_namespace=:producer AND f.event_id=:event
                    """), key)).one()
                    try:
                        record = AcceptedEvent.model_validate_json(json.dumps(row.accepted_event))
                        if canonical_hash(record) != row.canonical_hash or record.subject_ref != subject:
                            raise ValueError("stored fact integrity check failed")
                    except (ValueError, TypeError):
                        await session.execute(text("""UPDATE analytics.processing_jobs SET status='quarantined',
                            last_failure='invalid_fact',claim_id=NULL,failed_replay_id=(SELECT request_id FROM analytics.processing_replays
                              WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject AND phase<>'complete')
                            WHERE consumer=:consumer AND consumer_version=:version
                            AND producer_namespace=:producer AND event_id=:event"""), key)
                        continue
                    facts.append(ClaimedFact(record, bool(row.applied)))
                    chosen_classes.append(work_class)
                    await session.execute(text("""UPDATE analytics.processing_jobs SET status='claimed',claim_id=:batch
                        WHERE consumer=:consumer AND consumer_version=:version AND producer_namespace=:producer AND event_id=:event"""),
                        {**key, "batch": batch_id})
                if not facts:
                    continue
                await session.execute(text("""UPDATE analytics.processing_leases SET generation=:generation,owner=:owner,
                    expires_at=clock_timestamp()+(:seconds * interval '1 second'),claim_id=:batch,subject_epoch=:epoch,
                    live_streak=:live_streak
                    WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject"""),
                    {**params, "generation": generation, "owner": worker, "seconds": lease_seconds,
                     "batch": batch_id, "epoch": epoch,
                     "live_streak": 0 if "replay" in chosen_classes else min(3, lease.live_streak + 1)})
                return ClaimedBatch(consumer, version, subject, epoch, worker, generation, batch_id, tuple(facts))
        return None

    async def _lock_valid(self, session, batch: ClaimedBatch):
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(batch.subject_ref)})
        fence = (await session.execute(text("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=:subject"),
                                       {"subject": batch.subject_ref})).one_or_none()
        if (fence and fence.deleted) or (fence.epoch if fence else 0) != batch.subject_epoch:
            raise StaleClaim("subject fence changed")
        args = {"consumer": batch.consumer, "version": batch.version, "subject": batch.subject_ref,
                "owner": batch.worker, "generation": batch.generation, "batch": batch.batch_id,
                "epoch": batch.subject_epoch}
        row = (await session.execute(text("""SELECT generation FROM analytics.processing_leases
            WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject
              AND owner=:owner AND generation=:generation AND claim_id=:batch AND subject_epoch=:epoch
              AND expires_at>clock_timestamp() FOR UPDATE"""), args)).one_or_none()
        if row is None:
            raise StaleClaim("lease expired or superseded")
        return args

    async def renew(self, batch: ClaimedBatch, lease_seconds: int) -> bool:
        _bounds(batch.consumer, batch.version, batch.worker, len(batch.facts), lease_seconds)
        try:
            async with self.database.transaction() as session:
                args = await self._lock_valid(session, batch)
                renewed = await session.execute(text("""UPDATE analytics.processing_leases
                    SET expires_at=clock_timestamp()+(:seconds * interval '1 second')
                    WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject
                      AND owner=:owner AND generation=:generation AND claim_id=:batch
                      AND expires_at>clock_timestamp() RETURNING generation"""),
                    {**args, "seconds": lease_seconds})
                if renewed.scalar_one_or_none() is None:
                    raise StaleClaim("lease expired during renewal")
            return True
        except StaleClaim:
            return False


    async def commit(self, batch: ClaimedBatch, effect: SyntheticDelta) -> None:
        _bounds(batch.consumer, batch.version, batch.worker, len(batch.facts), 1)
        validated = synthetic_delta(batch.facts)
        if effect != validated:
            raise ValueError("synthetic effect does not match bounded claim")
        # Use a fresh private copy: caller-owned dict must not change after check.
        effect = validated
        async with self.database.transaction() as session:
            args = await self._lock_valid(session, batch)
            source_filter = " AND f.source_kind='browser_v2'" if await has_core_schema(session) else ""
            rows = (await session.execute(text("""
                SELECT j.producer_namespace,j.event_id,f.canonical_hash,
                  EXISTS(SELECT 1 FROM analytics.processing_receipts r WHERE r.consumer=j.consumer
                    AND r.consumer_version=j.consumer_version AND r.producer_namespace=j.producer_namespace
                    AND r.event_id=j.event_id AND r.subject_ref=j.subject_ref) AS applied
                FROM analytics.processing_jobs j JOIN analytics.activity_facts f
                  ON f.producer_namespace=j.producer_namespace AND f.event_id=j.event_id
                WHERE j.consumer=:consumer AND j.consumer_version=:version AND j.subject_ref=:subject
                  AND j.claim_id=:batch AND j.status='claimed'
            """ + source_filter + " FOR UPDATE OF j"), args)).all()
            actual = {(r.producer_namespace, r.event_id, r.canonical_hash, bool(r.applied)) for r in rows}
            expected = {(f.record.producer_namespace, f.record.event.event_id, canonical_hash(f.record), f.already_applied)
                        for f in batch.facts}
            if len(rows) != len(batch.facts) or len(expected) != len(batch.facts) or actual != expected:
                raise StaleClaim("claim membership changed")
            result = (await session.execute(text("""SELECT courses,fingerprint_xor,covered_count,subject_epoch FROM analytics.processing_results
                WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject FOR UPDATE"""), args)).one_or_none()
            if result and result.subject_epoch != batch.subject_epoch:
                raise StaleClaim("projection epoch changed; reconciliation required")
            courses = dict(result.courses) if result else {}
            for course, count in effect.course_counts.items():
                courses[course] = courses.get(course, 0) + count
            if len(courses) > MAX_COURSES:
                raise ValueError("synthetic result exceeds course bound")
            fingerprint = int(result.fingerprint_xor, 16) if result else 0
            fingerprint ^= int(effect.fingerprint_xor, 16)
            count = (result.covered_count if result else 0) + sum(effect.course_counts.values())
            for fact in batch.facts:
                if fact.already_applied:
                    continue
                added = await session.execute(text("""INSERT INTO analytics.processing_receipts
                    (consumer,consumer_version,producer_namespace,event_id,subject_ref)
                    VALUES (:consumer,:version,:producer,:event,:subject) ON CONFLICT DO NOTHING RETURNING event_id"""),
                    {**args, "producer": fact.record.producer_namespace, "event": fact.record.event.event_id})
                if added.scalar_one_or_none() is None:
                    raise StaleClaim("receipt already exists")
            await session.execute(text("""INSERT INTO analytics.processing_results
                (consumer,consumer_version,subject_ref,subject_epoch,courses,fingerprint_xor,covered_count)
                VALUES (:consumer,:version,:subject,:epoch,CAST(:courses AS jsonb),:fingerprint,:count)
                ON CONFLICT (consumer,consumer_version,subject_ref) DO UPDATE SET
                  subject_epoch=excluded.subject_epoch,courses=excluded.courses,fingerprint_xor=excluded.fingerprint_xor,
                  covered_count=excluded.covered_count,updated_at=clock_timestamp()"""),
                {**args, "courses": json.dumps(courses), "fingerprint": f"{fingerprint:064x}", "count": count})
            await session.execute(text("""UPDATE analytics.processing_jobs SET status='completed',last_failure=NULL,failed_replay_id=NULL
                WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject
                  AND claim_id=:batch AND status='claimed'"""), args)
            await self._before_commit()
            released = await session.execute(text("""UPDATE analytics.processing_leases
                SET owner=NULL,expires_at=clock_timestamp(),claim_id=NULL
                WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject AND owner=:owner
                  AND generation=:generation AND claim_id=:batch AND expires_at>clock_timestamp() RETURNING generation"""), args)
            if released.scalar_one_or_none() is None:
                raise StaleClaim("lease expired before commit")
        await self._after_commit()

    async def fail(self, batch: ClaimedBatch) -> str:
        _bounds(batch.consumer, batch.version, batch.worker, len(batch.facts), 1)
        async with self.database.transaction() as session:
            args = await self._lock_valid(session, batch)
            rows = (await session.execute(text("""UPDATE analytics.processing_jobs SET attempts=attempts+1,
                status=CASE WHEN attempts+1>=3 THEN 'quarantined' ELSE 'pending' END,
                next_attempt_at=clock_timestamp()+((LEAST(60,power(2,LEAST(attempts,5))::int)*1000
                  +get_byte(uuid_send(event_id),0)%250)*interval '1 millisecond'),
                last_failure='handler_failed',claim_id=NULL,failed_replay_id=(SELECT request_id FROM analytics.processing_replays
                  WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject AND phase<>'complete')
                WHERE consumer=:consumer AND consumer_version=:version
                  AND subject_ref=:subject AND claim_id=:batch AND status='claimed' RETURNING status"""), args)).all()
            if len(rows) != len(batch.facts):
                raise StaleClaim("claim membership changed")
            released = await session.execute(text("""UPDATE analytics.processing_leases SET owner=NULL,expires_at=clock_timestamp(),claim_id=NULL
                WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject
                  AND owner=:owner AND generation=:generation AND claim_id=:batch
                  AND expires_at>clock_timestamp() RETURNING generation"""), args)
            if released.scalar_one_or_none() is None:
                raise StaleClaim("lease expired during failure handling")
            return "retry" if any(r.status == "pending" for r in rows) else "quarantined"

    async def release(self, batch: ClaimedBatch) -> bool:
        _bounds(batch.consumer, batch.version, batch.worker, len(batch.facts), 1)
        try:
            async with self.database.transaction() as session:
                args = await self._lock_valid(session, batch)
                rows = (await session.execute(text("""UPDATE analytics.processing_jobs SET status='pending',claim_id=NULL
                    WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject
                      AND claim_id=:batch AND status='claimed' RETURNING event_id"""), args)).all()
                if len(rows) != len(batch.facts):
                    raise StaleClaim("claim membership changed")
                released = await session.execute(text("""UPDATE analytics.processing_leases SET owner=NULL,expires_at=clock_timestamp(),claim_id=NULL
                    WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject
                      AND owner=:owner AND generation=:generation AND claim_id=:batch
                      AND expires_at>clock_timestamp() RETURNING generation"""), args)
                if released.scalar_one_or_none() is None:
                    raise StaleClaim("lease expired during release")
            return True
        except StaleClaim:
            return False


class PostgresSyntheticCounts:
    """Technical read only; access to requested subject/course is checked by caller."""
    def __init__(self, database: Database):
        self.database = database

    async def _before_materialize(self):
        """Private test seam after both subject fences are locked and checked."""

    async def read_counts(self, consumer: str, version: int, actor: UUID, subject: UUID,
                          course_ids: tuple[str, ...]) -> SyntheticCountSnapshot | None:
        _bounds(consumer, version, UUID(int=0), 1, 1)
        if (not isinstance(actor, UUID) or not isinstance(subject, UUID) or type(course_ids) is not tuple or not 1 <= len(course_ids) <= 100
                or any(not isinstance(c, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", c) is None
                       for c in course_ids) or len(set(course_ids)) != len(course_ids)):
            raise ValueError("invalid bounded synthetic count scope")
        async with self.database.transaction() as session:
            source_filter = " AND f.source_kind='browser_v2'" if await has_core_schema(session) else ""
            for key in sorted({subject_lock_key(actor), subject_lock_key(subject)}):
                await session.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": key})
            actor_fence = (await session.execute(text("SELECT deleted FROM analytics.subject_fences WHERE subject_ref=:subject"),
                                                 {"subject": actor})).one_or_none()
            if actor_fence and actor_fence.deleted:
                raise ProjectionUnavailable("actor lifecycle is deleted")
            fence = (await session.execute(text("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=:subject"),
                                           {"subject": subject})).one_or_none()
            if fence and fence.deleted:
                return None
            await self._before_materialize()
            result = (await session.execute(text("""SELECT subject_epoch,courses FROM analytics.processing_results
                WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject"""),
                {"consumer": consumer, "version": version, "subject": subject})).one_or_none()
            dirty = (await session.execute(text("""SELECT 1 FROM analytics.processing_replays
                WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject
                  AND phase<>'complete' LIMIT 1"""),
                {"consumer": consumer, "version": version, "subject": subject})).first()
            scoped_rows = tuple(SyntheticCountRow(course, result.courses[course])
                                for course in course_ids if result and course in result.courses)
            if dirty:
                scoped_raw = (await session.execute(text("""SELECT 1 FROM analytics.activity_facts f
                    WHERE f.subject_ref=:subject AND f.course_id=ANY(CAST(:courses AS varchar[]))""" + source_filter + " LIMIT 1"),
                    {"subject": subject, "courses": list(course_ids)})).first()
                if scoped_rows or scoped_raw:
                    raise ProjectionUnavailable("subject projection is rebuilding")
                return None
            if result is None:
                return None
            rows = scoped_rows
            if not rows:
                return None
            if result.subject_epoch != (fence.epoch if fence else 0):
                raise ProjectionUnavailable("subject epoch requires reconciliation")
            as_of = (await session.execute(text("""
                SELECT max(f.received_at) FROM analytics.processing_receipts r
                JOIN analytics.activity_facts f ON f.producer_namespace=r.producer_namespace AND f.event_id=r.event_id
                WHERE r.consumer=:consumer AND r.consumer_version=:version AND r.subject_ref=:subject
                  AND f.subject_ref=:subject AND f.course_id=ANY(CAST(:courses AS varchar[]))
            """ + source_filter), {"consumer": consumer, "version": version, "subject": subject, "courses": list(course_ids)})).scalar_one()
            return SyntheticCountSnapshot(subject, consumer, version, rows, as_of)
