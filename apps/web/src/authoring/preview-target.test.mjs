import test from "node:test";
import assert from "node:assert/strict";
import { previewTarget } from "./preview-target.ts";

test("local preview accepts a new page without a catalog entry", () => {
  assert.equal(previewTarget("http://127.0.0.1:5173", true, "chapter_05/block_17/Lesson81_1"), "chapter_05/block_17/Lesson81_1");
  assert.equal(previewTarget("http://127.0.0.1:5173", true, null), "examples");
});

test("production and other origins cannot open the authoring preview", () => {
  for (const origin of ["https://pymentor.ru", "http://localhost:5173", "http://127.0.0.1:4173"]) {
    assert.throws(() => previewTarget(origin, true, "examples"));
  }
  assert.throws(() => previewTarget("http://127.0.0.1:5173", false, "examples"));
});

test("only a local lesson module path is accepted", () => {
  for (const value of ["../App", "chapter_01/../App", "chapter_01/block_01/Lesson01.tsx", "https://example.com/evil", "chapter_01/block_01/Lesson01?raw", "chapter_01\\block_01\\Lesson01"]) {
    assert.throws(() => previewTarget("http://127.0.0.1:5173", true, value));
  }
});
