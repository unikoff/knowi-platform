import type { ActivityMetrics, ActivityReportV1 } from "../analytics-reporting/types";
import type { CourseTimeSnapshot, CourseTimeValues } from "./course-statistics-model";

export type CourseReportRange = {courseId: string; from: string; to: string};
export function canShowCourseAnalytics(flag: unknown, role: string | undefined): boolean {
  return flag === "true" && role === "user";
}
export function canShowPersonalMeasurements(reportsFlag: unknown, measurementsFlag: unknown, role: string | undefined): boolean {
  return canShowCourseAnalytics(reportsFlag, role) && canShowCourseAnalytics(measurementsFlag, role);
}
export function courseReportFilters(range: CourseReportRange, offset?: number) {
  return {course_id: range.courseId, from_time: new Date(`${range.from}T00:00:00+03:00`).toISOString(),
    to_time: new Date(Date.parse(`${range.to}T00:00:00+03:00`) + 86400000).toISOString(),
    ...(offset === undefined ? {} : {offset, limit: 20})};
}
const values = (item: ActivityMetrics | null | undefined): CourseTimeValues => ({
  activeMs: item?.active_ms ?? null, theoryMs: item?.theory_ms ?? null, practiceMs: item?.practice_ms ?? null,
});
/** A sparse report with totals omits days with no recorded time, regardless of historical coverage. */
export function courseSnapshot(report: ActivityReportV1, range: CourseReportRange, dates: string[]): CourseTimeSnapshot {
  const daily = new Map(report.daily.map(day => [day.date, day]));
  const zero: ActivityMetrics = {unique_visitors: 0, active_ms: 0, theory_ms: 0, practice_ms: 0};
  return {courseId: range.courseId, from: range.from, to: range.to, quality: report.quality, asOf: report.as_of,
    totals: values(report.totals), days: dates.map(date => ({date, ...values(daily.get(date) ?? (report.totals === null ? null : zero))})),
    lessons: []};
}
