import test from "node:test";
import assert from "node:assert/strict";
import { MAX_DOCUMENT_BYTES, parseCourseDocument } from "./course-document.mjs";

test("ordinary YAML and JSON produce the same data, using Core's YAML 1.1 booleans", () => {
  const expected = { schema_version: 1, courses: [{ course_id: "sample", enabled: true, manifest: "sample/course.yaml" }] };
  assert.deepEqual(parseCourseDocument(JSON.stringify(expected)), expected);
  assert.deepEqual(parseCourseDocument("# creator registry\nschema_version: 1\ncourses:\n  - course_id: sample\n    enabled: yes\n    manifest: sample/course.yaml\n"), expected);
});

test("safe parsing rejects aliases, duplicate keys, custom tags and multiple documents", () => {
  for (const text of ["a: &value 1\nb: *value", "a: 1\na: 2", "a: !unsafe value", "a: !!python/object:example {}", "a: 1\n---\na: 2"]) {
    assert.throws(() => parseCourseDocument(text));
  }
});

test("size, depth, object root and string-key limits match the registry contract", () => {
  assert.throws(() => parseCourseDocument("a: " + "x".repeat(MAX_DOCUMENT_BYTES)), /bytes/);
  assert.throws(() => parseCourseDocument('{"a":'.repeat(34) + "0" + "}".repeat(34)), /nesting/);
  assert.throws(() => parseCourseDocument("- one\n- two"), /root/);
  assert.throws(() => parseCourseDocument("true: one"), /keys/);
});

test("safe inline merges preserve Core's duplicate rejection", () => {
  assert.deepEqual(parseCourseDocument("<<: {schema_version: 1}\ncourses: []"), { schema_version: 1, courses: [] });
  assert.throws(() => parseCourseDocument("<<: {schema_version: 1}\nschema_version: 2"), /Duplicate/);
});
