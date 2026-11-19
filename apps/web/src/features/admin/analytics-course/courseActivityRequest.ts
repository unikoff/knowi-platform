import type { CourseActivityReportV1 } from "./CourseActivityReport";

export type CourseActivityFilters = { from_time: string; to_time: string; course_id: string; timezone: "Europe/Moscow"; user_id?: number; expected_target_ref?: string };
// Expected actor and target are lifecycle guards checked by the shared client.
export type CourseActivityFetch = (filters: CourseActivityFilters, expectedActorRef: string, signal: AbortSignal) => Promise<CourseActivityReportV1>;
export type CourseActivityFailure = "access" | "error" | "timeout";

// One bounded request, no automatic retry. Cancelled or timed-out results are discarded
// even if a transport ignores AbortSignal. Never keep grants or credentials here.
export function startCourseActivityRequest({ filters, fetchReport, expectedActorRef, onReport, onFailure, timeoutMs = 12_000 }: {
  filters: CourseActivityFilters;
  fetchReport: CourseActivityFetch;
  expectedActorRef: string;
  onReport: (report: CourseActivityReportV1) => void;
  onFailure: (failure: CourseActivityFailure) => void;
  timeoutMs?: number;
}) {
  const controller = new AbortController();
  let finished = false;
  const timer = setTimeout(() => {
    if (finished) return;
    finished = true;
    controller.abort();
    onFailure("timeout");
  }, timeoutMs);
  Promise.resolve().then(() => {
    if (finished) return null;
    if (!expectedActorRef || (filters.user_id !== undefined && (!Number.isSafeInteger(filters.user_id) || filters.user_id <= 0 || !filters.expected_target_ref))) throw { kind: "denied" };
    return fetchReport(filters, expectedActorRef, controller.signal);
  }).then((report) => {
    if (finished || report === null) return;
    finished = true;
    clearTimeout(timer);
    const from = Date.parse(report.from_time), to = Date.parse(report.to_time);
    if (report.course_id !== filters.course_id || !Number.isFinite(from) || !Number.isFinite(to) || from !== Date.parse(filters.from_time) || to !== Date.parse(filters.to_time) || report.timezone !== filters.timezone) {
      onFailure("error");
      return;
    }
    onReport(report);
  }).catch((error: unknown) => {
    if (finished) return;
    finished = true;
    clearTimeout(timer);
    const status = typeof error === "object" && error !== null && "status" in error ? error.status : undefined;
    const kind = typeof error === "object" && error !== null && "kind" in error ? error.kind : undefined;
    onFailure(status === 401 || status === 403 || kind === "denied" ? "access" : "error");
  });
  return () => { finished = true; clearTimeout(timer); controller.abort(); };
}
