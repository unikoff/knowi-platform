"""Bounded, resumable same-version synthetic rebuild; no runtime scheduler."""
from uuid import UUID

from sqlalchemy import text

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.core_transfer import has_core_schema
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.replay_v1 import (
    MAX_REPLAY_ENQUEUE, MAX_REPLAY_PAGE, ReplayBusy, ReplayForbidden,
    ReplayHandle, ReplayStale, ReplayState,
)
from pymentor_analytics.contracts.work_v1 import SYNTHETIC_SUBSCRIPTIONS


def _valid(consumer, version, subject, request_id):
    if (type(version) is not int or not any((x.consumer, x.version) == (consumer, version) for x in SYNTHETIC_SUBSCRIPTIONS)
            or not isinstance(subject, UUID) or not isinstance(request_id, UUID)):
        raise ValueError("invalid synthetic replay identity")


def _state(row, *, blocked=False):
    return ReplayState(ReplayHandle(row.consumer, row.consumer_version, row.subject_ref,
                                    row.request_id, row.generation, row.subject_epoch),
                       row.phase, row.cursor_namespace, row.cursor_event_id, blocked)


class PostgresReplay:
    def __init__(self, database: Database):
        self.database = database

    async def _before_commit(self):
        """Private test seam for failure before the transaction completes."""

    async def _after_commit(self):
        """Private test seam for lost reply after a successful transaction."""

    async def _fence(self, session, subject):
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
        fence = (await session.execute(text("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=:subject"),
                                       {"subject": subject})).one_or_none()
        if fence and fence.deleted:
            raise ReplayForbidden("subject lifecycle is deleted")
        return fence.epoch if fence else 0

    async def begin(self, consumer: str, version: int, subject: UUID, request_id: UUID) -> ReplayState:
        _valid(consumer, version, subject, request_id)
        args = {"consumer": consumer, "version": version, "subject": subject, "request": request_id}
        async with self.database.transaction() as session:
            epoch = await self._fence(session, subject)
            prior = (await session.execute(text("""SELECT * FROM analytics.processing_replays WHERE consumer=:consumer
                AND consumer_version=:version AND subject_ref=:subject AND request_id=:request"""), args)).one_or_none()
            if prior:
                if prior.subject_epoch != epoch:
                    raise ReplayStale("subject epoch changed")
                return _state(prior)
            active = (await session.execute(text("""SELECT 1 FROM analytics.processing_replays WHERE consumer=:consumer
                AND consumer_version=:version AND subject_ref=:subject AND phase<>'complete'"""), args)).first()
            if active:
                raise ReplayBusy("another replay is active")
            policy = (await session.execute(text("SELECT * FROM analytics.admission_budget WHERE singleton=true FOR UPDATE"))).mappings().one()
            size = (await session.execute(text("SELECT pg_database_size(current_database())"))).scalar_one()
            if size * 100 >= policy["max_database_bytes"] * policy["critical_percent"]:
                raise ReplayBusy("replay storage capacity unavailable")
            generation = (await session.execute(text("""SELECT coalesce(max(generation),0)+1 FROM analytics.processing_replays
                WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject"""), args)).scalar_one()
            # Subject lock first; invalidate any in-flight processor generation.
            await session.execute(text("""UPDATE analytics.processing_leases SET generation=generation+1,
                owner=NULL,expires_at=clock_timestamp(),claim_id=NULL WHERE consumer=:consumer
                AND consumer_version=:version AND subject_ref=:subject"""), args)
            row = (await session.execute(text("""INSERT INTO analytics.processing_replays
                (consumer,consumer_version,subject_ref,request_id,generation,subject_epoch,phase)
                VALUES (:consumer,:version,:subject,:request,:generation,:epoch,'cleanup') RETURNING *"""),
                {**args, "generation": generation, "epoch": epoch})).one()
            await self._before_commit()
        await self._after_commit()
        return _state(row)

    async def _current(self, session, handle: ReplayHandle):
        _valid(handle.consumer, handle.version, handle.subject_ref, handle.request_id)
        epoch = await self._fence(session, handle.subject_ref)
        if epoch != handle.subject_epoch:
            raise ReplayStale("subject epoch changed")
        args = {"consumer": handle.consumer, "version": handle.version, "subject": handle.subject_ref,
                "request": handle.request_id, "generation": handle.generation}
        row = (await session.execute(text("""SELECT * FROM analytics.processing_replays
            WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject AND request_id=:request
              AND generation=:generation FOR UPDATE"""), args)).one_or_none()
        newest = (await session.execute(text("""SELECT max(generation) FROM analytics.processing_replays
            WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject"""), args)).scalar_one()
        if row is None or newest != handle.generation or row.subject_epoch != epoch:
            raise ReplayStale("replay generation superseded")
        return row, args

    async def step(self, handle: ReplayHandle, limit: int = MAX_REPLAY_PAGE) -> ReplayState:
        if type(limit) is not int or not 1 <= limit <= MAX_REPLAY_PAGE:
            raise ValueError("invalid replay page bound")
        blocked = False
        async with self.database.transaction() as session:
            row, args = await self._current(session, handle)
            if row.phase == "cleanup":
                removed = (await session.execute(text("""SELECT analytics.replay_cleanup(:consumer,:version,:subject,
                    :request,:generation,:epoch,:limit)"""), {**args, "epoch": handle.subject_epoch, "limit": limit})).scalar_one()
                if removed < limit:
                    row = (await session.execute(text("""UPDATE analytics.processing_replays SET phase='scan'
                        WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject
                          AND request_id=:request RETURNING *"""), args)).one()
            elif row.phase == "scan":
                source_filter = " AND f.source_kind='browser_v2'" if await has_core_schema(session) else ""
                cursor_filter = " AND (f.producer_namespace,f.event_id) > (:cursor_namespace,:cursor_event)" if row.cursor_namespace else ""
                page = (await session.execute(text("""SELECT f.producer_namespace,f.event_id,
                    EXISTS(SELECT 1 FROM analytics.processing_receipts r WHERE r.consumer=:consumer
                      AND r.consumer_version=:version AND r.producer_namespace=f.producer_namespace
                      AND r.event_id=f.event_id AND r.subject_ref=f.subject_ref) AS applied,
                    j.status,j.work_class,j.failed_replay_id FROM analytics.activity_facts f LEFT JOIN analytics.processing_jobs j
                      ON j.consumer=:consumer AND j.consumer_version=:version
                      AND j.producer_namespace=f.producer_namespace AND j.event_id=f.event_id AND j.subject_ref=f.subject_ref
                    WHERE f.subject_ref=:subject""" + source_filter + cursor_filter + " ORDER BY f.producer_namespace,f.event_id LIMIT :limit"),
                    {**args, "cursor_namespace": row.cursor_namespace, "cursor_event": row.cursor_event_id, "limit": limit})).all()
                last = None
                reserved = None
                enqueued = 0
                for fact in page:
                    old_quarantine = fact.status == "quarantined" and fact.failed_replay_id != handle.request_id
                    needs_work = not fact.applied and (fact.status is None or fact.status == "completed" or old_quarantine)
                    if needs_work:
                        if enqueued >= MAX_REPLAY_ENQUEUE:
                            blocked = True
                            break
                        if reserved is None:
                            policy = (await session.execute(text("SELECT * FROM analytics.admission_budget WHERE singleton=true FOR UPDATE"))).mappings().one()
                            size = (await session.execute(text("SELECT pg_database_size(current_database())"))).scalar_one()
                            total = (await session.execute(text("""SELECT count(*) FROM (SELECT 1 FROM analytics.processing_jobs
                                WHERE status<>'completed' LIMIT :bound) q"""), {"bound": policy["max_outstanding_jobs"] + 1})).scalar_one()
                            replay = (await session.execute(text("""SELECT count(*) FROM (SELECT 1 FROM analytics.processing_jobs
                                WHERE status<>'completed' AND work_class='replay' LIMIT :bound) q"""),
                                {"bound": policy["max_outstanding_jobs"] // 2 + 1})).scalar_one()
                            reserved = [policy, size, total, replay]
                        policy, size, total, replay = reserved
                        add_total = 1 if fact.status is None or fact.status == "completed" else 0
                        add_replay = 1 if add_total or fact.work_class != "replay" else 0
                        if (size * 100 >= policy["max_database_bytes"] * policy["critical_percent"]
                                or (add_total and total + add_total > policy["max_outstanding_jobs"])
                                or (add_replay and replay + add_replay > policy["max_outstanding_jobs"] // 2)):
                            blocked = True
                            break
                        key = {**args, "producer": fact.producer_namespace, "event": fact.event_id}
                        if fact.status is None:
                            await session.execute(text("""INSERT INTO analytics.processing_jobs
                                (producer_namespace,event_id,consumer,consumer_version,subject_ref,status,work_class)
                                VALUES (:producer,:event,:consumer,:version,:subject,'pending','replay')"""), key)
                        else:
                            await session.execute(text("""UPDATE analytics.processing_jobs SET status='pending',work_class='replay',
                                attempts=0,next_attempt_at=clock_timestamp(),claim_id=NULL,last_failure=NULL,failed_replay_id=NULL
                                WHERE producer_namespace=:producer AND event_id=:event AND consumer=:consumer AND consumer_version=:version
                                  AND status IN ('completed','quarantined')"""), key)
                        reserved[2] += add_total
                        reserved[3] += add_replay
                        enqueued += 1
                    last = (fact.producer_namespace, fact.event_id)
                if last:
                    row = (await session.execute(text("""UPDATE analytics.processing_replays
                        SET cursor_namespace=:namespace,cursor_event_id=:event WHERE consumer=:consumer
                          AND consumer_version=:version AND subject_ref=:subject AND request_id=:request RETURNING *"""),
                        {**args, "namespace": last[0], "event": last[1]})).one()
                if not blocked and len(page) < limit:
                    row = (await session.execute(text("""UPDATE analytics.processing_replays SET phase='drain'
                        WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject
                          AND request_id=:request RETURNING *"""), args)).one()
            await self._before_commit()
        await self._after_commit()
        return _state(row, blocked=blocked)

    async def finish(self, handle: ReplayHandle) -> ReplayState:
        async with self.database.transaction() as session:
            row, args = await self._current(session, handle)
            if row.phase == "drain":
                source_filter = " AND f.source_kind='browser_v2'" if await has_core_schema(session) else ""
                missing = (await session.execute(text("""SELECT EXISTS(SELECT 1 FROM analytics.activity_facts f
                    WHERE f.subject_ref=:subject""" + source_filter + """ AND NOT EXISTS(SELECT 1 FROM analytics.processing_receipts r
                      WHERE r.consumer=:consumer AND r.consumer_version=:version
                        AND r.producer_namespace=f.producer_namespace AND r.event_id=f.event_id
                        AND r.subject_ref=f.subject_ref))"""), args)).scalar_one()
                if not missing:
                    row = (await session.execute(text("""UPDATE analytics.processing_replays SET phase='complete'
                        WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject
                          AND request_id=:request RETURNING *"""), args)).one()
            await self._before_commit()
        await self._after_commit()
        return _state(row)
