import assert from "node:assert/strict";
import test from "node:test";
import { createServer } from "vite";
import { AUTH_CHANGE_KEY, createAuthChangeBus, createAuthSession, listenForAuthRefresh } from "./session-coordination.ts";

const profile = (id) => ({ id, username: `synthetic-${id}`, email: `synthetic-${id}@example.test` });
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function sessionHarness() {
  const requests = [];
  let announcements = 0;
  const session = createAuthSession(() => {
    const request = deferred();
    requests.push(request);
    return request.promise;
  }, () => { announcements += 1; });
  return { session, requests, announcements: () => announcements };
}

test("an auth change gates the old workspace and a delayed U2 probe cannot replace authoritative U1", async () => {
  const { session, requests } = sessionHarness();
  const initial = session.start();
  requests[0].resolve({ user: profile(2) });
  await initial;
  assert.equal(session.getSnapshot().user.id, 2);

  const slowOldProbe = session.refresh();
  assert.equal(session.getSnapshot().checking, true, "Workspace must be gated before waiting for /user/me");
  const nextProbe = session.refresh();
  requests[2].resolve({ user: profile(1) });
  await nextProbe;
  const accepted = session.getSnapshot();
  assert.equal(accepted.user.id, 1);
  assert.equal(accepted.checking, false);
  assert.equal(accepted.revision, 3, "Workspace gets a new mount key even when React batches a fast probe");

  requests[1].resolve({ user: profile(2) });
  await slowOldProbe;
  assert.equal(session.getSnapshot(), accepted, "the stale response must not publish any state");
  session.stop();
});

test("a revoked session fails closed and an older successful probe cannot resurrect its user", async () => {
  const harness = sessionHarness();
  const { session, requests } = harness;
  const initial = session.start();
  requests[0].resolve({ user: profile(2) });
  await initial;
  const slowOldProbe = session.refresh();
  const invalidated = session.refresh(true);
  requests[2].reject(new Error("401 session invalidated"));
  await invalidated;
  const loggedOut = session.getSnapshot();
  assert.equal(loggedOut.user, null);
  assert.equal(loggedOut.checking, false);
  assert.equal(loggedOut.initialized, true);
  assert.equal(harness.announcements(), 1, "a focus-discovered identity change is announced once");

  requests[1].resolve({ user: profile(2) });
  await slowOldProbe;
  assert.equal(session.getSnapshot(), loggedOut);
  const remote = session.refresh(false);
  requests[3].resolve({ user: null });
  await remote;
  assert.equal(harness.announcements(), 1, "remote probes must not create a broadcast loop");
  session.stop();
});

test("effect cleanup ignores pending probes and permits a fresh StrictMode start", async () => {
  const { session, requests } = sessionHarness();
  const previousMount = session.start();
  session.stop();
  const currentMount = session.start();
  requests[1].resolve({ user: profile(1) });
  await currentMount;
  const current = session.getSnapshot();
  requests[0].resolve({ user: profile(2) });
  await previousMount;
  assert.equal(session.getSnapshot(), current);
  session.stop();
});

function browserNetwork() {
  const windows = [];
  const channels = new Set();
  const writes = [];
  let sequence = 0;
  function createBrowser(origin, storageBlocked = false) {
    const browser = new EventTarget();
    browser.origin = origin;
    browser.crypto = { randomUUID: () => `nonce-${String(++sequence).padStart(10, "0")}` };
    browser.localStorage = {
      setItem(key, value) {
        if (storageBlocked) throw new Error("storage unavailable");
        writes.push({ key, value });
        for (const peer of windows) {
          if (peer === browser || peer.origin !== origin) continue;
          const event = new Event("storage");
          Object.assign(event, { key, newValue: value });
          peer.dispatchEvent(event);
        }
      },
    };
    browser.BroadcastChannel = class extends EventTarget {
      constructor(name) { super(); this.name = name; this.origin = origin; channels.add(this); }
      postMessage(data) {
        for (const peer of channels) {
          if (peer !== this && peer.origin === origin && peer.name === this.name) {
            peer.dispatchEvent(new MessageEvent("message", { data }));
          }
        }
      }
      close() { channels.delete(this); }
    };
    windows.push(browser);
    return browser;
  }
  return { createBrowser, writes, channels };
}

test("same-origin notification gates another session once, transmits only a nonce and cleans up", async () => {
  const network = browserNetwork();
  const sender = createAuthChangeBus(network.createBrowser("http://127.0.0.1:18519"));
  const receiver = createAuthChangeBus(network.createBrowser("http://127.0.0.1:18519"));
  const otherOrigin = createAuthChangeBus(network.createBrowser("http://127.0.0.1:18517"));
  const { session, requests } = sessionHarness();
  const initial = session.start();
  requests[0].resolve({ user: profile(2) });
  await initial;
  let notifications = 0;
  let crossOrigin = 0;
  let ownNotifications = 0;
  let pending;
  const unsubscribeSender = sender.subscribe(() => { ownNotifications += 1; });
  const unsubscribeReceiver = receiver.subscribe(() => { notifications += 1; pending = session.refresh(); });
  const unsubscribeOther = otherOrigin.subscribe(() => { crossOrigin += 1; });

  sender.publish();
  assert.equal(session.getSnapshot().checking, true);
  assert.equal(notifications, 1, "storage and BroadcastChannel delivery are deduplicated");
  assert.equal(ownNotifications, 0);
  assert.equal(crossOrigin, 0);
  assert.deepEqual(network.writes, [{ key: AUTH_CHANGE_KEY, value: "nonce-0000000001" }]);
  requests[1].resolve({ user: profile(1) });
  await pending;
  assert.equal(session.getSnapshot().user.id, 1);
  unsubscribeReceiver();
  sender.publish();
  assert.equal(notifications, 1);
  unsubscribeSender();
  unsubscribeOther();
  assert.equal(network.channels.size, 0);
  session.stop();
});

test("BroadcastChannel still delivers auth invalidation when localStorage is unavailable", () => {
  const network = browserNetwork();
  const sender = createAuthChangeBus(network.createBrowser("same-origin", true));
  const receiver = createAuthChangeBus(network.createBrowser("same-origin"));
  let received = 0;
  const unsubscribe = receiver.subscribe(() => { received += 1; });
  assert.doesNotThrow(() => sender.publish());
  assert.equal(received, 1);
  assert.equal(network.writes.length, 0);
  unsubscribe();
  assert.equal(network.channels.size, 0);
});

test("focus and becoming visible recheck external cookies, while hidden state and cleanup do not", () => {
  const network = browserNetwork();
  const browser = network.createBrowser("same-origin");
  const sender = createAuthChangeBus(network.createBrowser("same-origin"));
  const page = new EventTarget();
  page.visibilityState = "hidden";
  const originalWindow = globalThis.window;
  const originalDocument = globalThis.document;
  globalThis.window = browser;
  globalThis.document = page;
  const reasons = [];
  const unsubscribe = listenForAuthRefresh((announceIdentityChange) => reasons.push(announceIdentityChange));
  try {
    browser.dispatchEvent(new Event("focus"));
    page.dispatchEvent(new Event("visibilitychange"));
    page.visibilityState = "visible";
    page.dispatchEvent(new Event("visibilitychange"));
    sender.publish();
    assert.deepEqual(reasons, [true, true, false]);
    unsubscribe();
    browser.dispatchEvent(new Event("focus"));
    page.dispatchEvent(new Event("visibilitychange"));
    sender.publish();
    assert.deepEqual(reasons, [true, true, false]);
  } finally {
    unsubscribe();
    if (originalWindow === undefined) delete globalThis.window;
    else globalThis.window = originalWindow;
    if (originalDocument === undefined) delete globalThis.document;
    else globalThis.document = originalDocument;
  }
});

test("API login/logout announce only after success without sharing credentials or user data", async () => {
  const network = browserNetwork();
  const browser = network.createBrowser("same-origin");
  const originalWindow = globalThis.window;
  const originalFetch = globalThis.fetch;
  const originalStorage = globalThis.sessionStorage;
  globalThis.window = browser;
  const storage = new Map();
  globalThis.sessionStorage = { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) };
  const vite = await createServer({ configFile: false, base: "/platform/", server: { middlewareMode: true, hmr: false, ws: false } });
  try {
    const { loginUser, logoutUser } = await vite.ssrLoadModule("/src/features/auth/api.ts");
    let failed = false;
    globalThis.fetch = async (input) => {
      const capabilities = String(input).endsWith("/user/login-capabilities");
      return new Response(JSON.stringify(capabilities ? { idempotent_login: false } : failed ? { detail: "unauthorized" } : { user: profile(1) }), {
        status: !capabilities && failed ? 401 : 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    await loginUser({ email: "synthetic@example.test", password: "test-only-password" });
    await logoutUser();
    assert.equal(network.writes.length, 2);
    assert.ok(network.writes.every(({ key, value }) => key === AUTH_CHANGE_KEY && /^nonce-\d{10}$/.test(value)));
    failed = true;
    await assert.rejects(loginUser({ email: "synthetic@example.test", password: "test-only-password" }), /unauthorized/);
    await assert.rejects(logoutUser(), /unauthorized/);
    assert.equal(network.writes.length, 2);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalWindow === undefined) delete globalThis.window;
    else globalThis.window = originalWindow;
    if (originalStorage === undefined) delete globalThis.sessionStorage;
    else globalThis.sessionStorage = originalStorage;
    await vite.close();
  }
});
