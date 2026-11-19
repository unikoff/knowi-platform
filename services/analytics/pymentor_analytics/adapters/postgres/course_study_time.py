"""Private course totals from validated activity-v1 winning intervals."""

from datetime import datetime, time, timedelta

from sqlalchemy import text

from pymentor_analytics.adapters.postgres.activity_day_reports import _DAY_SNAPSHOT_VALIDATED
from pymentor_analytics.adapters.postgres.activity_projection_reports import (
    _SNAPSHOT_SOURCES, ProjectionUnavailable, require_valid_projection,
)
from pymentor_analytics.contracts.course_visitors import (
    MAX_MEMBERSHIPS, MOSCOW, CourseStudyTimeAggregation, CourseStudyTimeReport,
    CourseVisitorCandidates,
)
from pymentor_analytics.contracts.v2 import MAX_INTERVAL_MS


# Find a superset of subjects with winning time before consulting the projection.
# Dirty projections cannot be filtered by their old course labels. The fact index
# supplies identities only. The winner is still computed across all their courses.
_COURSE_CANDIDATES = """
    SELECT DISTINCT f.subject_ref
    FROM analytics.activity_facts f
    LEFT JOIN analytics.subject_fences fence ON fence.subject_ref = f.subject_ref
    WHERE f.source_kind = 'browser_v2' AND f.course_id = :course AND f.lesson_id IS NOT NULL
      AND f.started_at >= :candidate_start AND f.started_at < :to_time
      AND fence.deleted IS DISTINCT FROM TRUE
    ORDER BY f.subject_ref LIMIT :subject_limit
"""
_COURSE_CANDIDATE_QUERY = text(_COURSE_CANDIDATES)


# The canonical reader supplies the checked full and edge projection rows.
# Keep subject identities until after summing each learner's winning time for
# the day. Presence (including a course opening with no time) is not counted.
_STUDY_TIME_GROUPS = "course_candidates AS MATERIALIZED (" + _COURSE_CANDIDATES + """),
projection_check AS MATERIALIZED (
    SELECT CASE
        WHEN p.error IS NOT NULL THEN p.error
        WHEN COALESCE((SELECT array_agg(subject_ref ORDER BY subject_ref) FROM course_candidates), ARRAY[]::uuid[])
          <> CAST(:scope_subjects AS uuid[]) THEN 'candidate_changed'
        WHEN a.invalid THEN 'projection_unavailable'
        WHEN i.full_items > :max_full_items OR i.edge_items > :max_edge_items
          OR i.full_bytes > :max_full_bytes OR i.edge_bytes > :max_edge_bytes THEN 'item_limit'
    END AS error
    FROM precheck p CROSS JOIN array_checks a CROSS JOIN item_counts i
),
parts AS (
    SELECT b.subject_ref, b.local_day, v.active_ms AS duration_ms
    FROM full_valid b CROSS JOIN LATERAL jsonb_to_recordset(b.contributions)
        AS v(course_id text, active_ms bigint)
    WHERE v.course_id = :course
    UNION ALL
    SELECT b.subject_ref, b.local_day, LEAST(v.end_ms, b.hi_ms) - GREATEST(v.start_ms, b.lo_ms)
    FROM edge_valid b CROSS JOIN LATERAL jsonb_to_recordset(b.winner_intervals)
        AS v(course_id text, start_ms bigint, end_ms bigint)
    WHERE v.course_id = :course AND v.start_ms < b.hi_ms AND v.end_ms > b.lo_ms
),
learner_days AS MATERIALIZED (
    SELECT subject_ref, local_day, sum(duration_ms) AS active_ms
    FROM parts CROSS JOIN projection_check ok
    WHERE ok.error IS NULL AND duration_ms > 0
    GROUP BY subject_ref, local_day
),
digests AS MATERIALIZED (
    SELECT subject_ref, encode(sha256(convert_to(:nonce || CAST(subject_ref AS text), 'UTF8')), 'hex') AS digest
    FROM (SELECT DISTINCT subject_ref FROM learner_days) AS learners
),
classified AS MATERIALIZED (
    SELECT subject_ref, CASE
        WHEN digest = ANY(CAST(:active_subscriptions AS text[])) THEN 'paid'
        WHEN digest = ANY(CAST(:inactive_subscriptions AS text[])) THEN 'free'
        WHEN digest = ANY(CAST(:excluded_subjects AS text[])) THEN 'excluded'
    END AS cohort
    FROM digests
    WHERE :aggregate
),
membership_check AS MATERIALIZED (
    SELECT COALESCE(error, CASE
        WHEN :aggregate AND (
            EXISTS(SELECT 1 FROM classified WHERE cohort IS NULL)
            OR (SELECT count(*) FROM classified) <> (
                cardinality(CAST(:active_subscriptions AS text[]))
                + cardinality(CAST(:inactive_subscriptions AS text[]))
                + cardinality(CAST(:excluded_subjects AS text[]))
            )
        ) THEN 'membership_changed'
        WHEN EXISTS(SELECT 1 FROM learner_days WHERE active_ms > 86400000) THEN 'projection_unavailable'
    END) AS error
    FROM projection_check
),
day_values AS (
    SELECT local_day,
        sum(active_ms) FILTER (WHERE cohort = 'paid') AS paid_ms,
        count(*) FILTER (WHERE cohort = 'paid') AS paid_learners,
        sum(active_ms) FILTER (WHERE cohort = 'free') AS free_ms,
        count(*) FILTER (WHERE cohort = 'free') AS free_learners
    FROM learner_days JOIN classified USING (subject_ref)
    WHERE cohort IN ('paid', 'free') AND (SELECT error FROM membership_check) IS NULL
    GROUP BY local_day
),
daily AS (
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'date', to_char(local_day, 'YYYY-MM-DD'),
        'with_subscription', CASE WHEN paid_learners > 0
            THEN jsonb_build_object('active_ms', paid_ms, 'learners', paid_learners) END,
        'without_subscription', CASE WHEN free_learners > 0
            THEN jsonb_build_object('active_ms', free_ms, 'learners', free_learners) END
    ) ORDER BY local_day), '[]'::jsonb) AS value
    FROM day_values
)
SELECT m.error, daily.value AS daily,
    COALESCE((SELECT array_agg(digest ORDER BY digest) FROM digests
        WHERE NOT :aggregate AND m.error IS NULL), ARRAY[]::text[]) AS subject_digests
FROM membership_check m CROSS JOIN daily
"""
_BUCKET_STUDY_SNAPSHOT = text(_SNAPSHOT_SOURCES + _STUDY_TIME_GROUPS)
_DAY_STUDY_SNAPSHOT = text(_DAY_SNAPSHOT_VALIDATED + _STUDY_TIME_GROUPS)


async def read_course_study_time(activity_reports, grant, request):
    cutoff = request.at.replace(microsecond=request.at.microsecond // 1000 * 1000)
    first = cutoff.astimezone(MOSCOW).date() - timedelta(days=29)
    start = datetime.combine(first, time.min, tzinfo=MOSCOW)
    aggregate = isinstance(request, CourseStudyTimeAggregation)
    snapshot = _DAY_STUDY_SNAPSHOT if activity_reports.day_projection_enabled else _BUCKET_STUDY_SNAPSHOT
    memberships = {
        key: getattr(request, key) if aggregate else []
        for key in ("active_subscriptions", "inactive_subscriptions", "excluded_subjects")
    }

    async def read():
        async with activity_reports._guarded_snapshot(
            start, cutoff, actor=grant.sub, selected_subject=None, subjects=None,
            expires_at=grant.exp, max_subjects=MAX_MEMBERSHIPS,
            candidate_query=_COURSE_CANDIDATE_QUERY,
            candidate_params={"course": grant.course_id, "candidate_start": start - timedelta(milliseconds=MAX_INTERVAL_MS)},
        ) as (session, args, _):
            row = (await session.execute(snapshot, {
                **args, **memberships, "nonce": request.nonce, "aggregate": aggregate,
            })).mappings().one()
            require_valid_projection(row["error"])
            await activity_reports.database.transaction_guard(session)
            try:
                if not aggregate:
                    return CourseVisitorCandidates(course_id=grant.course_id, subject_digests=row["subject_digests"])
                return _daily_report(grant.course_id, first, row["daily"])
            except (ValueError, TypeError, KeyError):
                raise ProjectionUnavailable() from None

    return await activity_reports._retry_pending(read)


def _daily_report(course_id, first, rows):
    observed = {day["date"]: day for day in rows}
    if len(observed) != len(rows) or len(rows) > 30:
        raise ValueError("invalid observed day count")
    daily = []
    for offset in range(30):
        day = first + timedelta(days=offset)
        cohorts = observed.pop(day.isoformat(), {})
        daily.append({
            "date": day, "with_subscription": cohorts.get("with_subscription"),
            "without_subscription": cohorts.get("without_subscription"),
        })
    if observed:
        raise ValueError("unexpected study date")
    return CourseStudyTimeReport(course_id=course_id, daily=daily)
