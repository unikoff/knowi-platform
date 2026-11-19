import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import ts from 'typescript';

function load(file, dependencies = {}) {
  const source = fs.readFileSync(new URL(file, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
  const module = {exports: {}};
  new Function('require', 'module', 'exports', code)(name => dependencies[name], module, module.exports);
  return module.exports;
}
const model = load('./course-statistics-model.ts');
const {periodDates, shiftDate} = model;
const {createCourseStatisticsDemo} = load('./course-statistics-demo.ts', {'./course-statistics-model': model});
const dates = periodDates(90, '2026-11-17');
const metrics = ['activeMs', 'theoryMs', 'practiceMs'];

test('fictional curriculum and time reconcile across all metrics', () => {
  const {data, catalog, snapshot} = createCourseStatisticsDemo('python', dates);
  assert.ok(catalog.length >= 2);
  assert.ok(data.totalLessons >= 12);
  assert.ok(data.completedLessons > 0 && data.completedLessons < data.totalLessons);
  assert.ok(catalog.flatMap(chapter => chapter.lessons).every(lesson => lesson.id.startsWith('demo-')));
  assert.equal(snapshot.from, dates[0]);
  assert.equal(snapshot.to, dates.at(-1));
  assert.equal(snapshot.quality, 'complete');
  for (const metric of metrics) {
    assert.equal(snapshot.totals[metric], snapshot.days.reduce((sum, day) => sum + day[metric], 0));
    assert.equal(snapshot.totals[metric], snapshot.lessons.reduce((sum, lesson) => sum + lesson[metric], 0));
  }
  for (const value of [snapshot.totals, ...snapshot.days, ...snapshot.lessons]) assert.equal(value.activeMs, value.theoryMs + value.practiceMs);
  assert.ok(snapshot.days.some(day => day.activeMs === 0));
  const completed = new Set(data.courses.flatMap(chapter => chapter.blocks.flatMap(block => block.lessons.filter(lesson => lesson.completed).map(lesson => lesson.id))));
  assert.ok(snapshot.lessons.some(lesson => completed.has(lesson.lessonId) && lesson.activeMs > 0));
  assert.ok(snapshot.lessons.some(lesson => !completed.has(lesson.lessonId) && lesson.activeMs > 0));
});

test('overlapping dates and curriculum progress remain stable across ranges', () => {
  const long = createCourseStatisticsDemo('python', dates);
  const short = createCourseStatisticsDemo('python', dates.slice(-7));
  assert.deepEqual(short.snapshot.days, long.snapshot.days.slice(-7));
  assert.deepEqual(short.data, long.data);
  assert.deepEqual(short.progressSnapshot.lessons, long.progressSnapshot.lessons);
  assert.deepEqual(long, createCourseStatisticsDemo('python', dates));
  assert.notDeepEqual(long.snapshot.days, createCourseStatisticsDemo('other', dates).snapshot.days);
});

test('demo counts each completed lesson once, on a fixed date shared across overlapping ranges', () => {
  const today = '2026-11-17';
  const makeDemo = selected => createCourseStatisticsDemo('python', selected, undefined, undefined, today);
  const all = periodDates(120, today);
  const days = all.map(date => makeDemo([date]).completedLessonsInPeriod);
  const sum = values => values.reduce((total, value) => total + value, 0);
  assert.equal(sum(days), makeDemo(all).data.completedLessons);
  assert.equal(makeDemo(all.slice(-30)).completedLessonsInPeriod, sum(days.slice(-30)));
  assert.equal(makeDemo(all.slice(-7)).completedLessonsInPeriod, sum(days.slice(-7)));
  assert.equal(makeDemo([shiftDate(today, -120)]).completedLessonsInPeriod, 0);
  const lessons = makeDemo(all).data.courses.flatMap(chapter => chapter.blocks.flatMap(block => block.lessons));
  assert.equal(lessons.filter(lesson => lesson.completedOn && all.includes(lesson.completedOn)).length, makeDemo(all).completedLessonsInPeriod);
  assert.ok(lessons.every(lesson => lesson.completed ? typeof lesson.completedOn === 'string' : lesson.completedOn === null));
});

test('real catalog and structure are copied while actual progress is ignored', () => {
  const catalog = [{id: 'chapter', title: 'Глава', lessons: [{id: 'a', title: 'Первый'}, {id: 'b', title: 'Второй'}, {id: 'c', title: 'Третий'}]}];
  const source = {tracksLoaded: true, completedLessons: 3, totalLessons: 999, overallProgress: 99, activeDays: 999, currentStreak: 999, weeklyActivity: [], weeklyLessonsCompleted: [], courses: [{id: 'chapter', title: 'Глава', completed: 3, total: 999, blocks: [{id: 'block', title: 'Блок', completed: 3, total: 999, lessons: catalog[0].lessons.map(lesson => ({...lesson, completed: true}))}]}]};
  const before = structuredClone({source, catalog});
  const result = createCourseStatisticsDemo('python', dates, source, catalog);
  const changed = structuredClone(source);
  changed.courses[0].blocks[0].lessons.forEach(lesson => { lesson.completed = false; lesson.completedOn = '1999-01-01'; });
  changed.completedLessons = 0;
  changed.courses[0].completed = 0;
  assert.deepEqual(result, createCourseStatisticsDemo('python', dates, changed, catalog));
  assert.deepEqual({source, catalog}, before);
  assert.equal(result.data.courses[0].blocks[0].id, 'block');
  assert.equal(result.data.totalLessons, 3);
  assert.equal(result.data.completedLessons, 2);
  result.catalog[0].lessons[0].title = 'Изменено';
  assert.equal(catalog[0].lessons[0].title, 'Первый');
});

test('derives public curriculum from source blocks when catalog has no lessons', () => {
  const source = {tracksLoaded: false, completedLessons: 99, totalLessons: 99, overallProgress: 100, activeDays: 99, currentStreak: 99, weeklyActivity: null, weeklyLessonsCompleted: null, courses: [{id: 'chapter', title: 'Настоящая глава', completed: 99, total: 99, blocks: [{id: 'block', title: 'Настоящий блок', completed: 99, total: 99, lessons: [{id: 'lesson', title: 'Настоящее занятие', completed: false}]}]}]};
  const original = structuredClone(source);
  const result = createCourseStatisticsDemo('python', dates, source, []);
  assert.deepEqual(result.catalog, [{id: 'chapter', title: 'Настоящая глава', lessons: [{id: 'lesson', title: 'Настоящее занятие'}]}]);
  assert.equal(result.data.courses[0].blocks[0].id, 'block');
  assert.equal(result.data.totalLessons, 1);
  assert.equal(result.data.activeDays, null);
  assert.equal(result.data.currentStreak, null);
  assert.deepEqual(source, original);
});

test('duplicate lesson IDs retain their first occurrence across chapters', () => {
  const catalog = [{id: 'one', title: 'One', lessons: [{id: 'a', title: 'First'}, {id: 'a', title: 'Duplicate'}, {id: 'b', title: 'B'}]}, {id: 'two', title: 'Two', lessons: [{id: 'a', title: 'Again'}, {id: 'c', title: 'C'}]}];
  const original = structuredClone(catalog);
  const result = createCourseStatisticsDemo('python', dates, undefined, catalog);
  assert.deepEqual(result.catalog.flatMap(chapter => chapter.lessons), [{id: 'a', title: 'First'}, {id: 'b', title: 'B'}, {id: 'c', title: 'C'}]);
  assert.equal(result.data.totalLessons, 3);
  assert.equal(result.data.courses.reduce((sum, chapter) => sum + chapter.total, 0), 3);
  assert.equal(result.data.completedLessons, result.data.courses.flatMap(chapter => chapter.blocks.flatMap(block => block.lessons)).filter(lesson => lesson.completed).length);
  assert.equal(result.snapshot.lessons.length, 3);
  assert.deepEqual(catalog, original);
});
