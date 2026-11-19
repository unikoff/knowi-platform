import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import ts from "typescript";
const source = fs.readFileSync(new URL("./course-statistics-analytics.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
const module = {exports: {}}; new Function("module", "exports", code)(module, module.exports);
const {canShowCourseAnalytics, canShowPersonalMeasurements, courseReportFilters, courseSnapshot} = module.exports;
const range = {courseId: "backend", from: "2026-11-16", to: "2026-11-17"};
const zero = {unique_visitors: 0, active_ms: 0, theory_ms: 0, practice_ms: 0};
const measured = {unique_visitors: 1, active_ms: 60000, theory_ms: 60000, practice_ms: 0};
const report = {quality: "partial", as_of: "2026-11-17T10:00:00Z", totals: measured, daily: [{date: "2026-11-17", ...measured}]};
test("only explicit user role and enabled flag access personal Analytics", () => {
  assert.equal(canShowCourseAnalytics("true", "user"), true);
  for (const role of ["leader", "admin", undefined]) assert.equal(canShowCourseAnalytics("true", role), false);
  for (const flag of [undefined, "false", "1", true]) assert.equal(canShowCourseAnalytics(flag, "user"), false);
  assert.equal(canShowPersonalMeasurements("true", "true", "user"), true);
  for (const [reports, personal, role] of [["true", undefined, "user"], [undefined, "true", "user"], ["true", "true", "admin"], ["true", "true", "leader"], ["true", "true", undefined]]) assert.equal(canShowPersonalMeasurements(reports, personal, role), false);
});
test("bounded requests use inclusive Moscow days with UTC exclusive end", () => {
  assert.deepEqual(courseReportFilters(range), {course_id: "backend", from_time: "2026-11-15T21:00:00.000Z", to_time: "2026-11-17T21:00:00.000Z"});
  assert.deepEqual(courseReportFilters(range, 20), {course_id: "backend", from_time: "2026-11-15T21:00:00.000Z", to_time: "2026-11-17T21:00:00.000Z", offset: 20, limit: 20});
});
test("partial report preserves observed time and fills days without records", () => {
  const snapshot = courseSnapshot(report, range, [range.from, range.to]);
  assert.deepEqual(snapshot.days[0], {date: range.from, activeMs: 0, theoryMs: 0, practiceMs: 0});
  assert.equal(snapshot.days[1].activeMs, 60000);
  assert.equal(snapshot.days[1].practiceMs, 0);
  assert.equal(snapshot.quality, "partial");
  assert.equal(snapshot.totals.activeMs, 60000);
  assert.deepEqual(snapshot.lessons, []);
});
test("only a usable report with totals establishes zero recorded time for omitted days", () => {
  const dates = [range.from, range.to];
  for (const [status, quality] of [["no_data", "complete"], ["ok", "unknown"]]) {
    const snapshot = courseSnapshot({...report, status, quality, totals: zero, daily: []}, range, dates);
    assert.equal(snapshot.days[0].activeMs, 0);
    assert.equal(snapshot.quality, quality);
  }
  assert.equal(courseSnapshot({...report, status: "unavailable", quality: "unknown", totals: null, daily: []}, range, dates).days[0].activeMs, null);
  assert.equal(courseSnapshot({...report, status: "stale", quality: "complete", totals: null, daily: []}, range, dates).days[0].activeMs, null);
});
