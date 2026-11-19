import type { ReactNode } from "react";

// Presentation state only. The integration layer must map the versioned report
// and its coverage/availability to these states without guessing from values.
export type CourseAnalyticsViewState =
  | { kind: "loading" }
  | { kind: "error"; retry?: () => void }
  | { kind: "access" }
  | { kind: "empty" }
  | { kind: "stale"; retry?: () => void }
  | { kind: "unavailable"; message?: string }
  | { kind: "ready"; coverage: "complete" | "partial" | "unknown"; coverageNote?: string; children: ReactNode };

export function CourseAnalyticsPanel({ title, state }: { title: string; state: CourseAnalyticsViewState }) {
  return <section className="admin-overview__group course-analytics__panel" aria-label={title}>
    <div className="admin-overview__group-heading"><h2>{title}</h2></div>
    {state.kind === "loading" && <p className="admin-note" role="status">Загружаем статистику…</p>}
    {state.kind === "error" && <div className="form-error" role="alert">Не удалось загрузить статистику. {state.retry && <button className="admin-back" type="button" onClick={state.retry}>Повторить</button>}</div>}
    {state.kind === "access" && <p className="form-error" role="alert">Нет доступа к статистике. Проверьте учётную запись и права.</p>}
    {state.kind === "stale" && <div className="admin-note" role="status">Статистика ещё обновляется. Данные за период пока недоступны. {state.retry && <button className="admin-back" type="button" onClick={state.retry}>Обновить</button>}</div>}
    {state.kind === "empty" && <p className="admin-overview__empty">За выбранный период данных нет.</p>}
    {state.kind === "unavailable" && <p className="admin-overview__empty">{state.message ?? "Статистика пока недоступна."}</p>}
    {state.kind === "ready" && <>
      {state.coverage !== "complete" && <p className="admin-note" role="status">{state.coverageNote || (state.coverage === "partial" ? "Данные за период неполные." : "Полнота данных неизвестна.")}</p>}
      {state.children}
    </>}
  </section>;
}

// The caller supplies already formatted, scope-correct values. Null is not zero.
export function CourseAnalyticsMetric({ label, value, note, missingNote = "Данные недоступны" }: { label: string; value: string | null; note?: string; missingNote?: string }) {
  return <div className="admin-overview__metric"><small>{label}</small><strong>{value === null ? "—" : value}</strong><span>{value === null ? missingNote : note}</span></div>;
}
