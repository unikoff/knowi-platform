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
const panel = compile("./CourseAnalyticsPanel.tsx");
const { CourseActivityReport } = compile("./CourseActivityReport.tsx", {"./CourseAnalyticsPanel": panel});
const report = {
  schema_version: 1, status: "ok", quality: "complete", as_of: "2026-11-17T10:00:00Z", lag_seconds: 0,
  timezone: "Europe/Moscow", metric_version: "activity-v1", course_id: "backend",
  from_time: "2026-11-15T21:00:00Z", to_time: "2026-11-17T21:00:00Z",
  totals: { unique_visitors: 5, active_ms: 90000, theory_ms: 60000, practice_ms: 30000 },
  daily: [
    { date: "2026-11-16", unique_visitors: 3, active_ms: 60000, theory_ms: 60000, practice_ms: 0 },
    { date: "2026-11-17", unique_visitors: 4, active_ms: 30000, theory_ms: 0, practice_ms: 30000 },
  ], trusted_progress: { state: "known", completed_lessons: 2 },
};
const render = (view, courseId = "backend") => renderToStaticMarkup(React.createElement(CourseActivityReport, {courseId, view}));
const withReport = (patch = {}) => ({kind: "report", report: {...report, ...patch}});

test("renders course totals and daily distinct values without summing visitors", () => {
  const html = render(withReport());
  assert.match(html, /Посетители за период<\/small><strong>5<\/strong>/);
  assert.match(html, /1,5 мин/); assert.match(html, /30 с/);
  assert.match(html, /16\.11\.2026/); assert.match(html, /17\.11\.2026/);
  assert.match(html, /числа по дням нельзя складывать/);
  assert.match(html, /Завершено уроков<\/small><strong>2<\/strong>/);
  assert.match(html, /это не гарантия полноты/);
});

test("missing totals and metadata do not become zero; partial and unknown stay labelled", () => {
  const partial = render(withReport({totals: null, as_of: null, lag_seconds: null, quality: "partial", daily: [], trusted_progress: {state:"unavailable", completed_lessons:null}}));
  assert.match(partial, /Данные за период неполные/);
  assert.match(partial, /Срез событий: недоступен/); assert.match(partial, /Задержка по отчёту: неизвестна/);
  assert.equal((partial.match(/Данные недоступны/g) ?? []).length, 5);
  assert.match(partial, /Дневные данные за период недоступны/);
  assert.match(render(withReport({quality: "unknown"})), /Полнота данных неизвестна/);
});

test("no_data, stale, unavailable, error, and foreign course never show old counts", () => {
  const marker = {unique_visitors: 987654, active_ms: 123000, theory_ms: 60000, practice_ms: 63000};
  for (const status of ["no_data", "stale", "unavailable"]) {
    const html = render(withReport({status, totals:marker}));
    assert.doesNotMatch(html, /987\s?654|987654|2,05 мин/);
    if (status === "no_data") assert.match(html, /Статистика пока недоступна/);
    if (status === "stale") assert.match(html, /Статистика ещё обновляется/);
    if (status === "unavailable") assert.match(html, /Статистика пока недоступна/);
  }
  assert.doesNotMatch(render(withReport({totals:marker}), "backend_copy"), /987\s?654|987654/);
  assert.match(render({kind:"loading"}), /Загружаем статистику/);
  assert.match(render({kind:"error", retry:() => {}}), /Не удалось загрузить статистику.*Повторить/);
});

test("measured zero and tiny duration are not treated as unavailable", () => {
  const html = render(withReport({totals:{unique_visitors:0, active_ms:1, theory_ms:1, practice_ms:0}, daily:[]}));
  assert.match(html, /Посетители за период<\/small><strong>0<\/strong>/);
  assert.match(html, /0,001 с/); assert.match(html, /0 мин/);
});

test("incomplete coverage does not present observed zero as confirmed inactivity", () => {
  for (const quality of ["partial", "unknown"]) {
    const html = render(withReport({
      quality,
      totals: {unique_visitors:0, active_ms:60000, theory_ms:0, practice_ms:60000},
      daily: [{date:"2026-11-16", unique_visitors:0, active_ms:60000, theory_ms:0, practice_ms:60000}],
      trusted_progress: {state:"known", completed_lessons:0},
    }));
    assert.match(html, /нулевые наблюдения не подтверждают отсутствие активности/);
    assert.match(html, /Посетители за период<\/small><strong>—<\/strong><span>Ноль не подтверждён/);
    assert.match(html, /Теория<\/small><strong>—<\/strong><span>Ноль не подтверждён/);
    assert.match(html, /Активное время<\/small><strong>1 мин<\/strong>/);
    assert.equal((html.match(/aria-label="Ноль наблюдений не подтверждает отсутствие активности"/g) ?? []).length, 2);
    assert.match(html, /Завершено уроков<\/small><strong>0<\/strong>/);
  }
});


test("no_data requires complete coverage and zero report metrics", () => {
  const zero = {unique_visitors:0, active_ms:0, theory_ms:0, practice_ms:0};
  assert.match(render(withReport({status:"no_data", quality:"complete", totals:zero, daily:[]})), /За выбранный период данных нет/);
  assert.match(render(withReport({status:"no_data", quality:"unknown", totals:zero, daily:[]})), /Статистика пока недоступна/);
  assert.match(render(withReport({status:"no_data", quality:"complete", totals:null, daily:[]})), /Статистика пока недоступна/);
  assert.match(render(withReport({status:"no_data", quality:"complete", totals:zero})), /Статистика пока недоступна/);
});

test("access denial is explicit and does not offer a retry", () => {
  const html = render({kind:"error", reason:"access", retry:() => {}});
  assert.match(html, /Нет доступа к статистике/);
  assert.doesNotMatch(html, /Повторить/);
});
