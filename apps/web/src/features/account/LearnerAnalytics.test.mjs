import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import fs from "node:fs";
import {createRequire} from "node:module";
import ts from "typescript";

const require = createRequire(import.meta.url);
const source = fs.readFileSync(new URL("./LearnerAnalytics.tsx", import.meta.url), "utf8");
const code = ts.transpileModule(source, {compilerOptions: {jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
const output = {exports: {}};
new Function("require", "module", "exports", code)(name => name.endsWith(".css") || name === "../analytics-reporting/client" ? {} : require(name), output, output.exports);
const {LearnerReportSummary, LearnerDetailRows, learnerDuration, learnerPeriod} = output.exports;
const zero = {unique_visitors: 0, active_ms: 0, theory_ms: 0, practice_ms: 0};
const report = {schema_version: 1, status: "no_data", quality: "complete", as_of: "2026-11-17T09:00:00Z", lag_seconds: 0, timezone: "Europe/Moscow", metric_version: "activity-v1", course_id: null, from_time: "2026-11-16T21:00:00Z", to_time: "2026-11-17T21:00:00Z", totals: zero, daily: []};
const render = value => renderToStaticMarkup(React.createElement(LearnerReportSummary, {report: value}));

test("periods use Moscow calendar boundaries including today", () => {
  const now = Date.parse("2026-11-17T20:59:59Z");
  assert.deepEqual(learnerPeriod(1, now), {from_time: "2026-11-16T21:00:00.000Z", to_time: "2026-11-17T21:00:00.000Z"});
  assert.equal(Date.parse(learnerPeriod(7, now).to_time) - Date.parse(learnerPeriod(7, now).from_time), 7 * 86400000);
  assert.equal(Date.parse(learnerPeriod(30, now).to_time) - Date.parse(learnerPeriod(30, now).from_time), 30 * 86400000);
  assert.equal(learnerPeriod(1, now + 1000).from_time, "2026-11-17T21:00:00.000Z");
});
test("zero requires complete coverage and observed positive time remains visible", () => {
  assert.equal(learnerDuration(0, "unknown"), "—");
  assert.equal(learnerDuration(0, "partial"), "—");
  assert.equal(learnerDuration(0, "complete"), "0 мин");
  assert.equal(learnerDuration(1, "unknown"), "< 1 мин");
  const html = render({...report, status: "ok", quality: "partial", totals: {...zero, active_ms: 120000, theory_ms: 120000}});
  assert.match(html, /Наблюдаемая активность/); assert.match(html, /частичные/); assert.match(html, /2 мин/); assert.match(html, /—/);
});
test("no data, stale and unavailable have different labels", () => {
  assert.match(render(report), /активности нет/);
  assert.match(render({...report, status: "stale"}), /Данные устарели/);
  const unavailable = render({...report, status: "unavailable", quality: "unknown", totals: null, as_of: null});
  assert.match(unavailable, /временно недоступна/); assert.doesNotMatch(unavailable, /0 мин/);
});
test("mixed course and lesson rows retain context even across pages and archived IDs", () => {
  const rows = [
    {...zero, level: "course", course_id: "backend", lesson_id: null},
    {...zero, active_ms: 60000, theory_ms: 60000, level: "lesson", course_id: "backend", lesson_id: "old"},
    {...zero, level: "lesson", course_id: "archived", lesson_id: "gone"},
  ];
  const html = renderToStaticMarkup(React.createElement(LearnerDetailRows, {rows, quality: "unknown", course: null, catalog: [{id: "backend", title: "Backend", lessons: []}], onSelectCourse: () => {}}));
  assert.match(html, /<button type="button">Backend<\/button>/);
  assert.match(html, /Backend \/ Урок \(old\)/);
  assert.match(html, /Курс \(archived\) \/ Урок \(gone\)/);
  assert.match(html, /Теория: 1 мин/); assert.match(html, /Практика: —/);
});
