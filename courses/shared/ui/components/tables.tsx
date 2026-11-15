import React, { useId } from "react";
import { VisualHeading } from "@platform/shared/ui/VisualHeading";

export type InteractiveTableRow = {
  id: string;
  label: React.ReactNode;
  values: readonly React.ReactNode[];
};

/** A readable table with row highlighting on hover. */
export function InteractiveTable({ title, columns, rows }: {
  title: string;
  columns: readonly string[];
  rows: readonly InteractiveTableRow[];
}) {
  const id = useId();
  if (columns.length < 2 || rows.length === 0) return null;

  return (
    <section className="lesson-table-card" aria-labelledby={`${id}-title`}>
      <header className="lesson-table-heading">
        <VisualHeading kind="table" />
        <h3 id={`${id}-title`}>{title}</h3>
      </header>
      <div className="lesson-table-scroll" role="region" aria-labelledby={`${id}-title`} tabIndex={0}>
        <table className="lesson-interactive-table" aria-labelledby={`${id}-title`} style={{ "--lesson-table-min-width": `${columns.length * 10}rem` } as React.CSSProperties}>
          <thead>
            <tr>{columns.map((column, index) => <th scope="col" key={index}>{column}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <tr key={row.id}>
                <th scope="row">{row.label}</th>
                {columns.slice(1).map((_, cellIndex) => <td key={cellIndex}>{row.values[cellIndex] ?? "—"}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
