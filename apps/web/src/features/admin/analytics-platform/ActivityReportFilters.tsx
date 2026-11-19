import type { FormEvent } from "react";

export type ActivityFilterValues = { courseId: string; fromDate: string; toDateExclusive: string };
type CourseOption = { id: string; title: string };
type Props = { values: ActivityFilterValues; courses: readonly CourseOption[]; onChange: (values: ActivityFilterValues) => void; onApply: (values: ActivityFilterValues) => void; loading?: boolean; hideCourse?: boolean };

// Dates are calendar dates in Moscow; integration builds UTC [from, to).
function dayNumber(value: string): number | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const time = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value ? time / 86_400_000 : null;
}
export function ActivityReportFilters({ values, courses, onChange, onApply, loading = false, hideCourse = false }: Props) {
  const from = dayNumber(values.fromDate);
  const to = dayNumber(values.toDateExclusive);
  const valid = from !== null && to !== null && to > from && to - from <= 31 && (!values.courseId || courses.some(course => course.id === values.courseId));
  function submit(event: FormEvent) { event.preventDefault(); if (valid && !loading) onApply(values); }
  return <form className="admin-scoped-fields" onSubmit={submit} aria-label="Фильтры учебной активности">
    {!hideCourse && <label className="admin-scoped-field">Курс<select className="admin-scoped-input" value={values.courseId} onChange={event => onChange({...values, courseId: event.target.value})}><option value="">Все доступные курсы</option>{courses.map(course => <option key={course.id} value={course.id}>{course.title}</option>)}</select></label>}
    <label className="admin-scoped-field">С даты (Москва)<input className="admin-scoped-input" type="date" required value={values.fromDate} onChange={event => onChange({...values, fromDate: event.target.value})} aria-describedby="activity-period-hint" /></label>
    <label className="admin-scoped-field">До даты, не включая (Москва)<input className="admin-scoped-input" type="date" required value={values.toDateExclusive} onChange={event => onChange({...values, toDateExclusive: event.target.value})} aria-describedby="activity-period-hint" /></label>
    <button className="admin-scoped-button admin-scoped-button--primary" type="submit" disabled={!valid || loading}>Показать</button>
    <p id="activity-period-hint" className="admin-note">Период до 31 дня; конечная дата не включается.</p>
  </form>;
}
