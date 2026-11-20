import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement, useState } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const source = readFileSync(new URL("./CourseOverviewDashboard.tsx", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
});
const require = createRequire(import.meta.url);
function loadDashboard(initialMetric, initialMode, studyTimeView, selectedDay, onStudyTimeContext) {
  const module = { exports: {} };
  let firstEmptyState = true;
  new Function("require", "exports", outputText)((id) => {
    if (id.endsWith(".css")) return {};
    if (id === "./course-study-time") return {
      useCourseStudyTime: context => {
        onStudyTimeContext?.(context);
        return { view: studyTimeView, retry() {} };
      },
    };
    if (id === "react") return {
      ...require(id),
      useState(initial) {
        if (initial === undefined && firstEmptyState) {
          firstEmptyState = false;
          return useState(selectedDay);
        }
        return useState(initial === "visits" ? initialMetric : initial === "all" ? initialMode : initial);
      },
    };
    return require(id);
  }, module.exports);
  return module.exports.CourseOverviewDashboard;
}

const daily = Array.from({ length: 30 }, (_, index) => ({
  date: new Date(Date.UTC(2026, 9, 21 + index)).toISOString().slice(0, 10),
  with_subscription: 0,
  without_subscription: 0,
}));

function render(trends, { metric = "visits", mode = "all", studyTimeView = { status: "unavailable" },
  selectedDay, studyTimeContext, onStudyTimeContext } = {}) {
  const CourseOverviewDashboard = loadDashboard(metric, mode, studyTimeView, selectedDay, onStudyTimeContext);
  return renderToStaticMarkup(createElement(CourseOverviewDashboard, { trends, studyTimeContext }));
}

function axisLabels(html) {
  const axis = html.match(/class="course-trend-chart__axis"[^>]*>(.*?)<\/div>/)[1];
  return [...axis.matchAll(/<span[^>]*>([^<]+)<\/span>/g)].map(([, value]) => value);
}

function axisTicks(html) {
  return axisLabels(html).map(Number);
}

function barHeights(html) {
  return [...html.matchAll(/class="course-stats__bar"[^>]*style="height:([\d.]+)%"/g)].map(([, value]) => Number(value));
}

test("one chart shows only the selected metric with both subscription cohorts", () => {
  const visits = daily.map((day, index) => index === 0 ? { ...day, with_subscription: 3, without_subscription: 8 } : day);
  const completions = daily.map((day, index) => index === 29 ? { ...day, without_subscription: 5 } : day);
  const trends = { visits: { status: "available", daily: visits }, completions: { status: "available", daily: completions } };
  const html = render(trends);
  assert.equal((html.match(/<figure /g) ?? []).length, 1);
  assert.match(html, /Уникальные посетители курса по дням/);
  assert.doesNotMatch(html, /Завершённые уроки по дням/);
  assert.match(html, /aria-pressed="true"[^>]*>Ученики<\/button>/);
  assert.match(html, /aria-label="среда, 21 октября, 11, С подпиской: 3, Без подписки: 8"/);
  assert.match(html, /aria-pressed="true">Все<\/button>/);
  assert.doesNotMatch(html, /Значения по дням|Расписание|Общие подписки учеников курса|текущему статусу подписки/);

  const learning = render(trends, { metric: "completions" });
  assert.equal((learning.match(/<figure /g) ?? []).length, 1);
  assert.match(learning, /aria-pressed="true"[^>]*>Обучение<\/button>/);
  assert.match(learning, /Завершённые уроки по дням/);
  assert.doesNotMatch(learning, /Уникальные посетители курса по дням/);
  assert.match(learning, /aria-label="четверг, 19 ноября, 5, С подпиской: 0, Без подписки: 5"/);
});

test("subscription-only modes keep only the selected population in the chart", () => {
  const day = [{ date: "2026-10-21", with_subscription: 3, without_subscription: 8 }];
  const trends = { visits: { status: "available", daily: day } };

  const withSubscription = render(trends, { mode: "with_subscription" });
  assert.match(withSubscription, /aria-pressed="true">С подпиской<\/button>/);
  assert.match(withSubscription, /aria-label="среда, 21 октября, 3"/);
  assert.match(withSubscription, /data-bar-segment="with_subscription"/);
  assert.doesNotMatch(withSubscription, /data-bar-segment="without_subscription"/);

  const withoutSubscription = render(trends, { mode: "without_subscription" });
  assert.match(withoutSubscription, /aria-pressed="true">Без подписки<\/button>/);
  assert.match(withoutSubscription, /aria-label="среда, 21 октября, 8"/);
  assert.match(withoutSubscription, /data-bar-segment="without_subscription"/);
  assert.doesNotMatch(withoutSubscription, /data-bar-segment="with_subscription"/);
});

test("each metric and subscription mode uses its own exact peak as the top of the chart", () => {
  const trends = {
    visits: { status: "available", daily: [
      { ...daily[0], with_subscription: 3, without_subscription: 8 },
      { ...daily[1], with_subscription: 7, without_subscription: 4 },
    ] },
    completions: { status: "available", daily: [{ ...daily[0], with_subscription: 13 }] },
  };

  for (const [options, peak, heights] of [
    [{}, 11, [100, 100]],
    [{ mode: "with_subscription" }, 7, [300 / 7, 100]],
    [{ mode: "without_subscription" }, 8, [100, 50]],
    [{ metric: "completions" }, 13, [100]],
  ]) {
    const html = render(trends, options);
    const ticks = axisTicks(html);
    assert.equal(ticks[0], 0);
    assert.equal(ticks[ticks.length - 1], peak);
    assert.deepEqual(barHeights(html), heights);
  }
});

test("unavailable metric keeps the metric switch usable and a confirmed zero remains visible", () => {
  const trends = { visits: { status: "unavailable", daily }, completions: { status: "available", daily } };
  const unavailable = render(trends);
  assert.doesNotMatch(unavailable, /<figure /);
  assert.match(unavailable, /role="status">Данные за этот период пока недоступны/);
  assert.match(unavailable, /aria-pressed="false"[^>]*>Обучение<\/button>/);

  const html = render(trends, { metric: "completions" });
  assert.equal((html.match(/<figure /g) ?? []).length, 1);
  assert.equal((html.match(/Завершений не было/g) ?? []).length, 1);
  assert.match(html, /aria-label="среда, 21 октября, 0, С подпиской: 0, Без подписки: 0"/);
  assert.match(html, /course-stats__day-marker">0<\/span>/);
  assert.deepEqual(axisTicks(html), [0]);
  assert.deepEqual(barHeights(html), []);
  assert.doesNotMatch(html, /Данные за этот период пока недоступны/);
});

test("unknown visit coverage distinguishes missing observations and partial totals", () => {
  const observed = [
    { date: "2026-11-17", with_subscription: null, without_subscription: null },
    { date: "2026-11-18", with_subscription: 1, without_subscription: null },
    { date: "2026-11-19", with_subscription: 1, without_subscription: 1 },
  ];
  const html = render({ visits: { status: "available", quality: "unknown", daily: observed }, completions: { status: "unavailable", daily: [] } });
  assert.doesNotMatch(html, /полнота сбора неизвестна/);
  assert.match(html, /aria-label="вторник, 17 ноября, Нет данных, С подпиской: нет данных, Без подписки: нет данных"/);
  assert.match(html, /course-stats__day-marker">—<\/span>/);
  assert.doesNotMatch(html, /course-stats__day-marker">0<\/span>/);
  assert.match(html, /aria-label="среда, 18 ноября, Зафиксировано 1, С подпиской: 1, Без подписки: нет данных"/);
  assert.match(html, /aria-label="четверг, 19 ноября, 2, С подпиской: 1, Без подписки: 1"/);
  assert.deepEqual(axisTicks(html), [0, 1, 2]);
  assert.deepEqual(barHeights(html), [50, 100]);
  assert.doesNotMatch(html, /Посещений не было/);

  const missingOnly = render({ visits: { status: "available", quality: "unknown", daily: observed.slice(0, 2) } },
    { mode: "without_subscription" });
  assert.deepEqual(axisTicks(missingOnly), []);
  assert.deepEqual(barHeights(missingOnly), []);
  assert.match(missingOnly, /course-stats__day-marker">—<\/span>/);
  assert.doesNotMatch(missingOnly, /NaN|Infinity|course-stats__day-marker">0<\/span>/);
});

const studyTimeContext = {
  courseId: "backend",
  actorRef: "01234567-89ab-4cde-8fab-0123456789ab",
  actorRole: "leader",
  authRevision: 7,
};
const measuredDays = daily.map(day => ({ date: day.date, with_subscription: null, without_subscription: null }));
measuredDays[27] = { ...measuredDays[27], with_subscription: { active_ms: 3_690_000, learners: 1 }, without_subscription: { active_ms: 390_000, learners: 3 } };
measuredDays[28] = { ...measuredDays[28], with_subscription: { active_ms: 2_040_000, learners: 2 } };
const timeReport = { status: "available", quality: "unknown", daily: measuredDays };

test("study time stacks measured durations and describes each weighted daily average", () => {
  const base = { metric: "studyTime", studyTimeView: timeReport, selectedDay: "2026-11-17" };
  const all = render(undefined, base);
  assert.match(all, /aria-pressed="true"[^>]*>Время учеников<\/button>/);
  assert.match(all, /aria-label="вторник, 17 ноября, 1 ч 8 мин, С подпиской: 1 ч 1 мин 30 с, Без подписки: 6 мин 30 с, Среднее время на ученика: 17 мин"/);
  assert.match(all, /aria-label="среда, 18 ноября, Зафиксировано 34 мин, С подпиской: 34 мин, Без подписки: нет данных, Среднее время на ученика: 17 мин"/);
  assert.match(all, /course-trend-chart__average"><span>Среднее время на ученика<\/span><strong>17 мин<\/strong>/);
  assert.equal(axisLabels(all).at(-1), "1 ч 8 мин");
  assert.deepEqual(barHeights(all), [100, 50]);

  const withSubscription = render(undefined, { ...base, mode: "with_subscription" });
  assert.equal(axisLabels(withSubscription).at(-1), "1 ч 1 мин 30 с");
  assert.match(withSubscription, /17 ноября, 1 ч 1 мин 30 с, Среднее время на ученика: 1 ч 1 мин 30 с/);

  const withoutSubscription = render(undefined, { ...base, mode: "without_subscription", selectedDay: "2026-11-18" });
  assert.equal(axisLabels(withoutSubscription).at(-1), "6 мин 30 с");
  assert.deepEqual(barHeights(withoutSubscription), [100]);
  assert.match(withoutSubscription, /17 ноября, 6 мин 30 с, Среднее время на ученика: 2 мин 10 с/);
  assert.match(withoutSubscription, /18 ноября, Нет данных, Среднее время на ученика: Нет данных/);
  assert.match(withoutSubscription, /course-trend-chart__average"><span>Среднее время на ученика<\/span><strong>Нет данных<\/strong>/);
});

test("study time receives request context only when selected and shows unavailability explicitly", () => {
  const contexts = [];
  const props = { studyTimeContext, onStudyTimeContext: context => contexts.push(context) };
  const unavailable = render(undefined, { ...props, metric: "studyTime" });
  render(undefined, props);
  assert.deepEqual(contexts, [studyTimeContext, undefined]);
  assert.match(unavailable, /Время учеников за этот период пока недоступно/);
  assert.doesNotMatch(unavailable, /<figure /);
  const loading = render(undefined, { ...props, metric: "studyTime", studyTimeView: { status: "loading" } });
  assert.match(loading, /Загружаем время учеников/);
});

test("ordinary course time includes migrated hours and preserves live-only days", () => {
  const migrated = daily.map((day, index) => index === 27 ? {...day, with_subscription: 7_200_000} : day);
  const trends = {estimated_completion_time: {status: "available", daily: migrated}};
  const base = {metric: "studyTime", studyTimeView: timeReport};
  const html = render(trends, base);
  assert.doesNotMatch(html, /Условное время/);
  assert.match(html, /17 ноября, 3 ч 8 мин, С подпиской: 3 ч 1 мин 30 с, Без подписки: 6 мин 30 с, Среднее время на ученика: Нет данных/);
  assert.match(html, /18 ноября, Зафиксировано 34 мин/);
  assert.match(render(trends, {...base, studyTimeView: {status: "unavailable"}}), /17 ноября, Зафиксировано 2 ч/);
  assert.doesNotMatch(render(trends, {...base, studyTimeView: {status: "denied"}}), /<figure /);
});

const apiSource = readFileSync(new URL("./course-study-time.ts", import.meta.url), "utf8")
  .replace("import.meta.env.BASE_URL", JSON.stringify("/backend/"));
const apiModule = { exports: {} };
new Function("require", "exports", ts.transpileModule(apiSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText)(id => id === "./scopedApi" ? { coursePath: id => `/admin/courses/${encodeURIComponent(id)}` } : require(id), apiModule.exports);
const { fetchCourseStudyTime } = apiModule.exports;
const jsonResponse = (payload, status = 200) => new Response(JSON.stringify(payload), { status, headers: { "content-type": "application/json" } });
const fetchTime = signal => fetchCourseStudyTime(studyTimeContext.courseId, studyTimeContext.actorRef, studyTimeContext.authRevision, signal);

test("study time reads the exact course and actor without caching and rejects missing or invalid facts", async t => {
  const fetchMock = t.mock.method(globalThis, "fetch", async () => jsonResponse(timeReport));
  const controller = new AbortController();
  assert.deepEqual(await fetchTime(controller.signal), timeReport);
  const [[url, options]] = fetchMock.mock.calls.map(call => call.arguments);
  assert.equal(url, "/backend/api/admin/courses/backend/study-time");
  assert.equal(options.credentials, "include");
  assert.equal(options.cache, "no-store");
  assert.equal(options.redirect, "error");
  assert.equal(options.headers["X-Actor-Ref"], studyTimeContext.actorRef);
  assert.equal(options.headers["X-Auth-Revision"], String(studyTimeContext.authRevision));
  assert.equal(options.signal, controller.signal);

  fetchMock.mock.mockImplementationOnce(async () => jsonResponse({}, 404));
  assert.deepEqual(await fetchTime(controller.signal), { status: "unavailable" });
  fetchMock.mock.mockImplementationOnce(async () => jsonResponse({}, 403));
  assert.deepEqual(await fetchTime(controller.signal), { status: "denied" });

  const invalid = [...measuredDays];
  invalid[0] = { ...invalid[0], with_subscription: { active_ms: 60000, learners: 0 } };
  fetchMock.mock.mockImplementationOnce(async () => jsonResponse({ ...timeReport, daily: invalid }));
  assert.deepEqual(await fetchTime(controller.signal), { status: "error" });

  fetchMock.mock.mockImplementationOnce(async () => ({ ...jsonResponse(timeReport), ok: true, status: 200,
    headers: new Headers({ "content-type": "application/json" }), json: async () => { controller.abort(); return timeReport; } }));
  await assert.rejects(fetchTime(controller.signal), { name: "AbortError" });
});
