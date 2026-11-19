"""Default-off a010 bucket reader. No raw history is read on a report path.

Each subject lock is held through the one-statement snapshot and the transaction
privacy guard. A stale, dirty, oversized or not-backfilled result fails closed.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import date, timedelta
from math import isfinite
import time
from uuid import UUID
from sqlalchemy import text
from pymentor_analytics.contracts.auth_v1 import AuthorizationError
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.reports_v1 import (
    ActivityBreakdownRow, ActivityDay, ActivityLifetimeQuery, ActivityQuery, ActivityValues,
)
from pymentor_analytics.modules.analytics.activity.bucket import BUCKET_MS
from pymentor_analytics.modules.analytics.activity.calculator import EPOCH, _ms
from pymentor_analytics.modules.analytics.reports.activity_scope import resolve_scope
from pymentor_analytics.adapters.postgres.activity_reports import ReportLimitExceeded

MAX_SUBJECTS = 100
# 100 subjects across <=31 days, including a possible partial extra bucket.
MAX_BUCKET_ROWS = 300_000
MAX_FULL_ITEMS = 1_000_000
MAX_EDGE_ITEMS = 100_000
MAX_FULL_BYTES = 64 * 1024 * 1024
MAX_EDGE_BYTES = 32 * 1024 * 1024
MAX_BREAKDOWN_ROWS = 20_000

# Discovery has no course filter: a dirty row's old labels are not authoritative.
_DISCOVER = """
SELECT DISTINCT subject_ref FROM analytics.activity_projection_buckets
WHERE metric_version='activity-v1' AND bucket_start>=:first_bucket
  AND bucket_start<LEAST(:to_time,date_trunc('milliseconds',statement_timestamp()))
  AND :from_time<LEAST(:to_time,date_trunc('milliseconds',statement_timestamp()))
  AND (:all_subjects OR subject_ref=ANY(CAST(:scope_subjects AS uuid[])))
ORDER BY subject_ref LIMIT :limit
"""

# All checks and all aggregates below share the same READ COMMITTED statement
# snapshot, which is obtained only after numeric subject advisory locks. The
# initial discovery is repeated; a changed set fails, never silently truncates.
_SNAPSHOT_SOURCES = """
WITH
params AS MATERIALIZED (
 SELECT CAST(:from_ms AS bigint) AS lo_ms,
        LEAST(CAST(:to_ms AS bigint), floor(extract(epoch FROM statement_timestamp())*1000)::bigint) AS hi_ms,
        date_trunc('milliseconds', statement_timestamp()) AS as_of,
        LEAST(:to_time,date_trunc('milliseconds',statement_timestamp())) AS hi_time
),
backfill AS MATERIALIZED (
 SELECT EXISTS(SELECT 1 FROM analytics.activity_projection_backfill
               WHERE singleton=true AND phase='complete' AND run_id=:run_id) AS valid
),
candidates AS MATERIALIZED (
 SELECT DISTINCT b.subject_ref FROM analytics.activity_projection_buckets b CROSS JOIN params p
 WHERE b.metric_version='activity-v1' AND b.bucket_start>=:first_bucket AND b.bucket_start<p.hi_time
   AND p.lo_ms<p.hi_ms
   AND (:all_subjects OR b.subject_ref=ANY(CAST(:scope_subjects AS uuid[])))
 ORDER BY b.subject_ref LIMIT :subject_limit
),
scoped AS MATERIALIZED (
 SELECT b.subject_ref,b.bucket_start,
        floor(extract(epoch FROM b.bucket_start)*1000)::bigint AS bucket_ms,
        b.compact_item_count,b.edge_item_count,b.compact_json_bytes,b.edge_json_bytes,
        b.state IN ('dirty','claimed') AS pending,
        (COALESCE(f.deleted,false) OR b.state NOT IN ('ready','dirty','claimed')
         OR (b.state='ready' AND (b.projection_generation IS DISTINCT FROM b.generation
         OR b.subject_epoch IS DISTINCT FROM COALESCE(f.epoch,0)
         OR b.compact_item_count IS NULL OR b.edge_item_count IS NULL
         OR b.compact_json_bytes IS NULL OR b.edge_json_bytes IS NULL
         OR b.compact_item_count<0 OR b.edge_item_count<0 OR b.compact_json_bytes<0 OR b.edge_json_bytes<0))) AS invalid
 FROM analytics.activity_projection_buckets b
 JOIN candidates c ON c.subject_ref=b.subject_ref
 LEFT JOIN analytics.subject_fences f ON f.subject_ref=b.subject_ref
 CROSS JOIN params p
 WHERE b.metric_version='activity-v1' AND b.bucket_start>=:first_bucket AND b.bucket_start<p.hi_time
   AND p.lo_ms<p.hi_ms
   AND (SELECT count(*) FROM candidates)<=:max_subjects
 LIMIT :bucket_limit
),
scoped_stats AS MATERIALIZED (
 SELECT count(*) AS bucket_count,COALESCE(bool_or(b.invalid),false) AS invalid,
  COALESCE(bool_or(b.pending),false) AS pending,
  COALESCE(sum(b.compact_item_count) FILTER (WHERE p.lo_ms<p.hi_ms AND b.bucket_ms<p.hi_ms
      AND b.bucket_ms+:bucket_ms>p.lo_ms AND p.lo_ms<=b.bucket_ms AND b.bucket_ms+:bucket_ms<=p.hi_ms),0) AS full_items,
  COALESCE(sum(b.edge_item_count) FILTER (WHERE p.lo_ms<p.hi_ms AND b.bucket_ms<p.hi_ms
      AND b.bucket_ms+:bucket_ms>p.lo_ms AND (p.lo_ms>b.bucket_ms OR b.bucket_ms+:bucket_ms>p.hi_ms)),0) AS edge_items,
  COALESCE(sum(b.compact_json_bytes) FILTER (WHERE p.lo_ms<p.hi_ms AND b.bucket_ms<p.hi_ms
      AND b.bucket_ms+:bucket_ms>p.lo_ms AND p.lo_ms<=b.bucket_ms AND b.bucket_ms+:bucket_ms<=p.hi_ms),0) AS full_bytes,
  COALESCE(sum(b.edge_json_bytes) FILTER (WHERE p.lo_ms<p.hi_ms AND b.bucket_ms<p.hi_ms
      AND b.bucket_ms+:bucket_ms>p.lo_ms AND (p.lo_ms>b.bucket_ms OR b.bucket_ms+:bucket_ms>p.hi_ms)),0) AS edge_bytes
 FROM scoped b CROSS JOIN params p
),
precheck AS MATERIALIZED (
 SELECT CASE
  WHEN NOT (SELECT valid FROM backfill) THEN 'backfill_unavailable'
  WHEN (SELECT count(*) FROM candidates)>:max_subjects THEN 'subject_limit'
  WHEN COALESCE((SELECT array_agg(subject_ref ORDER BY subject_ref) FROM candidates),ARRAY[]::uuid[])
       <> CAST(:locked_subjects AS uuid[]) THEN 'candidate_changed'
  WHEN EXISTS(SELECT 1 FROM analytics.subject_fences WHERE subject_ref=:actor AND deleted)
       OR (CAST(:selected_subject AS uuid) IS NOT NULL AND EXISTS(SELECT 1 FROM analytics.subject_fences WHERE subject_ref=:selected_subject AND deleted)) THEN 'actor_or_target_deleted'
  WHEN (SELECT bucket_count FROM scoped_stats)>:max_buckets THEN 'bucket_limit'
  WHEN (SELECT invalid FROM scoped_stats) THEN 'projection_unavailable'
  WHEN (SELECT pending FROM scoped_stats) THEN 'projection_pending'
  ELSE NULL END AS error
),
item_counts AS MATERIALIZED (
 SELECT CASE WHEN c.error IS NULL THEN (SELECT full_items FROM scoped_stats) ELSE 0 END AS full_items,
        CASE WHEN c.error IS NULL THEN (SELECT edge_items FROM scoped_stats) ELSE 0 END AS edge_items,
        CASE WHEN c.error IS NULL THEN (SELECT full_bytes FROM scoped_stats) ELSE 0 END AS full_bytes,
        CASE WHEN c.error IS NULL THEN (SELECT edge_bytes FROM scoped_stats) ELSE 0 END AS edge_bytes
 FROM precheck c
),
ready AS MATERIALIZED (
 SELECT b.subject_ref,b.bucket_start,b.bucket_ms,b.compact_item_count,b.edge_item_count,
        (b.bucket_start AT TIME ZONE 'UTC' + interval '3 hours')::date AS local_day,
        p.lo_ms,p.hi_ms
 FROM scoped b CROSS JOIN params p CROSS JOIN item_counts i CROSS JOIN precheck c
 WHERE c.error IS NULL AND p.lo_ms<p.hi_ms AND b.bucket_ms<p.hi_ms AND b.bucket_ms+:bucket_ms>p.lo_ms
   AND i.full_items<=:max_full_items AND i.edge_items<=:max_edge_items
   AND i.full_bytes<=:max_full_bytes AND i.edge_bytes<=:max_edge_bytes
),
full_buckets AS MATERIALIZED (
 SELECT b.subject_ref,b.local_day,b.compact_item_count,src.contributions,src.presence_labels FROM ready b
 JOIN analytics.activity_projection_buckets src ON src.subject_ref=b.subject_ref AND src.bucket_start=b.bucket_start
                                               AND src.metric_version='activity-v1'
 WHERE b.lo_ms<=b.bucket_ms AND b.bucket_ms+:bucket_ms<=b.hi_ms
),
edge_buckets AS MATERIALIZED (
 SELECT b.subject_ref,b.local_day,b.edge_item_count,b.lo_ms,b.hi_ms,
        src.winner_intervals,src.presence_intervals,src.presence_points FROM ready b
 JOIN analytics.activity_projection_buckets src ON src.subject_ref=b.subject_ref AND src.bucket_start=b.bucket_start
                                               AND src.metric_version='activity-v1'
 WHERE NOT (b.lo_ms<=b.bucket_ms AND b.bucket_ms+:bucket_ms<=b.hi_ms)
),
array_checks AS MATERIALIZED (
 SELECT EXISTS(
   SELECT 1 FROM full_buckets WHERE CASE
    WHEN jsonb_typeof(contributions)='array' AND jsonb_typeof(presence_labels)='array'
    THEN compact_item_count<>jsonb_array_length(contributions)+jsonb_array_length(presence_labels) ELSE true END
   UNION ALL
   SELECT 1 FROM edge_buckets WHERE CASE
    WHEN jsonb_typeof(winner_intervals)='array' AND jsonb_typeof(presence_intervals)='array' AND jsonb_typeof(presence_points)='array'
    THEN edge_item_count<>jsonb_array_length(winner_intervals)+jsonb_array_length(presence_intervals)+jsonb_array_length(presence_points)
    ELSE true END
 ) AS invalid
),
full_valid AS NOT MATERIALIZED (SELECT * FROM full_buckets WHERE NOT (SELECT invalid FROM array_checks)),
edge_valid AS NOT MATERIALIZED (SELECT * FROM edge_buckets WHERE NOT (SELECT invalid FROM array_checks)),
"""
_SNAPSHOT = _SNAPSHOT_SOURCES + """
-- Every original bucket has already passed state, generation, budget and
-- array checks. Identical compact arrays may now be expanded only once.
full_time_groups AS MATERIALIZED (
 SELECT local_day,contributions,count(*) AS copies FROM full_valid
 GROUP BY local_day,contributions
),
full_visit_groups AS MATERIALIZED (
 SELECT subject_ref,local_day,presence_labels FROM full_valid
 GROUP BY subject_ref,local_day,presence_labels
),
raw_times AS NOT MATERIALIZED (
 SELECT b.local_day,v.course_id,v.lesson_id,v.section,CAST(v.active_ms AS numeric)*b.copies AS amount
 FROM full_time_groups b CROSS JOIN LATERAL jsonb_to_recordset(b.contributions)
   AS v(course_id text,lesson_id text,section text,active_ms bigint)
 UNION ALL
 SELECT b.local_day,v.course_id,v.lesson_id,v.section,
        LEAST(v.end_ms,b.hi_ms)-GREATEST(v.start_ms,b.lo_ms) AS amount
 FROM edge_valid b CROSS JOIN LATERAL jsonb_to_recordset(b.winner_intervals)
   AS v(start_ms bigint,end_ms bigint,course_id text,lesson_id text,section text)
 WHERE v.start_ms<b.hi_ms AND v.end_ms>b.lo_ms
),
times AS MATERIALIZED (
 SELECT local_day,course_id,lesson_id,section,sum(amount) AS amount FROM raw_times WHERE amount>0
   AND (:all_courses OR course_id=ANY(CAST(:courses AS varchar[])))
   AND (CAST(:lesson_id AS text) IS NULL OR lesson_id=:lesson_id)
 GROUP BY local_day,course_id,lesson_id,section
),
raw_visits AS NOT MATERIALIZED (
 SELECT b.subject_ref,b.local_day,v.course_id,v.lesson_id
 FROM full_visit_groups b CROSS JOIN LATERAL jsonb_to_recordset(b.presence_labels) AS v(course_id text,lesson_id text)
 UNION ALL
 SELECT b.subject_ref,b.local_day,v.course_id,v.lesson_id
 FROM edge_valid b CROSS JOIN LATERAL jsonb_to_recordset(b.presence_intervals)
   AS v(start_ms bigint,end_ms bigint,course_id text,lesson_id text,section text)
 WHERE v.start_ms<b.hi_ms AND v.end_ms>b.lo_ms
 UNION ALL
 SELECT b.subject_ref,b.local_day,v.course_id,NULL::text AS lesson_id
 FROM edge_valid b CROSS JOIN LATERAL jsonb_to_recordset(b.presence_points) AS v(at_ms bigint,course_id text)
 WHERE v.at_ms>=b.lo_ms AND v.at_ms<b.hi_ms
),
visits AS MATERIALIZED (
 SELECT DISTINCT subject_ref,local_day,course_id,lesson_id FROM raw_visits
 WHERE (:all_courses OR course_id=ANY(CAST(:courses AS varchar[])))
   AND (CAST(:lesson_id AS text) IS NULL OR lesson_id=:lesson_id)
),
total_values AS MATERIALIZED (
 SELECT jsonb_build_object('unique_visitors',(SELECT count(DISTINCT subject_ref) FROM visits),
  'active_ms',COALESCE(sum(amount),0),
  'theory_ms',COALESCE(sum(amount) FILTER (WHERE section='theory'),0),
  'practice_ms',COALESCE(sum(amount) FILTER (WHERE section='practice'),0)) AS value FROM times
),
day_times AS MATERIALIZED (
 SELECT local_day,sum(amount) AS active_ms,COALESCE(sum(amount) FILTER (WHERE section='theory'),0) AS theory_ms,
        COALESCE(sum(amount) FILTER (WHERE section='practice'),0) AS practice_ms FROM times WHERE :with_daily GROUP BY local_day
),
day_visits AS MATERIALIZED (
 SELECT local_day,count(DISTINCT subject_ref) AS visitors FROM visits WHERE :with_daily GROUP BY local_day
),
day_rows AS MATERIALIZED (
 SELECT COALESCE(t.local_day,v.local_day) AS day,COALESCE(v.visitors,0) AS visitors,
        COALESCE(t.active_ms,0) AS active_ms,COALESCE(t.theory_ms,0) AS theory_ms,COALESCE(t.practice_ms,0) AS practice_ms
 FROM day_times t FULL OUTER JOIN day_visits v USING(local_day)
),
daily AS MATERIALIZED (
 SELECT COALESCE(jsonb_agg(jsonb_build_object('date',to_char(day,'YYYY-MM-DD'),'unique_visitors',visitors,
        'active_ms',active_ms,'theory_ms',theory_ms,'practice_ms',practice_ms) ORDER BY day),'[]'::jsonb) AS value FROM day_rows
),
course_times AS MATERIALIZED (
 SELECT course_id,sum(amount) AS active_ms,COALESCE(sum(amount) FILTER (WHERE section='theory'),0) AS theory_ms,
        COALESCE(sum(amount) FILTER (WHERE section='practice'),0) AS practice_ms FROM times GROUP BY course_id
),
course_visits AS MATERIALIZED (SELECT course_id,count(DISTINCT subject_ref) AS visitors FROM visits GROUP BY course_id),
lesson_times AS MATERIALIZED (
 SELECT course_id,lesson_id,sum(amount) AS active_ms,COALESCE(sum(amount) FILTER (WHERE section='theory'),0) AS theory_ms,
        COALESCE(sum(amount) FILTER (WHERE section='practice'),0) AS practice_ms FROM times GROUP BY course_id,lesson_id
),
lesson_visits AS MATERIALIZED (
 SELECT course_id,lesson_id,count(DISTINCT subject_ref) AS visitors FROM visits WHERE lesson_id IS NOT NULL GROUP BY course_id,lesson_id
),
breakdown_unlimited AS MATERIALIZED (
 SELECT 'course'::text AS level,COALESCE(t.course_id,v.course_id) AS course_id,NULL::text AS lesson_id,
        COALESCE(v.visitors,0) AS visitors,COALESCE(t.active_ms,0) AS active_ms,
        COALESCE(t.theory_ms,0) AS theory_ms,COALESCE(t.practice_ms,0) AS practice_ms
 FROM course_times t FULL OUTER JOIN course_visits v USING(course_id)
 UNION ALL
 SELECT 'lesson',COALESCE(t.course_id,v.course_id),COALESCE(t.lesson_id,v.lesson_id),
        COALESCE(v.visitors,0),COALESCE(t.active_ms,0),COALESCE(t.theory_ms,0),COALESCE(t.practice_ms,0)
 FROM lesson_times t FULL OUTER JOIN lesson_visits v USING(course_id,lesson_id)
),
breakdown_limited AS MATERIALIZED (
 SELECT * FROM breakdown_unlimited WHERE :with_breakdown
 ORDER BY course_id,CASE WHEN level='course' THEN 0 ELSE 1 END,lesson_id LIMIT :breakdown_limit
),
breakdown AS MATERIALIZED (
 SELECT COALESCE(jsonb_agg(jsonb_build_object('level',level,'course_id',course_id,'lesson_id',lesson_id,
        'unique_visitors',visitors,'active_ms',active_ms,'theory_ms',theory_ms,'practice_ms',practice_ms)
        ORDER BY course_id,CASE WHEN level='course' THEN 0 ELSE 1 END,lesson_id),'[]'::jsonb) AS value FROM breakdown_limited
)
SELECT CASE WHEN precheck.error IS NOT NULL THEN precheck.error
            WHEN (SELECT invalid FROM array_checks) THEN 'projection_unavailable'
            WHEN item_counts.full_items>:max_full_items OR item_counts.edge_items>:max_edge_items
              OR item_counts.full_bytes>:max_full_bytes OR item_counts.edge_bytes>:max_edge_bytes THEN 'item_limit'
            WHEN (SELECT count(*) FROM breakdown_limited)>:max_breakdown THEN 'breakdown_limit'
            ELSE NULL END AS error,
       params.as_of,total_values.value AS totals,daily.value AS daily,breakdown.value AS breakdown
FROM precheck CROSS JOIN item_counts CROSS JOIN params CROSS JOIN total_values CROSS JOIN daily CROSS JOIN breakdown
"""

class ProjectionUnavailable(Exception):
    """Stored projection cannot prove a stable result at the current lifecycle."""


class ProjectionPending(ProjectionUnavailable):
    """A live worker is eligible to publish an up-to-date projection."""


class PostgresProjectionActivityReport:
    supports_history_revisions = True
    def __init__(self, database, fence, *, expected_run_id: UUID, day_projection_enabled: bool = False,
                 course_projection_enabled: bool = False, pending_wait_seconds: float = 0.0):
        if not isinstance(expected_run_id, UUID):
            raise ValueError("trusted projection run UUID required")
        if type(day_projection_enabled) is not bool:
            raise ValueError("day projection flag must be boolean")
        if type(course_projection_enabled) is not bool or (course_projection_enabled and not day_projection_enabled):
            raise ValueError("course projection requires day projection")
        if not isfinite(pending_wait_seconds) or not 0 <= pending_wait_seconds <= 2:
            raise ValueError("invalid projection wait budget")
        self.database, self.fence, self.expected_run_id = database, fence, expected_run_id
        self.day_projection_enabled = day_projection_enabled
        self.course_projection_enabled = course_projection_enabled
        self.pending_wait_seconds = pending_wait_seconds

    async def read_activity(self, grant, query: ActivityQuery, *, include_breakdown: bool = False,
                            include_history_revisions: bool = False):
        if not isinstance(query, ActivityQuery):
            raise ValueError("validated activity query required")
        return await self._read_with_pending_retry(grant, query, include_breakdown=include_breakdown,
                                                   include_history_revisions=include_history_revisions)

    async def read_lifetime_activity(self, grant, query: ActivityLifetimeQuery):
        if not isinstance(query, ActivityLifetimeQuery):
            raise ValueError("validated lifetime query required")
        return await self._read_with_pending_retry(grant, query, include_breakdown=True)

    async def _read_with_pending_retry(self, grant, query: ActivityQuery | ActivityLifetimeQuery, *,
                                       include_breakdown: bool, include_history_revisions: bool = False):
        return await self._retry_pending(lambda: self._read_snapshot(
            grant, query, include_breakdown=include_breakdown, include_history_revisions=include_history_revisions,
        ))

    async def _retry_pending(self, read):
        loop = asyncio.get_running_loop()
        retry_until = loop.time() + self.pending_wait_seconds
        delay = .05
        while True:
            try:
                return await read()
            except ProjectionPending:
                if retry_until - loop.time() <= delay:
                    raise
            # Recheck earlier while releasing subject locks and the pool slot.
            await asyncio.sleep(delay)
            delay = min(.25, delay * 1.5)

    async def _read_snapshot(self, grant, query: ActivityQuery | ActivityLifetimeQuery, *, include_breakdown: bool,
                             include_history_revisions: bool = False):
        scope = resolve_scope(grant, query)
        include_daily = isinstance(query, ActivityQuery)
        if include_history_revisions:
            from pymentor_analytics.adapters.postgres.activity_history_revisions import whole_days
            if (not include_daily or include_breakdown or grant.role != "user" or grant.scope != "report:read"
                    or grant.global_read or grant.subject_refs or query.subject_ref is not None
                    or query.course_id is None or query.lesson_id is not None or not whole_days(query)):
                raise AuthorizationError()
            from pymentor_analytics.adapters.postgres.activity_history_revisions import with_incarnation
            wrap = with_incarnation
        else:
            wrap = lambda sql: sql
        async with self._guarded_snapshot(
            query.from_time, query.to_time, actor=scope.actor, selected_subject=scope.selected_subject,
            subjects=scope.subjects, expires_at=grant.expires_at, max_subjects=MAX_SUBJECTS,
        ) as (session, args, snapshot):
            args.update(all_courses=scope.courses is None, courses=list(scope.courses or ()), lesson_id=query.lesson_id,
                        with_daily=include_daily, with_breakdown=include_breakdown,
                        breakdown_limit=MAX_BREAKDOWN_ROWS+1, max_breakdown=MAX_BREAKDOWN_ROWS)
            if self.course_projection_enabled and not include_breakdown and query.lesson_id is None:
                from pymentor_analytics.adapters.postgres.activity_course_reports import _COURSE_SNAPSHOT
                # The old reader stays available while existing rows are rebuilt.
                row = (await session.execute(text(wrap(_COURSE_SNAPSHOT)), args)).mappings().one()
                if row["error"] == "course_projection_pending":
                    row = (await session.execute(text(wrap(snapshot)), args)).mappings().one()
            else:
                row = (await session.execute(text(wrap(snapshot)), args)).mappings().one()
            require_valid_projection(row["error"])
            if time.time()>=grant.expires_at:
                raise AuthorizationError()
            try:
                totals=ActivityValues.model_validate(row["totals"]).model_dump()
                if totals["active_ms"] and not totals["unique_visitors"]:
                    raise ValueError("inconsistent activity result")
                if include_daily:
                    daily = _validated_daily(row["daily"], totals)
                    if include_history_revisions:
                        from pymentor_analytics.adapters.postgres.activity_history_revisions import encode_history_generation, encode_history_revisions
                        revisions = encode_history_revisions(daily, subject=scope.actor, course=query.course_id,
                            run_id=self.expected_run_id, started=row["revision_started"], db_oid=row["revision_db_oid"],
                            db_name=row["revision_db_name"], now=row["revision_now"])
                        generation = encode_history_generation(subject=scope.actor, course=query.course_id,
                            run_id=self.expected_run_id, started=row["revision_started"], db_oid=row["revision_db_oid"],
                            db_name=row["revision_db_name"])
                elif row["daily"] != []:
                    raise ValueError("unexpected daily result")
                if include_breakdown:
                    if not isinstance(row["breakdown"],list) or len(row["breakdown"])>MAX_BREAKDOWN_ROWS:
                        raise ValueError("invalid breakdown result")
                    breakdown=[ActivityBreakdownRow.model_validate(item).model_dump() for item in row["breakdown"]]
                    if bool(totals["unique_visitors"]) != bool(breakdown):
                        raise ValueError("inconsistent breakdown result")
            except (ValueError,TypeError,KeyError):
                raise ProjectionUnavailable() from None
            values={"as_of":row["as_of"] if totals["unique_visitors"] else None,
                    "quality":"unknown","course_id":scope.course_id,
                    "totals":totals}
            if include_daily:
                values.update(daily=daily, lag_seconds=None)
                if include_history_revisions:
                    values["history_revisions"] = revisions
                    values["history_generation"] = generation
            if include_breakdown:
                values["breakdown"]=breakdown
        return values

    @asynccontextmanager
    async def _guarded_snapshot(
        self, from_time, to_time, *, actor, selected_subject, subjects, expires_at, max_subjects,
        candidate_query=None, candidate_params=None,
    ):
        """Hold the validated run and subject fences for a bounded projection read."""
        if self.database.transaction_guard is None:
            raise PrivacyUnavailable()
        first_ms = (_ms(from_time)//BUCKET_MS)*BUCKET_MS
        params = {"first_bucket": EPOCH+timedelta(milliseconds=first_ms), "from_time": from_time, "to_time": to_time,
                  "scope_subjects": list(subjects or ()), "all_subjects": subjects is None,
                  "limit": max_subjects+1}
        discovery, snapshot = _DISCOVER, _SNAPSHOT
        if self.day_projection_enabled:
            from pymentor_analytics.adapters.postgres.activity_day_reports import _DAY_DISCOVER, _DAY_SNAPSHOT
            discovery, snapshot = _DAY_DISCOVER, _DAY_SNAPSHOT
        async with self.database.transaction() as session:
            if candidate_query is not None:
                params.update(candidate_params)
                scope_subjects = (await session.execute(
                    candidate_query, {**params, "subject_limit": max_subjects+1},
                )).scalars().all()
                if len(scope_subjects) > max_subjects:
                    raise ReportLimitExceeded()
                params.update(scope_subjects=scope_subjects, all_subjects=False)
            if self.day_projection_enabled:
                from pymentor_analytics.adapters.postgres.activity_day_projection import has_activity_day_projection_schema
                try:
                    available = await has_activity_day_projection_schema(session)
                except Exception:
                    raise ProjectionUnavailable() from None
                if not available:
                    raise ProjectionUnavailable()
            candidates=(await session.execute(text(discovery),params)).scalars().all()
            if len(candidates)>max_subjects:
                raise ReportLimitExceeded()
            identities=set(candidates)|{actor}
            if candidate_query is not None:
                identities.update(params["scope_subjects"])
            if selected_subject is not None:
                identities.add(selected_subject)
            keys = sorted({subject_lock_key(subject) for subject in identities})
            # Sort the input key; PostgreSQL evaluates the volatile lock after sorting.
            await session.execute(text("""SELECT pg_advisory_xact_lock_shared(locks.lock_key)
                FROM unnest(CAST(:keys AS bigint[])) AS locks(lock_key)
                ORDER BY locks.lock_key"""), {"keys": keys})
            if time.time()>=expires_at:
                raise AuthorizationError()
            # A current external deletion barrier must still hold AFTER any lock wait.
            await self.database.transaction_guard(session)
            locked=(await session.execute(text("SELECT analytics.lock_activity_projection_run(:run_id)"),
                                          {"run_id":self.expected_run_id})).scalar_one()
            if locked is not True:
                raise ProjectionUnavailable()
            # The shared dataset-generation lock can itself wait; observe a fresh
            # privacy authority barrier again after it before materialization.
            await self.database.transaction_guard(session)
            args={**params,"from_ms":_ms(from_time),"to_ms":_ms(to_time),"run_id":self.expected_run_id,
                  "subject_limit":max_subjects+1,"max_subjects":max_subjects,
                  "locked_subjects":sorted(candidates,key=lambda item:item.int),"actor":actor,
                  "selected_subject":selected_subject,"bucket_limit":MAX_BUCKET_ROWS+1,"max_buckets":MAX_BUCKET_ROWS,
                  "bucket_ms":BUCKET_MS,"max_full_items":MAX_FULL_ITEMS,"max_edge_items":MAX_EDGE_ITEMS,
                  "max_full_bytes":MAX_FULL_BYTES,"max_edge_bytes":MAX_EDGE_BYTES}
            yield session, args, snapshot
            if time.time()>=expires_at:
                raise AuthorizationError()
        if time.time()>=expires_at:
            raise AuthorizationError()


def require_valid_projection(error):
    if error == "actor_or_target_deleted":
        raise AuthorizationError()
    if error in {"subject_limit", "bucket_limit", "item_limit", "breakdown_limit"}:
        raise ReportLimitExceeded()
    if error == "projection_pending":
        raise ProjectionPending()
    if error is not None:
        raise ProjectionUnavailable()


def _validated_daily(rows, totals):
    if not isinstance(rows, list) or len(rows) > 32:
        raise ValueError("invalid daily result")
    daily = [ActivityDay.model_validate({**item, "date": date.fromisoformat(item["date"])}).model_dump()
             for item in rows]
    dates = [item["date"] for item in daily]
    if dates != sorted(set(dates)) or bool(totals["unique_visitors"]) != bool(daily):
        raise ValueError("inconsistent activity result")
    if (sum(item["unique_visitors"] for item in daily) < totals["unique_visitors"]
            or any(item["unique_visitors"] > totals["unique_visitors"] for item in daily)):
        raise ValueError("inconsistent distinct visitor count")
    for field in ("active_ms", "theory_ms", "practice_ms"):
        if sum(item[field] for item in daily) != totals[field]:
            raise ValueError("inconsistent daily duration")
    return daily
