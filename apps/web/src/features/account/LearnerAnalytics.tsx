import { useEffect, useMemo, useState } from "react";
import { AnalyticsReportError, fetchActivityReport, fetchActivityDetails } from "../analytics-reporting/client";
import type { ActivityMetrics, ActivityReportV1 } from "../analytics-reporting/types";
import "./learner-analytics.css";

type Period = 1 | 7 | 30;
type Quality = ActivityReportV1["quality"];
type DetailRow = ActivityMetrics & { level: "course" | "lesson"; course_id: string; lesson_id: string | null };
type Details = { report: ActivityReportV1; lesson_id: string | null; rows: DetailRow[]; next_offset: number | null };
export type LearnerAnalyticsCatalog = { id: string; title: string; lessons: {id: string; title: string}[] }[];
type Result<T> = { key: string; data: T | null; error: "denied" | "unavailable" | "invalid_response" | null };

export function learnerPeriod(days: Period, now = Date.now()) {
  const moscowToday = new Date(now + 3 * 3600000).toISOString().slice(0, 10);
  const end = Date.parse(`${moscowToday}T00:00:00+03:00`) + 86400000;
  return { from_time: new Date(end - days * 86400000).toISOString(), to_time: new Date(end).toISOString() };
}
export function learnerDuration(value: number, quality: Quality): string {
  if (value === 0 && quality !== "complete") return "—";
  if (value === 0) return "0 мин";
  if (value < 60000) return "< 1 мин";
  const minutes = Math.floor(value / 60000);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} ч ${minutes % 60} мин` : `${minutes} мин`;
}
function failure(error: unknown): Result<never>["error"] {
  return error instanceof AnalyticsReportError ? error.kind : "unavailable";
}
function StateMessage({error, retry}: {error: Result<never>["error"]; retry: () => void}) {
  if (!error) return <p role="status">Загружаем учебную активность…</p>;
  return <div role="alert"><p>{error === "denied" ? "Нет доступа к учебной аналитике." : error === "invalid_response" ? "Не удалось прочитать ответ аналитики." : "Не удалось загрузить учебную аналитику. Попробуйте ещё раз."}</p>{error !== "denied" && <button type="button" onClick={retry}>Повторить</button>}</div>;
}
export function LearnerReportSummary({ report }: {report: ActivityReportV1}) {
  const quality = report.quality === "complete" ? "полные" : report.quality === "partial" ? "частичные" : "полнота неизвестна";
  return <>
    {report.status === "stale" && <p role="status">Данные устарели и могут измениться после обновления.</p>}
    {report.status === "unavailable" ? <p role="status">Учебная аналитика временно недоступна.</p> : <>
      <p>Наблюдаемая активность · Данные: {quality}{report.as_of ? ` · по состоянию на ${new Date(report.as_of).toLocaleString("ru-RU", {timeZone: "Europe/Moscow"})} (Москва)` : ""}</p>
      {report.status === "no_data" && <p>За выбранный период активности нет.</p>}
      <div className="learner-analytics__metrics">
        {([["Всего", "active_ms"], ["Теория", "theory_ms"], ["Практика", "practice_ms"]] as const).map(([title, field]) => <div key={field}><span>{title}</span><strong>{report.totals ? learnerDuration(report.totals[field], report.quality) : "—"}</strong></div>)}
      </div>
      {report.quality !== "complete" && <p>Показано наблюдаемое время; отсутствие данных не означает нулевую активность.</p>}
    </>}
  </>;
}
export function LearnerDetailRows({rows, quality, course, catalog, onSelectCourse}: {rows: DetailRow[]; quality: Quality; course: string | null; catalog: LearnerAnalyticsCatalog; onSelectCourse: (id: string) => void}) {
  const courseTitle = (id: string) => catalog.find(item => item.id === id)?.title ?? `Курс (${id})`;
  const lessonTitle = (courseId: string, id: string) => catalog.find(item => item.id === courseId)?.lessons.find(item => item.id === id)?.title ?? `Урок (${id})`;
  return <ul>{rows.map(row => <li className={row.level === "lesson" ? "learner-analytics__lesson" : undefined} key={JSON.stringify([row.course_id, row.lesson_id])}>
    {row.level === "course" ? (course ? <strong>{courseTitle(row.course_id)}</strong> : <button type="button" onClick={() => onSelectCourse(row.course_id)}>{courseTitle(row.course_id)}</button>) : <strong>{courseTitle(row.course_id)} / {lessonTitle(row.course_id, row.lesson_id ?? "")}</strong>}
    <span>Всего: {learnerDuration(row.active_ms, quality)} · Теория: {learnerDuration(row.theory_ms, quality)} · Практика: {learnerDuration(row.practice_ms, quality)}</span>
  </li>)}</ul>;
}

/** Requests are keyed to account and period; old responses are never rendered after a scope change. */
export function LearnerAnalytics({ actorRef, authRevision, role, catalog }: { actorRef?: string; authRevision?: number; role?: string; catalog: LearnerAnalyticsCatalog }) {
  const [days, setDays] = useState<Period>(7);
  const [windowAnchor, setWindowAnchor] = useState(() => Date.now());
  const [course, setCourse] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [previousOffsets, setPreviousOffsets] = useState<number[]>([]);
  const [retry, setRetry] = useState(0);
  const range = useMemo(() => learnerPeriod(days, windowAnchor), [days, windowAnchor]);
  const key = JSON.stringify([actorRef, authRevision, role, range, retry]);
  const detailKey = JSON.stringify([key, course, offset]);
  const [summary, setSummary] = useState<Result<ActivityReportV1>>({key: "", data: null, error: null});
  const [details, setDetails] = useState<Result<Details>>({key: "", data: null, error: null});
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    fetchActivityReport(range, actorRef ?? "", controller.signal).then(data => {if (active) setSummary({key, data, error: null});}, error => {if (active && error?.name !== "AbortError") setSummary({key, data: null, error: failure(error)});});
    return () => {active = false; controller.abort();};
  }, [key]);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    fetchActivityDetails({...range, ...(course ? {course_id: course} : {}), offset, limit: 20}, actorRef ?? "", controller.signal).then(data => {if (active) setDetails({key: detailKey, data, error: null});}, error => {if (active && error?.name !== "AbortError") setDetails({key: detailKey, data: null, error: failure(error)});});
    return () => {active = false; controller.abort();};
  }, [detailKey]);
  const current = summary.key === key ? summary : null;
  const currentDetails = details.key === detailKey ? details : null;
  const courseTitle = (id: string) => catalog.find(item => item.id === id)?.title ?? `Курс (${id})`;
  const selectCourse = (id: string | null) => {setCourse(id); setOffset(0); setPreviousOffsets([]);};
  const changePeriod = (value: Period) => {setWindowAnchor(Date.now()); setDays(value); setCourse(null); setOffset(0); setPreviousOffsets([]);};
  return <section className="account-surface account-detail-card learner-analytics" aria-label="Учебная активность">
    <header><div><h3>Учебная активность</h3><p>За календарные дни по московскому времени</p></div></header>
    <div className="account-statistics__period" role="group" aria-label="Период учебной активности">
      {([[1, "День"], [7, "Неделя"], [30, "Месяц (30 дней)"]] as const).map(([value, label]) => <button key={value} type="button" aria-pressed={days === value} onClick={() => changePeriod(value)}>{label}</button>)}
    </div>
    {!current || (!current.data && !current.error) ? <StateMessage error={null} retry={() => setRetry(n => n + 1)} /> : current.error ? <StateMessage error={current.error} retry={() => setRetry(n => n + 1)} /> : current.data && <LearnerReportSummary report={current.data} />}
    <div className="learner-analytics__details">
      <h4>{course ? "Активность курса и уроков" : "Активность по курсам и урокам"}</h4>
      {course && <p><button type="button" onClick={() => selectCourse(null)}>Все курсы</button> / {courseTitle(course)}</p>}
      {!currentDetails || (!currentDetails.data && !currentDetails.error) ? <StateMessage error={null} retry={() => setRetry(n => n + 1)} /> : currentDetails.error ? <StateMessage error={currentDetails.error} retry={() => setRetry(n => n + 1)} /> : currentDetails.data && <>
        {currentDetails.data.report.status === "unavailable" ? <p>Детализация временно недоступна.</p> : <>
          {currentDetails.data.report.status === "stale" && <p>Детализация устарела и может измениться после обновления.</p>}
          <p>Наблюдаемое время · {currentDetails.data.report.quality === "complete" ? "полные данные" : currentDetails.data.report.quality === "partial" ? "частичные данные" : "полнота данных неизвестна"}</p>
          {currentDetails.data.rows.length === 0 && <p>{currentDetails.data.report.quality === "complete" ? "За этот период данных об активности нет." : "Наблюдаемая активность не найдена; данные могут быть неполными."}</p>}
          <LearnerDetailRows rows={currentDetails.data.rows} quality={currentDetails.data.report.quality} course={course} catalog={catalog} onSelectCourse={selectCourse} />
          <div className="learner-analytics__pages">{previousOffsets.length > 0 && <button type="button" onClick={() => {setOffset(previousOffsets[previousOffsets.length - 1]); setPreviousOffsets(previousOffsets.slice(0, -1));}}>Назад</button>}{currentDetails.data.next_offset !== null && <button type="button" onClick={() => {setPreviousOffsets([...previousOffsets, offset]); setOffset(currentDetails.data!.next_offset!);}}>Далее</button>}</div>
        </>}
      </>}
    </div>
  </section>;
}
