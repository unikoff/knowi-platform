import assert from "node:assert/strict";
import test from "node:test";
import { learningTimeScale } from "./learningTimeScale.ts";

test("daily learning goal reaches the accent at 4h and white at 8h", () => {
  for (const [hours, fillPercent, whitePercent] of [
    [0, 0, 0], [2, 50, 0], [4, 100, 0], [6, 100, 50], [8, 100, 100], [12, 100, 100],
  ]) {
    assert.deepEqual(learningTimeScale(hours * 60), { fillPercent, whitePercent }, `${hours}h`);
  }
});

test("missing or invalid measurements never become recorded zero minutes", () => {
  for (const value of [null, -1, NaN, Infinity, -Infinity]) assert.equal(learningTimeScale(value), null);
  assert.notEqual(learningTimeScale(0), null);
});

test("colour remains continuous immediately around the daily goal and cap", () => {
  assert.ok(learningTimeScale(239).fillPercent < 100);
  assert.equal(learningTimeScale(239).whitePercent, 0);
  assert.equal(learningTimeScale(241).fillPercent, 100);
  assert.ok(learningTimeScale(241).whitePercent > 0);
  assert.ok(learningTimeScale(479).whitePercent < 100);
  assert.deepEqual(learningTimeScale(481), learningTimeScale(480));
});
