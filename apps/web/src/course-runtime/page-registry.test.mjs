import test from "node:test";
import assert from "node:assert/strict";
import { createLessonPageRegistry } from "./page-registry.ts";

test("a missing lesson does not prevent other lessons from registering", () => {
  const definePages = createLessonPageRegistry({
    "./chapter_01/block_01/Lesson02.tsx": async () => ({ Lesson02: () => null }),
  });
  const pages = definePages("chapter_01", {
    "block_01/02.md": "block_01/Lesson02",
    "block_01/81.md": "block_17/Lesson81",
  });
  assert.ok(pages["chapter_01/block_01/02.md"]);
  assert.ok(pages["chapter_01/block_01/81.md"]);
});
