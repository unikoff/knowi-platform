"""Fast activity-v1 aggregates with the same snapshot fences as the day reader."""

from pymentor_analytics.adapters.postgres.activity_day_reports import _DAY_SNAPSHOT_SOURCES


_COURSE_SNAPSHOT = _DAY_SNAPSHOT_SOURCES + """
course_window AS MATERIALIZED (
 SELECT b.*,s.course_projection_generation,s.course_projection_run_id,
        s.presence_labels,s.presence_intervals,s.presence_points
 FROM ready b JOIN analytics.activity_projection_buckets s USING (subject_ref,bucket_start)
 WHERE s.metric_version='activity-v1'
),
course_day AS MATERIALIZED (
 SELECT s.*,d.course_projection_generation,d.course_projection_run_id,d.presence_labels
 FROM day_scoped s JOIN analytics.activity_projection_days d USING (subject_ref,local_day)
 CROSS JOIN item_counts i CROSS JOIN precheck c
 WHERE d.metric_version='activity-v1' AND c.error IS NULL
   AND i.full_items<=:max_full_items AND i.edge_items<=:max_edge_items
   AND i.full_bytes<=:max_full_bytes AND i.edge_bytes<=:max_edge_bytes
),
course_check AS MATERIALIZED (
 SELECT CASE
   WHEN c.error IS NOT NULL THEN c.error
   WHEN i.full_items>:max_full_items OR i.edge_items>:max_edge_items
     OR i.full_bytes>:max_full_bytes OR i.edge_bytes>:max_edge_bytes THEN 'item_limit'
   WHEN EXISTS(SELECT 1 FROM course_window WHERE course_projection_generation IS DISTINCT FROM generation
                OR course_projection_run_id IS DISTINCT FROM :run_id)
     OR EXISTS(SELECT 1 FROM course_day WHERE course_projection_generation IS DISTINCT FROM generation
                OR course_projection_run_id IS DISTINCT FROM :run_id) THEN 'course_projection_pending'
 END AS error FROM precheck c CROSS JOIN item_counts i
),
window_parts AS MATERIALIZED (
 SELECT b.subject_ref,b.local_day,b.lo_ms,b.hi_ms,
        b.lo_ms<=b.bucket_ms AND b.bucket_ms+:bucket_ms<=b.hi_ms AS whole,
        c.theory_ms,c.practice_ms,c.winner_intervals
 FROM course_window b JOIN analytics.activity_course_buckets c
   ON c.subject_ref=b.subject_ref AND c.bucket_start=b.bucket_start AND c.metric_version='activity-v1'
   AND c.generation=b.generation AND c.subject_epoch=b.current_epoch AND c.run_id=:run_id
 WHERE (SELECT error FROM course_check) IS NULL
   AND (:all_courses OR c.course_id=ANY(CAST(:courses AS varchar[])))
),
time_parts AS (
 SELECT d.local_day,c.theory_ms,c.practice_ms
 FROM course_day d JOIN analytics.activity_course_days c
   ON c.subject_ref=d.subject_ref AND c.local_day=d.local_day AND c.metric_version='activity-v1'
   AND c.generation=d.generation AND c.subject_epoch=d.current_epoch AND c.run_id=:run_id
 WHERE (SELECT error FROM course_check) IS NULL
   AND (:all_courses OR c.course_id=ANY(CAST(:courses AS varchar[])))
 UNION ALL
 SELECT local_day,theory_ms,practice_ms FROM window_parts WHERE whole
 UNION ALL
 SELECT b.local_day,
   CASE WHEN v.section='theory' THEN LEAST(v.end_ms,b.hi_ms)-GREATEST(v.start_ms,b.lo_ms) ELSE 0 END,
   CASE WHEN v.section='practice' THEN LEAST(v.end_ms,b.hi_ms)-GREATEST(v.start_ms,b.lo_ms) ELSE 0 END
 FROM window_parts b CROSS JOIN LATERAL jsonb_to_recordset(b.winner_intervals)
   AS v(start_ms bigint,end_ms bigint,section text)
 WHERE NOT b.whole AND v.start_ms<b.hi_ms AND v.end_ms>b.lo_ms
),
time_days AS MATERIALIZED (
 SELECT local_day,sum(theory_ms) AS theory_ms,sum(practice_ms) AS practice_ms FROM time_parts GROUP BY local_day
),
presence_sources AS (
 SELECT subject_ref,local_day,presence_labels FROM course_day WHERE (SELECT error FROM course_check) IS NULL
 UNION ALL
 SELECT subject_ref,local_day,presence_labels FROM course_window
 WHERE (SELECT error FROM course_check) IS NULL AND lo_ms<=bucket_ms AND bucket_ms+:bucket_ms<=hi_ms
),
partial_presence AS MATERIALIZED (
 SELECT * FROM course_window
 WHERE (SELECT error FROM course_check) IS NULL AND (lo_ms>bucket_ms OR bucket_ms+:bucket_ms>hi_ms)
),
observed_courses AS (
 SELECT s.subject_ref,s.local_day,v.course_id
 FROM presence_sources s CROSS JOIN LATERAL jsonb_to_recordset(s.presence_labels) AS v(course_id text,lesson_id text)
 WHERE v.lesson_id IS NULL
 UNION ALL
 SELECT b.subject_ref,b.local_day,v.course_id FROM partial_presence b
 CROSS JOIN LATERAL jsonb_to_recordset(b.presence_intervals) AS v(start_ms bigint,end_ms bigint,course_id text)
 WHERE v.start_ms<b.hi_ms AND v.end_ms>b.lo_ms
 UNION ALL
 SELECT b.subject_ref,b.local_day,v.course_id FROM partial_presence b
 CROSS JOIN LATERAL jsonb_to_recordset(b.presence_points) AS v(at_ms bigint,course_id text)
 WHERE v.at_ms>=b.lo_ms AND v.at_ms<b.hi_ms
),
visits AS MATERIALIZED (
 SELECT DISTINCT subject_ref,local_day FROM observed_courses
 WHERE (:all_courses OR course_id=ANY(CAST(:courses AS varchar[])))
),
visit_days AS MATERIALIZED (SELECT local_day,count(*) AS visitors FROM visits GROUP BY local_day),
day_rows AS (
 SELECT COALESCE(t.local_day,v.local_day) AS day,COALESCE(v.visitors,0) AS visitors,
        COALESCE(t.theory_ms,0) AS theory_ms,COALESCE(t.practice_ms,0) AS practice_ms
 FROM time_days t FULL OUTER JOIN visit_days v USING(local_day)
),
totals AS (
 SELECT jsonb_build_object('unique_visitors',(SELECT count(DISTINCT subject_ref) FROM visits),
        'active_ms',COALESCE(sum(theory_ms+practice_ms),0),'theory_ms',COALESCE(sum(theory_ms),0),
        'practice_ms',COALESCE(sum(practice_ms),0)) AS value FROM day_rows
),
daily AS (
 SELECT COALESCE(jsonb_agg(jsonb_build_object('date',to_char(day,'YYYY-MM-DD'),'unique_visitors',visitors,
   'active_ms',theory_ms+practice_ms,'theory_ms',theory_ms,'practice_ms',practice_ms) ORDER BY day),'[]'::jsonb) AS value
 FROM day_rows
)
SELECT course_check.error,params.as_of,totals.value AS totals,daily.value AS daily
FROM course_check CROSS JOIN params CROSS JOIN totals CROSS JOIN daily
"""
