import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import { createServer } from "vite";
import { courseFrontendWatch } from "./course-frontend-watch.mjs";

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForMap(server, expected) {
  const deadline = Date.now() + 4000;
  while (Date.now() < deadline) {
    const result = await server.transformRequest("/entry.js");
    if (result.code.includes("Lesson81.tsx") === expected) return;
    await delay(100);
  }
  assert.fail(`glob did not ${expected ? "add" : "remove"} Lesson81.tsx`);
}

test("external course glob updates after lesson creation and deletion", async () => {
  const fixture = await mkdtemp(join(tmpdir(), "pymentor-course-watch-"));
  let server;
  try {
    const root = join(fixture, "web");
    const course = join(fixture, "course", "frontend");
    await mkdir(root, { recursive: true });
    await mkdir(course, { recursive: true });
    const glob = relative(root, course).replaceAll("\\", "/");
    await writeFile(join(root, "entry.js"), `export const pages = import.meta.glob(${JSON.stringify(`${glob}/*.tsx`)});`);
    server = await createServer({ configFile: false, root, logLevel: "silent", server: { port: 0, host: "127.0.0.1", fs: { allow: [root, course] } }, plugins: [courseFrontendWatch([course])] });
    await server.listen();
    await waitForMap(server, false);
    await delay(500);
    const lesson = join(course, "Lesson81.tsx");
    await writeFile(lesson, "export default function Lesson81() { return null; }");
    await waitForMap(server, true);
    await rm(lesson);
    await waitForMap(server, false);
  } finally {
    await server?.close();
    await rm(fixture, { recursive: true, force: true });
  }
});
