import type { ActivityDetailsReport } from "../../analytics-reporting/types";
import { formatActivityCount, formatActivityMinutes } from "./activityFormatting";

type CourseOption = {id: string; title: string};
type Props = { data: ActivityDetailsReport; courses: readonly CourseOption[]; offset: number; onPrevious: () => void; onNext: (offset: number) => void };

/** Per-row figures are independent of the server's distinct period total. */
export function ActivityBreakdown({data, courses, offset, onPrevious, onNext}: Props) {
  if (data.report.status !== "ok") return null;
  const incomplete = data.report.quality !== "complete";
  return <section className="admin-overview__group" aria-label="Детали по курсам и урокам">
    <div className="admin-overview__group-heading"><h2>Детали по курсам и урокам</h2></div>
    <p className="admin-note">Посетители в разных строках могут совпадать. Названия уроков показаны по их идентификаторам.</p>
    {data.rows.length ? <div className="activity-report__days">{data.rows.map(row => {
      const course = courses.find(item => item.id === row.course_id)?.title ?? row.course_id;
      return <article className="activity-report__detail" key={`${row.level}:${row.course_id}:${row.lesson_id ?? ""}`}>
        <h3>{row.level === "course" ? `Курс: ${course}` : `Урок: ${row.lesson_id}`}</h3>
        {row.level === "lesson" && <p className="admin-note">Курс: {course}</p>}
        <dl><div><dt>Посетители</dt><dd>{formatActivityCount(row.unique_visitors, incomplete)}</dd></div><div><dt>Активное время</dt><dd>{formatActivityMinutes(row.active_ms, incomplete)}</dd></div><div><dt>Теория</dt><dd>{formatActivityMinutes(row.theory_ms, incomplete)}</dd></div><div><dt>Практика</dt><dd>{formatActivityMinutes(row.practice_ms, incomplete)}</dd></div></dl>
      </article>;
    })}</div> : <p className="admin-overview__empty">{incomplete ? "Наблюдений по курсам и урокам нет; покрытие неполное или неизвестно." : "За выбранный период данные по курсам и урокам отсутствуют."}</p>}
    {(offset > 0 || data.next_offset !== null) && <nav className="admin-scoped-fields" aria-label="Страницы деталей">
      {offset > 0 && <button className="admin-scoped-button" type="button" onClick={onPrevious}>Предыдущие</button>}
      {data.next_offset !== null && <button className="admin-scoped-button" type="button" onClick={() => onNext(data.next_offset!)}>Следующие</button>}
    </nav>}
  </section>;
}
