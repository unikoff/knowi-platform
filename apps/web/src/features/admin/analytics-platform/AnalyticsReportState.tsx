import type { ReactNode } from "react";

// UI state only. The integration layer must map the agreed Analytics DTO to it.
export type AnalyticsReportViewState =
  | { status: "loading" }
  | { status: "error"; message: string; retry?: () => void }
  | { status: "empty"; message: string }
  | { status: "unavailable"; message: string }
  | { status: "ready"; freshness?: string; partial?: string };

type Props = { title: string; state: AnalyticsReportViewState; children?: ReactNode };

/** Preserves the distinction between no data, unavailable data and zero values. */
export function AnalyticsReportState({ title, state, children }: Props) {
  return <section className="admin-overview__group" aria-label={title}>
    <div className="admin-overview__group-heading"><h2>{title}</h2></div>
    {state.status === "loading" && <p className="admin-note" role="status">Загружаем показатели…</p>}
    {state.status === "error" && <><p className="form-error" role="alert">{state.message}</p>{state.retry && <button className="admin-scoped-button" type="button" onClick={state.retry}>Повторить</button>}</>}
    {(state.status === "empty" || state.status === "unavailable") && <p className="admin-overview__empty">{state.message}</p>}
    {state.status === "ready" && <>
      {state.partial && <p className="admin-note" role="status">{state.partial}</p>}
      {state.freshness && <p className="admin-note">{state.freshness}</p>}
      {children}
    </>}
  </section>;
}
