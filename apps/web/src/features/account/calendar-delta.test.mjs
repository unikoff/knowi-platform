import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

function load(file, imports = {}) {
  const source = fs.readFileSync(new URL(file, import.meta.url), "utf8");
  const {outputText} = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}});
  const module = {exports: {}};
  new Function("require", "module", "exports", outputText)(name => imports[name], module, module.exports);
  return module.exports;
}

const adapter = load("./course-statistics-analytics.ts");
const {mergeCalendarDelta} = load("./calendar-delta.ts", {"./course-statistics-analytics": adapter});
const dates = ["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"];
const range = {courseId: "backend", from: dates[0], to: dates[3]};
const closed = dates.slice(0, 2);
const generation = "a".repeat(64);
const revisions = closed.map((date, index) => ({date, token: String(index + 1).repeat(64)}));
const saved = [{date: closed[0], revision: revisions[0].token, uniqueVisitors: 1, activeMs: 60000, theoryMs: 60000, practiceMs: 0}];
const delta = {
  history_generation: generation,
  revisions,
  reused_dates: [closed[0]],
  daily: [{date: dates[3], unique_visitors: 1, active_ms: 120000, theory_ms: 0, practice_ms: 120000}],
  as_of: "2026-11-19T10:00:00Z",
};

test("cached metrics and fetched days produce the same single-learner totals and only closed days are written", () => {
  const result = mergeCalendarDelta(delta, range, dates, closed, saved, generation);
  assert.deepEqual(result.report.totals, {unique_visitors: 1, active_ms: 180000, theory_ms: 60000, practice_ms: 120000});
  assert.deepEqual(result.report.daily.map(day => day.date), [dates[0], dates[3]]);
  assert.deepEqual(result.replacedDates, [dates[1]]);
  assert.deepEqual(result.rows, [{date: dates[1], revision: revisions[1].token, activeMs: 0, theoryMs: 0, practiceMs: 0, uniqueVisitors: 0}]);
});

test("a day cannot be reused after a restore, with a different revision, or without saved values", () => {
  assert.equal(mergeCalendarDelta(delta, range, dates, closed, saved, "b".repeat(64)), null);
  assert.equal(mergeCalendarDelta(delta, range, dates, closed, [{...saved[0], revision: "c".repeat(64)}], generation), null);
  assert.equal(mergeCalendarDelta(delta, range, dates, closed, [], generation), null);
  assert.equal(mergeCalendarDelta({...delta, reused_dates: [dates[2]]}, range, dates, closed, saved, generation), null);
});
