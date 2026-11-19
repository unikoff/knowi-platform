"""Unwired PostgreSQL transfer adapter; requires the staged a007 expansion."""
import asyncio
from datetime import date, datetime, time as daytime, timedelta, timezone
import json
import time
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text

from pymentor_analytics.adapters.postgres.backpressure import PostgresBackpressure
from pymentor_analytics.adapters.postgres.activity_projection import has_activity_projection_schema
from pymentor_analytics.adapters.postgres.activity_day_projection import has_activity_day_projection_schema
from pymentor_analytics.adapters.postgres.activity_course_projection import has_activity_course_projection_schema
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient
from pymentor_analytics.contracts.core_transfer_v1 import entity_key, fingerprint, validate_event
from pymentor_analytics.contracts.locking_v1 import event_dedup_lock_key, subject_lock_key
from pymentor_analytics.contracts.privacy_control_v1 import VerifiedFrame
from pymentor_analytics.modules.collector.ports import IntakeForbidden, IntakeUnavailable
from pymentor_analytics.modules.analytics.core_transfer.history import conditional_history_days

CONSUMER = "core.transfer.projection"
VERSION = 1


async def has_core_schema(session):
    """Detect expansion in each transaction, including online a006→a007 upgrades."""
    row = (await session.execute(text("""SELECT
        EXISTS(SELECT 1 FROM pg_catalog.pg_attribute WHERE attrelid=to_regclass('analytics.activity_facts')
          AND attname='source_kind' AND NOT attisdropped),
        to_regclass('analytics.core_transfer_entities') IS NOT NULL,
        to_regclass('analytics.core_transfer_projection') IS NOT NULL"""))).one()
    if len(set(row)) != 1:
        raise IntakeUnavailable("incomplete Core transfer schema")
    return row[0]


async def has_snapshot_proof_schema(session):
    row = (await session.execute(text("""SELECT to_regclass('analytics.core_snapshot_proofs') IS NOT NULL,
        to_regprocedure('analytics.invalidate_core_snapshot_proofs(text,uuid,bigint)') IS NOT NULL,
        to_regprocedure('analytics.core_snapshot_fact_inserted()') IS NOT NULL,
        EXISTS(SELECT 1 FROM pg_catalog.pg_trigger WHERE tgrelid=to_regclass('analytics.activity_facts')
            AND tgname='core_snapshot_fact_inserted' AND NOT tgisinternal AND tgenabled IN ('O','A')
            AND tgfoid=to_regprocedure('analytics.core_snapshot_fact_inserted()') AND tgtype=5)"""))).one()
    if len(set(row)) != 1:
        raise IntakeUnavailable("incomplete snapshot proof schema")
    return row[0]


class PostgresCoreTransfer:
    def __init__(self, database, *, browser_run_id=None):
        if browser_run_id is not None and not isinstance(browser_run_id, UUID):
            raise ValueError("invalid browser projection generation")
        self.database = database
        self.browser_run_id = browser_run_id
        self.backpressure = PostgresBackpressure()
        self._projection_cursor = None

    async def _after_fact(self):
        """Fault-injection seam inside transaction."""

    async def _after_commit(self):
        """Fault-injection seam for a lost response."""

    async def accept(self, namespace, event, digest, *, deadline):
        subject, event_id = UUID(event["subject_ref"]), UUID(event["event_id"])
        args = {"ns": namespace, "event": event_id, "subject": subject, "hash": digest,
                "entity": entity_key(event), "revision": event["revision"],
                "body": json.dumps(event, ensure_ascii=False, separators=(",", ":")),
                "consumer": CONSUMER, "version": VERSION}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntakeUnavailable()
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
                deleted = (await session.execute(text("SELECT deleted FROM analytics.subject_fences WHERE subject_ref=:subject"), args)).scalar_one_or_none()
                if deleted is True:
                    raise IntakeForbidden()
                first, second = event_dedup_lock_key(namespace, event_id)
                await session.execute(text("SELECT pg_advisory_xact_lock(:first,:second)"), {"first": first, "second": second})
                prior = (await session.execute(text("""SELECT f.canonical_hash,f.subject_ref,f.source_kind,
                    EXISTS(SELECT 1 FROM analytics.core_transfer_entities e WHERE e.producer_namespace=f.producer_namespace
                      AND e.event_id=f.event_id AND e.subject_ref=f.subject_ref AND e.canonical_hash=f.canonical_hash) AS entity_present
                    FROM analytics.activity_facts f WHERE f.producer_namespace=:ns AND f.event_id=:event"""), args)).one_or_none()
                if prior:
                    if prior.canonical_hash != digest or prior.subject_ref != subject or prior.source_kind != "core_transfer_v1":
                        return "conflict"
                    if not prior.entity_present:
                        raise IntakeUnavailable("stored fact missing entity identity")
                    exists = (await session.execute(text("""SELECT
                        EXISTS(SELECT 1 FROM analytics.processing_jobs WHERE producer_namespace=:ns AND event_id=:event
                          AND subject_ref=:subject AND consumer=:consumer AND consumer_version=:version)
                        OR EXISTS(SELECT 1 FROM analytics.processing_receipts WHERE producer_namespace=:ns AND event_id=:event
                          AND subject_ref=:subject AND consumer=:consumer AND consumer_version=:version)"""), args)).scalar_one()
                    if not exists:
                        raise IntakeUnavailable("stored fact missing job and processing receipt")
                    return "accepted"
                validate_event(event)
                existing = (await session.execute(text("""SELECT canonical_hash FROM analytics.core_transfer_entities
                    WHERE producer_namespace=:ns AND subject_ref=:subject AND entity_key=:entity AND revision=:revision"""), args)).scalar_one_or_none()
                if existing is not None:
                    return "conflict"
                # Once a009 exists, every Core fact INSERT must fire its atomic
                # proof invalidation trigger, even for older supported writers.
                await has_snapshot_proof_schema(session)
                await self.backpressure.check(session, subject=subject, new_fact=True, new_jobs=1)
                await session.execute(text("""INSERT INTO analytics.activity_facts
                    (producer_namespace,event_id,subject_ref,course_id,catalog_version,lesson_id,
                     started_at,received_at,canonical_hash,hash_version,accepted_event,source_kind)
                    VALUES (:ns,:event,:subject,NULL,NULL,NULL,NULL,clock_timestamp(),:hash,1,CAST(:body AS jsonb),'core_transfer_v1')"""), args)
                await self._after_fact()
                await session.execute(text("""INSERT INTO analytics.core_transfer_entities
                    (producer_namespace,event_id,subject_ref,entity_key,revision,canonical_hash)
                    VALUES (:ns,:event,:subject,:entity,:revision,:hash)"""), args)
                await session.execute(text("""INSERT INTO analytics.processing_jobs
                    (producer_namespace,event_id,subject_ref,consumer,consumer_version)
                    VALUES (:ns,:event,:subject,:consumer,:version)"""), args)
            await self._after_commit()
        return "accepted"

    async def receipt(self, namespace, event_id, digest, *, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntakeUnavailable()
        args = {"ns": namespace, "event": event_id}
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                # Discover subject without holding a row lock; re-read under its lock.
                subject = (await session.execute(text("""SELECT subject_ref FROM analytics.activity_facts
                    WHERE producer_namespace=:ns AND event_id=:event"""), args)).scalar_one_or_none()
                if subject is None:
                    return "absent"
                args["subject"] = subject
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
                deleted = (await session.execute(text("SELECT deleted FROM analytics.subject_fences WHERE subject_ref=:subject"), args)).scalar_one_or_none()
                if deleted is True:
                    raise IntakeForbidden()
                row = (await session.execute(text("""SELECT f.canonical_hash,f.source_kind,
                    EXISTS(SELECT 1 FROM analytics.core_transfer_entities e WHERE e.producer_namespace=f.producer_namespace
                      AND e.event_id=f.event_id AND e.subject_ref=f.subject_ref AND e.canonical_hash=f.canonical_hash) AS entity_present,
                    (EXISTS(SELECT 1 FROM analytics.processing_jobs j WHERE j.producer_namespace=f.producer_namespace
                      AND j.event_id=f.event_id AND j.subject_ref=f.subject_ref
                      AND j.consumer=:consumer AND j.consumer_version=:version)
                    OR EXISTS(SELECT 1 FROM analytics.processing_receipts r WHERE r.producer_namespace=f.producer_namespace
                      AND r.event_id=f.event_id AND r.subject_ref=f.subject_ref
                      AND r.consumer=:consumer AND r.consumer_version=:version)) AS job
                    FROM analytics.activity_facts f WHERE f.producer_namespace=:ns AND f.event_id=:event
                    AND f.subject_ref=:subject"""), {**args, "consumer": CONSUMER, "version": VERSION})).one_or_none()
                if row is None:
                    return "absent"
                if row.canonical_hash != digest or row.source_kind != "core_transfer_v1":
                    return "conflict"
                if not row.job or not row.entity_present:
                    raise IntakeUnavailable("stored fact missing entity identity or job")
                return "accepted"

    async def read_subject(self, namespace, subject, today, *, deadline, visit_through=None, require_coverage=False,
                           actor=None, include_all_visits=False):
        """Bounded internal snapshot. Caller must authenticate and authorize subject."""
        if (not isinstance(subject, UUID) or not isinstance(today, date) or isinstance(today, datetime)
                or (visit_through is not None and (not isinstance(visit_through, date) or isinstance(visit_through, datetime)))
                or (actor is not None and not isinstance(actor, UUID)) or type(include_all_visits) is not bool):
            raise ValueError("invalid subject or date")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntakeUnavailable()
        tz = ZoneInfo("Europe/Moscow")
        start = datetime.combine(today - timedelta(days=6), daytime.min, tz).astimezone(timezone.utc)
        end = datetime.combine(today + timedelta(days=1), daytime.min, tz).astimezone(timezone.utc)
        args = {"ns": namespace, "subject": subject, "today": (visit_through or today).isoformat(), "start": start, "end": end}
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                identities = {subject} | ({actor} if actor is not None else set())
                for key in sorted({subject_lock_key(identity) for identity in identities}):
                    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
                if actor is not None and actor != subject:
                    deleted = (await session.execute(text("SELECT deleted FROM analytics.subject_fences WHERE subject_ref=:subject"),
                                                     {"subject": actor})).scalar_one_or_none()
                    if deleted is True:
                        raise IntakeForbidden()
                fence = (await session.execute(text("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=:subject"), args)).one_or_none()
                if fence and fence.deleted:
                    raise IntakeForbidden()
                coverage = None
                if require_coverage:
                    coverage = await self._coverage(session, namespace, [subject], {subject: None}, {subject: fence.epoch if fence else 0})
                browser = await self._browser_days(session, [subject], end=visit_through or today) if self.browser_run_id else None
                base = """ FROM analytics.core_transfer_projection p JOIN analytics.activity_facts f
                    ON f.producer_namespace=p.producer_namespace AND f.event_id=p.event_id AND f.subject_ref=p.subject_ref
                    WHERE p.producer_namespace=:ns AND p.subject_ref=:subject AND f.source_kind='core_transfer_v1' """
                cutoff = "" if include_all_visits else " AND f.accepted_event->'payload'->>'activity_date' <= :today "
                visits = (await session.execute(text("SELECT f.accepted_event->'payload' " + base + """
                    AND f.accepted_event->>'kind'='legacy_activity_day' """ + cutoff + """
                    ORDER BY f.accepted_event->'payload'->>'activity_date' LIMIT 10001"""), args)).scalars().all()
                completions = (await session.execute(text("SELECT f.accepted_event->'payload' " + base + """
                    AND f.accepted_event->>'kind'='lesson_progress'
                    AND f.accepted_event->'payload'->>'completed_at' IS NOT NULL
                    AND (f.accepted_event->'payload'->>'completed_at')::timestamptz >= :start
                    AND (f.accepted_event->'payload'->>'completed_at')::timestamptz < :end
                    ORDER BY p.entity_key LIMIT 5001"""), args)).scalars().all()
                if len(visits) > 10000 or len(completions) > 5000:
                    raise IntakeUnavailable("legacy snapshot exceeds bound")
                merged = {v["activity_date"]: {"activity_date": v["activity_date"], "visited_at": v["visited_at"]} for v in visits}
                if browser is not None:
                    for row in browser["days"]:
                        merged.setdefault(row["day"], {"activity_date": row["day"], "visited_at": None})
                if len(merged) > 10000:
                    raise IntakeUnavailable("legacy snapshot exceeds bound")
                result = (list(merged.values()), completions)
                if browser is not None:
                    result = (*result, coverage[0] if coverage else None, browser["as_of"])
                    return result
                return (*result, coverage[0]) if require_coverage else result

    async def read_history(self, namespace, subject, start, end, *, deadline, actor=None):
        """Read Core dates only. Caller authenticates and authorizes the subject."""
        if (not isinstance(subject, UUID) or (actor is not None and not isinstance(actor, UUID))
                or type(start) is not date or type(end) is not date or not 0 <= (end - start).days <= 30):
            raise ValueError("invalid subject or date range")
        if self.database.transaction_guard is None:
            raise IntakeUnavailable("privacy barrier unavailable")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntakeUnavailable()
        args = {"ns": namespace, "subject": subject, "start": start.isoformat(), "end": end.isoformat()}
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                for key in sorted({subject_lock_key(identity) for identity in ({subject} | ({actor} if actor else set()))}):
                    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
                for identity in {subject} | ({actor} if actor else set()):
                    fence = (await session.execute(text("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=:subject"),
                                                   {"subject": identity})).one_or_none()
                    if fence and fence.deleted:
                        raise IntakeForbidden()
                    if identity == subject:
                        epoch = fence.epoch if fence else 0
                coverage = (await self._coverage(session, namespace, [subject], {subject: None}, {subject: epoch}))[0]
                dates = (await session.execute(text("""SELECT DISTINCT f.accepted_event->'payload'->>'activity_date' AS day
                    FROM analytics.activity_facts f
                    WHERE f.producer_namespace=:ns AND f.subject_ref=:subject AND f.source_kind='core_transfer_v1'
                      AND f.accepted_event->>'kind'='legacy_activity_day'
                      AND f.accepted_event->'revision'='0'::jsonb
                      AND f.accepted_event->'payload'->>'activity_date' BETWEEN :start AND :end
                    ORDER BY day LIMIT 10001"""), args)).scalars().all()
                if len(dates) > 10000:
                    raise IntakeUnavailable("history snapshot exceeds bound")
                return {"days": conditional_history_days(dates), "coverage": coverage}

    async def read_admin(self, namespace, subjects, today, *, deadline, actor=None, required_revisions=None):
        """Bounded authorized roster; callers own roster completeness and access."""
        if not isinstance(today, date) or isinstance(today, datetime) or not isinstance(subjects, (list, tuple, set, frozenset)):
            raise ValueError("invalid roster")
        if len(subjects) > 200 or any(not isinstance(subject, UUID) for subject in subjects) or (actor is not None and not isinstance(actor, UUID)):
            raise ValueError("roster exceeds bound")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntakeUnavailable()
        ordered = sorted(set(subjects), key=lambda value: value.int)
        if required_revisions is not None and (not isinstance(required_revisions, dict) or set(required_revisions) != set(ordered)
                or any(type(value) is not int or not 0 <= value <= 2**63-1 for value in required_revisions.values())):
            raise ValueError("invalid required revision vector")
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                identities = set(ordered) | ({actor} if actor is not None else set())
                for key in sorted({subject_lock_key(subject) for subject in identities}):
                    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
                if actor is not None:
                    dead = (await session.execute(text("SELECT deleted FROM analytics.subject_fences WHERE subject_ref=:subject"), {"subject": actor})).scalar_one_or_none()
                    if dead is True:
                        raise IntakeForbidden()
                args = {"ns": namespace, "subjects": ordered, "start": (today - timedelta(days=29)).isoformat(), "end": today.isoformat()}
                stale = (await session.execute(text("""SELECT EXISTS(SELECT 1 FROM analytics.subject_fences
                    WHERE subject_ref=ANY(CAST(:subjects AS uuid[])) AND deleted)"""), args)).scalar_one()
                if stale:
                    raise IntakeUnavailable("trusted roster requires refresh")
                coverage = await self._coverage(session, namespace, ordered, required_revisions) if required_revisions is not None else None
                browser = await self._browser_days(session, ordered, start=today - timedelta(days=29), end=today) if self.browser_run_id else None
                rows = (await session.execute(text("""SELECT p.subject_ref, f.accepted_event->'payload' AS payload
                    FROM analytics.core_transfer_projection p JOIN analytics.activity_facts f
                      ON f.producer_namespace=p.producer_namespace AND f.event_id=p.event_id AND f.subject_ref=p.subject_ref
                    WHERE p.producer_namespace=:ns AND p.subject_ref=ANY(CAST(:subjects AS uuid[]))
                      AND f.source_kind='core_transfer_v1' AND f.accepted_event->>'kind'='legacy_activity_day'
                      AND f.accepted_event->'payload'->>'activity_date' BETWEEN :start AND :end
                      AND NOT EXISTS (SELECT 1 FROM analytics.subject_fences s WHERE s.subject_ref=p.subject_ref AND s.deleted)
                    ORDER BY p.subject_ref,p.entity_key LIMIT 6001"""), args)).all()
                if len(rows) > 6000:
                    raise IntakeUnavailable("admin snapshot exceeds bound")
                visits = [{"subject_ref": str(row.subject_ref), "activity_date": row.payload["activity_date"]} for row in rows]
                if browser is not None:
                    present = {(row["subject_ref"], row["activity_date"]) for row in visits}
                    visits.extend({"subject_ref": row["subject_ref"], "activity_date": row["day"]}
                                  for row in browser["days"] if (row["subject_ref"], row["day"]) not in present)
                    return visits, coverage, browser["as_of"]
                return (visits, coverage) if required_revisions is not None else visits

    async def read_completion_summary(self, namespace, roster, actor, at, *, deadline, **scope):
        from pymentor_analytics.adapters.postgres.core_completion_summary import read_completion_summary
        return await read_completion_summary(self, namespace, roster, actor, at, deadline=deadline, **scope)

    async def _browser_days(self, session, subjects, *, start=None, end):
        """Subject locks are held; one snapshot checks every bucket before using days."""
        if self.database.transaction_guard is None:
            raise IntakeUnavailable("privacy barrier unavailable")
        await self.database.transaction_guard(session)
        locked = (await session.execute(text("SELECT analytics.lock_activity_projection_run(:run)"),
                                        {"run": self.browser_run_id})).scalar_one()
        if locked is not True:
            raise IntakeUnavailable("activity projection backfill incomplete")
        await self.database.transaction_guard(session)
        tz = ZoneInfo("Europe/Moscow")
        lower = datetime.combine(start, daytime.min, tz).astimezone(timezone.utc) if start else None
        upper = datetime.combine(end + timedelta(days=1), daytime.min, tz).astimezone(timezone.utc)
        row = (await session.execute(text("""WITH bounds AS MATERIALIZED (
            SELECT LEAST(CAST(:upper AS timestamptz),statement_timestamp()) AS hi,
                   floor(extract(epoch FROM LEAST(CAST(:upper AS timestamptz),statement_timestamp()))*1000)::bigint AS hi_ms,
                   statement_timestamp() AS as_of
        ), selected AS MATERIALIZED (
            SELECT b.subject_ref,b.bucket_start,b.has_presence,b.state,b.generation,b.projection_generation,b.subject_epoch,
                   b.edge_item_count,b.edge_json_bytes,coalesce(f.epoch,0) AS fence_epoch,coalesce(f.deleted,false) AS deleted
            FROM analytics.activity_projection_buckets b LEFT JOIN analytics.subject_fences f USING(subject_ref)
            WHERE b.subject_ref=ANY(CAST(:subjects AS uuid[])) AND b.metric_version='activity-v1'
              AND (CAST(:lower AS timestamptz) IS NULL OR b.bucket_start>=:lower)
              AND b.bucket_start<(SELECT hi FROM bounds) LIMIT 300001
        ), checks AS (SELECT count(*) AS n, coalesce(bool_or(state<>'ready' OR generation IS DISTINCT FROM projection_generation
               OR subject_epoch IS DISTINCT FROM fence_epoch OR deleted OR has_presence IS NULL
               OR edge_item_count IS NULL OR edge_json_bytes IS NULL OR edge_item_count<0 OR edge_json_bytes<0),false) AS bad,
               coalesce(sum(CASE WHEN bucket_start+interval '15 minutes'>(SELECT hi FROM bounds) THEN edge_item_count ELSE 0 END),0) AS edge_items,
               coalesce(sum(CASE WHEN bucket_start+interval '15 minutes'>(SELECT hi FROM bounds) THEN edge_json_bytes ELSE 0 END),0) AS edge_bytes
               FROM selected),
        edge AS MATERIALIZED (
            SELECT s.subject_ref,s.bucket_start,b.presence_intervals,b.presence_points
            FROM selected s JOIN analytics.activity_projection_buckets b
              ON b.subject_ref=s.subject_ref AND b.bucket_start=s.bucket_start AND b.metric_version='activity-v1'
            CROSS JOIN checks c CROSS JOIN bounds p
            WHERE c.n<=300000 AND NOT c.bad AND c.edge_items<=100000 AND c.edge_bytes<=33554432
              AND s.has_presence AND s.bucket_start+interval '15 minutes'>p.hi
        ), presence AS MATERIALIZED (
            SELECT e.subject_ref,e.bucket_start FROM edge e CROSS JOIN bounds p
            WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(e.presence_intervals) v
                          WHERE (v->>'start_ms')::bigint<p.hi_ms)
               OR EXISTS (SELECT 1 FROM jsonb_array_elements(e.presence_points) v
                          WHERE (v->>'at_ms')::bigint<p.hi_ms)
        ), days AS (SELECT DISTINCT subject_ref,(bucket_start AT TIME ZONE 'Europe/Moscow')::date AS day FROM (
                 SELECT s.subject_ref,s.bucket_start FROM selected s CROSS JOIN bounds p
                 WHERE s.has_presence AND s.bucket_start+interval '15 minutes'<=p.hi
                 UNION ALL SELECT subject_ref,bucket_start FROM presence) observed LIMIT 10001)
        SELECT checks.n,checks.bad,count(days.day) AS day_count,
               coalesce(jsonb_agg(jsonb_build_object('subject_ref',days.subject_ref,'day',days.day))
                        FILTER (WHERE days.day IS NOT NULL),'[]'::jsonb) AS days,
               checks.edge_items,checks.edge_bytes,(SELECT as_of FROM bounds) AS as_of
               FROM checks LEFT JOIN days ON true GROUP BY checks.n,checks.bad,checks.edge_items,checks.edge_bytes"""),
            {"subjects": subjects, "lower": lower, "upper": upper})).one()
        if row.n > 300000 or row.bad or row.day_count > 10000 or row.edge_items > 100000 or row.edge_bytes > 33554432:
            raise IntakeUnavailable("activity projection unavailable or exceeds bounds")
        return {"days": row.days, "as_of": row.as_of.astimezone(timezone.utc).isoformat()}

    async def _coverage(self, session, namespace, subjects, required_revisions, epochs=None):
        """Called only while all listed subject locks and the privacy guard are held."""
        if not subjects:
            return []
        rows = (await session.execute(text("""SELECT u.subject_ref,u.required_cut,p.cut_revision,p.subject_epoch,
            p.source_created_at,coalesce(f.epoch,0) AS current_epoch,
            EXISTS(SELECT 1 FROM analytics.core_transfer_projection q WHERE q.producer_namespace=:ns
                AND q.subject_ref=u.subject_ref AND q.revision>p.cut_revision) AS newer_projection
            FROM unnest(CAST(:subjects AS uuid[]),CAST(:cuts AS bigint[])) WITH ORDINALITY AS u(subject_ref,required_cut,position)
            LEFT JOIN analytics.core_snapshot_proofs p ON p.producer_namespace=:ns AND p.subject_ref=u.subject_ref AND p.status='verified'
            LEFT JOIN analytics.subject_fences f ON f.subject_ref=u.subject_ref ORDER BY u.position"""),
            {"ns": namespace, "subjects": subjects,
             "cuts": [required_revisions[subject] for subject in subjects]})).all()
        if len(rows) != len(subjects):
            raise IntakeUnavailable("snapshot coverage unavailable")
        output = []
        for row in rows:
            if (row.cut_revision is None or row.subject_epoch != row.current_epoch or row.newer_projection
                    or (row.required_cut is not None and row.cut_revision != row.required_cut)
                    or (epochs is not None and row.current_epoch != epochs[row.subject_ref])):
                raise IntakeUnavailable("snapshot coverage unavailable")
            output.append({"subject_ref": str(row.subject_ref), "coverage_through_revision": row.cut_revision,
                           "source_snapshot_at": row.source_created_at.astimezone(timezone.utc).isoformat()})
        return output

    async def project_one(self, *, deadline):
        """One short atomic transaction, no leased work or external calls held open.

        The subject advisory lock fences deletion and concurrent processors; job,
        latest revision and receipt commit together. Crash rolls all three back.
        """
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntakeUnavailable()
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                query = """SELECT j.subject_ref, min(j.created_at) AS oldest FROM analytics.processing_jobs j
                    WHERE j.consumer=:consumer AND j.consumer_version=:version AND j.status='pending'
                      AND j.next_attempt_at<=clock_timestamp()
                      AND NOT EXISTS (SELECT 1 FROM analytics.subject_fences s WHERE s.subject_ref=j.subject_ref AND s.deleted)
                    GROUP BY j.subject_ref """
                args = {"consumer": CONSUMER, "version": VERSION}
                cursor = self._projection_cursor
                after = " HAVING (min(j.created_at),j.subject_ref) > (:after_time,:after_subject)" if cursor else ""
                if cursor:
                    args.update(after_time=cursor[0], after_subject=cursor[1])
                row = (await session.execute(text(query + after + " ORDER BY oldest,j.subject_ref LIMIT 1"), args)).one_or_none()
                if row is None and cursor:
                    row = (await session.execute(text(query + " ORDER BY oldest,j.subject_ref LIMIT 1"), args)).one_or_none()
            if row is None:
                self._projection_cursor = None
                return False
            self._projection_cursor = (row.oldest, row.subject_ref)
            candidate = row.subject_ref
            async with self.database.transaction() as session:
                locked = (await session.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": subject_lock_key(candidate)})).scalar_one()
                if not locked:
                    return False
                args = {"subject": candidate, "consumer": CONSUMER, "version": VERSION}
                deleted = (await session.execute(text("SELECT deleted FROM analytics.subject_fences WHERE subject_ref=:subject"), args)).scalar_one_or_none()
                if deleted is True:
                    return False
                row = (await session.execute(text("""SELECT j.producer_namespace,j.event_id,f.accepted_event,f.canonical_hash,
                    e.entity_key,e.revision FROM analytics.processing_jobs j
                    JOIN analytics.activity_facts f USING (producer_namespace,event_id,subject_ref)
                    JOIN analytics.core_transfer_entities e USING (producer_namespace,event_id,subject_ref)
                    WHERE j.consumer=:consumer AND j.consumer_version=:version AND j.subject_ref=:subject
                      AND f.source_kind='core_transfer_v1'
                      AND j.status='pending' AND j.next_attempt_at<=clock_timestamp()
                    ORDER BY j.created_at,j.producer_namespace,j.event_id LIMIT 1 FOR UPDATE OF j SKIP LOCKED"""), args)).one_or_none()
                if row is None:
                    return False
                try:
                    event = validate_event(row.accepted_event)
                    if fingerprint(event) != row.canonical_hash or entity_key(event) != row.entity_key or event["revision"] != row.revision or event["subject_ref"] != str(candidate) or event["event_id"] != str(row.event_id):
                        raise ValueError
                except (ValueError, TypeError, UnicodeError):
                    await session.execute(text("""UPDATE analytics.processing_jobs SET status='quarantined',last_failure='invalid_transfer_fact'
                        WHERE producer_namespace=:ns AND event_id=:event AND subject_ref=:subject
                        AND consumer=:consumer AND consumer_version=:version"""),
                        {**args, "ns": row.producer_namespace, "event": row.event_id})
                    return False
                args.update(ns=row.producer_namespace, event=row.event_id, entity=row.entity_key, revision=row.revision)
                await session.execute(text("""INSERT INTO analytics.core_transfer_projection
                    (producer_namespace,subject_ref,entity_key,revision,event_id)
                    VALUES (:ns,:subject,:entity,:revision,:event)
                    ON CONFLICT (producer_namespace,subject_ref,entity_key) DO UPDATE
                    SET revision=excluded.revision,event_id=excluded.event_id
                    WHERE analytics.core_transfer_projection.revision < excluded.revision"""), args)
                await session.execute(text("""INSERT INTO analytics.processing_receipts
                    (producer_namespace,event_id,subject_ref,consumer,consumer_version)
                    VALUES (:ns,:event,:subject,:consumer,:version) ON CONFLICT DO NOTHING"""), args)
                await session.execute(text("""UPDATE analytics.processing_jobs SET status='completed'
                    WHERE producer_namespace=:ns AND event_id=:event AND subject_ref=:subject
                    AND consumer=:consumer AND consumer_version=:version"""), args)
                if await has_snapshot_proof_schema(session):
                    await session.execute(text("""UPDATE analytics.core_snapshot_proofs SET status='pending',attempts=0,
                        next_attempt_at=clock_timestamp(),last_failure=NULL WHERE producer_namespace=:ns
                        AND subject_ref=:subject AND cut_revision>=:revision AND status IN ('pending','quarantined')"""), args)
            return True


class CheckedDeletionStatus:
    """Fresh signed authority page plus a fenced, complete local purge check.

    The caller supplies a dedicated AuthorityClient instance; attach invalidates
    that client's previous session and must not share PrivacyRuntime's client.
    The database role needs read-only access to the listed privacy obligations.
    """
    def __init__(self, database, authority_client, *, producer_by_namespace):
        if not isinstance(authority_client, AuthorityClient):
            raise ValueError("verified AuthorityClient is required")
        self.database = database
        self.client = authority_client
        self.producers = dict(producer_by_namespace)
        if not self.producers or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in self.producers.items()):
            raise ValueError("invalid authority producer mapping")
        self._lock = asyncio.Lock()

    async def status(self, namespace, control_id, subject_ref, epoch, *, deadline):
        producer = self.producers.get(namespace)
        if producer is None or not isinstance(control_id, UUID) or not isinstance(subject_ref, UUID) or type(epoch) is not int or not 1 <= epoch <= 2**63-1:
            raise ValueError("invalid deletion identity")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntakeUnavailable()
        async with asyncio.timeout(remaining):
            async with self._lock:
                async with self.database.transaction() as session:
                    prior = (await session.execute(text("""SELECT seq FROM analytics.privacy_receipts
                        WHERE control_id=:control"""), {"control": control_id})).scalar_one_or_none()
                if prior is None:
                    return "pending"
                attach = await self.client.attach(prior - 1)
                if not isinstance(attach, VerifiedFrame) or not attach.is_fresh():
                    raise IntakeUnavailable("authority attachment unavailable")
                barrier = await self.client.barrier(attach.frame.session)
                if not isinstance(barrier, VerifiedFrame) or not barrier.is_fresh() or barrier.frame.high_watermark < prior:
                    raise IntakeUnavailable("authority barrier unavailable")
                page = await self.client.page(attach.frame.session, prior - 1, prior, limit=1)
                if (not isinstance(page, VerifiedFrame) or not page.is_fresh() or page.frame.authority_id != self.client.config.authority_id
                        or page.frame.session != attach.frame.session or len(page.frame.entries) != 1
                        or page.frame.high_watermark < barrier.frame.high_watermark):
                    raise IntakeUnavailable("authority page unavailable")
                entry = page.frame.entries[0]
                if (entry.seq != prior or entry.control.control_id != control_id or entry.producer != producer
                        or entry.control.subject_ref != subject_ref or entry.control.epoch != epoch):
                    return "conflict"
                args = {"subject": subject_ref, "control": control_id, "seq": prior}
                async with self.database.transaction() as session:
                    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject_ref)})
                    if not await has_core_schema(session):
                        raise IntakeUnavailable("Core schema unavailable")
                    row = (await session.execute(text("""SELECT r.payload_hash,r.subject_ref,r.fence_epoch,r.phase,
                        s.authority_id,s.applied_through,f.epoch AS current_epoch,f.deleted
                        FROM analytics.privacy_receipts r CROSS JOIN analytics.privacy_state s
                        LEFT JOIN analytics.subject_fences f ON f.subject_ref=r.subject_ref
                        WHERE r.seq=:seq AND r.control_id=:control AND s.singleton=true"""), args)).one_or_none()
                    if row is None:
                        return "pending"
                    if row.payload_hash != entry.payload_hash or row.subject_ref != subject_ref or row.authority_id != page.frame.authority_id:
                        raise IntakeUnavailable("local privacy journal mismatch")
                    if row.phase != "complete" or row.applied_through < page.frame.high_watermark or not row.deleted or row.current_epoch < epoch or row.fence_epoch < epoch:
                        return "pending"
                    proof_check = (" OR EXISTS(SELECT 1 FROM analytics.core_snapshot_proofs WHERE subject_ref=:subject)"
                                   if await has_snapshot_proof_schema(session) else "")
                    if await has_activity_projection_schema(session):
                        proof_check += " OR EXISTS(SELECT 1 FROM analytics.activity_projection_buckets WHERE subject_ref=:subject)"
                    if await has_activity_day_projection_schema(session):
                        proof_check += (" OR EXISTS(SELECT 1 FROM analytics.activity_projection_days WHERE subject_ref=:subject)"
                                        " OR EXISTS(SELECT 1 FROM analytics.activity_projection_day_sources WHERE subject_ref=:subject)")
                    if await has_activity_course_projection_schema(session):
                        proof_check += (" OR EXISTS(SELECT 1 FROM analytics.activity_course_buckets WHERE subject_ref=:subject)"
                                        " OR EXISTS(SELECT 1 FROM analytics.activity_course_days WHERE subject_ref=:subject)")
                    remaining_data = (await session.execute(text("""SELECT
                        EXISTS(SELECT 1 FROM analytics.activity_facts WHERE subject_ref=:subject)
                        OR EXISTS(SELECT 1 FROM analytics.processing_jobs WHERE subject_ref=:subject)
                        OR EXISTS(SELECT 1 FROM analytics.processing_receipts WHERE subject_ref=:subject)
                        OR EXISTS(SELECT 1 FROM analytics.processing_results WHERE subject_ref=:subject)
                        OR EXISTS(SELECT 1 FROM analytics.processing_leases WHERE subject_ref=:subject)
                        OR EXISTS(SELECT 1 FROM analytics.processing_replays WHERE subject_ref=:subject)
                        OR EXISTS(SELECT 1 FROM analytics.admission_subject_windows WHERE subject_ref=:subject)
                        OR EXISTS(SELECT 1 FROM analytics.core_transfer_entities WHERE subject_ref=:subject)
                        OR EXISTS(SELECT 1 FROM analytics.core_transfer_projection WHERE subject_ref=:subject)""" + proof_check), args)).scalar_one()
                    if remaining_data or not page.is_fresh():
                        return "pending"
                    return "accepted"
