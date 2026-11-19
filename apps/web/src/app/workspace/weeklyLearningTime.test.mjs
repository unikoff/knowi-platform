import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("./weeklyLearningTime.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
const module = {exports: {}}; new Function("module", "exports", code)(module, module.exports);
const {weekDates, reportBounds, reportDays, cacheableDays, parseCache, firstUncachedDay} = module.exports;
const dates = weekDates("2026-11-19");
const metrics = {unique_visitors: 1, active_ms: 90000, theory_ms: 90000, practice_ms: 0};
const report = {status: "ok", quality: "unknown", totals: metrics, daily: [{date: dates[0], ...metrics}]};

test("Moscow dates use an inclusive week and an exclusive UTC boundary", () => {
  assert.deepEqual(dates, ["2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"]);
  assert.deepEqual(reportBounds(dates[0], dates[6]), {from_time: "2026-11-12T21:00:00.000Z", to_time: "2026-11-19T21:00:00.000Z"});
  assert.equal(weekDates("2027-01-01")[0], "2026-12-26");
});

test("partial observations stay distinct from zero and failures are not cached", () => {
  const days = reportDays(report, dates);
  assert.equal(days[0].minutes, 1.5);
  assert.equal(days[1].minutes, null);
  assert.equal(reportDays({...report, quality: "complete"}, dates)[1].minutes, 0);
  assert.equal(cacheableDays(report, days, dates[6]).length, 6);
  assert.equal(cacheableDays({...report, status: "stale"}, days, dates[6]).length, 0);
  assert.equal(cacheableDays({...report, status: "unavailable", totals: null}, days, dates[6]).length, 0);
});

test("warm cache requests only today; midnight and expiry require historical refresh", () => {
  const now = Date.parse("2026-11-19T10:00:00Z");
  const entries = dates.slice(0, 6).map((date, index) => [date, {minutes: index ? null : 1.5, savedAt: now, partial: true}]);
  const cached = parseCache(JSON.stringify(entries), dates.slice(0, 6), now);
  assert.equal(cached.size, 6); assert.equal(cached.get(dates[1]).minutes, null);
  assert.equal(firstUncachedDay(dates, cached), "2026-11-19");
  assert.equal(firstUncachedDay(weekDates("2026-11-20"), cached), "2026-11-19");
  assert.equal(parseCache(JSON.stringify(entries), dates, now + 24 * 60 * 60 * 1000).size, 0);
  assert.equal(firstUncachedDay(dates, new Map()), dates[0]);
  assert.equal(parseCache("broken", dates, now).size, 0);
});
