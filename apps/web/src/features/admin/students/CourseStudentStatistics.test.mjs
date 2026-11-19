import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "vite";

const vite = await createServer({configFile: false, optimizeDeps: {noDiscovery: true, include: []}, server: {middlewareMode: true, ws: false}});
const { fetchStudentStatisticsCatalog } = await vite.ssrLoadModule("/src/features/admin/students/student-statistics-api.ts");
test.after(() => vite.close());

const courseId = "backend";
const actorRef = "01234567-89ab-4cde-8fab-0123456789ab";
const target = {id: 7, account_ref: "11111111-2222-4333-8444-555555555555"};
const catalog = {
  tracks: [{id: "chapter-1", title: "Глава", lessons_completed: 1, lessons_countable: 1, blocks: [
    {id: "block-1", title: "Блок", completed: 1, total: 1, lesson_ids: ["lesson-1"]},
  ]}],
  courses: {"chapter-1": [
    {id: "lesson-1", title: "Урок", completed: true, completed_on: "2026-11-19", counts_toward_statistics: true},
  ]},
};
const payload = {course_id: courseId, user_id: target.id, account_ref: target.account_ref, ...catalog};
const response = (data, status = 200) => new Response(JSON.stringify(data), {status, headers: {"Content-Type": "application/json"}});

test("the progress read includes both identities and fetches a fresh catalog on every open", async t => {
  const mock = t.mock.method(globalThis, "fetch", async () => response(payload));
  for (let i = 0; i < 2; i += 1) {
    const signal = new AbortController().signal;
    assert.deepEqual(await fetchStudentStatisticsCatalog(courseId, target, actorRef, signal), catalog);
    const [url, options] = mock.mock.calls[i].arguments;
    assert.match(url, /\/api\/admin\/courses\/backend\/users\/7\/statistics$/);
    assert.equal(options.headers["X-Actor-Ref"], actorRef);
    assert.equal(options.headers["X-Account-Ref"], target.account_ref);
    assert.equal(options.cache, "no-store");
    assert.equal(options.credentials, "include");
    assert.equal(options.signal, signal);
  }
  assert.equal(mock.mock.callCount(), 2);
});

test("a reused ID or a response for another course cannot populate the selected profile", async t => {
  const mock = t.mock.method(globalThis, "fetch", async () => response(payload));
  for (const mismatch of [{course_id: "another-course"}, {user_id: target.id + 1}, {account_ref: actorRef}]) {
    mock.mock.mockImplementationOnce(async () => response({...payload, ...mismatch}));
    await assert.rejects(fetchStudentStatisticsCatalog(courseId, target, actorRef, new AbortController().signal), /некорректный ответ/);
  }
});

test("a closed profile ignores late JSON and an expired search selection is recognised", async t => {
  const controller = new AbortController();
  const mock = t.mock.method(globalThis, "fetch", async () => response({detail: "Select an account through course search"}, 403));
  mock.mock.mockImplementationOnce(async () => ({
    ok: true, status: 200, headers: new Headers({"Content-Type": "application/json"}),
    json: async () => {controller.abort(); return payload;},
  }));
  await assert.rejects(fetchStudentStatisticsCatalog(courseId, target, actorRef, controller.signal), {name: "AbortError"});
  await assert.rejects(fetchStudentStatisticsCatalog(courseId, target, actorRef, new AbortController().signal), {status: 403, studentSearchRequired: true});
});
