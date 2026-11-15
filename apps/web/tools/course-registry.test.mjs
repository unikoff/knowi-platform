import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { stringify } from "yaml";
import { discoverCourses } from "./course-registry.mjs";

function fixture(fn) {
  const dir = fs.mkdtempSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "registry-test-"));
  try {
    const records = [];
    for (const id of ["first", "second"]) {
      const frontend = path.join(dir, id, "frontend");
      fs.mkdirSync(frontend, { recursive: true });
      for (const name of ["course.ts", "personal.ts", "about.ts"]) fs.writeFileSync(path.join(frontend, name), "");
      fs.writeFileSync(path.join(dir, id, "course.yaml"), JSON.stringify({ course_id: id, slug: id === "first" ? "first-course" : id, frontend: { course: "frontend/course.ts", personal_landing: "frontend/personal.ts", course_landing: "frontend/about.ts" } }));
      records.push({ course_id: id, enabled: id === "first", manifest: `${id}/course.yaml` });
    }
    const save = () => fs.writeFileSync(path.join(dir, "registry.json"), JSON.stringify({ schema_version: 1, courses: records }));
    save();
    fn(dir, records, save);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
}
test("only enabled registered courses are discoverable", () => fixture((dir) => {
  assert.deepEqual(discoverCourses(dir).map(({ id, slug }) => ({ id, slug })), [{ id: "first", slug: "first-course" }]);
}));
test("duplicate IDs and path traversal fail closed", () => fixture((dir, records, save) => {
  records.push({ ...records[0] }); save(); assert.throws(() => discoverCourses(dir), /duplicate/);
  records.pop(); records[0].manifest = "../first/course.yaml"; save(); assert.throws(() => discoverCourses(dir), /Unsafe/);
}));
test("frontend entries cannot expose backend", () => fixture((dir) => {
  const file = path.join(dir, "first", "course.yaml");
  const manifest = JSON.parse(fs.readFileSync(file, "utf8"));
  manifest.frontend.course = "backend/private.ts";
  fs.writeFileSync(file, JSON.stringify(manifest));
  assert.throws(() => discoverCourses(dir), /frontend entry/);
}));

test("duplicate and reserved slugs fail closed", () => fixture((dir) => {
  const first = path.join(dir, "first", "course.yaml"), second = path.join(dir, "second", "course.yaml");
  const one = JSON.parse(fs.readFileSync(first, "utf8")), two = JSON.parse(fs.readFileSync(second, "utf8"));
  two.slug = one.slug; fs.writeFileSync(second, JSON.stringify(two));
  assert.throws(() => discoverCourses(dir), /slug/);
  for (const reserved of ["shared", "api", "assets", "devtools", "src", "node_modules", "public", "config", "tools", "authoring", "preview-loading", "platform", "courses"]) {
    two.slug = reserved; fs.writeFileSync(second, JSON.stringify(two));
    assert.throws(() => discoverCourses(dir), /slug/);
  }
}));

test("different course ID and slug round-trip through routes", async () => {
  const dir = fs.mkdtempSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "route-test-"));
  try {
    fs.mkdirSync(path.join(dir, "src", "shared"), { recursive: true });
    fs.mkdirSync(path.join(dir, ".cache", "course-registry"), { recursive: true });
    fs.copyFileSync(new URL("../src/shared/course-paths.ts", import.meta.url), path.join(dir, "src", "shared", "course-paths.ts"));
    fs.writeFileSync(path.join(dir, ".cache", "course-registry", "course-ids.ts"), 'export const registeredCourses = [{ id: "isolation_fixture", slug: "isolation-fixture" }]; export const courseIds = registeredCourses.map(course => course.id);');
    const routes = await import(pathToFileURL(path.join(dir, "src", "shared", "course-paths.ts")).href);
    for (const base of ["/", "/platform/"]) {
      assert.equal(routes.courseUrls(base, "isolation_fixture").learning, `${base}isolation-fixture`);
      assert.deepEqual(routes.resolveCourseRoute(`${base}isolation-fixture`, base), { kind: "learning", courseId: "isolation_fixture", legacy: false });
      assert.deepEqual(routes.resolveCourseRoute(`${base}isolation_fixture`, base), { kind: "not-found" });
      assert.deepEqual(routes.resolveCourseRoute(`${base}unknown`, base), { kind: "not-found" });
    }
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test("registry and manifest accept ordinary safe YAML", () => fixture((dir) => {
  for (const relative of ["registry.json", "first/course.yaml", "second/course.yaml"]) {
    const file = path.join(dir, relative);
    fs.writeFileSync(file, "# ordinary YAML\n" + stringify(JSON.parse(fs.readFileSync(file, "utf8"))));
  }
  assert.deepEqual(discoverCourses(dir).map(course => course.slug), ["first-course"]);
}));

test("frontend paths reject Windows aliases and symlinked directories", () => fixture((dir) => {
  const file = path.join(dir, "first", "course.yaml");
  const manifest = JSON.parse(fs.readFileSync(file, "utf8"));
  for (const value of ["frontend/course.ts:private", "frontend/NUL.ts", "frontend/sub /course.ts", "frontend/../backend/private.ts"]) {
    manifest.frontend.course = value;
    fs.writeFileSync(file, JSON.stringify(manifest));
    assert.throws(() => discoverCourses(dir), /Unsafe|Invalid frontend entry/);
  }
  manifest.frontend.course = "frontend/course.ts";
  fs.writeFileSync(file, JSON.stringify(manifest));
  const frontend = path.join(dir, "first", "frontend");
  const renamed = path.join(dir, "first", "browser-files");
  fs.renameSync(frontend, renamed);
  fs.symlinkSync(renamed, frontend, process.platform === "win32" ? "junction" : "dir");
  assert.throws(() => discoverCourses(dir), /Symlink/);
}));
