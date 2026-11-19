"""a011 report statements; reuse the a010 final aggregation unchanged.

The source registry is authoritative for fully covered UTC+03 days. Partial
days still read a010 buckets. All checks and expansion share one SQL snapshot.
"""
from pymentor_analytics.adapters.postgres.activity_projection_reports import _SNAPSHOT

_DAY_START = "((s.local_day::timestamp - interval '3 hours') AT TIME ZONE 'UTC')"
_BUCKET_DAY = "((date_trunc('day', b.bucket_start AT TIME ZONE 'UTC' + interval '3 hours') - interval '3 hours') AT TIME ZONE 'UTC')"
_DAY_RANGE = f"{_DAY_START} >= :from_time AND {_DAY_START} + interval '1 day' <= p.hi_time"
_PARTIAL = f"NOT ({_BUCKET_DAY} >= :from_time AND {_BUCKET_DAY} + interval '1 day' <= p.hi_time)"

_DAY_DISCOVER = f"""
WITH p AS MATERIALIZED (
 SELECT LEAST(:to_time,date_trunc('milliseconds',statement_timestamp())) AS hi_time
), subjects AS (
 SELECT s.subject_ref FROM analytics.activity_projection_day_sources s CROSS JOIN p
 WHERE s.metric_version='activity-v1' AND :from_time<p.hi_time
   AND s.local_day >= (:from_time AT TIME ZONE 'UTC' + interval '3 hours')::date
   AND s.local_day <= (p.hi_time AT TIME ZONE 'UTC' + interval '3 hours')::date
   AND {_DAY_RANGE} AND (:all_subjects OR s.subject_ref=ANY(CAST(:scope_subjects AS uuid[])))
 UNION
 SELECT b.subject_ref FROM analytics.activity_projection_buckets b CROSS JOIN p
 WHERE b.metric_version='activity-v1' AND b.bucket_start>=:first_bucket AND b.bucket_start<p.hi_time
   AND :from_time<p.hi_time AND {_PARTIAL}
   AND (:all_subjects OR b.subject_ref=ANY(CAST(:scope_subjects AS uuid[])))
)
SELECT subject_ref FROM subjects ORDER BY subject_ref LIMIT :limit
"""

_BASE_PARAMS = _SNAPSHOT[:_SNAPSHOT.index("backfill AS MATERIALIZED (")]
_TAIL = _SNAPSHOT[_SNAPSHOT.index("raw_times AS NOT MATERIALIZED ("):]

_DAY_SNAPSHOT_SOURCES = _BASE_PARAMS + f"""
backfill AS MATERIALIZED (
 SELECT EXISTS(SELECT 1 FROM analytics.activity_projection_backfill
               WHERE singleton=true AND phase='complete' AND run_id=:run_id)
    AND EXISTS(SELECT 1 FROM analytics.activity_projection_day_backfill
               WHERE singleton=true AND phase='complete' AND run_id=:run_id) AS valid
),
candidates AS MATERIALIZED (
 SELECT subject_ref FROM (
  SELECT s.subject_ref FROM analytics.activity_projection_day_sources s CROSS JOIN params p
  WHERE s.metric_version='activity-v1' AND p.lo_ms<p.hi_ms
    AND s.local_day >= (:from_time AT TIME ZONE 'UTC' + interval '3 hours')::date
    AND s.local_day <= (p.hi_time AT TIME ZONE 'UTC' + interval '3 hours')::date
    AND {_DAY_RANGE} AND (:all_subjects OR s.subject_ref=ANY(CAST(:scope_subjects AS uuid[])))
  UNION
  SELECT b.subject_ref FROM analytics.activity_projection_buckets b CROSS JOIN params p
  WHERE b.metric_version='activity-v1' AND b.bucket_start>=:first_bucket AND b.bucket_start<p.hi_time
    AND p.lo_ms<p.hi_ms AND {_PARTIAL}
    AND (:all_subjects OR b.subject_ref=ANY(CAST(:scope_subjects AS uuid[])))
 ) candidate_set ORDER BY subject_ref LIMIT :subject_limit
),
day_scoped AS MATERIALIZED (
 SELECT s.subject_ref,s.local_day,s.state,s.generation,d.generation AS payload_generation,
        d.subject_epoch,d.run_id,d.source_bucket_count,d.source_compact_items,d.source_compact_bytes,
        COALESCE(f.epoch,0) AS current_epoch,COALESCE(f.deleted,false) AS deleted
 FROM analytics.activity_projection_day_sources s JOIN candidates c ON c.subject_ref=s.subject_ref
 CROSS JOIN params p
 LEFT JOIN analytics.activity_projection_days d ON d.subject_ref=s.subject_ref AND d.local_day=s.local_day AND d.metric_version=s.metric_version
 LEFT JOIN analytics.subject_fences f ON f.subject_ref=s.subject_ref
 WHERE s.metric_version='activity-v1' AND p.lo_ms<p.hi_ms AND {_DAY_RANGE} /* calendar-day-filter */
   AND s.local_day >= (:from_time AT TIME ZONE 'UTC' + interval '3 hours')::date
   AND s.local_day <= (p.hi_time AT TIME ZONE 'UTC' + interval '3 hours')::date
   AND (SELECT count(*) FROM candidates)<=:max_subjects
),
bucket_scoped AS MATERIALIZED (
 SELECT b.subject_ref,b.bucket_start,b.state,b.generation,b.projection_generation,b.subject_epoch,
        b.compact_item_count,b.edge_item_count,b.compact_json_bytes,b.edge_json_bytes,
        COALESCE(f.epoch,0) AS current_epoch,COALESCE(f.deleted,false) AS deleted,
        floor(extract(epoch FROM b.bucket_start)*1000)::bigint AS bucket_ms
 FROM analytics.activity_projection_buckets b JOIN candidates c ON c.subject_ref=b.subject_ref
 LEFT JOIN analytics.subject_fences f ON f.subject_ref=b.subject_ref CROSS JOIN params p
 WHERE b.metric_version='activity-v1' AND b.bucket_start>=:first_bucket AND b.bucket_start<p.hi_time
   AND p.lo_ms<p.hi_ms AND {_PARTIAL} /* calendar-bucket-filter */ AND (SELECT count(*) FROM candidates)<=:max_subjects
 LIMIT :bucket_limit
),
precheck AS MATERIALIZED (
 SELECT CASE
  WHEN NOT (SELECT valid FROM backfill) THEN 'backfill_unavailable'
  WHEN (SELECT count(*) FROM candidates)>:max_subjects THEN 'subject_limit'
  WHEN COALESCE((SELECT array_agg(subject_ref ORDER BY subject_ref) FROM candidates),ARRAY[]::uuid[])
       <> CAST(:locked_subjects AS uuid[]) THEN 'candidate_changed'
  WHEN EXISTS(SELECT 1 FROM analytics.subject_fences WHERE subject_ref=:actor AND deleted)
       OR (CAST(:selected_subject AS uuid) IS NOT NULL AND EXISTS(SELECT 1 FROM analytics.subject_fences WHERE subject_ref=:selected_subject AND deleted)) THEN 'actor_or_target_deleted'
  WHEN (SELECT count(*) FROM bucket_scoped)+COALESCE((SELECT sum(source_bucket_count) FROM day_scoped),0)>:max_buckets THEN 'bucket_limit'
  WHEN EXISTS(SELECT 1 FROM bucket_scoped WHERE deleted OR state NOT IN ('ready','dirty','claimed')
       OR (state='ready' AND (projection_generation IS DISTINCT FROM generation OR subject_epoch IS DISTINCT FROM current_epoch
       OR compact_item_count IS NULL OR edge_item_count IS NULL OR compact_json_bytes IS NULL OR edge_json_bytes IS NULL
       OR compact_item_count<0 OR edge_item_count<0 OR compact_json_bytes<0 OR edge_json_bytes<0)))
    OR EXISTS(SELECT 1 FROM day_scoped WHERE deleted OR state NOT IN ('ready','dirty','claimed')
       OR (state='ready' AND (payload_generation IS DISTINCT FROM generation OR subject_epoch IS DISTINCT FROM current_epoch
       OR run_id IS DISTINCT FROM :run_id OR source_bucket_count IS NULL OR source_bucket_count<0 OR source_bucket_count>96
       OR source_compact_items IS NULL OR source_compact_items<0 OR source_compact_bytes IS NULL OR source_compact_bytes<0)))
    THEN 'projection_unavailable'
  WHEN EXISTS(SELECT 1 FROM bucket_scoped WHERE state IN ('dirty','claimed'))
    OR EXISTS(SELECT 1 FROM day_scoped WHERE state IN ('dirty','claimed')) THEN 'projection_pending'
  ELSE NULL END AS error
),
item_counts AS MATERIALIZED (
 SELECT COALESCE((SELECT sum(source_compact_items) FROM day_scoped),0)+
        COALESCE(sum(CASE WHEN p.lo_ms<=b.bucket_ms AND b.bucket_ms+:bucket_ms<=p.hi_ms THEN b.compact_item_count ELSE 0 END),0) AS full_items,
        COALESCE(sum(CASE WHEN p.lo_ms>b.bucket_ms OR b.bucket_ms+:bucket_ms>p.hi_ms THEN b.edge_item_count ELSE 0 END),0) AS edge_items,
        COALESCE((SELECT sum(source_compact_bytes) FROM day_scoped),0)+
        COALESCE(sum(CASE WHEN p.lo_ms<=b.bucket_ms AND b.bucket_ms+:bucket_ms<=p.hi_ms THEN b.compact_json_bytes ELSE 0 END),0) AS full_bytes,
        COALESCE(sum(CASE WHEN p.lo_ms>b.bucket_ms OR b.bucket_ms+:bucket_ms>p.hi_ms THEN b.edge_json_bytes ELSE 0 END),0) AS edge_bytes
 FROM params p LEFT JOIN bucket_scoped b ON p.lo_ms<p.hi_ms AND b.bucket_ms<p.hi_ms AND b.bucket_ms+:bucket_ms>p.lo_ms
 CROSS JOIN precheck c WHERE c.error IS NULL
),
ready AS MATERIALIZED (
 SELECT b.*, (b.bucket_start AT TIME ZONE 'UTC' + interval '3 hours')::date AS local_day,p.lo_ms,p.hi_ms
 FROM bucket_scoped b CROSS JOIN params p CROSS JOIN item_counts i CROSS JOIN precheck c
 WHERE c.error IS NULL AND p.lo_ms<p.hi_ms AND b.bucket_ms<p.hi_ms AND b.bucket_ms+:bucket_ms>p.lo_ms
   AND i.full_items<=:max_full_items AND i.edge_items<=:max_edge_items
   AND i.full_bytes<=:max_full_bytes AND i.edge_bytes<=:max_edge_bytes
),
"""

_DAY_SNAPSHOT_VALIDATED = _DAY_SNAPSHOT_SOURCES + f"""
full_buckets AS MATERIALIZED (
 SELECT b.subject_ref,b.local_day,b.compact_item_count,src.contributions,src.presence_labels FROM ready b
 JOIN analytics.activity_projection_buckets src ON src.subject_ref=b.subject_ref AND src.bucket_start=b.bucket_start AND src.metric_version='activity-v1'
 WHERE b.lo_ms<=b.bucket_ms AND b.bucket_ms+:bucket_ms<=b.hi_ms
),
edge_buckets AS MATERIALIZED (
 SELECT b.subject_ref,b.local_day,b.edge_item_count,b.lo_ms,b.hi_ms,src.winner_intervals,src.presence_intervals,src.presence_points FROM ready b
 JOIN analytics.activity_projection_buckets src ON src.subject_ref=b.subject_ref AND src.bucket_start=b.bucket_start AND src.metric_version='activity-v1'
 WHERE NOT (b.lo_ms<=b.bucket_ms AND b.bucket_ms+:bucket_ms<=b.hi_ms)
),
day_payload AS MATERIALIZED (
 SELECT s.subject_ref,s.local_day,s.source_bucket_count,s.source_compact_items,s.source_compact_bytes,
        d.contributions,d.presence_labels
 FROM day_scoped s JOIN analytics.activity_projection_days d ON d.subject_ref=s.subject_ref AND d.local_day=s.local_day AND d.metric_version='activity-v1'
 CROSS JOIN item_counts i CROSS JOIN precheck c
 WHERE c.error IS NULL AND i.full_items<=:max_full_items AND i.edge_items<=:max_edge_items
   AND i.full_bytes<=:max_full_bytes AND i.edge_bytes<=:max_edge_bytes
),
day_shape AS MATERIALIZED (
 SELECT EXISTS(SELECT 1 FROM day_payload WHERE CASE WHEN jsonb_typeof(contributions)='array' AND jsonb_typeof(presence_labels)='array'
   THEN jsonb_array_length(contributions)+jsonb_array_length(presence_labels)>:max_full_items
     OR jsonb_array_length(contributions)+jsonb_array_length(presence_labels)>source_compact_items
     OR (source_bucket_count=0 AND (jsonb_array_length(contributions)>0 OR jsonb_array_length(presence_labels)>0
                                    OR source_compact_items<>0 OR source_compact_bytes<>0))
     OR octet_length(contributions::text)::bigint+octet_length(presence_labels::text)::bigint>:max_full_bytes ELSE true END) AS invalid
),
day_contribution_rows AS MATERIALIZED (
 SELECT d.subject_ref,d.local_day,x FROM day_payload d CROSS JOIN day_shape shape
 CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN NOT shape.invalid AND jsonb_typeof(d.contributions)='array' THEN d.contributions ELSE '[]'::jsonb END) x
 WHERE NOT shape.invalid
),
day_label_rows AS MATERIALIZED (
 SELECT d.subject_ref,d.local_day,x FROM day_payload d CROSS JOIN day_shape shape
 CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN NOT shape.invalid AND jsonb_typeof(d.presence_labels)='array' THEN d.presence_labels ELSE '[]'::jsonb END) x
 WHERE NOT shape.invalid
),
array_checks AS MATERIALIZED (
 SELECT EXISTS(
   SELECT 1 FROM full_buckets WHERE CASE WHEN jsonb_typeof(contributions)='array' AND jsonb_typeof(presence_labels)='array'
     THEN compact_item_count<>jsonb_array_length(contributions)+jsonb_array_length(presence_labels) ELSE true END
   UNION ALL
   SELECT 1 FROM edge_buckets WHERE CASE WHEN jsonb_typeof(winner_intervals)='array' AND jsonb_typeof(presence_intervals)='array' AND jsonb_typeof(presence_points)='array'
     THEN edge_item_count<>jsonb_array_length(winner_intervals)+jsonb_array_length(presence_intervals)+jsonb_array_length(presence_points) ELSE true END
   UNION ALL
   SELECT 1 FROM day_shape WHERE invalid
   UNION ALL
   SELECT 1 FROM day_contribution_rows
   WHERE CASE WHEN jsonb_typeof(x)<>'object' THEN true
     WHEN NOT (x ?& ARRAY['course_id','lesson_id','section','active_ms']) OR (x - ARRAY['course_id','lesson_id','section','active_ms'])<>'{{}}'::jsonb THEN true
     WHEN jsonb_typeof(x->'course_id')<>'string' OR (x->>'course_id') !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{{0,127}}$'
       OR jsonb_typeof(x->'lesson_id')<>'string' OR (x->>'lesson_id') !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{{0,127}}$'
       OR jsonb_typeof(x->'section')<>'string' OR (x->>'section') NOT IN ('theory','practice')
       OR jsonb_typeof(x->'active_ms')<>'number' OR (x->>'active_ms') !~ '^[1-9][0-9]{{0,7}}$' THEN true
     ELSE (x->>'active_ms')::bigint>86400000 END
   UNION ALL
   SELECT 1 FROM day_label_rows
   WHERE CASE WHEN jsonb_typeof(x)<>'object' THEN true
     WHEN NOT (x ?& ARRAY['course_id','lesson_id']) OR (x - ARRAY['course_id','lesson_id'])<>'{{}}'::jsonb THEN true
     WHEN jsonb_typeof(x->'course_id')<>'string' OR (x->>'course_id') !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{{0,127}}$' THEN true
     WHEN jsonb_typeof(x->'lesson_id')='null' THEN false
     WHEN jsonb_typeof(x->'lesson_id')='string' THEN (x->>'lesson_id') !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{{0,127}}$'
     ELSE true END
   UNION ALL
   SELECT 1 FROM day_contribution_rows
   GROUP BY subject_ref,local_day,x->>'course_id',x->>'lesson_id',x->>'section' HAVING count(*)>1
   UNION ALL
   SELECT 1 FROM day_label_rows
   GROUP BY subject_ref,local_day,x->>'course_id',x->>'lesson_id' HAVING count(*)>1
   UNION ALL
   SELECT 1 FROM day_contribution_rows
   GROUP BY subject_ref,local_day
   HAVING sum(CASE WHEN jsonb_typeof(x->'active_ms')='number' AND (x->>'active_ms') ~ '^[1-9][0-9]{{0,7}}$'
                   THEN (x->>'active_ms')::bigint ELSE 0 END)>86400000
   UNION ALL
   SELECT 1 FROM day_contribution_rows c
   LEFT JOIN day_label_rows lesson ON lesson.subject_ref=c.subject_ref AND lesson.local_day=c.local_day
      AND lesson.x->>'course_id'=c.x->>'course_id' AND lesson.x->>'lesson_id'=c.x->>'lesson_id'
   LEFT JOIN day_label_rows course ON course.subject_ref=c.subject_ref AND course.local_day=c.local_day
      AND course.x->>'course_id'=c.x->>'course_id' AND course.x->'lesson_id'='null'::jsonb
   WHERE lesson.subject_ref IS NULL OR course.subject_ref IS NULL
 ) AS invalid
),
full_valid AS MATERIALIZED (
 SELECT subject_ref,local_day,contributions,presence_labels FROM full_buckets WHERE NOT (SELECT invalid FROM array_checks)
 UNION ALL
 SELECT subject_ref,local_day,contributions,presence_labels FROM day_payload WHERE NOT (SELECT invalid FROM array_checks)
),
edge_valid AS NOT MATERIALIZED (SELECT * FROM edge_buckets WHERE NOT (SELECT invalid FROM array_checks)),
"""
_DAY_SNAPSHOT = _DAY_SNAPSHOT_VALIDATED + """
full_time_groups AS MATERIALIZED (
 SELECT local_day,contributions,count(*) AS copies FROM full_valid GROUP BY local_day,contributions
),
full_visit_groups AS MATERIALIZED (
 SELECT subject_ref,local_day,presence_labels FROM full_valid GROUP BY subject_ref,local_day,presence_labels
),
""" + _TAIL
