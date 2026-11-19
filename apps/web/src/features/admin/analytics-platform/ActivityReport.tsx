import { AnalyticsReportState, type AnalyticsReportViewState } from "./AnalyticsReportState";
import type { ActivityReportV1 } from "./activityReportTypes";
import "./activity-report.css";
import { formatActivityCount as observedCount, formatActivityMinutes as minutes } from "./activityFormatting";

type Props = { report: ActivityReportV1; scopeLabel: string };
const number = (value: number) => Number.isFinite(value) && value >= 0 ? value.toLocaleString("ru-RU") : "—";
function timestamp(value: string | null) {
  if (!value) return "неизвестно";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "неизвестно" : new Intl.DateTimeFormat("ru-RU", {dateStyle: "short", timeStyle: "short", timeZone: "Europe/Moscow"}).format(date) + " (Москва)";
}
function metadata(report: ActivityReportV1) {
  const lag = report.lag_seconds;
  return `Срез на: ${timestamp(report.as_of)}. Задержка обработки: ${typeof lag === "number" && Number.isFinite(lag) && lag >= 0 ? `${number(lag)} с` : "неизвестна"}.`;
}
function stateFor(report: ActivityReportV1): AnalyticsReportViewState {
  const freshness = metadata(report);
  if (report.status === "stale") return {status: "unavailable", message: `Отчёт устарел, показатели временно недоступны. ${freshness}`};
  if (report.status === "unavailable") return {status: "unavailable", message: `Показатели недоступны. ${freshness}`};
  if (report.status === "no_data") return {status: "empty", message: `Нет данных за выбранный период. ${report.quality === "complete" ? "Покрытие полное." : report.quality === "partial" ? "Покрытие неполное." : "Полнота неизвестна."} ${freshness}`};
  return {status: "ready", freshness, ...(report.quality === "partial" ? {partial: "Данные за период неполные; нулевые показатели не подтверждают отсутствие активности."} : report.quality === "unknown" ? {partial: "Полнота данных неизвестна; нулевые показатели не подтверждают отсутствие активности."} : {})};
}
function Metric({ title, value }: {title: string; value: string}) {
  return <div className="admin-overview__metric"><small>{title}</small><strong>{value}</strong></div>;
}

/** Does not aggregate distinct counts: period and each day come from the server. */
export function ActivityReport({ report, scopeLabel }: Props) {
  const totals = report.totals;
  const incomplete = report.quality !== "complete";
  const progress = report.trusted_progress;
  return <AnalyticsReportState title="Учебная активность" state={stateFor(report)}>
    <p className="admin-note">{scopeLabel} · с {timestamp(report.from_time)} до {timestamp(report.to_time)}, не включая конечный момент</p>
    {totals ? <div className="admin-overview__group-metrics activity-report__metrics">
      <Metric title="Посетители за период" value={observedCount(totals.unique_visitors, incomplete)} />
      <Metric title="Активное время" value={minutes(totals.active_ms, incomplete)} />
      <Metric title="Теория" value={minutes(totals.theory_ms, incomplete)} />
      <Metric title="Практика" value={minutes(totals.practice_ms, incomplete)} />
    </div> : <p className="admin-overview__empty">Суммарные показатели недоступны.</p>}
    {progress && <p className="admin-note">Завершено уроков: {progress.state === "known" && progress.completed_lessons !== null ? number(progress.completed_lessons) : "данные недоступны"}.</p>}
    <section aria-label="Активность по дням"><div className="admin-overview__section-heading"><h3>Активность по дням</h3></div>
      {report.daily.length ? <div className="activity-report__days">{report.daily.map(day => <div className="activity-report__day" key={day.date}>
        <time dateTime={day.date}>{day.date}</time><span>Посетители: <strong>{observedCount(day.unique_visitors, incomplete)}</strong></span><span>Время: <strong>{minutes(day.active_ms, incomplete)}</strong></span><span>Теория: {minutes(day.theory_ms, incomplete)}</span><span>Практика: {minutes(day.practice_ms, incomplete)}</span>
      </div>)}</div> : <p className="admin-overview__empty">Данные по дням отсутствуют.</p>}
      <p className="admin-note">Посетитель учитывается один раз за период; суммы по дням могут отличаться от общего числа.</p>
    </section>
  </AnalyticsReportState>;
}
