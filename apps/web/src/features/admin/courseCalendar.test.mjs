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
test("course calendar renders all dates and statuses without exposing student identifiers", () => {
  const dates = compile("../../shared/lib/dates.ts");
  const { CourseCalendar } = compile("./CourseCalendar.tsx", {"../../shared/lib/dates": dates});
  const html = renderToStaticMarkup(React.createElement(CourseCalendar, { month: { year: 2026, month: 11 }, slots: [
    { id: 1, starts_at: "2026-11-19T10:00:00+03:00", ends_at: "2026-11-19T11:00:00+03:00", status: "free", student_id: null },
    { id: 2, starts_at: "2026-11-20T11:00:00+03:00", ends_at: "2026-11-20T12:00:00+03:00", status: "booked", student_id: 987654321 },
    { id: 3, starts_at: "2026-11-21T10:00:00+03:00", ends_at: "2026-11-21T11:00:00+03:00", status: "archived", student_id: null },
  ] }));
  assert.equal((html.match(/class="admin-calendar__date"/g) ?? []).length, 30);
  assert.match(html, /Свободно/); assert.match(html, /Занято/);
  assert.doesNotMatch(html, /987654321|В архиве/);
  assert.match(html, /Расписание за месяц/);
});
