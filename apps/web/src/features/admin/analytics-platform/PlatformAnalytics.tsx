import { useEffect, useState } from "react";
import { fetchActivityDetails, AnalyticsReportError } from "../../analytics-reporting/client";
import type { ActivityDetailsReport } from "../../analytics-reporting/types";
import { ActivityReport } from "./ActivityReport";
import { ActivityBreakdown } from "./ActivityBreakdown";
import { ActivityReportFilters, type ActivityFilterValues } from "./ActivityReportFilters";
import { AnalyticsReportState } from "./AnalyticsReportState";

type CourseOption = { id: string; title: string };
type Props = { actorRef?: string; courseId: string; scopeLabel: string; courses: readonly CourseOption[] };
type Result = {key: string; data: ActivityDetailsReport | null; loading: boolean; error: string; denied: boolean};
const pageSize = 20;
function dateString(ms: number) { return new Date(ms + 3 * 3600000).toISOString().slice(0, 10); }
function initialDates(): Pick<ActivityFilterValues, "fromDate" | "toDateExclusive"> {
  const now = Date.now();
  return {fromDate: dateString(now), toDateExclusive: dateString(now + 86400000)};
}
function toUtc(date: string) { return new Date(`${date}T00:00:00+03:00`).toISOString(); }

/** Course filter is shared with the Core overview; report results are keyed by the applied scope and dates. */
export function PlatformAnalytics({actorRef, courseId, scopeLabel, courses}: Props) {
  const [dates, setDates] = useState(initialDates);
  const [applied, setApplied] = useState<Pick<ActivityFilterValues, "fromDate" | "toDateExclusive"> | null>(() => dates);
  const [retry, setRetry] = useState(0);
  const [page, setPage] = useState({scopeKey: "", offset: 0});
  const scopeKey = applied ? JSON.stringify([actorRef, courseId, applied.fromDate, applied.toDateExclusive]) : "";
  const offset = page.scopeKey === scopeKey ? page.offset : 0;
  const key = scopeKey ? JSON.stringify([scopeKey, offset, retry]) : "";
  const [result, setResult] = useState<Result>({key: "", data: null, loading: false, error: "", denied: false});
  useEffect(() => {
    if (!applied) return;
    const controller = new AbortController();
    let active = true;
    setResult({key, data: null, loading: true, error: "", denied: false});
    fetchActivityDetails({from_time: toUtc(applied.fromDate), to_time: toUtc(applied.toDateExclusive), ...(courseId ? {course_id: courseId} : {}), offset, limit: pageSize}, actorRef ?? "", controller.signal)
      .then(data => { if (active) setResult({key, data, loading: false, error: "", denied: false}); })
      .catch(error => { if (active && error?.name !== "AbortError") setResult({key, data: null, loading: false, error: error instanceof Error ? error.message : "Учебная аналитика временно недоступна.", denied: error instanceof AnalyticsReportError && error.kind === "denied"}); });
    return () => {active = false; controller.abort();};
  }, [key]);
  const current = result.key === key && key ? result : {data: null, loading: Boolean(key), error: "", denied: false};
  return <section className="platform-overview" aria-label="Учебная аналитика">
    <h2>Учебная аналитика</h2>
    <p className="admin-note">Курс выбирается выше. Показатели ниже рассчитывает Analytics; общая статистика платформы расположена далее.</p>
    <ActivityReportFilters hideCourse values={{courseId, ...dates}} courses={courses}
      onChange={value => {setDates({fromDate: value.fromDate, toDateExclusive: value.toDateExclusive}); setPage({scopeKey: "", offset: 0}); setApplied(null);}}
      onApply={value => {setPage({scopeKey: "", offset: 0}); setApplied({fromDate: value.fromDate, toDateExclusive: value.toDateExclusive}); setRetry(n => n + 1);}} />
    {current.loading ? <AnalyticsReportState title="Учебная активность" state={{status: "loading"}} />
      : current.error ? <AnalyticsReportState title="Учебная активность" state={{status: "error", message: current.error, ...(!current.denied ? {retry: () => setRetry(n => n + 1)} : {})}} />
      : current.data ? <><ActivityReport report={current.data.report} scopeLabel={scopeLabel} /><ActivityBreakdown data={current.data} courses={courses} offset={offset} onPrevious={() => setPage({scopeKey, offset: Math.max(0, offset - pageSize)})} onNext={next => setPage({scopeKey, offset: next})} /></>
      : <AnalyticsReportState title="Учебная активность" state={{status: "unavailable", message: "Выберите период и нажмите «Показать»."}} />}
  </section>;
}
