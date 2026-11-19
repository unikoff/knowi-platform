"""Bounded trusted Core completion aggregates for authorized rosters."""
import asyncio
from datetime import datetime, time as daytime, timedelta, timezone
import time
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text

from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.modules.collector.ports import IntakeUnavailable

MOSCOW = ZoneInfo("Europe/Moscow")
MAX_SUBJECTS = 80
MAX_FACTS = 100000


async def read_completion_summary(store, namespace, roster, actor, now, *, deadline, course_id=None,
                                  dashboard_subjects=None, monthly_window=None):
    """Aggregate a non-admin student roster; callers recheck authorization afterward.

    Roster maps lifecycle UUIDs to exact source revisions. The actor is excluded.
    Returned subject keys are UUIDs; Core resolves labels and applies tie ordering.
    Coverage proves a source snapshot, not cross-service atomicity or freshness.
    """
    if (not isinstance(roster, dict) or len(roster) > MAX_SUBJECTS
            or not isinstance(actor, UUID) or not isinstance(namespace, str) or not namespace
            or not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() != timedelta(0)
            or (course_id is not None and (not isinstance(course_id, str) or not 1 <= len(course_id) <= 80))
            or any(not isinstance(subject, UUID) or type(revision) is not int
                   or not 0 <= revision <= 2**63 - 1 for subject, revision in roster.items())):
        raise ValueError("invalid completion summary request")
    if getattr(store.database, "transaction_guard", None) is None:
        raise IntakeUnavailable("privacy guard unavailable")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise IntakeUnavailable()
    now = now.astimezone(timezone.utc)
    today = now.astimezone(MOSCOW).date()
    first_day = today - timedelta(days=29)
    trend_start = datetime.combine(first_day, daytime.min, MOSCOW).astimezone(timezone.utc)
    if dashboard_subjects is None:
        dashboard_subjects = set(roster) - {actor}
    if not set(dashboard_subjects) <= set(roster) or (monthly_window is not None and course_id is None):
        raise ValueError("invalid course summary scope")
    subjects = sorted(dashboard_subjects, key=lambda value: value.int)
    covered = sorted(roster, key=lambda value: value.int) if course_id is not None else subjects
    identities = set(covered) | {actor}
    monthly_count = None
    async with asyncio.timeout(remaining):
        async with store.database.transaction() as session:
            for key in sorted({subject_lock_key(subject) for subject in identities}):
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            deleted = (await session.execute(text("""SELECT EXISTS(SELECT 1 FROM analytics.subject_fences
                WHERE subject_ref=ANY(CAST(:subjects AS uuid[])) AND deleted)"""),
                {"subjects": sorted(identities, key=lambda value: value.int)})).scalar_one()
            if deleted:
                raise IntakeUnavailable("trusted roster requires refresh")
            coverage = await store._coverage(session, namespace, covered, roster)
            rows = (await session.execute(text("""WITH selected AS MATERIALIZED (
                SELECT p.subject_ref, (f.accepted_event->'payload'->>'completed_at')::timestamptz AS completed_at
                FROM analytics.core_transfer_projection p JOIN analytics.activity_facts f
                  ON f.producer_namespace=p.producer_namespace AND f.event_id=p.event_id AND f.subject_ref=p.subject_ref
                WHERE p.producer_namespace=:namespace AND p.subject_ref=ANY(CAST(:subjects AS uuid[]))
                  AND f.source_kind='core_transfer_v1' AND f.accepted_event->>'kind'='lesson_progress'
                  AND f.accepted_event->'payload'->>'completed_at' IS NOT NULL
                  AND (CAST(:course AS text) IS NULL OR f.accepted_event->'payload'->>'course_id'=:course)
                  AND (f.accepted_event->'payload'->>'completed_at')::timestamptz >= :trend_start
                  AND (f.accepted_event->'payload'->>'completed_at')::timestamptz <= :now
                LIMIT 100001
            ), aggregated AS (
                SELECT subject_ref, (completed_at AT TIME ZONE 'Europe/Moscow')::date AS day,
                    count(*) AS total, count(*) FILTER (WHERE completed_at >= :week_start) AS weekly
                FROM selected GROUP BY subject_ref, day
            )
            SELECT subject_ref, day, total, weekly, (SELECT count(*) FROM selected) AS fact_count
            FROM aggregated ORDER BY subject_ref, day"""), {
                "namespace": namespace, "subjects": subjects, "course": course_id,
                "trend_start": trend_start, "week_start": now - timedelta(days=7), "now": now,
            })).all()
            if monthly_window is not None:
                start, end = monthly_window
                monthly_count = (await session.execute(text("""SELECT count(*) FROM (
                    SELECT 1 FROM analytics.core_transfer_projection p JOIN analytics.activity_facts f
                      ON f.producer_namespace=p.producer_namespace AND f.event_id=p.event_id AND f.subject_ref=p.subject_ref
                    WHERE p.producer_namespace=:namespace AND p.subject_ref=ANY(CAST(:subjects AS uuid[]))
                      AND f.source_kind='core_transfer_v1' AND f.accepted_event->>'kind'='lesson_progress'
                      AND f.accepted_event->'payload'->>'course_id'=:course
                      AND (f.accepted_event->'payload'->>'completed_at')::timestamptz >= :start
                      AND (f.accepted_event->'payload'->>'completed_at')::timestamptz < :end
                    LIMIT 100001) AS scoped"""), {
                    "namespace": namespace, "subjects": covered, "course": course_id, "start": start, "end": end,
                })).scalar_one()
                if monthly_count > MAX_FACTS:
                    raise IntakeUnavailable("completion month exceeds bound")
    if rows and rows[0].fact_count > MAX_FACTS:
        raise IntakeUnavailable("completion snapshot exceeds bound")
    daily = {first_day + timedelta(days=offset): 0 for offset in range(30)}
    weekly = {subject: 0 for subject in subjects}
    for row in rows:
        if row.subject_ref not in weekly or row.day not in daily:
            raise IntakeUnavailable("invalid completion snapshot")
        daily[row.day] += row.total
        weekly[row.subject_ref] += row.weekly
    result = {
        "daily_lessons_completed": [{"date": day.isoformat(), "lessons_completed": count} for day, count in daily.items()],
        "lessons_completed_7d": sum(weekly.values()),
        "weekly_by_subject": weekly,
        "coverage_subjects": coverage,
    }
    if course_id is not None:
        result["completed_lessons_month"] = monthly_count
    return result
