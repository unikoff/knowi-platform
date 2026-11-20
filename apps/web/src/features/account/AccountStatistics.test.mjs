import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import fs from "node:fs";
import {createRequire} from "node:module";
import ts from "typescript";

const require = createRequire(import.meta.url);
const source = fs.readFileSync(new URL("./AccountStatistics.tsx", import.meta.url), "utf8");
const code = ts.transpileModule(source, {compilerOptions: {jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
let state;
const output = {exports: {}};
new Function("require", "module", "exports", code)(name => name === "./useAccountStatistics" ? {useAccountStatistics: () => state} : require(name), output, output.exports);
const data = {tracksLoaded: true, completedLessons: 0, totalLessons: 0, overallProgress: 0, activeDays: null, currentStreak: null, weeklyLessonsCompleted: null, courses: []};
const render = () => renderToStaticMarkup(React.createElement(output.exports.AccountStatistics, {data, courseId: "backend", userId: 1}));

test("unknown and partial measurements do not become false zeroes", () => {
  const day = {date: "2026-11-19", visited: null, completed_lessons: 0, active_seconds: 0, theory_seconds: null, practice_seconds: null};
  const report = {period: {days: 7}, coverage: {visits: "partial", time: "partial"}, summary: {active_days_total: null, current_streak: null, active_days_period: null, completed_lessons_period: 0, active_seconds: 120}, days: [day]};
  state = {status: "ready", report, retry() {}};
  let html = render();
  assert.match(html, /нет данных о посещении/);
  assert.match(html, /Итог за период недоступен/);
  assert.match(html, /Имеющиеся измерения за неполный период: 2 мин/);
  assert.match(html, /19 ноября: Нет данных/);
  assert.doesNotMatch(html, /За этот период посещений пока нет|0 мин/);
  state = {status: "ready", report: {...report, coverage: {...report.coverage, time: "measured"}, summary: {...report.summary, active_seconds: 0}, days: [{...day, visited: false}]}, retry() {}};
  html = render();
  assert.match(html, /Измерено за период: 0 мин/);
  assert.match(html, /без посещения/);
  state = {status: "error", report: null, retry() {}};
  html = render();
  assert.match(html, /Не удалось загрузить статистику/);
  assert.doesNotMatch(html, /Посещения платформы|0 мин/);
});
