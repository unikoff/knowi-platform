import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {createServer} from "vite";

const vite = await createServer({configFile: false, optimizeDeps: {noDiscovery: true, include: []}, server: {middlewareMode: true}});
const {ActivityReport} = await vite.ssrLoadModule("/src/features/admin/analytics-platform/ActivityReport.tsx");
const {ActivityReportFilters} = await vite.ssrLoadModule("/src/features/admin/analytics-platform/ActivityReportFilters.tsx");
test.after(async () => { await vite.close(); });
const metrics = {unique_visitors: 3, active_ms: 120000, theory_ms: 60000, practice_ms: 60000};
const base = {schema_version: 1, status: "ok", quality: "complete", as_of: "2026-11-17T09:00:00Z", lag_seconds: 60,
  timezone: "Europe/Moscow", metric_version: "activity-v1", course_id: null, from_time: "2026-11-15T21:00:00Z", to_time: "2026-11-17T21:00:00Z", totals: metrics,
  daily: [{date: "2026-11-16", ...metrics}, {date: "2026-11-17", ...metrics}]};
const render = report => renderToStaticMarkup(React.createElement(ActivityReport, {report, scopeLabel: "Все курсы"}));

test("uses period distinct directly, never sums days, and formats duration", () => {
  const html = render(base);
  assert.match(html, /Посетители за период<\/small><strong>3<\/strong>/);
  assert.doesNotMatch(html, /Посетители за период<\/small><strong>6<\/strong>/);
  assert.match(html, /Активное время<\/small><strong>2 мин<\/strong>/);
  assert.match(html, /16\.11\.2026, 00:00/);
  assert.match(html, /Задержка обработки: 60 с/);
});
test("unknown and partial coverage are explicit; null totals are unavailable", () => {
  const unknown = render({...base, quality: "unknown", totals: null, lag_seconds: null, as_of: null});
  assert.match(unknown, /Полнота данных неизвестна/);
  assert.match(unknown, /Суммарные показатели недоступны/);
  assert.match(unknown, /Задержка обработки: неизвестна/);
  assert.match(unknown, /Срез на: неизвестно/);
  assert.match(render({...base, quality: "partial"}), /Данные за период неполные/);
});
test("no_data, stale and unavailable suppress report counts even if supplied", () => {
  for (const status of ["no_data", "stale", "unavailable"]) {
    const html = render({...base, status, quality: "unknown"});
    assert.doesNotMatch(html, /Посетители за период|Активность по дням|2 мин/);
    if (status === "no_data") assert.match(html, /Полнота неизвестна/);
  }
});
test("zero is shown when provided; tiny duration is not rounded to zero", () => {
  const html = render({...base, totals: {...metrics, unique_visitors: 0, active_ms: 1}});
  assert.match(html, /Посетители за период<\/small><strong>0<\/strong>/);
  assert.match(html, /&lt; 0,01 мин/);
});
test("trusted progress is distinct and unavailable without a known count", () => {
  assert.match(render({...base, trusted_progress: {state: "known", completed_lessons: 5}}), /Завершено уроков: 5/);
  assert.match(render({...base, trusted_progress: {state: "unavailable", completed_lessons: null}}), /Завершено уроков: данные недоступны/);
});
test("filters allow at most 31 Moscow calendar days and only available courses", () => {
  const props = {courses: [{id: "backend", title: "Python"}], onChange() {}, onApply() {}};
  const html = values => renderToStaticMarkup(React.createElement(ActivityReportFilters, {...props, values}));
  assert.doesNotMatch(html({courseId: "backend", fromDate: "2026-11-01", toDateExclusive: "2026-12-02"}), /type="submit" disabled/);
  for (const values of [
    {courseId: "backend", fromDate: "2026-11-01", toDateExclusive: "2026-12-03"},
    {courseId: "backend", fromDate: "2026-11-01", toDateExclusive: "2026-11-01"},
    {courseId: "other", fromDate: "2026-11-01", toDateExclusive: "2026-11-02"},
    {courseId: "", fromDate: "2026-02-30", toDateExclusive: "2026-03-02"},
  ]) assert.match(html(values), /type="submit" disabled/);
});

test("incomplete or unknown coverage hides zero observations without hiding nonzero values", () => {
  for (const quality of ["unknown", "partial"]) {
    const html = render({...base, quality, totals: {...metrics, unique_visitors: 0, active_ms: 0, theory_ms: 60000, practice_ms: 0}, daily: [{date: "2026-11-16", unique_visitors: 0, active_ms: 0, theory_ms: 60000, practice_ms: 0}]});
    assert.match(html, /Посетители за период<\/small><strong>—<\/strong>/);
    assert.match(html, /Активное время<\/small><strong>—<\/strong>/);
    assert.match(html, /Теория<\/small><strong>1 мин<\/strong>/);
    assert.match(html, /Посетители: <strong>—<\/strong>/);
    assert.match(html, /Время: <strong>—<\/strong>/);
    assert.match(html, /нулевые показатели не подтверждают отсутствие активности/);
  }
});
