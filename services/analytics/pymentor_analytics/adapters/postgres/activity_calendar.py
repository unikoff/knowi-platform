"""Bounded self calendar: registry first, materialize only changed days."""
from datetime import timedelta
from hashlib import sha256
import json
from sqlalchemy import text

from pymentor_analytics.adapters.postgres.activity_history_revisions import MOSCOW, encode_history_generation, whole_days
from pymentor_analytics.adapters.postgres.activity_projection_reports import (
    MAX_SUBJECTS, ProjectionPending, ProjectionUnavailable, _validated_daily, require_valid_projection,
)
from pymentor_analytics.contracts.auth_v1 import AuthorizationError
from pymentor_analytics.contracts.reports_v1 import ActivityValues
from pymentor_analytics.modules.analytics.reports.activity_scope import resolve_scope

_REGISTRY = """
WITH metadata AS MATERIALIZED (
 SELECT date_trunc('milliseconds', statement_timestamp()) AS now, pg_postmaster_start_time() AS started,
        (SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS db_oid,
        current_database() AS db_name,
        EXISTS (SELECT 1 FROM analytics.activity_projection_backfill WHERE singleton AND phase='complete' AND run_id=:run_id)
        AND EXISTS (SELECT 1 FROM analytics.activity_projection_day_backfill WHERE singleton AND phase='complete' AND run_id=:run_id) AS valid,
        COALESCE((SELECT epoch FROM analytics.subject_fences WHERE subject_ref=:actor),0) AS epoch,
        COALESCE((SELECT deleted FROM analytics.subject_fences WHERE subject_ref=:actor),false) AS deleted
), days AS (
 SELECT generate_series(CAST(:first_day AS date), CAST(:last_day AS date), interval '1 day')::date AS day
)
SELECT m.*, days.day, s.generation, s.state
FROM metadata m CROSS JOIN days
LEFT JOIN analytics.activity_projection_day_sources s ON s.subject_ref=:actor AND s.local_day=days.day AND s.metric_version='activity-v1'
ORDER BY days.day
"""


def _sparse_statement(snapshot):
    day = "/* calendar-day-filter */"
    bucket = "/* calendar-bucket-filter */"
    if snapshot.count(day) != 1 or snapshot.count(bucket) != 1:
        raise ProjectionUnavailable()
    return snapshot.replace(day, "AND s.local_day=ANY(CAST(:fetch_days AS date[]))", 1).replace(
        bucket, "AND (b.bucket_start AT TIME ZONE 'UTC' + interval '3 hours')::date=ANY(CAST(:fetch_days AS date[]))", 1)


def _token(generation, day, source_generation, epoch):
    values = ["calendar-registry-v1", generation, day.isoformat(), source_generation, epoch]
    return sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


async def read_calendar(store, grant, query, known_generation, known_revisions):
    scope = resolve_scope(grant, query)
    if (not store.day_projection_enabled or grant.role != "user" or grant.scope != "report:read"
            or grant.global_read or grant.subject_refs or query.subject_ref is not None or query.course_id is None
            or query.lesson_id is not None or not whole_days(query)):
        raise AuthorizationError()
    first = query.from_time.astimezone(MOSCOW).date()
    last = query.to_time.astimezone(MOSCOW).date() - timedelta(days=1)
    async with store._guarded_snapshot(query.from_time, query.to_time, actor=scope.actor,
            selected_subject=scope.selected_subject, subjects=scope.subjects, expires_at=grant.expires_at,
            max_subjects=MAX_SUBJECTS) as (session, args, snapshot):
        rows = (await session.execute(text(_REGISTRY), {**args, "first_day": first, "last_day": last})).mappings().all()
        if not rows or not rows[0]["valid"]:
            raise ProjectionUnavailable()
        if rows[0]["deleted"]:
            raise AuthorizationError()
        meta = rows[0]
        generation = encode_history_generation(subject=scope.actor, course=query.course_id, run_id=store.expected_run_id,
            started=meta["started"], db_oid=meta["db_oid"], db_name=meta["db_name"])
        today = meta["now"].astimezone(MOSCOW).date()
        revisions, reused, fetch = [], [], []
        known = {item.date: item.token for item in known_revisions} if known_generation == generation else {}
        # Ready is published atomically with the matching payload under the
        # subject lock. Out-of-band payload edits that bypass the source
        # generation cannot be detected without reading reused payloads.
        for row in rows:
            day, source, state = row["day"], row["generation"], row["state"]
            if source is not None:
                if state in ("dirty", "claimed"):
                    raise ProjectionPending()
                if state != "ready":
                    raise ProjectionUnavailable()
            if today-timedelta(days=29) <= day <= today-timedelta(days=2):
                token = _token(generation, day, source, meta["epoch"])
                revisions.append({"date": day.isoformat(), "token": token})
                if known.get(day) == token:
                    reused.append(day.isoformat())
                    continue
            fetch.append(day)
        daily, as_of = [], meta["now"]
        if fetch:
            args.update(all_courses=False, courses=[query.course_id], lesson_id=None, with_daily=True, with_breakdown=False,
                        breakdown_limit=20001, max_breakdown=20000, fetch_days=fetch)
            if store.course_projection_enabled:
                from pymentor_analytics.adapters.postgres.activity_course_reports import _COURSE_SNAPSHOT
                row = (await session.execute(text(_sparse_statement(_COURSE_SNAPSHOT)), args)).mappings().one()
                if row["error"] == "course_projection_pending":
                    row = (await session.execute(text(_sparse_statement(snapshot)), args)).mappings().one()
            else:
                row = (await session.execute(text(_sparse_statement(snapshot)), args)).mappings().one()
            require_valid_projection(row["error"])
            try:
                totals = ActivityValues.model_validate(row["totals"]).model_dump()
                daily = _validated_daily(row["daily"], totals)
            except (ValueError, TypeError, KeyError):
                raise ProjectionUnavailable() from None
        return {"history_generation": generation, "revisions": revisions, "reused_dates": reused,
                "daily": daily, "as_of": as_of}
