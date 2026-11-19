"""Maintenance-only local synthetic reconciliation and aggregate diagnostics."""
import asyncio
import json
import re
from datetime import datetime
from uuid import UUID

import asyncpg
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.core_transfer import has_core_schema
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.processing_v1 import ClaimedFact, MAX_COURSES, MAX_FACT_BYTES, synthetic_delta
from pymentor_analytics.contracts.quality_v1 import Coverage, DiagnosticSnapshot, DiagnosticUnavailable, JobCount, QualityReport
from pymentor_analytics.contracts.v2 import AcceptedEvent, canonical_hash
from pymentor_analytics.contracts.work_v1 import SYNTHETIC_SUBSCRIPTIONS

_STATUSES = ("pending", "claimed", "completed", "quarantined")
_RESULT_BYTES = 160_000
_PAGE = 100
_VERSION = "synthetic-count-v1"


def _subscription(consumer, version):
    if type(version) is not int or not any((x.consumer, x.version) == (consumer, version) for x in SYNTHETIC_SUBSCRIPTIONS):
        raise ValueError("invalid synthetic consumer")


def _report(status, consumer, version, reason, *, complete=False, coverage=None,
            data_through=None, expected_count=None, actual_count=None):
    return QualityReport(status, consumer, version, _VERSION, complete, coverage,
                         data_through, expected_count, actual_count, reason)


class PostgresReconciler:
    """Checks one entire local subject ledger; never a browser report/ACL."""
    def __init__(self, database: Database):
        self.database = database

    async def _after_subject_lock(self):
        """Private test seam, called only while the shared subject lock is held."""

    async def check(self, consumer: str, version: int, subject: UUID, *, course_id: str | None = None) -> QualityReport:
        _subscription(consumer, version)
        if (not isinstance(subject, UUID) or (course_id is not None and
                (not isinstance(course_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", course_id) is None))):
            raise ValueError("invalid synthetic reconciliation scope")
        try:
            async with self.database.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": subject_lock_key(subject)})
                await self._after_subject_lock()
                fence = (await session.execute(text("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=:subject"),
                                               {"subject": subject})).one_or_none()
                if fence and fence.deleted:
                    return _report("deleted", consumer, version, "subject_deleted")
                epoch = fence.epoch if fence else 0
                source_filter = " AND f.source_kind='browser_v2'" if await has_core_schema(session) else ""
                args = {"consumer": consumer, "version": version, "subject": subject}
                dirty = bool((await session.execute(text("""SELECT 1 FROM analytics.processing_replays
                    WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject
                      AND phase<>'complete' LIMIT 1"""), args)).first())
                facts = receipts = pending = claimed = quarantined = missing_jobs = invalid = 0
                completed_without_receipt = unknown_status = False
                expected_scope = 0
                computed_courses: dict[str, int] = {}
                fingerprint = 0
                data_through: datetime | None = None
                cursor = None
                while True:
                    after = " AND (f.producer_namespace,f.event_id) > (:namespace,:event)" if cursor else ""
                    page = (await session.execute(text("""SELECT f.producer_namespace,f.event_id,f.subject_ref,
                        f.course_id,f.catalog_version,f.lesson_id,f.started_at,f.received_at,f.canonical_hash,f.hash_version,
                        octet_length(f.accepted_event::text) AS payload_bytes,j.status,
                        (r.event_id IS NOT NULL) AS applied
                        FROM analytics.activity_facts f
                        LEFT JOIN analytics.processing_jobs j ON j.consumer=:consumer AND j.consumer_version=:version
                          AND j.producer_namespace=f.producer_namespace AND j.event_id=f.event_id AND j.subject_ref=f.subject_ref
                        LEFT JOIN analytics.processing_receipts r ON r.consumer=:consumer AND r.consumer_version=:version
                          AND r.producer_namespace=f.producer_namespace AND r.event_id=f.event_id AND r.subject_ref=f.subject_ref
                        WHERE f.subject_ref=:subject""" + source_filter + after + " ORDER BY f.producer_namespace,f.event_id LIMIT :limit"),
                        {**args, "namespace": cursor[0] if cursor else None, "event": cursor[1] if cursor else None, "limit": _PAGE})).all()
                    for row in page:
                        facts += 1
                        if row.applied: receipts += 1
                        if row.status == "pending": pending += 1
                        elif row.status == "claimed": claimed += 1
                        elif row.status == "quarantined": quarantined += 1
                        elif row.status not in (None, "completed"): unknown_status = True
                        if not row.applied and row.status is None: missing_jobs += 1
                        if not row.applied and row.status == "completed": completed_without_receipt = True
                        if row.payload_bytes > MAX_FACT_BYTES:
                            invalid += 1
                            continue
                        value = (await session.execute(text("""SELECT accepted_event FROM analytics.activity_facts
                            WHERE producer_namespace=:namespace AND event_id=:event AND subject_ref=:subject"""),
                            {"namespace": row.producer_namespace, "event": row.event_id, "subject": subject})).scalar_one()
                        try:
                            record = AcceptedEvent.model_validate_json(json.dumps(value))
                            if (record.producer_namespace != row.producer_namespace or record.event.event_id != row.event_id
                                    or record.subject_ref != row.subject_ref or record.event.course_id != row.course_id
                                    or record.event.catalog_version != row.catalog_version or record.event.lesson_id != row.lesson_id
                                    or record.event.started_at != row.started_at or record.received_at != row.received_at
                                    or row.hash_version != 1 or canonical_hash(record) != row.canonical_hash):
                                raise ValueError("fact integrity mismatch")
                        except (ValidationError, ValueError, TypeError):
                            invalid += 1
                            continue
                        in_scope = course_id is None or record.event.course_id == course_id
                        if in_scope: expected_scope += 1
                        if row.applied:
                            delta = synthetic_delta((ClaimedFact(record, False),))
                            for course, count in delta.course_counts.items():
                                computed_courses[course] = computed_courses.get(course, 0) + count
                            fingerprint ^= int(delta.fingerprint_xor, 16)
                            if len(computed_courses) > MAX_COURSES:
                                return _report("unavailable", consumer, version, "result_bound_exceeded")
                            if in_scope and (data_through is None or record.received_at > data_through):
                                data_through = record.received_at
                    if len(page) < _PAGE:
                        break
                    cursor = (page[-1].producer_namespace, page[-1].event_id)
                totals = (await session.execute(text("""SELECT
                    (SELECT count(*) FROM analytics.processing_receipts WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject),
                    (SELECT count(*) FROM analytics.processing_jobs WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject)"""), args)).one()
                matched_jobs = (await session.execute(text("""SELECT count(*) FROM analytics.processing_jobs j
                    JOIN analytics.activity_facts f ON f.producer_namespace=j.producer_namespace AND f.event_id=j.event_id AND f.subject_ref=j.subject_ref
                    WHERE j.consumer=:consumer AND j.consumer_version=:version AND j.subject_ref=:subject""" + source_filter), args)).scalar_one()
                parity_error = totals[0] != receipts or totals[1] != matched_jobs
                projection = (await session.execute(text("""SELECT subject_epoch,covered_count,fingerprint_xor,
                    octet_length(courses::text) AS result_bytes FROM analytics.processing_results
                    WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject"""), args)).one_or_none()
                stored_courses = None
                projection_error = False
                stale_epoch = False
                if projection:
                    stale_epoch = projection.subject_epoch != epoch
                    if projection.result_bytes > _RESULT_BYTES:
                        projection_error = True
                    else:
                        raw_courses = (await session.execute(text("""SELECT courses FROM analytics.processing_results
                            WHERE consumer=:consumer AND consumer_version=:version AND subject_ref=:subject"""), args)).scalar_one()
                        valid_courses = (isinstance(raw_courses, dict) and len(raw_courses) <= MAX_COURSES
                                         and all(isinstance(k, str) and type(v) is int and v >= 0 for k, v in raw_courses.items()))
                        stored_courses = raw_courses if valid_courses else None
                        projection_error = (not valid_courses or raw_courses != computed_courses
                                            or projection.covered_count != receipts
                                            or projection.fingerprint_xor != f"{fingerprint:064x}")
                elif receipts:
                    projection_error = True
                coverage = Coverage(facts, receipts, pending, claimed, quarantined, missing_jobs, invalid)
                actual = None if stored_courses is None else (sum(stored_courses.values()) if course_id is None else stored_courses.get(course_id, 0))
                expected = None if invalid else expected_scope
                detail = dict(complete=True, coverage=coverage, data_through=data_through,
                              expected_count=expected, actual_count=actual)
                if dirty or pending or claimed or quarantined or stale_epoch:
                    return _report("stale", consumer, version, "replay_or_unprocessed", **detail)
                if invalid or missing_jobs or completed_without_receipt or unknown_status or parity_error or projection_error:
                    return _report("mismatch", consumer, version, "integrity_or_projection_mismatch", **detail)
                if not facts:
                    return _report("no_data", consumer, version, "empty_local_ledger", **detail)
                if course_id is not None and expected_scope == 0:
                    return _report("zero", consumer, version, "synthetic_scoped_zero_catalog_unverified", **detail)
                return _report("ok", consumer, version, "local_consistency_only", **detail)
        except (PrivacyUnavailable, TimeoutError, OSError, SQLAlchemyError, asyncpg.PostgresError):
            return _report("unavailable", consumer, version, "privacy_or_storage_unavailable")


class PostgresDiagnostics:
    """Read-only fixed aggregate snapshot; intentionally bypasses the privacy gate."""
    def __init__(self, database: Database):
        self.database = database

    async def read(self) -> DiagnosticSnapshot:
        try:
            async with asyncio.timeout(self.database.timeout):
                async with self.database.sessions.begin() as session:
                    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                    raw = (await session.execute(text("""SELECT consumer,consumer_version,status,count(*)
                        FROM analytics.processing_jobs WHERE consumer_version=1
                          AND consumer IN ('synthetic.activity','synthetic.independent')
                          AND status IN ('pending','claimed','completed','quarantined')
                        GROUP BY consumer,consumer_version,status"""))).all()
                    values = {(row.consumer, row.consumer_version, row.status): row[3] for row in raw}
                    jobs = tuple(JobCount(sub.consumer, sub.version, status, values.get((sub.consumer, sub.version, status), 0))
                                 for sub in SYNTHETIC_SUBSCRIPTIONS for status in _STATUSES)
                    known_consumers = ("'synthetic.activity','synthetic.independent','core.transfer.projection'"
                                       if await has_core_schema(session) else "'synthetic.activity','synthetic.independent'")
                    row = (await session.execute(text("""SELECT
                      (SELECT count(*) FROM analytics.activity_facts),
                      (SELECT count(*) FROM analytics.processing_jobs WHERE NOT (consumer_version=1
                        AND consumer IN (""" + known_consumers + """))),
                      (SELECT count(*) FROM analytics.processing_jobs WHERE status NOT IN ('pending','claimed','completed','quarantined')),
                      (SELECT CASE WHEN count(*)=0 THEN NULL ELSE
                        greatest(0,floor(extract(epoch FROM clock_timestamp()-min(created_at))))::bigint END
                        FROM analytics.processing_jobs WHERE status='pending'),
                      (SELECT count(*) FROM analytics.processing_jobs WHERE attempts>0 AND status IN ('pending','claimed')),
                      (SELECT count(*) FROM analytics.processing_jobs WHERE status='quarantined'),
                      (SELECT count(*) FROM analytics.processing_replays WHERE phase<>'complete'),
                      pg_database_size(current_database()),
                      (SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()),
                      (SELECT applied_through FROM analytics.privacy_state WHERE singleton=true),
                      (SELECT count(*) FROM pg_locks AS locks
                        JOIN pg_stat_activity AS activity ON activity.pid=locks.pid
                        WHERE activity.datname=current_database() AND NOT locks.granted)
                    """))).one()
                    return DiagnosticSnapshot(row[0], jobs, row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10])
        except (TimeoutError, OSError, SQLAlchemyError, asyncpg.PostgresError):
            raise DiagnosticUnavailable("synthetic diagnostics unavailable") from None
