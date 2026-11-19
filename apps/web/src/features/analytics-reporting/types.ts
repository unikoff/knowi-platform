export type ActivityMetrics = { unique_visitors: number; active_ms: number; theory_ms: number; practice_ms: number };
export type ActivityReportV1 = {
  schema_version: 1;
  status: "ok" | "no_data" | "stale" | "unavailable";
  quality: "complete" | "partial" | "unknown";
  as_of: string | null;
  lag_seconds: number | null;
  timezone: "Europe/Moscow";
  metric_version: "activity-v1";
  course_id: string | null;
  from_time: string;
  to_time: string;
  totals: ActivityMetrics | null;
  daily: (ActivityMetrics & { date: string })[];
  trusted_progress?: { state: "known" | "unavailable"; completed_lessons: number | null } | null;
  history_revisions?: unknown;
  history_generation?: unknown;
};
export type ActivityReportFilters = { from_time: string; to_time: string; course_id?: string | null; lesson_id?: string | null; subject_ref?: string | null; user_id?: number | null; expected_target_ref?: string | null };
export type ActivityDetailsFilters = ActivityReportFilters & { offset?: number; limit?: number };
export type CalendarRevision = {date: string; token: string};
export type CalendarValidators = {known_generation: string | null; known_revisions: CalendarRevision[]};
export type CalendarActivityDelta = {
  history_generation: string;
  revisions: {date: string; token: string | null}[];
  reused_dates: string[];
  daily: (ActivityMetrics & {date: string})[];
  as_of: string | null;
};
export type StudentActivityFilters = Pick<ActivityReportFilters, "from_time" | "to_time"> & { course_id: string };
export type StudentActivityTarget = { user_id: number; expected_target_ref: string };
export type ActivityBreakdownRow = ActivityMetrics & { level: "course" | "lesson"; course_id: string; lesson_id: string | null };
export type ActivityDetailsReport = { report: ActivityReportV1; lesson_id: string | null; rows: ActivityBreakdownRow[]; next_offset: number | null };

export type ActivityChaptersFilters = { course_id: string; from_time: string; to_time: string; chapters: {chapter_id: string; lesson_ids: string[]}[] };
export type ActivityLifetimeOutlineFilters = Pick<ActivityChaptersFilters, "course_id" | "chapters">;
export type ActivityChaptersReport = { course_id: string; from_time: string; to_time: string; status: "ok" | "no_data" | "stale" | "unavailable"; quality: "complete" | "partial" | "unknown"; as_of: string | null; chapters: {chapter_id: string; active_ms: number | null; theory_ms: number | null; practice_ms: number | null}[] };
export type ActivityOutlineLesson = { lesson_id: string; active_ms: number; theory_ms: number; practice_ms: number };
export type ActivityOutlineReport = ActivityChaptersReport & {
  lessons: ActivityOutlineLesson[];
  unmapped_lessons: ActivityOutlineLesson[];
};
