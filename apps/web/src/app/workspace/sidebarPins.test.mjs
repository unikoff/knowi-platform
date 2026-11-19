import assert from "node:assert/strict";
import test from "node:test";
import { isSidebarToolPinned, readSidebarPins, sidebarPinsKey, toggleSidebarPin, writeSidebarPins } from "./sidebarPins.ts";

function memoryStorage() {
  const values = new Map();
  return {
    values,
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
  };
}

test("pins are isolated by owner and shared across courses and viewport sizes", () => {
  const storage = memoryStorage();
  assert.equal(writeSidebarPins("student:12", ["interpreter"], storage), true);
  assert.deepEqual(readSidebarPins("student:12", ["theory"], storage), ["interpreter"]);
  assert.deepEqual(readSidebarPins("student:13", ["theory"], storage), ["theory"]);
  assert.equal(sidebarPinsKey("student:12"), "pymentor:sidebar-pins:v1:student%3A12");
  assert.equal(storage.values.size, 1);
});

test("missing or malformed preferences fall back to unique defaults", () => {
  const storage = memoryStorage();
  assert.deepEqual(readSidebarPins("student", ["lessons", "lessons", "theory"], storage), ["lessons", "theory"]);
  storage.setItem(sidebarPinsKey("student"), "{");
  assert.deepEqual(readSidebarPins("student", ["lessons", "theory"], storage), ["lessons", "theory"]);
  storage.setItem(sidebarPinsKey("student"), JSON.stringify({ version: 2, pins: ["interpreter"] }));
  assert.deepEqual(readSidebarPins("student", ["lessons"], storage), ["lessons"]);
  storage.setItem(sidebarPinsKey("student"), JSON.stringify({ version: 1, pins: ["interpreter", null] }));
  assert.deepEqual(readSidebarPins("student", ["lessons"], storage), ["lessons"]);
});

test("an intentionally empty selection is distinct from missing preferences", () => {
  const storage = memoryStorage();
  assert.equal(writeSidebarPins("student", [], storage), true);
  assert.deepEqual(readSidebarPins("student", ["lessons", "theory"], storage), []);
});

test("required tools remain pinned even in older empty or incomplete preferences", () => {
  for (const id of ["lessons", "theory", "practice", "admin", "account"]) {
    assert.equal(isSidebarToolPinned(id, []), true);
    assert.deepEqual(toggleSidebarPin(id, ["interpreter"]), ["interpreter"]);
  }
  assert.equal(isSidebarToolPinned("interpreter", []), false);
  assert.deepEqual(toggleSidebarPin("interpreter", []), ["interpreter"]);
  assert.deepEqual(toggleSidebarPin("interpreter", ["interpreter", "admin"]), ["admin"]);
});

test("unknown saved IDs survive deduplication for tools that may return later", () => {
  const storage = memoryStorage();
  storage.setItem(sidebarPinsKey("student"), JSON.stringify({ version: 1, pins: ["future-tool", "theory", "future-tool"] }));
  const pins = readSidebarPins("student", ["lessons"], storage);
  assert.deepEqual(pins, ["future-tool", "theory"]);
  assert.equal(writeSidebarPins("student", pins, storage), true);
  assert.deepEqual(readSidebarPins("student", [], storage), ["future-tool", "theory"]);
});

test("storage access failures safely use defaults and report unsuccessful writes", () => {
  const storage = {
    getItem() { throw new Error("blocked"); },
    setItem() { throw new Error("blocked"); },
  };
  assert.deepEqual(readSidebarPins("student", ["lessons"], storage), ["lessons"]);
  assert.equal(writeSidebarPins("student", ["theory"], storage), false);
  assert.deepEqual(readSidebarPins("student", ["lessons"], null), ["lessons"]);
  assert.equal(writeSidebarPins("student", ["theory"], null), false);
});
