import type { ActivityDetailsReport, ActivityMetrics } from "../../analytics-reporting/types";

type LessonOption = { id: string; title: string };
const number = new Intl.NumberFormat("ru-RU", {maximumFractionDigits: 3});
function duration(value: number, incomplete: boolean) {
  if (value === 0) return incomplete ? "—" : "0 мин";
  return value < 60_000 ? `${number.format(value / 1000)} с` : `${number.format(value / 60_000)} мин`;
}
function metrics(row: ActivityMetrics, incomplete: boolean) {
  return `Активное: ${duration(row.active_ms, incomplete)} · Теория: ${duration(row.theory_ms, incomplete)} · Практика: ${duration(row.practice_ms, incomplete)}`;
}
export function CourseActivityDetails({ details, lessons, selectedLesson, onSelectLesson, offset, onPrevious, onNext }: {
  details: ActivityDetailsReport; lessons: readonly LessonOption[]; selectedLesson: string; onSelectLesson: (id: string) => void;
  offset: number; onPrevious: () => void; onNext: (offset: number) => void;
}) {
  const { report, rows, next_offset: next } = details;
  if (report.status === "unavailable" || report.status === "stale") return null;
  const incomplete = report.quality !== "complete";
  const title = (id: string) => lessons.find((item) => item.id === id)?.title ?? `Урок (${id})`;
  return <section className="admin-overview__group course-analytics-details" aria-label="Активность курса и уроков">
    <div className="admin-overview__group-heading"><h2>Активность курса и уроков</h2></div>
    {incomplete && <p className="admin-note">Показаны наблюдения; отсутствие времени не подтверждает отсутствие активности.</p>}
    {report.status === "no_data" || rows.length === 0 ? <p className="admin-overview__empty">{incomplete ? (report.quality === "partial" ? "Наблюдаемая активность не найдена; данные неполные." : "Наблюдаемая активность не найдена; полнота данных неизвестна.") : "За выбранный период данных об активности нет."}</p> : <div className="admin-overview__student-list">
      {rows.map((row) => <article className="course-analytics-details__row" key={`${row.course_id}:${row.lesson_id ?? "course"}`}>
        {row.level === "lesson" && row.lesson_id ? <button className="admin-back" type="button" onClick={() => onSelectLesson(row.lesson_id!)} disabled={row.lesson_id === selectedLesson}>{title(row.lesson_id)}</button> : <strong>{selectedLesson ? "Итог по выбранному уроку" : "Весь курс"}</strong>}
        <span>{metrics(row, incomplete)}</span>
      </article>)}
    </div>}
    <nav className="admin-scoped-fields" aria-label="Страницы детализации">
      <button className="admin-back" type="button" disabled={offset === 0} onClick={onPrevious}>Назад</button>
      <button className="admin-back" type="button" disabled={next === null} onClick={() => { if (next !== null) onNext(next); }}>Далее</button>
    </nav>
  </section>;
}
