// Run current app, shared UI and registered course browser regressions in isolation.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { discoverCourses } from "./course-registry.mjs";

const app = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const root = path.resolve(app, "../..");
const tests = new Set();
function collect(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory() && !["node_modules", ".cache", ".local", "dist"].includes(entry.name)) collect(full);
    else if (entry.isFile() && /\.test\.(mjs|cjs)$/.test(entry.name)) tests.add(full);
  }
}
for (const directory of [path.join(app, "src"), path.join(app, "tools"), path.join(root, "courses/shared/ui"), path.join(root, "courses/shared/demo"), ...discoverCourses().map(course => course.frontendDir)]) collect(directory);
if (!tests.size) throw new Error("No browser regression tests discovered");
for (const args of [
  ["--test", "--test-concurrency=1", ...[...tests].sort()],
  [path.join(root, "tooling/courses/test_course_ui.cjs")],
  [path.join(root, "tooling/courses/test_lesson_structure.cjs")],
  [path.join(root, "tooling/courses/test_lesson_visuals.cjs")],
  [path.join(app, "tests/check_candidate_types.mjs")],
  [path.join(app, "tests/test_task_result_render.mjs")],
]) {
  const result = spawnSync(process.execPath, args, { cwd: app, stdio: "inherit" });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
