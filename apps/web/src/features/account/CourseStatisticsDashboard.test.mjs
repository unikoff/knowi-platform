import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import {createRequire} from "node:module";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import ts from "typescript";

const require=createRequire(import.meta.url);
function compile(path, imports={}) {
  const source=fs.readFileSync(new URL(path,import.meta.url),"utf8");
  const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020,jsx:ts.JsxEmit.ReactJSX}}).outputText;
  const module={exports:{}};
  new Function("require","module","exports",code)(name => Object.hasOwn(imports,name) ? imports[name] : require(name),module,module.exports);
  return module.exports;
}
const model=compile("./course-statistics-model.ts");
const today = "2026-11-19";
const compileDashboard = react => compile("./CourseStatisticsDashboard.tsx", {
  react,
  "./course-statistics-model":{...model,moscowToday:() => today},
  "./course-statistics-analytics":compile("./course-statistics-analytics.ts"),
  "./CourseStatisticsProgress":{CourseStatisticsProgress:() => null},
  "./course-statistics.css":{},
}).CourseStatisticsDashboard;
const CourseStatisticsDashboard = compileDashboard({...React,useLayoutEffect:() => {}});
const data={tracksLoaded:true,completedLessons:2,totalLessons:5,courses:[]};
const render=(timeState,timeData=null,props={}) => renderToStaticMarkup(React.createElement(CourseStatisticsDashboard,{
  courseId:"backend",data,catalog:[],timeState,timeData,onRetry:() => {},...props,
}));

test("completed lesson history is part of ordinary study time with live activity", () => {
  const dates = model.periodDates(7, today);
  const time = {activeMs: 120000, theoryMs: 120000, practiceMs: 0};
  const empty = model.emptyCourseSnapshot("backend", dates);
  const snapshot = {...empty, totals: time, days: [...empty.days.slice(0, -1), {date: today, ...time}]};
  const progress = {...data, courses: [{id: "ch1", title: "Глава", blocks: [{id: "b1", title: "Блок", lessons: [
    {id: "one", title: "Занятие 1", completed: true, completedOn: today, historicalCredit: true},
    {id: "two", title: "Занятие 2", completed: false, historicalCredit: true},
  ]}]}]};
  const html = render("ready", snapshot, {data: progress});
  assert.match(html, /Время в курсе<\/span><strong[^>]*>2 ч 2 мин<\/strong>/);
  assert.doesNotMatch(html, /Условное время|Измеренное время|Источник времени/);
});

test("disabled time is explained without a retry or fabricated values",() => {
  const html=render("disabled");
  assert.match(html,/Отображение личного времени занятий пока не включено/);
  assert.doesNotMatch(html,/Повторить/);
  assert.match(html,/Пройдено занятий/);
  assert.match(html,/>2<small> \/ 5<\/small>/);
});

test("a disabled-time instruction guides staff to another way to open the profile", () => {
  const props = {timeDisabledMessage: "Откройте профиль через «Поиск по всем ученикам»."};
  const disabled = render("disabled", null, props);
  assert.match(disabled, /Откройте профиль через «Поиск по всем ученикам»/);
  assert.doesNotMatch(disabled, /Отображение личного времени занятий пока не включено|Повторить/);

  const unavailable = render("unavailable", null, props);
  assert.match(unavailable, /Учебная аналитика временно недоступна/);
  assert.doesNotMatch(unavailable, /Поиск по всем ученикам/);
});

test("unsupported role and temporary unavailability remain distinct",() => {
  const blocked=render("role_blocked"), unavailable=render("unavailable");
  assert.match(blocked,/Личная аналитика времени недоступна для этой роли/);
  assert.doesNotMatch(blocked,/Повторить/);
  assert.match(unavailable,/Учебная аналитика временно недоступна/);
  assert.match(unavailable,/Повторить/);
});

test("pending time uses decorative placeholders and a single accessible announcement", () => {
  for (const state of ["pending", "loading"]) {
    const html = render(state);
    assert.match(html, /class="course-stats__sr-only" role="status">Загружаем время занятий<\/span>/);
    assert.match(html, /aria-label="Сводка по курсу" aria-busy="true"/);
    assert.equal([...html.matchAll(/class="course-stats__metric-value" data-ready="false"><span class="course-stats__loading"/g)].length, 3);
    assert.match(html, /class="course-stats__day-track"><span class="course-stats__loading" aria-hidden="true">/);
    assert.match(html, /course-stats__distribution-total[^>]*><strong><span class="course-stats__loading" aria-hidden="true">/);
    assert.doesNotMatch(html, /course-stats__distribution-trigger|course-stats__chart-preview/);
    assert.doesNotMatch(html, /нет данных о времени|Данные о времени недоступны/);
  }
  assert.doesNotMatch(render("unavailable"), /class="course-stats__loading"/);
});

test("the learner sees recorded days, their share, average and chart zeros without the redundant notices", () => {
  const dates = model.periodDates(7, "2026-11-19");
  const totals = {activeMs: 180000, theoryMs: 120000, practiceMs: 60000};
  const days = dates.map((date, index) => ({date, activeMs: index === 1 ? 120000 : index === 6 ? 60000 : 0,
    theoryMs: index === 1 ? 120000 : 0, practiceMs: index === 6 ? 60000 : 0}));
  const snapshot = {courseId:"backend",from:dates[0],to:dates.at(-1),quality:"unknown",asOf:null,totals,days,lessons:[]};
  const html = render("ready", snapshot);
  for (const label of ["Сводка по курсу", "Время занятий по дням"]) {
    const section = html.match(new RegExp(`<section[^>]*aria-label="${label}"[^>]*>`))?.[0];
    assert.ok(section, label);
    assert.doesNotMatch(section, /\bhidden(?:[\s=>]|$)/);
  }
  assert.match(html, /Дней с занятиями<\/span><div class="course-stats__metric-reading"><strong[^>]*>2<small> \/ 7/);
  assert.match(html, /Доля дней с занятиями: <\/span>29%/);
  assert.match(html, /В среднем за учебный день<\/span><strong[^>]*>1 мин<\/strong>/);
  assert.match(html, /class="course-stats__day-marker" aria-hidden="true">0<\/span>/);
  assert.doesNotMatch(html, /Полнота замеров неизвестна|за 7 дней/);
  assert.match(html, /теория: 2 мин, практика: 0 мин/);

  const failed = render("error", snapshot);
  assert.match(failed, /Дней с занятиями<\/span><div class="course-stats__metric-reading"><strong[^>]*>—<small> \/ 7/);
  assert.doesNotMatch(failed, /course-stats__metric-share/);
  assert.match(failed, /Не удалось загрузить учебную аналитику/);
  assert.doesNotMatch(failed, /aria-hidden="true">0<\/span>/);
});

test("the selected-period completion count distinguishes a real zero from a stale range and loading", () => {
  const period = {from:"2026-11-13", to:today, count:0};
  const withCount = props => render("disabled", null, {periodCompletedLessons:{...period,...props}});
  assert.match(withCount({}), /За выбранный период<\/span><strong data-ready="true">0<\/strong>/);
  assert.match(withCount({count:2}), /За выбранный период<\/span><strong data-ready="true">2<\/strong>/);
  assert.match(withCount({count:2, from:"2026-10-21"}), /За выбранный период<\/span><strong data-ready="false">—<\/strong>/);
  assert.match(withCount({count:null}), /За выбранный период<\/span><strong data-ready="false">—<\/strong>/);
  assert.match(withCount({loading:true}), /Загружаем число пройденных занятий/);
});

function snapshot(theoryMs, practiceMs, quality = "complete", dates = model.periodDates(7, today)) {
  const totals = {activeMs: (theoryMs ?? 0) + (practiceMs ?? 0), theoryMs, practiceMs};
  const days = dates.map(date => ({date, ...(date === dates.at(-1) ? totals : {activeMs:0,theoryMs:0,practiceMs:0})}));
  return {courseId:"backend",from:dates[0],to:dates.at(-1),quality,asOf:null,totals,days,lessons:[]};
}

test("the chart uses the largest displayed duration as the top of the scale", () => {
  const html = render("ready", snapshot(180000, 120000));
  const axis = html.match(/<div class="course-stats__plot-axis"[^>]*>(.*?)<\/div>/)?.[1];
  assert.equal(axis, "<span>5 мин</span><span>2 мин 30 с</span><span>0</span>");
});

test("progress reads one complete outline independently of the chart and never falls back to a detail page", () => {
  let progress;
  const Dashboard = compile("./CourseStatisticsDashboard.tsx", {
    react:{...React,useLayoutEffect:()=>{}},
    "./course-statistics-model":{...model,moscowToday:()=>today},
    "./course-statistics-analytics":compile("./course-statistics-analytics.ts"),
    "./CourseStatisticsProgress":{CourseStatisticsProgress:props=>{progress=props;return null;}},
    "./course-statistics.css":{},
  }).CourseStatisticsDashboard;
  const dates = model.periodDates(7, today);
  const timeData = {...snapshot(180000,60000), lessons:[{lessonId:"ignored-page",activeMs:240000,theoryMs:180000,practiceMs:60000}]};
  const catalog = [{id:"chapter",title:"Глава",lessons:[{id:"lesson",title:"Занятие"}]}];
  const report = {
    course_id:"backend",from_time:`${model.shiftDate(dates[0],-1)}T21:00:00Z`,to_time:`${today}T21:00:00Z`,
    status:"ok",quality:"partial",as_of:null,chapters:[],
    lessons:[{lesson_id:"lesson",active_ms:90000,theory_ms:30000,practice_ms:60000}],
    unmapped_lessons:[{lesson_id:"retired",active_ms:0,theory_ms:0,practice_ms:0}],
  };
  const draw = (props = {}) => renderToStaticMarkup(React.createElement(Dashboard, {
    courseId:"backend",data,catalog,timeState:"ready",timeData,...props,
  }));
  draw({timeState:"loading",chapterTime:{status:"ready",report}});
  assert.equal(progress.lessonRowsComplete,true);
  assert.equal(progress.lessonTimeLoading,false);
  assert.deepEqual(progress.snapshot.lessons, [
    {lessonId:"lesson",activeMs:90000,theoryMs:30000,practiceMs:60000},
    {lessonId:"retired",activeMs:0,theoryMs:0,practiceMs:0},
  ]);
  assert.equal(progress.lessons.find(lesson => lesson.id === "retired").trackId, "");
  assert.equal(progress.lessons.some(lesson => lesson.id === "ignored-page"), false);
  assert.equal("detailPage" in progress, false);
  assert.equal(progress.metric, "activeMs");
  draw({chapterTime:{status:"ready",report:{...report,to_time:"2026-11-18T21:00:00Z"}}});
  assert.equal(progress.lessonRowsComplete, true);

  for (const [chapterTime, loading] of [
    [{status:"loading",report:null}, true],
    [{status:"stale",report:{...report,status:"stale"}}, false],
    [{status:"ready",report:{...report,course_id:"old-course"}}, true],
    [{status:"unavailable",report:null}, false],
  ]) {
    draw({chapterTime});
    assert.equal(progress.lessonRowsComplete, false);
    assert.equal(progress.lessonTimeLoading, loading);
    assert.deepEqual(progress.snapshot.lessons, []);
  }
  const demoProgressSnapshot = {...timeData, lessons:[{lessonId:"demo-lifetime",activeMs:60000,theoryMs:60000,practiceMs:0}]};
  draw({demo:true,demoProgressSnapshot});
  assert.equal(progress.lessonRowsComplete,true);
  assert.equal(progress.chapterTime,undefined);
  assert.deepEqual(progress.snapshot.lessons,demoProgressSnapshot.lessons);
});

test("the ring represents observed shares and exposes each share and duration in text", () => {
  for (const [theory, practice, share, mixed] of [
    [180000,60000,75,true], [60000,0,100,false], [0,60000,0,false], [1,60000,0,true], [60000,1,100,true],
  ]) {
    const html = render("ready", snapshot(theory, practice, "unknown"));
    assert.match(html, /data-state="ready" data-preview="false"/);
    assert.match(html, /class="course-stats__distribution-arcs"[^>]*aria-hidden="true"/);
    const arcs = [...html.matchAll(/<(path|circle) class="course-stats__distribution-arc" data-ring-arc="(theory|practice)"/g)].map(match => match.slice(1));
    assert.deepEqual(arcs, mixed ? [["path", "theory"], ["path", "practice"]] : [["circle", theory > 0 ? "theory" : "practice"]]);
    const duration = ms => model.formatStudyTime(ms).replace("<", "&lt;");
    assert.ok(html.includes(`aria-label="Разбивка времени. За весь период. Теория: ${duration(theory)}, ${share}%. Практика: ${duration(practice)}, ${100 - share}%"`));
    assert.doesNotMatch(html, /course-stats__chart-legend|course-stats__chart-preview|course-stats__distribution-info/);
  }
});

test("the ring does not invent shares when time is zero, unknown, being loaded, or unavailable", () => {
  const zero = render("ready", snapshot(0, 0));
  assert.match(zero, /data-state="empty" data-preview="false"/);
  assert.match(zero, /<strong[^>]*>0 мин<\/strong>/);
  assert.match(zero, /Доля дней с занятиями: <\/span>0%/);
  const partial = render("ready", snapshot(120000, null, "partial"));
  assert.match(partial, /data-state="unknown" data-preview="false"/);
  assert.match(partial, /aria-label="Разбивка времени. За весь период. Теория: 2 мин. Практика: нет данных"/);
  assert.match(partial, /Замеры доступны частично/);
  const loading = render("loading", snapshot(120000, 60000));
  assert.match(loading, /data-state="loading" data-preview="false"/);
  const failed = render("error", snapshot(120000, 60000));
  assert.match(failed, /data-state="unknown" data-preview="false"/);
  assert.match(failed, /Повторить/);
  for (const html of [zero, partial, loading, failed]) {
    assert.match(html, /class="course-stats__distribution-ring" aria-hidden="true"/);
    assert.doesNotMatch(html, /data-ring-arc=/);
  }
});

test("range navigation preserves keyboard selection and chart previews support focus, pointer and press", t => {
  const states = [], refs = [], rangeChanges = [], focused = [];
  let stateIndex = 0, refIndex = 0;
  let theory = 180000, practice = 120000;
  const Dashboard = compileDashboard({
    ...React,
    useId: () => "dashboard-test",
    useEffect: () => {},
    useLayoutEffect: fn => fn(),
    useMemo: fn => fn(),
    useRef(initial) { const index = refIndex++; return refs[index] ??= {current:initial}; },
    useState(initial) {
      const index = stateIndex++;
      if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial;
      return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }];
    },
  });
  const draw = () => {
    stateIndex = refIndex = 0;
    const range = rangeChanges.at(-1);
    const dates = range ? model.periodDates(range.from === "2026-11-13" ? 7 : 30, range.to) : model.periodDates(7,today);
    return Dashboard({courseId:"backend", data, catalog:[], timeState:"ready", timeData:snapshot(theory,practice,"complete",dates), onRangeChange: value => rangeChanges.push(value)});
  };
  function find(tree, predicate) {
    if (!React.isValidElement(tree)) return [];
    return [...(predicate(tree) ? [tree] : []), ...React.Children.toArray(tree.props.children).flatMap(node => find(node, predicate))];
  }
  const buttons = (tree, name) => find(tree, node => node.type === "button" && (!name || node.props["aria-label"] === name || node.props.children === name));
  const days = tree => find(tree, node => node.type === "button" && node.props.className?.startsWith("course-stats__day"));
  const preview = tree => find(tree, node => node.props.className === "course-stats__chart-preview")[0];
  const ring = tree => buttons(tree).find(node => node.props.className === "course-stats__distribution-trigger");
  const key = key => ({key, preventDefault: () => {}});

  let tree = draw();
  assert.match(renderToStaticMarkup(tree), /type="button" aria-pressed="true">Неделя<\/button><button type="button" aria-pressed="false">Месяц<\/button>/);
  assert.doesNotMatch(renderToStaticMarkup(tree), /Свой период|type="date"/);
  const balance = find(tree, node => node.props.className === "course-stats__balance")[0];
  assert.match(ring(balance).props["aria-label"], /За весь период/);
  assert.equal(days(tree)[0].props["aria-controls"], balance.props.id);
  assert.deepEqual(rangeChanges.at(-1), {courseId:"backend", from:"2026-11-13", to:today});
  assert.equal(buttons(tree,"Следующий период")[0].props.disabled, true);
  buttons(tree,"Месяц")[0].props.onClick();
  tree = draw();
  assert.equal(days(tree).length, 30);
  assert.equal(buttons(tree,"Месяц")[0].props["aria-pressed"], true);
  assert.deepEqual(rangeChanges.at(-1), {courseId:"backend", from:"2026-10-21", to:today});

  buttons(tree,"Предыдущий период")[0].props.onClick();
  tree = draw();
  assert.deepEqual(rangeChanges.at(-1), {courseId:"backend", from:"2026-09-21", to:"2026-10-20"});
  assert.equal(buttons(tree,"Следующий период")[0].props.disabled, false);
  buttons(tree,"Следующий период")[0].props.onClick();
  tree = draw();
  assert.equal(buttons(tree,"Следующий период")[0].props.disabled, true);
  assert.deepEqual(rangeChanges.at(-1), {courseId:"backend", from:"2026-10-21", to:today});

  days(tree)[0].ref({focus: () => focused.push("first")});
  days(tree).at(-1).props.onKeyDown(key("Home"));
  tree = draw();
  assert.deepEqual(focused, ["first"]);
  assert.equal(days(tree)[0].props["aria-pressed"], true);
  assert.equal(days(tree)[0].props.tabIndex, 0);
  assert.match(ring(tree).props["aria-label"], /21 октября/);
  assert.match(renderToStaticMarkup(preview(tree)), /21 октября/);
  days(tree)[0].props.onKeyDown(key("Escape"));
  tree = draw();
  assert.equal(preview(tree), undefined);
  assert.equal(days(tree)[0].props["aria-pressed"], true);
  days(tree)[0].props.onKeyDown(key("Escape"));
  tree = draw();
  assert.equal(days(tree)[0].props["aria-pressed"], false);
  assert.match(ring(tree).props["aria-label"], /За весь период/);

  const day = days(tree).at(-1);
  day.props.onClick({currentTarget:{}});
  tree = draw();
  assert.equal(days(tree).at(-1).props["aria-pressed"], true);
  assert.match(renderToStaticMarkup(preview(tree)), /19 ноября.*5 мин.*Теория.*60%.*3 мин.*Практика.*40%.*2 мин/);
  assert.doesNotMatch(renderToStaticMarkup(preview(tree)), /data-spotlight="true"/);

  ring(tree).props.onClick({currentTarget:{}});
  tree = draw();
  assert.equal(ring(tree).props["aria-pressed"], true);
  assert.match(renderToStaticMarkup(preview(tree)), /19 ноября.*Практика.*40%.*2 мин/);
  ring(tree).props.onKeyDown(key("Escape"));
  tree = draw();
  assert.equal(preview(tree), undefined);
  assert.equal(days(tree).at(-1).props["aria-pressed"], true);

  t.mock.timers.enable({apis:["setTimeout"]});
  const anchor = {
    matches: () => true,
    ownerDocument:{defaultView:{matchMedia:() => ({matches:true})}},
    getBoundingClientRect:() => ({top:100, left:100, height:100, width:100}),
  };
  const pointer = (pointerType, clientX = 167, clientY = 133) => ({currentTarget:anchor,pointerType,clientX,clientY});
  ring(tree).props.onPointerEnter(pointer("touch"));
  assert.equal(preview(draw()), undefined);
  ring(draw()).props.onPointerEnter(pointer("mouse", 195, 195));
  assert.equal(preview(draw()), undefined, "the corners of the button's bounding box are outside the disk");
  ring(tree).props.onFocus({currentTarget:anchor});
  tree = draw();
  assert.ok(preview(tree));
  ring(tree).props.onPointerEnter(pointer("mouse"));
  tree = draw();
  assert.match(renderToStaticMarkup(preview(tree)), /data-segment="theory" data-spotlight="true"/);
  assert.match(renderToStaticMarkup(ring(tree)), /data-ring-arc="theory" data-active="true".*data-ring-arc="practice" data-active="false"/);
  ring(tree).props.onPointerMove(pointer("mouse", 130, 150));
  tree = draw();
  assert.match(renderToStaticMarkup(preview(tree)), /data-segment="practice" data-spotlight="true"/);
  assert.match(renderToStaticMarkup(ring(tree)), /data-ring-arc="theory" data-active="false".*data-ring-arc="practice" data-active="true"/);
  ring(tree).props.onPointerMove(pointer("mouse", 150, 150));
  tree = draw();
  assert.match(renderToStaticMarkup(preview(tree)), /data-segment="practice" data-spotlight="true"/, "the exact center keeps the last sector");
  ring(tree).props.onPointerMove(pointer("mouse", 190, 190));
  tree = draw();
  assert.doesNotMatch(renderToStaticMarkup(ring(tree)), /data-active="true"/);
  assert.ok(preview(tree), "keyboard access to the breakdown survives movement outside the disk");
  ring(tree).props.onPointerLeave(pointer("mouse"));
  t.mock.timers.tick(181);
  assert.ok(preview(draw()), "keyboard focus preserves the preview after mouse leave");
  ring(draw()).props.onClick({currentTarget:anchor});
  tree = draw();
  assert.equal(ring(tree).props["aria-pressed"], true);
  ring(tree).props.onBlur();
  tree = draw();
  assert.equal(preview(tree), undefined);

  for (const [amounts, expected] of [
    [[1, 120000], "practice"], [[0, 120000], "practice"], [[180000, 0], "theory"],
    [[0, 0], null], [[180000, null], null],
  ]) {
    [theory, practice] = amounts;
    ring(draw()).props.onPointerEnter(pointer("mouse"));
    tree = draw();
    const markup = renderToStaticMarkup(ring(tree));
    if (expected) assert.match(markup, new RegExp(`data-ring-arc="${expected}" data-active="true"`));
    else assert.doesNotMatch(markup, /data-active="true"/);
    ring(tree).props.onKeyDown(key("Escape"));
  }

  const lastDay = tree => days(tree).at(-1);
  const barPointer = (barSegment, pointerType = "mouse") => ({...pointer(pointerType), target:{dataset:{barSegment}}});
  [theory, practice] = [180000, 120000];
  lastDay(draw()).props.onPointerEnter(barPointer("theory","touch"));
  assert.equal(preview(draw()), undefined, "touch movement alone does not select a bar segment");
  lastDay(draw()).props.onPointerEnter(barPointer("theory"));
  tree = draw();
  assert.match(renderToStaticMarkup(lastDay(tree)), /data-detached="true" style="height:100%".*data-bar-segment="theory" data-active="true" style="height:60%".*data-bar-segment="practice" data-active="false" style="height:40%"/);
  assert.match(renderToStaticMarkup(preview(tree)), /data-segment="theory" data-spotlight="true"/);
  assert.doesNotMatch(renderToStaticMarkup(ring(tree)), /data-active="true"/);
  lastDay(tree).props.onPointerMove(barPointer("practice"));
  tree = draw();
  assert.match(renderToStaticMarkup(lastDay(tree)), /data-detached="true".*data-bar-segment="theory" data-active="false".*data-bar-segment="practice" data-active="true"/);
  assert.match(renderToStaticMarkup(preview(tree)), /data-segment="practice" data-spotlight="true"/);
  assert.doesNotMatch(renderToStaticMarkup(ring(tree)), /data-active="true"/);
  find(tree, node => node.props.className === "course-stats__chart-scroll")[0].props.onScroll();
  tree = draw();
  assert.doesNotMatch(renderToStaticMarkup(lastDay(tree)), /data-detached="true"/);
  assert.doesNotMatch(renderToStaticMarkup(preview(tree)), /data-spotlight="true"/);

  for (const filter of ["Теория", "Практика"]) {
    const segment = filter === "Теория" ? "theory" : "practice";
    buttons(draw(),filter)[0].props.onClick();
    assert.equal(buttons(draw(),filter)[0].props["aria-pressed"], true);
    lastDay(draw()).props.onPointerEnter(barPointer(segment));
    tree = draw();
    assert.match(renderToStaticMarkup(lastDay(tree)), new RegExp(`data-detached="false".*data-bar-segment="${segment}" data-active="true" style="height:100%"`));
    assert.match(renderToStaticMarkup(preview(tree)), new RegExp(`data-segment="${segment}" data-spotlight="true"`));
  }

  buttons(draw(),"Всего")[0].props.onClick();
  [theory, practice] = [180000, 0];
  lastDay(draw()).props.onPointerEnter(barPointer("theory"));
  tree = draw();
  assert.match(renderToStaticMarkup(lastDay(tree)), /data-detached="false".*data-bar-segment="theory" data-active="true"/);
  assert.doesNotMatch(renderToStaticMarkup(lastDay(tree)), /data-bar-segment="practice"/);
  lastDay(tree).props.onKeyDown(key("Escape"));

  [theory, practice] = [180000, null];
  lastDay(draw()).props.onPointerEnter(barPointer());
  tree = draw();
  assert.match(renderToStaticMarkup(lastDay(tree)), /class="course-stats__bar is-total-unknown" data-detached="false"/);
  assert.doesNotMatch(renderToStaticMarkup(lastDay(tree)), /data-bar-segment=/);
  assert.doesNotMatch(renderToStaticMarkup(preview(tree)), /data-spotlight="true"/);
});
