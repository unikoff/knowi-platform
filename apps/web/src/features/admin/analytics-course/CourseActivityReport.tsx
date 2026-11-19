import "../overview/overview.css";
import "./course-analytics.css";
import { CourseAnalyticsMetric, CourseAnalyticsPanel, type CourseAnalyticsViewState } from "./CourseAnalyticsPanel";
import type { ActivityMetrics, ActivityReportV1 } from "../../analytics-reporting/types";

// Report-shaped presentation input; this file neither fetches nor authorizes data.
// The integration layer must validate the final wire DTO and bind every response
// to its request, including actor, course, period and selected roster lifecycle.
export type CourseActivityReportV1 = ActivityReportV1;
type ActivityTotals = ActivityMetrics;
export type CourseActivityView =
  | { kind: "loading" }
  | { kind: "error"; reason?: "access" | "error" | "timeout"; retry?: () => void }
  | { kind: "report"; report: CourseActivityReportV1; retry?: () => void };

const number = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 });
function duration(ms: number) {
  if (ms === 0) return "0 мин";
  return ms < 60_000 ? `${number.format(ms / 1000)} с` : `${number.format(ms / 60_000)} мин`;
}
function timestamp(value: string | null) {
  if (value === null || !Number.isFinite(Date.parse(value))) return "недоступен";
  return new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Moscow", day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}
function calendarDate(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : value;
}
function reportState(view: CourseActivityView, courseId: string): CourseAnalyticsViewState | null {
  if (view.kind === "loading") return { kind: "loading" };
  if (view.kind === "error") return view.reason === "access" ? { kind: "access" } : { kind: "error", retry: view.retry };
  const report = view.report;
  if (report.schema_version !== 1 || report.metric_version !== "activity-v1" || report.timezone !== "Europe/Moscow" || report.course_id !== courseId) return { kind: "unavailable" };
  if (report.status === "unavailable") return { kind: "unavailable" };
  if (report.status === "stale") return { kind: "stale", retry: view.retry };
  if (report.status !== "ok" && report.status !== "no_data") return { kind: "unavailable" };
  if (report.status === "no_data") {
    const zero = (values: ActivityTotals) => values.unique_visitors === 0 && values.active_ms === 0 && values.theory_ms === 0 && values.practice_ms === 0;
    if (report.quality !== "complete" || report.totals === null || !zero(report.totals) || report.daily.some((day) => !zero(day))) return { kind: "unavailable" };
  }
  return null;
}

export function CourseActivityReport({ courseId, view, title = "Учебная активность", scopeDescription }: { courseId: string; view: CourseActivityView; title?: string; scopeDescription?: string }) {
  const fallback = reportState(view, courseId);
  if (fallback) return <CourseAnalyticsPanel title={title} state={fallback} />;
  if (view.kind !== "report") return null;
  const { report } = view;
  const totals = report.totals;
  const progress = report.trusted_progress;
  const incomplete = report.quality !== "complete";
  const display = (value: number | null, format: (value: number) => string) => value === null || (incomplete && value === 0) ? null : format(value);
  const missingNote = (value: number | null) => value === 0 && incomplete ? "Ноль не подтверждён" : undefined;
  const dailyValue = (value: number, format: (value: number) => string) => incomplete && value === 0
    ? <span aria-label="Ноль наблюдений не подтверждает отсутствие активности">—</span> : format(value);
  return <CourseAnalyticsPanel title={title} state={{ kind: "ready", coverage: report.quality, children: <>
    {scopeDescription && <p className="course-analytics__meta">{scopeDescription}</p>}
    <p className="course-analytics__period">Период: {timestamp(report.from_time)} — до {timestamp(report.to_time)} · Москва</p>
    <p className="course-analytics__meta">Срез событий: {timestamp(report.as_of)}; это не гарантия полноты. Задержка по отчёту: {report.lag_seconds === null ? "неизвестна" : `${number.format(report.lag_seconds)} с`}.</p>
    {incomplete && report.status === "ok" && <p className="course-analytics__meta">При неполном покрытии нулевые наблюдения не подтверждают отсутствие активности.</p>}
    {report.status === "no_data" ? <p className="admin-overview__empty">За выбранный период данных нет.</p> : <>
      <div className="admin-overview__group-metrics course-analytics__metrics">
        <CourseAnalyticsMetric label="Посетители за период" value={display(totals?.unique_visitors ?? null, (value) => number.format(value))} missingNote={missingNote(totals?.unique_visitors ?? null)} note="Уникальные пользователи курса" />
        <CourseAnalyticsMetric label="Активное время" value={display(totals?.active_ms ?? null, duration)} missingNote={missingNote(totals?.active_ms ?? null)} />
        <CourseAnalyticsMetric label="Теория" value={display(totals?.theory_ms ?? null, duration)} missingNote={missingNote(totals?.theory_ms ?? null)} />
        <CourseAnalyticsMetric label="Практика" value={display(totals?.practice_ms ?? null, duration)} missingNote={missingNote(totals?.practice_ms ?? null)} />
        {progress && <CourseAnalyticsMetric label="Завершено уроков" value={progress.state === "known" && progress.completed_lessons !== null ? number.format(progress.completed_lessons) : null} note="Данные прогресса Core" />}
      </div>
      <section aria-label="Активность по дням"><div className="admin-overview__section-heading"><h3>По дням</h3></div>
        {report.daily.length === 0 ? <p className="admin-overview__empty">Дневные данные за период недоступны.</p> : <>
          <p className="course-analytics__meta">Посетители за период считаются отдельно: числа по дням нельзя складывать.</p>
          <div className="course-analytics__table-wrap" role="region" aria-label="Таблица активности по дням" tabIndex={0}><table className="course-analytics__table"><thead><tr><th scope="col">День</th><th scope="col">Посетители</th><th scope="col">Активное время</th><th scope="col">Теория</th><th scope="col">Практика</th></tr></thead><tbody>{report.daily.map((day) => <tr key={day.date}><th scope="row"><time dateTime={day.date}>{calendarDate(day.date)}</time></th><td>{dailyValue(day.unique_visitors, (value) => number.format(value))}</td><td>{dailyValue(day.active_ms, duration)}</td><td>{dailyValue(day.theory_ms, duration)}</td><td>{dailyValue(day.practice_ms, duration)}</td></tr>)}</tbody></table></div>
        </>}
      </section>
    </>}
  </>}} />;
}
