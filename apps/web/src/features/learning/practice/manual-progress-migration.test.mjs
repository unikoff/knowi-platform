import assert from "node:assert/strict";
import test from "node:test";
import { lesson70PreviousSignature } from "./manual-progress-migration.ts";

test("only the repaired third task can reuse its original progress signature", () => {
  const practice = [{ tasks: [
    { title: "Первый шаг", requirements: ["Проверить"] },
    { title: "Второй шаг", requirements: ["Проверить"] },
    { title: "Штатная сборка остаётся JSON", task: "Условие", requirements: ["Сохранить JSON"] },
  ] }];
  const signature = JSON.stringify(practice);
  const previous = lesson70PreviousSignature("lesson-c4878f92ca23a93b777d9fa8", signature);
  assert.ok(previous);
  const oldPractice = JSON.parse(previous);
  assert.equal("requirements" in oldPractice[0].tasks[2], false);
  assert.deepEqual(oldPractice[0].tasks.slice(0, 2), practice[0].tasks.slice(0, 2));
  assert.equal(lesson70PreviousSignature("another-lesson", signature), undefined);
  practice[0].tasks[2].title = "Другое задание";
  assert.equal(lesson70PreviousSignature("lesson-c4878f92ca23a93b777d9fa8", JSON.stringify(practice)), undefined);
});
