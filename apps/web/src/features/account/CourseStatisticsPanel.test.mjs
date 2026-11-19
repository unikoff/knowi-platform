import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import {createRequire} from "node:module";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const originalData = {tracksLoaded: true, completedLessons: 0, totalLessons: 0, overallProgress: 0, activeDays: null, currentStreak: null, weeklyActivity: null, weeklyLessonsCompleted: null, courses: []};
const actorRef = "a70fe569-29bd-4bd6-b4cd-d5f04edee31f";
function renderPanel(overrides = {}) {
  let hookEnabled;
  let chapterHook;
  let periodHook;
  let dashboard;
  const actualSnapshot = {courseId: "backend", totals: {activeMs: 123}};
  const cache = new Map();
  function load(name) {
    if (name.endsWith(".css")) return {};
    if (name === "./useCourseChapterAnalytics") return {useCourseChapterAnalytics: (enabled, actor, range) => {
      chapterHook = {enabled, actor, range};
      return {status: enabled ? "loading" : "pending", report: null};
    }};
    if (name === "./useLearnerHistory") assert.fail("The statistics panel must not load imported history");
    if (name === "./usePeriodCompletion") return {usePeriodCompletion(enabled, actor, range) {
      periodHook = {enabled, actor, range};
      return enabled ? {from: range.from, to: range.to, count: 0} : undefined;
    }};
    if (name === "./useCourseStatisticsAnalytics") return {useCourseStatisticsAnalytics(enabled) {
      hookEnabled = enabled;
      return {status: enabled ? "ready" : "pending", snapshot: enabled ? actualSnapshot : null};
    }};
    if (name === "./CourseStatisticsDashboard") return {CourseStatisticsDashboard(props) {
      dashboard = props;
      return React.createElement("div", {"data-demo": String(props.demo)});
    }};
    if (!name.startsWith("./")) return require(name);
    if (cache.has(name)) return cache.get(name);
    const source = fs.readFileSync(new URL(`${name}.tsx`, import.meta.url), "utf8");
    return compile(name, source);
  }
  function compile(name, source) {
    const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX}}).outputText;
    const output = {exports: {}};
    new Function("require", "module", "exports", code)(load, output, output.exports);
    cache.set(name, output.exports);
    return output.exports;
  }
  for (const name of ["course-statistics-model", "course-statistics-demo"]) compile(`./${name}`, fs.readFileSync(new URL(`./${name}.ts`, import.meta.url), "utf8"));
  const {CourseStatisticsPanel} = load("./CourseStatisticsPanel");
  const html = renderToStaticMarkup(React.createElement(CourseStatisticsPanel, {courseId: "backend", data: originalData, catalog: [], analyticsEnabled: true, hasActiveSubscription: false, role: "user", actorRef, ...overrides}));
  return {html, hookEnabled, chapterHook, periodHook, dashboard, actualSnapshot};
}

test("non-subscriber starts in isolated demo with a visible mode control", () => {
  const {html, hookEnabled, chapterHook, periodHook, dashboard} = renderPanel({personalMeasurementsEnabled: true});
  assert.equal(hookEnabled, false);
  assert.equal(chapterHook.enabled, false);
  assert.equal(periodHook.enabled, false);
  assert.ok(dashboard.periodCompletedLessons.count > 0);
  assert.equal(dashboard.periodCompletedLessons.from, dashboard.timeData.from);
  assert.equal(dashboard.periodCompletedLessons.to, dashboard.timeData.to);
  assert.equal(dashboard.demo, true);
  assert.equal(dashboard.chapterTime, undefined);
  assert.equal(dashboard.timeState, "ready");
  assert.ok(dashboard.timeData.totals.activeMs > 0);
  assert.notEqual(dashboard.data, originalData);
  assert.ok(dashboard.data.completedLessons > 0);
  assert.equal(originalData.completedLessons, 0);
  assert.match(html, /aria-label="Источник данных статистики"/);
  assert.match(html, /Демо<\/button>/);
});

test("subscriber starts with real progress but no time even if analytics rollout is enabled", () => {
  const {hookEnabled, periodHook, dashboard} = renderPanel({hasActiveSubscription: true});
  assert.equal(hookEnabled, false);
  assert.equal(dashboard.demo, false);
  assert.equal(dashboard.data, originalData);
  assert.equal(dashboard.timeState, "disabled");
  assert.equal(dashboard.timeData, null);
  assert.equal(dashboard.chapterTime, undefined);
  assert.equal(periodHook.enabled, true);
  assert.equal(periodHook.actor, actorRef);
  assert.deepEqual(dashboard.periodCompletedLessons, {from: periodHook.range.from, to: periodHook.range.to, count: 0});
});

test("real time requires a separate explicit opt-in in addition to rollout", () => {
  const enabled = renderPanel({hasActiveSubscription: true, personalMeasurementsEnabled: true});
  assert.equal(enabled.hookEnabled, true);
  assert.equal(enabled.dashboard.timeData, enabled.actualSnapshot);
  assert.equal(enabled.chapterHook.enabled, true);
  assert.equal(enabled.chapterHook.actor, actorRef);
  assert.equal(enabled.dashboard.chapterTime.status, "loading");
  const disabled = renderPanel({hasActiveSubscription: true, personalMeasurementsEnabled: true, analyticsEnabled: false});
  assert.equal(disabled.hookEnabled, false);
  assert.equal(disabled.dashboard.timeData, null);
  assert.equal(disabled.dashboard.timeState, "disabled");
});

test("staff get a role-specific state without reading personal Analytics", () => {
  for (const role of ["leader", "admin", undefined]) {
    for (const enabled of [false, true]) {
      const result = renderPanel({hasActiveSubscription: true, role, analyticsEnabled: enabled, personalMeasurementsEnabled: enabled});
      assert.equal(result.hookEnabled, false);
      assert.equal(result.dashboard.timeState, "role_blocked");
      assert.equal(result.dashboard.timeData, null);
      assert.equal(result.chapterHook.enabled, false);
      assert.equal(result.periodHook.enabled, false);
      assert.equal(result.dashboard.periodCompletedLessons, undefined);
      assert.equal(result.dashboard.chapterTime, undefined);
      assert.equal(result.dashboard.data, originalData);
    }
  }
});

test("the personal panel shows measured statistics without loading imported history", () => {
  const personal = {hasActiveSubscription:true, personalMeasurementsEnabled:true};
  const {html, hookEnabled, dashboard} = renderPanel(personal);
  assert.doesNotMatch(html, /Импортированная история/);
  assert.equal(hookEnabled, true);
  assert.equal(dashboard.timeState, "ready");
  assert.equal(dashboard.chapterTime.status, "loading");
  assert.equal("detailPage" in dashboard, false);
  assert.equal("lessonReportActorRef" in dashboard, false);
});
