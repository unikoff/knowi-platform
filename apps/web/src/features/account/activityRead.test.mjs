import assert from "node:assert/strict";
import test from "node:test";
import { createActivityRead } from "./activityRead.ts";

const failure = (status) => Object.assign(new Error("synthetic"), { status });
const retryable = (error) => error instanceof Error && error.status === 503;
const flush = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); };
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

test("503 retries once after 250ms and applies the recovered GET", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let calls = 0;
  const applied = [], errors = [];
  const read = createActivityRead({
    request: async () => { calls++; if (calls === 1) throw failure(503); return "recovered"; },
    isCurrent: () => true, isRetryable: retryable,
    onSuccess: (value) => applied.push(value), onError: (error) => errors.push(error),
  });
  read.load(); await flush();
  assert.equal(calls, 1);
  t.mock.timers.tick(249); await flush(); assert.equal(calls, 1);
  t.mock.timers.tick(1); await flush();
  assert.equal(calls, 2); assert.deepEqual(applied, ["recovered"]); assert.deepEqual(errors, []);
  read.dispose();
});

test("persistent 503 has exactly seven attempts and bounded delays", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let calls = 0;
  const errors = [];
  const read = createActivityRead({
    request: async () => { calls++; throw failure(503); }, isCurrent: () => true,
    isRetryable: retryable, onSuccess: () => assert.fail("unexpected success"), onError: (error) => errors.push(error),
  });
  read.load(); await flush();
  for (const delay of [250, 500, 1000, 2000, 4000, 8000]) { t.mock.timers.tick(delay - 1); await flush(); const before = calls; t.mock.timers.tick(1); await flush(); assert.equal(calls, before + 1); }
  assert.equal(calls, 7); assert.equal(errors.length, 1); assert.equal(errors[0].status, 503);
  t.mock.timers.tick(30000); await flush(); assert.equal(calls, 7);
  read.dispose();
});

test("503 beyond the former 1.75s window can recover", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let calls = 0;
  const values = [];
  const read = createActivityRead({
    request: async () => { calls++; if (calls < 5) throw failure(503); return "ready"; },
    isCurrent: () => true, isRetryable: retryable,
    onSuccess: (value) => values.push(value), onError: () => assert.fail("unexpected error"),
  });
  read.load(); await flush();
  for (const delay of [250, 500, 1000, 2000]) { t.mock.timers.tick(delay); await flush(); }
  assert.equal(calls, 5); assert.deepEqual(values, ["ready"]); read.dispose();
});

for (const status of [401, 403, 500]) {
  test(`${status} is reported without a retry`, async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    let calls = 0, error;
    const read = createActivityRead({ request: async () => { calls++; throw failure(status); },
      isCurrent: () => true, isRetryable: retryable, onSuccess: () => assert.fail("unexpected success"),
      onError: (value) => { error = value; } });
    read.load(); await flush(); t.mock.timers.tick(10000); await flush();
    assert.equal(calls, 1); assert.equal(error.status, status); read.dispose();
  });
}

test("cleanup or scope change while waiting prevents another request", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  for (const mode of ["cleanup", "scope"]) {
    let calls = 0, current = true;
    const read = createActivityRead({ request: async () => { calls++; throw failure(503); },
      isCurrent: () => current, isRetryable: retryable, onSuccess: () => assert.fail("stale success"),
      onError: () => assert.fail("stale error") });
    read.load(); await flush();
    if (mode === "cleanup") read.dispose(); else current = false;
    t.mock.timers.tick(10000); await flush(); assert.equal(calls, 1); read.dispose();
  }
});

test("a newer refresh cancels the older retry timer", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let calls = 0;
  const values = [];
  const read = createActivityRead({
    request: async () => { calls++; if (calls === 1) throw failure(503); return "new"; },
    isCurrent: () => true, isRetryable: retryable, onSuccess: (value) => values.push(value),
    onError: () => assert.fail("stale error"),
  });
  read.load(); await flush(); read.load(); await flush();
  t.mock.timers.tick(10000); await flush();
  assert.equal(calls, 2); assert.deepEqual(values, ["new"]); read.dispose();
});

for (const oldResult of ["success", "failure"]) {
  test(`an older ${oldResult} cannot overwrite a newer event refresh`, async () => {
    const requests = [], states = [];
    const read = createActivityRead({ request: () => { const item = deferred(); requests.push(item); return item.promise; },
      isCurrent: () => true, isRetryable: retryable, onSuccess: (value) => states.push(value),
      onError: () => states.push("error") });
    read.load(); read.load();
    requests[1].resolve("new"); await flush();
    if (oldResult === "success") requests[0].resolve("old"); else requests[0].reject(failure(503));
    await flush(); assert.deepEqual(states, ["new"]); read.dispose();
  });
  test(`an older ${oldResult} cannot overwrite a new user or month scope`, async () => {
    const requests = [], states = [];
    let scope = "old";
    const makeRead = (expected) => createActivityRead({
      request: () => { const item = deferred(); requests.push(item); return item.promise; },
      isCurrent: () => scope === expected, isRetryable: retryable,
      onSuccess: (value) => states.push(value), onError: () => states.push("error"),
    });
    const old = makeRead("old"); old.load(); scope = "new";
    const current = makeRead("new"); current.load(); requests[1].resolve("new"); await flush();
    if (oldResult === "success") requests[0].resolve("old"); else requests[0].reject(failure(503));
    await flush(); assert.deepEqual(states, ["new"]); old.dispose(); current.dispose();
  });
}
