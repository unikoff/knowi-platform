import assert from "node:assert/strict";
import test from "node:test";
import { getGenericLessonMetadata, getLessonTags } from "./metadata.ts";

test("generic metadata uses only the source, module label and displayed lesson number", () => {
  assert.deepEqual(getGenericLessonMetadata("chapter_03/block_07/Lesson18_2", "Блок 7 · Общая тема", "18.2. Проверка"), {
    block: "Блок 7 Общая тема",
    blockNumber: 7,
    lessonNumber: "18.2",
  });
});

test("generic metadata does not infer course identity for non-lesson paths", () => {
  assert.equal(getGenericLessonMetadata("chapter_03/block_00/LearningRoadmap"), null);
  assert.equal(getGenericLessonMetadata("examples"), null);
  assert.equal(getGenericLessonMetadata("chapter_01/block_01/Lesson01", "Блок 2. Другая тема").block, "Блок 1");
});

test("generic tags avoid block-specific course defaults", () => {
  assert.deepEqual(getLessonTags(), ["тема занятия", "практика"]);
});
