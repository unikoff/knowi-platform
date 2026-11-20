import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import ts from 'typescript';

const source = fs.readFileSync(new URL('./course-statistics-model.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
const module = {exports: {}};
new Function('module', 'exports', code)(module, module.exports);
const {moscowToday, periodDates, validateStudyRange, shiftDate, formatStudyTime, courseTimeChartScale, formatStudyDate, emptyCourseSnapshot, acceptCourseSnapshot, flattenCourseLessons, summarizeCourseTime} = module.exports;

test('historical credit uses only eligible completions and keeps lifetime lessons outside the selected dates', () => {
  const day = '2026-11-19';
  const first = {id: 'first', completed: true, completedOn: day, historicalCredit: true};
  const proof = module.exports.completionCreditSnapshot('backend', [day, '2026-11-20'], [
    first, first,
    {id: 'old', completed: true, completedOn: '2026-10-01', historicalCredit: true},
    {id: 'unfinished', completed: false, completedOn: day, historicalCredit: true},
    {id: 'new', completed: true, completedOn: day, historicalCredit: false},
  ]);
  assert.deepEqual(proof.totals, {activeMs: 7_200_000, theoryMs: 3_600_000, practiceMs: 3_600_000});
  assert.equal(proof.days[1].activeMs, 0);
  assert.deepEqual(proof.lessons.map(row => row.lessonId), ['first', 'old']);
});

test('custom ranges validate inclusive dates and calendar boundaries', () => {
  const today = '2025-01-31';
  assert.deepEqual(validateStudyRange('2025-01-31', '2025-01-31', today), {valid: true, days: 1});
  assert.deepEqual(validateStudyRange('2025-01-01', '2025-01-31', today), {valid: true, days: 31});
  assert.deepEqual(validateStudyRange('2024-12-31', '2025-01-31', today), {valid: false, message: 'Можно выбрать не больше 31 дня'});
  assert.deepEqual(validateStudyRange('2024-02-28', '2024-03-01', today), {valid: true, days: 3});
  assert.deepEqual(validateStudyRange('2024-12-31', '2025-01-01', today), {valid: true, days: 2});
  for (const [from, to] of [['', today], ['2025-02-29', today], ['2025-1-01', today], [today, '']]) {
    assert.equal(validateStudyRange(from, to, today).valid, false);
  }
  assert.equal(validateStudyRange('2025-01-02', '2025-01-01', today).valid, false);
  assert.equal(validateStudyRange(today, '2025-02-01', today).valid, false);
});

test('Moscow date and inclusive periods cross calendar boundaries', () => {
  assert.equal(moscowToday(new Date('2026-12-31T21:00:00Z')), '2027-01-01');
  for (const length of [1, 7, 30, 90]) {
    const dates = periodDates(length, '2027-01-01');
    assert.equal(dates.length, length);
    assert.equal(dates.at(-1), '2027-01-01');
    assert.equal(shiftDate(dates[0], length - 1), dates.at(-1));
  }
  assert.equal(shiftDate('2024-03-01', -1), '2024-02-29');
  assert.throws(() => shiftDate('2025-02-29', 1), RangeError);
  assert.match(formatStudyDate('2027-01-01', {day: 'numeric', month: 'numeric', year: 'numeric'}), /01\.01\.2027/);
});

test('missing and invalid times differ from observed zero', () => {
  for (const value of [null, -1, NaN, Infinity]) assert.equal(formatStudyTime(value), '—');
  assert.equal(formatStudyTime(0), '0 мин');
  assert.equal(formatStudyTime(1), '< 1 мин');
  assert.equal(formatStudyTime(60000), '1 мин');
  assert.equal(formatStudyTime(3660000), '1 ч 1 мин');
});

test('course chart scales each metric by its own highest observed day', () => {
  const days = [
    {activeMs: 0, theoryMs: 0, practiceMs: 0},
    {activeMs: null, theoryMs: null, practiceMs: null},
    {activeMs: 180000, theoryMs: 60000, practiceMs: 120000},
    {activeMs: 300000, theoryMs: 240000, practiceMs: 60000},
  ];
  assert.equal(courseTimeChartScale([], 'activeMs'), null);
  assert.equal(courseTimeChartScale(days.slice(0, 2), 'activeMs'), null);
  assert.deepEqual(courseTimeChartScale(days, 'activeMs'), {maxMs: 300000, ticks: ['5 мин', '2 мин 30 с', '0']});
  assert.deepEqual(courseTimeChartScale(days, 'theoryMs'), {maxMs: 240000, ticks: ['4 мин', '2 мин', '0']});
  assert.deepEqual(courseTimeChartScale(days, 'practiceMs'), {maxMs: 120000, ticks: ['2 мин', '1 мин', '0']});
});

test('course chart labels distinguish seconds and subsecond times and support hours', () => {
  const scale = activeMs => courseTimeChartScale([{activeMs, theoryMs: null, practiceMs: null}], 'activeMs');
  assert.deepEqual(scale(45842), {maxMs: 45842, ticks: ['46 с', '23 с', '0']});
  assert.deepEqual(scale(1000), {maxMs: 1000, ticks: ['1 с', '500 мс', '0']});
  assert.deepEqual(scale(1), {maxMs: 1, ticks: ['1 мс', '0,5 мс', '0']});
  assert.deepEqual(scale(7500000), {maxMs: 7500000, ticks: ['2 ч 5 мин', '1 ч 2 мин 30 с', '0']});
});

test('snapshots reject mismatched course and range, preserving unknown values', () => {
  const dates = periodDates(7, '2027-01-01');
  const empty = emptyCourseSnapshot('course', dates);
  assert.equal(empty.quality, 'unknown');
  assert.equal(empty.days.length, 7);
  assert.ok(empty.days.every(day => day.activeMs === null));
  const input = {...empty, quality: 'complete', totals: {activeMs: 0, theoryMs: -1, practiceMs: Infinity},
    days: [{date: dates[0], activeMs: 0, theoryMs: NaN, practiceMs: 5}],
    lessons: [{lessonId: 'one', activeMs: 0, theoryMs: -2, practiceMs: 2}]};
  assert.deepEqual(acceptCourseSnapshot(input, 'other', dates), emptyCourseSnapshot('other', dates));
  assert.deepEqual(acceptCourseSnapshot({...input, from: '2020-01-01'}, 'course', dates), empty);
  const accepted = acceptCourseSnapshot(input, 'course', dates);
  assert.deepEqual(accepted.totals, {activeMs: 0, theoryMs: null, practiceMs: null});
  assert.equal(accepted.days[0].activeMs, 0);
  assert.equal(accepted.days[0].theoryMs, null);
  assert.equal(accepted.days[1].activeMs, null);
  assert.equal(accepted.lessons[0].theoryMs, null);
  assert.equal(input.totals.theoryMs, -1);
});

test('catalog duplicates retain first occurrence and order', () => {
  const chapters = [{id: 'ch1', title: 'Chapter', blocks: [{id: 'b1', title: 'Block', lessons: [
    {id: 'a', title: 'First', completed: true, completedOn: '2026-11-19'}, {id: 'b', title: 'Second', completed: false},
  ]}, {id: 'b2', title: 'Other', lessons: [{id: 'a', title: 'Duplicate', completed: false}]}]}];
  assert.deepEqual(flattenCourseLessons(chapters), [
    {id: 'a', trackId: 'ch1', blockId: 'b1', chapterTitle: 'Chapter', blockTitle: 'Block', title: 'First', completed: true, completedOn: '2026-11-19'},
    {id: 'b', trackId: 'ch1', blockId: 'b1', chapterTitle: 'Chapter', blockTitle: 'Block', title: 'Second', completed: false, completedOn: undefined},
  ]);
});

test('zero from the report survives normalization with unknown or partial coverage', () => {
  const dates = periodDates(7, '2027-01-01');
  const values = {activeMs: 60000, theoryMs: 60000, practiceMs: 0};
  const source = {...emptyCourseSnapshot('course', dates), totals: values,
    days: [{date: dates[0], ...values}], lessons: [{lessonId: 'one', ...values}]};
  for (const quality of ['unknown', 'partial']) {
    const result = acceptCourseSnapshot({...source, quality}, 'course', dates);
    for (const item of [result.totals, result.days[0], result.lessons[0]]) {
      assert.equal(item.activeMs, 60000);
      assert.equal(item.practiceMs, 0);
      assert.equal(item.theoryMs, 60000);
    }
    assert.equal(result.days[1].activeMs, null);
    assert.equal(result.quality, quality);
  }
});

test('week and month summaries count positive observed dates and average over those dates', () => {
  const durations = [240000, 120000, 300000, 180000, 240000, 0, 39118];
  for (const period of [7, 30]) {
    const dates = periodDates(period, '2026-11-19');
    const zero = {activeMs: 0, theoryMs: 0, practiceMs: 0};
    const empty = emptyCourseSnapshot('course', dates);
    const days = dates.map((date, index) => {
      const activeMs = durations[index + 7 - period] ?? 0;
      return {date, activeMs, theoryMs: activeMs, practiceMs: 0};
    });
    const totalMs = durations.reduce((sum, day) => sum + day, 0);
    assert.deepEqual(summarizeCourseTime({...empty, days, totals: {activeMs: totalMs, theoryMs: totalMs, practiceMs: 0}}),
      {activeDays: 6, averageMs: totalMs / 6});
    assert.deepEqual(summarizeCourseTime({...empty, totals: zero, days: dates.map(date => ({date, ...zero}))}),
      {activeDays: 0, averageMs: 0});
    assert.deepEqual(summarizeCourseTime(empty), {activeDays: null, averageMs: null});
  }
});
