import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
const require = createRequire(import.meta.url);
function compile(path, imports = {}) {
  const source = fs.readFileSync(new URL(path, import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const output = { exports: {} };
  new Function("require", "module", "exports", code)((name) => name.endsWith(".css") ? {} : (imports[name] ?? require(name)), output, output.exports);
  return output.exports;
}

test("course month grid marks free and booked days without exposing a student ID", () => {
  const dates = compile("../../shared/lib/dates.ts");
  const grid = compile("../../shared/ui/calendar/CalendarMonthGrid.tsx", {"../../lib/dates": dates});
  const { ScopedCalendar } = compile("./ScopedCalendar.tsx", {"../../shared/ui/calendar/CalendarMonthGrid": grid});
  const html = renderToStaticMarkup(React.createElement(ScopedCalendar, { month: {year: 2026, month: 11}, slots: [
    { id: 1, course_id: "backend", owner_id: 2, starts_at: "2026-11-10T10:00:00+03:00", ends_at: "2026-11-10T11:00:00+03:00", status: "free", student_id: null },
    { id: 2, course_id: "backend", owner_id: 2, starts_at: "2026-11-12T10:00:00+03:00", ends_at: "2026-11-12T11:00:00+03:00", status: "booked", student_id: 987654321 },
  ] }));
  assert.match(html, /Свободное время/); assert.match(html, /Занято/);
  assert.doesNotMatch(html, /987654321|Ваша запись/); assert.match(html, /role="grid"/);
});

test("booking month uses Moscow midnight rather than the browser timezone", () => {
  const { moscowCalendarMonth } = compile("../../shared/lib/moscow-month.ts");
  assert.deepEqual(moscowCalendarMonth(new Date("2026-10-31T21:30:00Z")), {year: 2026, month: 11});
});
