import test from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { getLessonMetadata, getLessonTags } from "./metadata.ts";

test("a lesson path supplies its block and fractional number without authored labels", () => {
  assert.deepEqual(getLessonMetadata("chapter_05/block_17/Lesson81_1"), {
    block: "Блок 17 Сохраняем данные и готовим учётные записи", blockNumber: 17, lessonNumber: "81.1",
    tags: ["SQLite", "миграции", "пользователи"],
  });
});

test("catalog block titles take precedence over preview defaults and normalize separators", () => {
  assert.equal(getLessonMetadata("chapter_01/block_01/Lesson01", "Блок 1. Первые шаги в Python").block,
    "Блок 1 Первые шаги в Python");
  assert.equal(getLessonMetadata("chapter_05/block_17/Lesson81_1", "Блок 17 — Миграции базы (6 занятий)").block,
    "Блок 17 Миграции базы");
});

test("page numbering wins over legacy filenames, and roadmaps have no lesson number", () => {
  assert.equal(getLessonMetadata("chapter_03/block_10/Lesson48_1").lessonNumber, "48.1");
  assert.equal(getLessonMetadata("chapter_05/block_17/81.1 - Сверяем реальную схему.md").lessonNumber, "81.1");
  assert.equal(getLessonMetadata("chapter_05/block_00/LearningRoadmap"), null);
  assert.equal(getLessonMetadata("examples"), null);
  assert.equal(getLessonMetadata("chapter_03/block_10/Lesson54").lessonNumber, "48.1");
  assert.equal(getLessonMetadata("chapter_03/block_11/Lesson57").lessonNumber, "51.1");
  assert.equal(getLessonMetadata("chapter_05/block_17/Lesson81_1", undefined, "81.1. Сверяем реальную схему").lessonNumber, "81.1");
});

test("tags follow a changed lesson title automatically without generic filler or duplicates", () => {
  const schema = getLessonTags("Сверяем реальную схему", 17);
  const backup = getLessonTags("Проверяем восстановление", 17);
  assert.ok(schema.includes("схема базы"));
  assert.ok(backup.includes("восстановление"));
  assert.notDeepEqual(schema, backup);
  for (const tags of [schema, backup, getLessonTags("Новая тема", 17)]) {
    assert.ok(tags.length >= 2 && tags.length <= 3);
    assert.equal(new Set(tags).size, tags.length);
  }
});

test("specific title topics are selected ahead of block defaults", () => {
  const tags = getLessonTags("GitHub Actions, CI и первый деплой", 32);
  assert.ok(tags.includes("GitHub Actions"));
  assert.ok(tags.includes("CI/CD"));
  assert.ok(tags.includes("деплой"));
});

test("database queries do not receive an HTTP tag", () => {
  const tags = getLessonTags("SQL-запросы и планы запросов", 24);
  assert.ok(tags.includes("SQL"));
  assert.ok(!tags.includes("HTTP"));
});

test("every registered active lesson gets a block name, number and topics", (t) => {
  let checked = 0;
  for (const chapter of readdirSync(new URL(".", import.meta.url))) {
    if (!/^chapter_\d+$/.test(chapter)) continue;
    const manifest = readFileSync(new URL(`${chapter}/index.ts`, import.meta.url), "utf8");
    for (const [, page] of manifest.matchAll(/:\s*"(block_\d+\/Lesson[\d_]+)"/g)) {
      const metadata = getLessonMetadata(`${chapter}/${page}`);
      assert.match(metadata?.block ?? "", /^Блок \d+ \S/, page);
      assert.match(metadata.lessonNumber, /^\d+(?:\.\d+)*$/);
      assert.ok(getLessonTags("", metadata.blockNumber).length >= 2, page);
      checked++;
    }
  }
  assert.ok(checked > 200, `Only ${checked} lessons were checked`);
  t.diagnostic(`Checked ${checked} registered lesson entries across all active chapters`);
});
