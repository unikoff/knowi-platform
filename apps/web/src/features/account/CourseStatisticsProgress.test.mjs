import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import {createRequire} from "node:module";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const cache = new Map();
function load(name, react = React) {
  if (name === "react") return react;
  if (name.endsWith(".css")) return {};
  if (name === "./CourseStatisticsLessonDetails") assert.fail("Lesson rows must not load the detail view");
  if (!name.startsWith("./")) return require(name);
  if (cache.has(name)) return cache.get(name);
  const extension = name === "./CourseStatisticsProgress" ? ".tsx" : ".ts";
  const source = fs.readFileSync(new URL(`${name}${extension}`, import.meta.url), "utf8");
  const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX}}).outputText;
  const output = {exports: {}};
  new Function("require", "module", "exports", code)(moduleName => load(moduleName, react), output, output.exports);
  if (name !== "./CourseStatisticsProgress") cache.set(name, output.exports);
  return output.exports;
}
const {CourseStatisticsProgress} = load("./CourseStatisticsProgress");
const chapter = {id: "chapter", title: "Глава из прогресса", completed: 1, total: 2, blocks: []};
const lesson = {id: "lesson", trackId: "chapter", chapterTitle: chapter.title, blockId: "block", blockTitle: "Блок", title: "Занятие", completed: true, completionKnown: true};
const data = {tracksLoaded: true, completedLessons: 1, totalLessons: 2, overallProgress: 50, activeDays: null, currentStreak: null, weeklyActivity: null, weeklyLessonsCompleted: null, courses: [chapter]};
const snapshot = {courseId: "course", from: "2026-11-01", to: "2026-11-07", quality: "complete", asOf: null, totals: {activeMs: 0, theoryMs: 0, practiceMs: 0}, days: [], lessons: []};
const render = overrides => renderToStaticMarkup(React.createElement(CourseStatisticsProgress, {data, lessons: [lesson], snapshot, metric: "activeMs", ...overrides}));

test("chapter progress uses Core completion independently of measured lesson time", () => {
  const html = render({snapshot: {...snapshot, lessons: [{lessonId: "lesson", activeMs: 0, theoryMs: 0, practiceMs: 0}]}});
  assert.match(html, /1 \/ 2/);
  assert.match(html, />Пройдено<\/small>/);
  assert.match(html, /<dt>Всего<\/dt><dd>0 мин<\/dd>/);
  assert.match(html, /aria-expanded="false"/);
});

test("catalog title changes do not duplicate a chapter with the same stable ID", () => {
  const html = render({lessons: [lesson, {...lesson, id: "catalog-only", chapterTitle: "Новое название из каталога", completionKnown: false}]});
  assert.equal((html.match(/class="course-progress__chapter"/g) ?? []).length, 1);
  assert.match(html, /Глава из прогресса/);
  assert.match(html, /Статус недоступен/);
});

test("missing completion and time stay unavailable instead of becoming zero progress", () => {
  const html = render({data: {...data, tracksLoaded: false}, lessons: [{...lesson, completionKnown: false}]});
  assert.match(html, /Нет данных/);
  assert.match(html, /Статус недоступен/);
  assert.doesNotMatch(html, /<dd>0 мин<\/dd>/);
  assert.doesNotMatch(html, /width:0%/);
});

test("completion date appears only for a completed lesson with a valid date", () => {
  const html = render({lessons: [
    {...lesson, completedOn: "2026-11-13"},
    {...lesson, id: "unfinished", completed: false, completedOn: "2026-11-14"},
    {...lesson, id: "invalid", completedOn: "2026-02-30"},
    {...lesson, id: "unknown", completionKnown: false, completedOn: "2026-11-15"},
  ]});
  assert.match(html, /Пройдено <span aria-hidden="true">·<\/span> <time dateTime="2026-11-13">13\.11\.2026<\/time>/);
  assert.equal((html.match(/<time /g) ?? []).length, 1);
});

const blockLessons = [lesson, {...lesson, id: "lesson-2", title: "Второе занятие", completed: false}];
const blockData = {...data, courses: [{...chapter, blocks: [{id: "block", title: "Блок", completed: 1, total: 2, lessons: blockLessons}]}]};
const blockHeader = html => html.match(/<button[^>]*class="course-progress__block-button"[\s\S]*?<\/button>/)?.[0] ?? "";
const lessonRows = html => html.match(/<li class="course-progress__lesson"[\s\S]*?<\/li>/g) ?? [];

test("chapter, block and lesson display combined time without granting it to unfinished lessons", () => {
  const migrated = {lessonId: "lesson", activeMs: 7_200_000, theoryMs: 3_600_000, practiceMs: 3_600_000};
  const html = render({data: blockData, lessons: blockLessons, completionTime: [migrated],
    snapshot: {...snapshot, lessons: [{...migrated, activeMs: 7_320_000, theoryMs: 3_720_000}]},
    chapterTime: {status: "ready", report: {status: "ok", chapters: [{chapter_id: "chapter", active_ms: 120_000}]}},
  });
  const chapterHeader = html.match(/<button[^>]*class="course-progress__chapter-button"[\s\S]*?<\/button>/)?.[0] ?? "";
  assert.match(chapterHeader, /<strong>2 ч 2 мин<\/strong>/);
  assert.match(blockHeader(html), /<strong>2 ч 2 мин<\/strong>/);
  assert.match(lessonRows(html)[0], /<dt>Всего<\/dt><dd>2 ч 2 мин<\/dd>/);
  assert.doesNotMatch(lessonRows(html)[1], /2 ч 2 мин/);
});

test("overview blocks count their own lessons without changing chapter progress", () => {
  const overview = {...lesson, blockTitle: "00 Обзор", title: "Карта обучения"};
  const overviewBlock = {id: "block", title: "00 Обзор", completed: 0, total: 0, lesson_ids: ["lesson"], lessons: [overview]};
  const overviewData = {...data, courses: [{...chapter, completed: 1, total: 45, blocks: [overviewBlock]}]};
  const html = render({data: overviewData, lessons: [overview]});
  const chapterHeader = html.match(/<button[^>]*class="course-progress__chapter-button"[\s\S]*?<\/button>/)?.[0] ?? "";
  assert.match(chapterHeader, /1 \/ 45/);
  assert.match(blockHeader(html), /1 \/ 1/);
  assert.match(blockHeader(html), /width:100%/);

  const incomplete = {...overviewData, courses: [{...overviewData.courses[0], blocks: [{...overviewBlock, lessons: []}]}]};
  assert.match(blockHeader(render({data: incomplete, lessons: []})), /Нет данных/);
  const regular = {...overviewData, courses: [{...overviewData.courses[0], blocks: [{...overviewBlock, title: "Блок 1"}]}]};
  assert.match(blockHeader(render({data: regular, lessons: [overview]})), /Нет данных/);
  const unfinished = {...overviewData, courses: [{...overviewData.courses[0], blocks: [{...overviewBlock, lessons: [{...overview, completed: false}]}]}]};
  assert.match(blockHeader(render({data: unfinished, lessons: [{...overview, completed: false}]})), /0 \/ 1/);
});

test("lesson rows show all three measured times and completion without controls or detail views", () => {
  const html = render({data:blockData, lessons:blockLessons, metric:"practiceMs", snapshot:{...snapshot, lessons:[
    {lessonId:"lesson", activeMs:180000, theoryMs:60000, practiceMs:120000},
  ]}});
  const [first, second] = lessonRows(html);
  assert.match(first, /<strong>Занятие<\/strong><small>Пройдено<\/small>/);
  assert.match(first, /<dt>Всего<\/dt><dd>3 мин<\/dd>/);
  assert.match(first, /<dt>Теория<\/dt><dd>1 мин<\/dd>/);
  assert.match(first, /<div data-current="true"><dt>Практика<\/dt><dd>2 мин<\/dd>/);
  assert.match(second, /<strong>Второе занятие<\/strong><small>Не пройдено<\/small>/);
  assert.equal((second.match(/Время недоступно/g) ?? []).length, 3);
  for (const row of [first, second]) assert.doesNotMatch(row, /<button|aria-expanded|chevron-down|course-progress__detail/);
  assert.equal((html.match(/aria-expanded="false"/g) ?? []).length, 2);
});

test("a collapsed block has its own Core progress and summed period time", () => {
  const html = render({data: blockData, lessons: blockLessons, snapshot: {...snapshot, lessons: [
    {lessonId: "lesson", activeMs: 60000, theoryMs: 60000, practiceMs: 0},
    {lessonId: "lesson-2", activeMs: 120000, theoryMs: 0, practiceMs: 120000},
  ]}});
  const header = blockHeader(html);
  assert.match(header, /aria-expanded="false"/);
  assert.match(header, /1 \/ 2/);
  assert.match(header, /3 мин/);
  assert.match(header, /width:50%/);
});

test("a missing lesson measurement does not turn a partial sum into block time", () => {
  const html = render({data: blockData, lessons: blockLessons, snapshot: {...snapshot, lessons: [
    {lessonId: "lesson", activeMs: 60000, theoryMs: 60000, practiceMs: 0},
  ]}});
  const header = blockHeader(html);
  assert.match(header, /1 \/ 2/);
  assert.match(header, /—/);
  assert.doesNotMatch(header, /1 мин/);
});

test("closed chapter shows selected period metric without affecting completion", () => {
  const chapterTime={status:"ready",report:{quality:"complete",chapters:[{chapter_id:"chapter",active_ms:3600000,theory_ms:1200000,practice_ms:2400000}]}};
  const html=render({chapterTime,metric:"theoryMs"});
  assert.match(html,/>Теория<\/small>/); assert.doesNotMatch(html,/за период/); assert.match(html,/20 мин/); assert.match(html,/1 \/ 2/);
});

test("a complete demo snapshot provides chapter time without a separate chapter request", () => {
  const html = render({data:blockData, lessons:blockLessons, lessonRowsComplete:true, metric:"practiceMs", snapshot:{...snapshot, lessons:[
    {lessonId:"lesson", activeMs:240000, theoryMs:60000, practiceMs:180000},
  ]}});
  const chapterHeader = html.match(/<button[^>]*class="course-progress__chapter-button"[\s\S]*?<\/button>/)?.[0] ?? "";
  assert.match(chapterHeader, /Практика<\/small><strong>3 мин<\/strong>/);
});

test("chapter and lesson time load in place while real progress remains visible", () => {
  const chapterTime = {status:"loading", report:null};
  const chapterOnly = render({chapterTime});
  assert.match(chapterOnly, /class="course-stats__sr-only" role="status">Загружаем время глав<\/span>/);
  assert.match(chapterOnly, /<strong><span class="course-stats__loading" aria-hidden="true">/);
  assert.doesNotMatch(chapterOnly, /Загрузка…/);

  const loading = render({chapterTime,lessonTimeLoading:true,data:blockData,lessons:blockLessons});
  assert.equal((loading.match(/class="course-stats__loading"/g) ?? []).length, 8);
  assert.match(loading, /1 \/ 2/);
  assert.match(loading, /Пройдено/);
  assert.match(loading, /Загружаем время глав и занятий/);
  assert.equal((loading.match(/aria-busy="true"/g) ?? []).length, 2);
  assert.doesNotMatch(render({chapterTime,timeLoading:true,lessonTimeLoading:false}), /course-progress__lesson-time[^>]+aria-busy="true"/);
  assert.doesNotMatch(render({chapterTime:{status:"pending",report:null}}), /class="course-stats__loading"/);
});

test("successful chapter and full breakdown display missing recordings as zero", () => {
  const chapterTime = {status:"ready", report:{status:"ok", quality:"unknown", chapters:[{
    chapter_id:"chapter", active_ms:null, theory_ms:null, practice_ms:null,
  }]}};
  const completeData = {...blockData, courses:[{...chapter, blocks:[{
    ...blockData.courses[0].blocks[0], lesson_ids:["lesson", "lesson-2"],
  }]}]};
  const props = {data:completeData, lessons:blockLessons, snapshot:{...snapshot,quality:"unknown"}, chapterTime};
  const complete = render({...props, lessonRowsComplete:true});
  assert.doesNotMatch(complete, /Полнота замеров времени глав неизвестна|Прохождение за весь курс|course-progress__footnote/);
  assert.equal((complete.match(/<strong>0 мин<\/strong>/g) ?? []).length, 2);
  assert.equal((complete.match(/<dd>0 мин<\/dd>/g) ?? []).length, 6);
  const incomplete = render({...props, lessonRowsComplete:false});
  assert.equal((incomplete.match(/<strong>0 мин<\/strong>/g) ?? []).length, 1);
  assert.doesNotMatch(incomplete, /<dd>0 мин<\/dd>/);
  assert.match(blockHeader(incomplete), /<strong>—<\/strong>/);
  const explicitNull = render({...props, lessonRowsComplete:true, snapshot:{...props.snapshot, lessons:[{lessonId:"lesson",activeMs:60000,theoryMs:60000,practiceMs:null}]}});
  assert.match(lessonRows(explicitNull)[0], /<dt>Практика<\/dt><dd><span aria-hidden="true">—<\/span>/);
  const unknownMembership = render({...props,data:blockData});
  assert.doesNotMatch(unknownMembership, /<strong>0 мин<\/strong>/);
  for (const lesson_ids of [[], ["lesson"], ["lesson", "lesson"], ["lesson", "word with spaces"]]) {
    const incompleteData = {...blockData, courses:[{...chapter, blocks:[{...blockData.courses[0].blocks[0],lesson_ids}]}]};
    const incomplete = render({...props,data:incompleteData});
    assert.doesNotMatch(incomplete, /<strong>0 мин<\/strong>/);
  }
  const stale = render({...props,chapterTime:{status:"stale",report:{...chapterTime.report,status:"stale"}}});
  assert.doesNotMatch(stale, /<strong>0 мин<\/strong>/);
  const unavailable = render({...props,chapterTime:{status:"unavailable",report:null}});
  assert.doesNotMatch(unavailable, /<strong>0 мин<\/strong>/);
});

test("outline errors and refusal remain actionable and never show zero or loading", () => {
  for (const status of ["error", "unavailable", "denied"]) {
    const html = render({chapterTime:{status,report:null},onChapterRetry:()=>{}});
    assert.doesNotMatch(html, /<dd>0 мин<\/dd>|class="course-stats__loading"|course-progress__pagination/);
    if (status === "denied") {
      assert.match(html, /Нет доступа к времени по главам и занятиям/);
      assert.doesNotMatch(html, /Повторить/);
    } else assert.match(html, /Время по главам и занятиям временно недоступно\.<\/span><button type="button">Повторить<\/button>/);
  }
});

test("search opens matching chapters and blocks and their native toggles remain usable", () => {
  const state = [];
  let stateIndex = 0;
  const {CourseStatisticsProgress: InteractiveProgress} = load("./CourseStatisticsProgress", {...React,
    useId: () => "course-progress-test",
    useMemo: fn => fn(),
    useState(initial) {
      const index = stateIndex++;
      if (!(index in state)) state[index] = typeof initial === "function" ? initial() : initial;
      return [state[index], value => { state[index] = typeof value === "function" ? value(state[index]) : value; }];
    },
  });
  const draw = () => {
    stateIndex = 0;
    return InteractiveProgress({data:blockData, lessons:blockLessons, snapshot, metric:"activeMs"});
  };
  function byClass(tree, className) {
    if (!React.isValidElement(tree)) return undefined;
    if (tree.props.className === className) return tree;
    return React.Children.toArray(tree.props.children).map(child => byClass(child, className)).find(Boolean);
  }
  const chapterButton = tree => byClass(tree, "course-progress__chapter-button");
  const blockButton = tree => byClass(tree, "course-progress__block-button");
  let tree = draw();
  assert.equal(chapterButton(tree).props["aria-expanded"], false);
  chapterButton(tree).props.onClick();
  tree = draw();
  assert.equal(chapterButton(tree).props["aria-expanded"], true);
  blockButton(tree).props.onClick();
  assert.equal(blockButton(draw()).props["aria-expanded"], true);

  const search = byClass(draw(), "course-progress__search").props.children[1];
  search.props.onChange({target:{value:"второе"}});
  tree = draw();
  assert.equal(chapterButton(tree).props["aria-expanded"], true);
  assert.equal(blockButton(tree).props["aria-expanded"], true);
  assert.deepEqual(lessonRows(renderToStaticMarkup(tree)).length, 1);
  assert.match(renderToStaticMarkup(tree), /Второе занятие/);
  blockButton(tree).props.onClick();
  assert.equal(blockButton(draw()).props["aria-expanded"], false);
});
