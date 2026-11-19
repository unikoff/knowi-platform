import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import test from "node:test";
import { createServer } from "vite";

test("unknown command outcome reuses a stored key and rejects altered authority or payload", async () => {
  const vite = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true } });
  const original = { fetch: globalThis.fetch, sessionStorage: globalThis.sessionStorage };
  const values = new Map();
  globalThis.sessionStorage = { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
  const seen = [];
  try {
    const { command } = await vite.ssrLoadModule("/src/features/admin/scopedApi.ts");
    globalThis.fetch = async (_url, init) => { seen.push(init.headers); throw new TypeError("connection lost"); };
    const path = "/admin/courses/backend/users/4/quota-grants";
    const body = { year: 2026, month: 11, add: 3, reason: "test" };
    await assert.rejects(command(path, body, "target-ref", undefined, "leader:7:backend"), /подтвердить/);
    assert.equal(seen.length, 0);
    await assert.rejects(command(path, body, "target-ref", "actor", "leader:7:backend"));
    assert.equal(values.size, 1);
    assert.doesNotMatch(JSON.stringify([...values]), /target-ref|reason|actor|backend/);
    await assert.rejects(command(path, { ...body, add: 4 }, "target-ref", "actor", "leader:7:backend"), /неизвестен/);
    await assert.rejects(command(path, body, "target-ref", "actor", "user:8:backend"), /неизвестен/);
    globalThis.fetch = async (_url, init) => { seen.push(init.headers); return new Response(JSON.stringify({detail: "forbidden"}), {status: 403, headers: { "Content-Type": "application/json" }}); };
    await assert.rejects(command(path, body, "target-ref", "actor", "leader:7:backend"), /forbidden/);
    assert.equal(values.size, 1); // Refusal after a lost response does not prove that the earlier action failed.
    const [storageKey, saved] = [...values][0];
    const expired = JSON.parse(saved); expired.createdAt = Date.now() - 25 * 60 * 60 * 1000;
    values.set(storageKey, JSON.stringify(expired));
    await assert.rejects(command(path, body, "target-ref", "actor", "leader:7:backend"), /истёк/);
    assert.equal(JSON.parse(values.get(storageKey)).key, undefined);
    values.set(storageKey, saved);
    globalThis.fetch = async (_url, init) => { seen.push(init.headers); return new Response("{}", { headers: { "Content-Type": "application/json" } }); };
    await command(path, body, "target-ref", "actor", "leader:7:backend");
    assert.equal(seen[0]["Idempotency-Key"], seen[2]["Idempotency-Key"]);
    assert.equal(seen[2]["X-Account-Ref"], "target-ref");
    assert.equal(values.size, 0);
  } finally {
    globalThis.fetch = original.fetch; globalThis.sessionStorage = original.sessionStorage;
    await vite.close();
  }
});

test("subscription mutations reconcile legacy paths and the shared index before sending, and replay the same key", async () => {
  const vite = await createServer({
    configFile: false,
    optimizeDeps: { noDiscovery: true, include: [] },
    server: { middlewareMode: true },
    plugins: [{
      name: "test-command-course-registry",
      enforce: "pre",
      resolveId: (id) => id === "../../../.cache/course-registry/course-ids" ? "\0test-command-course-registry" : null,
      load: (id) => id === "\0test-command-course-registry"
        ? 'export const allRegisteredCourseIds = ["backend", "disabled_course"];' : null,
    }],
  });
  const original = { fetch: globalThis.fetch, sessionStorage: globalThis.sessionStorage };
  const values = new Map();
  globalThis.sessionStorage = { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
  const requests = [];
  const base = "/admin/courses/backend/users/4";
  const path = `${base}/subscription-and-quota`;
  const quotaPath = `${base}/quota-grants`;
  const subscriptionPath = `${base}/subscription-extensions`;
  const disabledCourse = "/admin/courses/disabled_course/users/4";
  const removedCourse = "/admin/courses/removed_course/users/4/subscription-and-quota";
  const removedCourseQuota = "/admin/courses/removed_course/users/4/quota-grants";
  const platformExtension = "/admin-platform/users/4/subscription-extensions";
  const platformReset = "/admin-platform/users/4/subscription-reset";
  const body = { year: 2026, month: 11, add: 3, days: 20, reason: "Решение ведущего" };
  const target = "target-ref";
  const actor = "actor-ref";
  const authority = "leader:7:backend";
  try {
    const { command, acknowledgeReconciliation, ReconciliationRequired } = await vite.ssrLoadModule("/src/features/admin/scopedApi.ts");
    const send = (requestPath, payload = body) => command(requestPath, payload, target, actor, authority);
    globalThis.fetch = async (url, init) => { requests.push({url, ...init}); throw new TypeError("connection lost"); };
    const seedLegacyMarker = (requestPath) => {
      const digest = createHash("sha256").update(JSON.stringify([actor, requestPath, target])).digest("hex");
      const key = "pymentor:pending-command:v1:" + digest;
      values.set(key, JSON.stringify({fingerprint: digest}));
      return key;
    };
    let reconciliation;
    async function assertReconciliation(requestPath, expectedMarker) {
      const pendingBefore = [...values];
      const requestCount = requests.length;
      await assert.rejects(send(requestPath), error => {
        assert.ok(error instanceof ReconciliationRequired);
        assert.equal(error.operation, expectedMarker);
        reconciliation = error;
        return true;
      });
      assert.equal(requests.length, requestCount);
      assert.deepEqual([...values], pendingBefore);
    }
    for (const formerPath of [quotaPath, subscriptionPath, `${disabledCourse}/subscription-extensions`,
      `${disabledCourse}/subscription-and-quota`, platformExtension, platformReset]) {
      await assertReconciliation(path, seedLegacyMarker(formerPath));
      acknowledgeReconciliation(reconciliation);
    }
    await assertReconciliation(quotaPath, seedLegacyMarker(path));
    acknowledgeReconciliation(reconciliation);
    assert.equal(values.size, 0);

    const otherQuota = seedLegacyMarker(`${disabledCourse}/quota-grants`);
    globalThis.fetch = async (url, init) => {
      requests.push({url, ...init});
      return Response.json({detail: "Not Found"}, {status: 404});
    };
    await assert.rejects(send(path), {status: 404, message: "Not Found"});
    assert.equal(requests.length, 1); // No fallback to either legacy mutation.
    assert.ok(requests[0].url.endsWith(path));
    assert.deepEqual([...values.keys()], [otherQuota]);
    values.delete(otherQuota);

    globalThis.fetch = async (url, init) => { requests.push({url, ...init}); throw new TypeError("connection lost"); };
    await assert.rejects(send(platformReset, {confirmed: true, reason: body.reason}), /connection lost/);
    const subscriptionIndex = [...values.keys()].find(key => key.startsWith("pymentor:pending-subscription:v1:"));
    assert.match(subscriptionIndex, /^pymentor:pending-subscription:v1:[a-f\d]{64}$/);
    await assertReconciliation(path, values.get(subscriptionIndex));
    await assertReconciliation(platformExtension, values.get(subscriptionIndex));
    acknowledgeReconciliation(reconciliation);
    assert.equal(values.size, 0);

    await assert.rejects(send(removedCourse), /connection lost/);
    await assertReconciliation(path, values.get(subscriptionIndex));
    await assertReconciliation(platformExtension, values.get(subscriptionIndex));
    await assertReconciliation(platformReset, values.get(subscriptionIndex));
    await assertReconciliation(removedCourseQuota, values.get(subscriptionIndex));
    await assert.rejects(send(removedCourse, {...body, days: 21}), /неизвестен/);
    assert.equal(values.size, 2);
    assert.doesNotMatch(JSON.stringify([...values]), /actor-ref|target-ref|removed_course|Решение/);
    assert.equal(requests.length, 3);
    const result = {subscription_until: "2026-12-09T12:00:00Z", quota: {remaining: 3}};
    globalThis.fetch = async (url, init) => { requests.push({url, ...init}); return Response.json(result); };
    const pending = [...values];
    const {days, ...quotaBody} = body;
    await send(quotaPath, quotaBody);
    assert.deepEqual([...values], pending);
    assert.deepEqual(await send(removedCourse), result);
    assert.equal(values.size, 0);
    const [unknown, replay] = requests.filter(request => request.url.endsWith(removedCourse));
    for (const request of [unknown, replay]) {
      assert.ok(request.url.endsWith(removedCourse));
      assert.equal(request.method, "POST");
      assert.deepEqual(JSON.parse(request.body), body);
      assert.equal(request.headers["X-Actor-Ref"], actor);
      assert.equal(request.headers["X-Account-Ref"], target);
    }
    assert.match(unknown.headers["Idempotency-Key"], /^[a-f\d-]{36}$/i);
    assert.equal(replay.headers["Idempotency-Key"], unknown.headers["Idempotency-Key"]);

    // A delayed replay or stale reconciliation banner for K1 cannot clear a newer K2.
    async function holdCombined(payload) {
      let start;
      const started = new Promise(resolve => { start = resolve; });
      globalThis.fetch = async (_url, init) => new Promise(resolve => start({key: init.headers["Idempotency-Key"], resolve}));
      const response = send(path, payload);
      const request = await Promise.race([started, response]);
      globalThis.fetch = async () => { throw new Error("Unexpected request during reconciliation"); };
      return {response, ...request};
    }
    const first = await holdCombined(body);
    const late = await holdCombined(body);
    assert.equal(first.key, late.key);
    await assertReconciliation(platformExtension, values.get(subscriptionIndex));
    const oldBanner = reconciliation;
    first.resolve(Response.json(result));
    await first.response;
    assert.equal(values.size, 0);

    const newer = await holdCombined({...body, days: 21});
    assert.notEqual(newer.key, first.key);
    const newerMarkers = [...values];
    acknowledgeReconciliation(oldBanner);
    assert.deepEqual([...values], newerMarkers);
    late.resolve(Response.json(result));
    await late.response;
    assert.deepEqual([...values], newerMarkers);
    await assertReconciliation(platformReset, values.get(subscriptionIndex));

    newer.resolve(Response.json(result));
    await newer.response;
    assert.equal(values.size, 0);
  } finally {
    globalThis.fetch = original.fetch; globalThis.sessionStorage = original.sessionStorage;
    await vite.close();
  }
});
