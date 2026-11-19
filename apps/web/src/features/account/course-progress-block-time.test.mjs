import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import ts from 'typescript';

const source = fs.readFileSync(new URL('./course-progress-block-time.ts', import.meta.url), 'utf8');
const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
const module = {exports: {}};
new Function('module', 'exports', code)(module, module.exports);
const {sumBlockTime} = module.exports;

const row = (lessonId, activeMs, theoryMs, practiceMs) => ({lessonId, activeMs, theoryMs, practiceMs});
const times = new Map([
  ['a', row('a', 100, 30, 70)],
  ['b', row('b', 200, 80, 120)],
  ['zero', row('zero', 0, 0, 0)],
  ['partial', row('partial', 20, null, 10)],
]);

test('sums each unique lesson and preserves observed zero', () => {
  assert.equal(sumBlockTime(['a', 'b'], times, 'activeMs'), 300);
  assert.equal(sumBlockTime(['a', 'a', 'b'], times, 'activeMs', 2), 300);
  assert.equal(sumBlockTime(['zero'], times, 'activeMs'), 0);
});

test('returns unknown for missing, invalid or incomplete data', () => {
  assert.equal(sumBlockTime([], times, 'activeMs'), null);
  assert.equal(sumBlockTime(['a', 'missing'], times, 'activeMs'), null);
  assert.equal(sumBlockTime(['partial'], times, 'theoryMs'), null);
  assert.equal(sumBlockTime(['a', 'a'], times, 'activeMs', 2), null);
  for (const invalid of [NaN, Infinity, -1]) {
    assert.equal(sumBlockTime(['bad'], new Map([['bad', row('bad', invalid, 0, 0)]]), 'activeMs'), null);
  }
});

test('uses only the selected metric', () => {
  assert.equal(sumBlockTime(['a', 'b'], times, 'theoryMs'), 110);
  assert.equal(sumBlockTime(['a', 'b'], times, 'practiceMs'), 190);
  assert.equal(sumBlockTime(['partial'], times, 'practiceMs'), 10);
  assert.equal(sumBlockTime(['partial'], times, 'theoryMs'), null);
});

test('omitted rows are zero only when the entire breakdown and block membership are available', () => {
  assert.equal(sumBlockTime(['a', 'missing'], times, 'activeMs', 2, true), 100);
  assert.equal(sumBlockTime(['missing'], times, 'practiceMs', 1, true), 0);
  assert.equal(sumBlockTime(['missing'], times, 'practiceMs', 1), null);
  assert.equal(sumBlockTime(['missing'], times, 'practiceMs', 2, true), null);
  assert.equal(sumBlockTime(['partial'], times, 'theoryMs', 1, true), null);
});
