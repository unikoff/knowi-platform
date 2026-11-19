"""Read only accepted visits for the authorized course under the deletion barrier."""
from datetime import datetime, time as day_time, timedelta
import time

from sqlalchemy import text

from pymentor_analytics.contracts.auth_v1 import AuthorizationError
from pymentor_analytics.contracts.course_visitors import (
    MAX_MEMBERSHIPS, MOSCOW, CourseVisitorCandidates, CourseVisitorReport,
)
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.adapters.postgres.activity_reports import ReportLimitExceeded

MAX_FACTS = 300_000

_FACT_SCOPE = """
    f.source_kind = 'browser_v2' AND f.course_id = :course
    AND f.started_at >= :lookup_from AND f.started_at < :at
    AND f.received_at <= :at
"""
_CANDIDATES = text("""
    SELECT DISTINCT f.subject_ref FROM analytics.activity_facts f
    WHERE """ + _FACT_SCOPE + """
    ORDER BY f.subject_ref LIMIT :subject_limit
""")
_VISIT_SNAPSHOT = """
WITH sample AS MATERIALIZED (
    SELECT f.subject_ref, f.started_at, f.accepted_event->'event' AS event
    FROM analytics.activity_facts f WHERE """ + _FACT_SCOPE + """
    LIMIT :fact_limit
), check_scope AS MATERIALIZED (
    SELECT CASE
        WHEN EXISTS (
            SELECT 1 FROM analytics.subject_fences f WHERE f.subject_ref = :actor AND f.deleted
        ) THEN 'actor_deleted'
        WHEN (SELECT count(*) FROM sample) >= :fact_limit
          OR EXISTS (
            SELECT 1 FROM sample s WHERE NOT (s.subject_ref = ANY(CAST(:locked AS uuid[])))
        ) THEN 'snapshot_changed_or_limit'
    END AS error
), visible AS MATERIALIZED (
    SELECT s.* FROM sample s CROSS JOIN check_scope c
    LEFT JOIN analytics.subject_fences f USING (subject_ref)
    WHERE c.error IS NULL AND f.deleted IS DISTINCT FROM TRUE
), segments AS MATERIALIZED (
    SELECT subject_ref, greatest(started_at, :period_from) AS starts,
           least(CAST(event->>'ended_at' AS timestamptz), :at) AS ends
    FROM visible WHERE event->>'type' = 'activity_segment'
), presence AS (
    SELECT subject_ref, CAST(started_at AT TIME ZONE 'Europe/Moscow' AS date) AS day
    FROM visible WHERE event->>'type' = 'course_opened' AND started_at >= :period_from
    UNION ALL
    SELECT s.subject_ref, CAST(d.day AS date)
    FROM segments s
    CROSS JOIN LATERAL generate_series(
        date_trunc('day', s.starts AT TIME ZONE 'Europe/Moscow'),
        date_trunc('day', (s.ends - interval '1 microsecond') AT TIME ZONE 'Europe/Moscow'),
        interval '1 day'
    ) AS d(day)
    WHERE s.starts < s.ends
), visit_days AS MATERIALIZED (
    SELECT DISTINCT subject_ref, day FROM presence
), visitor_digests AS MATERIALIZED (
    SELECT subject_ref, encode(sha256(convert_to(:nonce || CAST(subject_ref AS text), 'UTF8')), 'hex') AS digest
    FROM (SELECT DISTINCT subject_ref FROM visit_days) AS visitors
)
"""
_DISCOVER = text(_VISIT_SNAPSHOT + """
SELECT c.error, d.digest FROM check_scope c LEFT JOIN visitor_digests d ON c.error IS NULL
""")
_SUMMARY = text(_VISIT_SNAPSHOT + """
, memberships AS MATERIALIZED (
    SELECT subject_ref, CASE
        WHEN digest = ANY(CAST(:active_subscriptions AS text[])) THEN TRUE
        WHEN digest = ANY(CAST(:inactive_subscriptions AS text[])) THEN FALSE
    END AS active
    FROM visitor_digests
), membership_check AS (
    SELECT COALESCE(error, CASE
        WHEN EXISTS (SELECT 1 FROM memberships WHERE active IS NULL)
        OR (SELECT count(*) FROM memberships) <> (
            cardinality(CAST(:active_subscriptions AS text[]))
            + cardinality(CAST(:inactive_subscriptions AS text[]))
        ) THEN 'membership_changed'
    END) AS error
    FROM check_scope
), counts AS (
    SELECT day, count(*) FILTER (WHERE active) AS with_subscription,
           count(*) FILTER (WHERE NOT active) AS without_subscription
    FROM visit_days JOIN memberships USING (subject_ref) GROUP BY day
)
SELECT c.error, v.day, v.with_subscription, v.without_subscription
FROM membership_check c LEFT JOIN counts v ON c.error IS NULL
""")


async def _read_snapshot(database, grant, request, statement, *, memberships=None):
    if database.transaction_guard is None:
        raise PrivacyUnavailable()
    today = request.at.astimezone(MOSCOW).date()
    first = today - timedelta(days=29)
    start = datetime.combine(first, day_time.min, tzinfo=MOSCOW)
    params = {
        "course": grant.course_id,
        "at": request.at,
        "lookup_from": start - timedelta(minutes=1),
        "subject_limit": MAX_MEMBERSHIPS + 1,
    }
    async with database.transaction() as session:
        candidates = (await session.execute(_CANDIDATES, params)).scalars().all()
        if len(candidates) > MAX_MEMBERSHIPS:
            raise ReportLimitExceeded()
        keys = sorted({subject_lock_key(ref) for ref in {*candidates, grant.sub}})
        await session.execute(text("""
            SELECT pg_advisory_xact_lock_shared(lock_key)
            FROM unnest(CAST(:keys AS bigint[])) AS held(lock_key)
            ORDER BY lock_key
        """), {"keys": keys})
        await database.transaction_guard(session)
        if time.time() >= grant.exp:
            raise AuthorizationError()
        rows = (await session.execute(statement, {
            **params, "period_from": start, "actor": grant.sub, "locked": candidates,
            "fact_limit": MAX_FACTS + 1, "nonce": request.nonce, **(memberships or {}),
        })).mappings().all()
        if rows[0]["error"] == "actor_deleted":
            raise AuthorizationError()
        if rows[0]["error"] is not None:
            raise ReportLimitExceeded()
        await database.transaction_guard(session)
    if time.time() >= grant.exp:
        raise AuthorizationError()
    return first, rows


async def discover_course_visitor_candidates(database, grant, request):
    _, rows = await _read_snapshot(database, grant, request, _DISCOVER)
    return CourseVisitorCandidates(course_id=grant.course_id,
                                   subject_digests=sorted(row["digest"] for row in rows if row["digest"] is not None))


async def read_course_visitor_report(database, grant, request):
    first, rows = await _read_snapshot(database, grant, request, _SUMMARY, memberships={
        "active_subscriptions": request.active_subscriptions, "inactive_subscriptions": request.inactive_subscriptions,
    })
    counts = {row["day"]: row for row in rows}
    daily = []
    for offset in range(30):
        day = first + timedelta(days=offset)
        values = counts.get(day)
        daily.append({
            "date": day,
            "with_subscription": (values["with_subscription"] or None) if values else None,
            "without_subscription": (values["without_subscription"] or None) if values else None,
        })
    return CourseVisitorReport(course_id=grant.course_id, daily=daily)
