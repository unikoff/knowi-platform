import type {ActivityMetrics, ActivityReportV1, CalendarActivityDelta} from "../analytics-reporting/types";
import type {CalendarDay} from "./calendar-cache";
import {courseReportFilters, type CourseReportRange} from "./course-statistics-analytics";

const zero: ActivityMetrics = {unique_visitors: 0, active_ms: 0, theory_ms: 0, practice_ms: 0};

export function mergeCalendarDelta(delta: CalendarActivityDelta, range: CourseReportRange, dates: string[],
                                   closedDates: string[], saved: readonly CalendarDay[], generation?: string) {
  if (delta.revisions.length !== closedDates.length ||
      delta.revisions.some((item, index) => item.date !== closedDates[index])) return null;

  const requested = new Set(dates);
  const revisions = new Map(delta.revisions.map(item => [item.date, item.token]));
  const cached = new Map(saved.map(day => [day.date, day]));
  const reused = new Set(delta.reused_dates);
  if (reused.size !== delta.reused_dates.length || (reused.size && generation !== delta.history_generation)) return null;
  const fresh = new Map<string, ActivityMetrics>();
  for (const {date, ...metrics} of delta.daily) {
    if (!requested.has(date) || reused.has(date) || fresh.has(date) || (metrics.active_ms && !metrics.unique_visitors)) return null;
    fresh.set(date, metrics);
  }
  const totals = {...zero};
  const daily: ActivityReportV1["daily"] = [];
  const replacedDates: string[] = [];
  const rows: CalendarDay[] = [];
  for (const date of reused) if (!revisions.has(date)) return null;
  for (const date of dates) {
    const revision = revisions.get(date);
    const previous = reused.has(date) ? cached.get(date) : undefined;
    if (reused.has(date) && (!previous || revision !== previous.revision)) return null;
    const values = previous ? {unique_visitors: previous.uniqueVisitors,
      active_ms: previous.activeMs, theory_ms: previous.theoryMs, practice_ms: previous.practiceMs} : fresh.get(date) ?? zero;
    totals.unique_visitors = Math.max(totals.unique_visitors, values.unique_visitors);
    totals.active_ms += values.active_ms;
    totals.theory_ms += values.theory_ms;
    totals.practice_ms += values.practice_ms;
    if (values.unique_visitors) daily.push({date, ...values});
    if (revisions.has(date) && !previous) {
      replacedDates.push(date);
      if (revision) rows.push({date, revision, activeMs: values.active_ms, theoryMs: values.theory_ms,
        practiceMs: values.practice_ms, uniqueVisitors: values.unique_visitors});
    }
  }
  if (!Number.isSafeInteger(totals.active_ms)) return null;
  const {from_time, to_time} = courseReportFilters(range);
  const report: ActivityReportV1 = {schema_version: 1, status: "ok", quality: "unknown", as_of: totals.unique_visitors ? delta.as_of : null,
    lag_seconds: null, timezone: "Europe/Moscow", metric_version: "activity-v1", course_id: range.courseId, from_time, to_time, totals, daily};
  return {report, replacedDates, rows};
}
