import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { createConnection } from "node:net";
import { resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const offset = Number(process.env.CORP607_MIXED_OFFSET ?? "0");
assert([0, 1620].includes(offset), "Unexpected CORP-607 synthetic port offset");
const origin = "http://127.0.0.1:" + (52707 + offset);
const debugPort = 52708 + offset;
const lessonName = "Создаём и заменяем задачу в SQLite";
const lessonId = "lesson-64c9d597a164bbdb4f145d5a";
const reportPath = "/api/user/analytics/statistics?days=7&course_id=backend";
const grantPath = "/api/analytics/windows/grant";
const batchPath = "/api/analytics/windows/batch";
const eventsPath = "/api/analytics/events/batch";
const v2GrantPath = "/api/analytics/grants/collect";
const windowStats = [{ tool_id: "theory", course_id: "backend", lesson_id: lessonId, active_ms: 60000 }];
const modes = {
  "old-web-new-server": { windowsGrant: "none", schema: "a015" },
  "new-web-old-server": { windowsGrant: "unsupported", schema: "a015" },
  "new-web-server-off": { windowsGrant: "unsupported", schema: "a015" },
  "rollback-drain": { windowsGrant: "ready", schema: "a015" },
  "rollback-gap": { windowsGrant: "none", schema: "a015" },
  "rollback-catchup": { windowsGrant: "none", schema: "a015" },
};

function processTarget(run, learner) {
  const python = process.env.CORP607_MIXED_PYTHON;
  assert(python, "CORP607_MIXED_PYTHON must point to the locally pinned Analytics interpreter");
  try {
    return JSON.parse(execFileSync(python, ["-B", "tools/shadow_607_mixed_target.py", "observe",
      "--run", run, "--learner", learner], { encoding: "utf8", timeout: 12000, windowsHide: true }));
  } catch {
    throw Error("independent PostgreSQL observation failed for this synthetic run");
  }
}

function keyAt(utcMs) {
  const moscow = new Date(utcMs + 3 * 60 * 60 * 1000);
  return { activity_date: moscow.toISOString().slice(0, 10),
    slot_no: moscow.getUTCHours() * 4 + Math.floor(moscow.getUTCMinutes() / 15) + 1 };
}

function endOfKey(key) {
  return Date.parse(key.activity_date + "T00:00:00+03:00") + key.slot_no * 15 * 60 * 1000;
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function syntheticUuid(value) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value ?? "");
}

function unsupported(item) {
  const allow = item.allow?.split(",").map(method => method.trim().toUpperCase());
  return item.status === 404 || item.code === "unsupported_version"
    || (item.status === 405 && allow?.length > 0 && !allow.includes("POST"));
}
function ownResponse(response, path) {
  const url = new URL(response.url());
  return url.origin === origin && url.pathname === path;
}

function sameKey(row, key) {
  return row.activity_date === key.activity_date && row.slot_no === key.slot_no;
}

async function waitFor(read, description, timeout = 20000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const current = await read();
    if (current) return current;
    await delay(120);
  }
  throw Error("timed out waiting for " + description);
}

function readFocus(page) {
  return page.evaluate(() => ({ focused: document.hasFocus(), visible: document.visibilityState,
    utc: new Date().toISOString() }));
}

async function focus(page, focused) {
  return waitFor(async () => {
    const state = await readFocus(page);
    return state.focused === focused ? state : null;
  }, "native Chromium focus", 5000);
}

async function navigateToTheory(page) {
  const published = page.waitForResponse(response =>
    ownResponse(response, "/api/learning/analytics-catalog") && response.ok(), { timeout: 22000 });
  await page.goto(origin + "/backend", { waitUntil: "domcontentloaded" });
  const catalog = await published;
  assert.equal(catalog.headers()["x-analytics-collection-mode"], "parallel");
  assert((await catalog.json()).lesson_ids.includes(lessonId), "published catalog contains the lesson");
  const chapters = page.getByRole("button", { name: "Выбрать главу" });
  const open = page.getByRole("button", { name: "Открыть список занятий" });
  await chapters.or(open).first().waitFor({ timeout: 22000 });
  if (!(await chapters.isVisible())) await open.click({ force: true });
  await chapters.click({ force: true, timeout: 15000 });
  await page.getByRole("dialog", { name: "Главы курса" }).getByRole("button", { name: /FastAPI и базы данных/ })
    .click({ force: true, timeout: 15000 });
  const lesson = page.locator("button.lesson-row").filter({ hasText: lessonName });
  await lesson.waitFor({ state: "attached", timeout: 15000 });
  const expander = page.locator(".module-group").filter({ has: lesson }).locator("button.module-toggle");
  if (await expander.getAttribute("aria-expanded") !== "true") await expander.press("Enter");
  assert.doesNotMatch(await lesson.getAttribute("class"), /is-locked/);
  await lesson.press("Enter");
  await page.getByRole("button", { name: "Открыть теорию" }).press("Enter");
  return page.locator("article.dock-lesson").filter({ has: page.locator("h1,h2") })
    .and(page.locator('article[aria-label*="' + lessonName + '"]'));
}

function readOutbox(page) {
  return page.evaluate(async () => {
    const name = "knowi:analytics:windows:v1";
    if (!(await indexedDB.databases()).some(database => database.name === name)) return null;
    return new Promise((resolve, reject) => {
      const opened = indexedDB.open(name);
      opened.onerror = reject;
      opened.onsuccess = () => {
        const db = opened.result;
        const transaction = db.transaction("partition", "readonly");
        const request = transaction.objectStore("partition").get("current");
        request.onerror = reject;
        request.onsuccess = () => {
          const partition = request.result;
          resolve(partition ? { writer: partition.writer, windows: Object.values(partition.windows)
            .map(draft => ({ key: draft.key, pending: draft.pending?.body ?? null,
              attempts: draft.pending?.attempts, next_at_ms: draft.pending?.nextAt,
              reason: draft.reason, confirmed: draft.confirmed ?? null })) } : null);
        };
        transaction.oncomplete = () => db.close();
      };
    });
  });
}

async function login(context, learner) {
  const request = await context.request.post(origin + "/api/user/login", {
    headers: { Origin: origin, Referer: origin + "/backend", "Idempotency-Key": randomUUID() },
    data: { email: learner.email, password: learner.password },
  });
  assert.equal(request.status(), 200, "synthetic learner login");
  const identity = await context.request.get(origin + "/api/user/me");
  assert.equal(identity.status(), 200);
  assert.equal((await identity.json()).user.account_ref, learner.subject_ref, "session belongs to selected lifecycle");
}

async function report(context) {
  const response = await context.request.get(origin + reportPath, { timeout: 5000 });
  if (response.status() !== 200) {
    throw Object.assign(new Error("ordinary learner report HTTP " + response.status()), { status: response.status() });
  }
  return response.json();
}

async function reportAt60(context) {
  return waitFor(async () => {
    try {
      const body = await report(context);
      return body.summary?.theory_seconds === 60 ? body : null;
    } catch (error) {
      if (![502, 503, 504].includes(error.status)) throw error;
      return null;
    }
  }, "real v2 report projection", 90000);
}
function withoutTime(body) {
  const { as_of: unused, ...reportFields } = body;
  return reportFields;
}

function verifyReport(body) {
  assert.equal(body.schema_version, 1);
  assert.deepEqual(body.scope, { course_id: "backend", activity: "platform" });
  assert.equal(body.summary.active_seconds, 60);
  assert.equal(body.summary.theory_seconds, 60);
  assert.equal(body.summary.practice_seconds, null);
  assert.equal(body.summary.completed_lessons_period, 0);
}

function listen(page, evidence) {
  const requests = new Set();
  const tracked = new Set([grantPath, batchPath, v2GrantPath, eventsPath]);
  let lastNetworkChange = Date.now();
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.origin === origin && tracked.has(url.pathname)) {
      requests.add(request);
      lastNetworkChange = Date.now();
    }
  });
  const finished = request => {
    if (requests.delete(request)) lastNetworkChange = Date.now();
  };
  page.on("requestfinished", finished);
  page.on("requestfailed", finished);
  page.on("response", async response => {
    try {
      if (ownResponse(response, grantPath)) {
        const body = await response.json().catch(() => ({}));
        const request = JSON.parse(response.request().postDataBuffer().toString("utf8"));
        evidence.window_grants.push({ status: response.status(), code: body.code, allow: response.headers().allow,
          subject_ref: body.subject_ref, server_time: body.server_time, writer_id: request.writer_id,
          writer_generation: request.writer_generation, observed_utc: new Date().toISOString() });
      }
      if (ownResponse(response, batchPath)) {
        const body = await response.json().catch(() => ({}));
        evidence.window_batches.push({ status: response.status(), sha256: sha256(response.request().postDataBuffer()),
          items: body.items ?? [] });
      }
      if (ownResponse(response, v2GrantPath)) {
        const grant = await response.json().catch(() => ({}));
        evidence.v2_grants.push({ status: response.status(), server_time: grant.server_time });
      }
      if (ownResponse(response, eventsPath)) {
        const body = await response.json();
        const { events } = JSON.parse(response.request().postDataBuffer().toString("utf8"));
        for (const event of events) {
          if (event.type === "activity_segment" && event.course_id === "backend" && event.lesson_id === lessonId
              && event.section === "theory") {
            const ack = body.items?.find(item => item.event_id === event.event_id);
            evidence.v2.push({ event_id: event.event_id, status: response.status(), ack,
              start: event.started_at, end: event.ended_at, active_ms: event.active_ms });
          }
        }
      }
    } catch {
      evidence.unreadable.push(new URL(response.url()).pathname);
    }
  });
  return () => waitFor(() => requests.size === 0 && Date.now() - lastNetworkChange >= 175,
    "settlement of real Analytics requests with virtual clock paused", 12000);
}

function matchingFacts(rows, evidence) {
  const ids = new Set(evidence.v2.map(event => event.event_id));
  assert(ids.size, "captured a v2 batch for the ordinary lesson");
  const facts = rows.facts.filter(row => ids.has(row.event_id));
  assert.equal(facts.length, ids.size, "every accepted v2 event is independently persisted");
  assert.equal(facts.reduce((sum, row) => sum + row.active_ms, 0), 60000);
  for (const row of facts) {
    assert.equal(row.course_id, "backend");
    assert.equal(row.lesson_id, lessonId);
    assert.equal(row.section, "theory");
  }
  return facts;
}

async function syntheticEndpointProbe(context, subject) {
  const response = await context.request.post(origin + grantPath, {
    headers: { Origin: origin, Referer: origin + "/backend" },
    data: { protocol_version: 1, writer_id: randomUUID(), writer_generation: 1 },
  });
  assert.equal(response.status(), 200, "new server grants v3 to the old Web subject when deliberately probed");
  assert.equal((await response.json()).subject_ref, subject);
  return { status: response.status(), done_by: "verification driver; no window submitted" };
}

async function activity(context, evidence, config) {
  const page = context.pages()[0] ?? await context.newPage();
  await page.setViewportSize({ width: 1440, height: 980 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  const transportIdle = listen(page, evidence);
  let letThrough = config.mode !== "rollback-drain";
  if (!letThrough) {
    await page.route(origin + batchPath, route => {
      if (letThrough) return route.continue();
      evidence.aborted.push(sha256(route.request().postDataBuffer()));
      return route.abort("failed");
    });
  }
  const initial = config.expectedClock ?? Date.now() + 10000;
  await page.clock.install({ time: new Date(initial - 1000) });
  await page.clock.pauseAt(new Date(initial));
  const theory = await navigateToTheory(page);
  const initialDocumentClock = await page.evaluate(() => performance.timeOrigin);
  const distractor = await context.newPage();
  await distractor.bringToFront();
  await focus(page, false);
  if (config.windowsGrant === "ready") {
    const grant = await waitFor(() => evidence.window_grants.find(item => item.status === 200), "live window grant");
    assert.equal(grant.subject_ref, config.learner.subject_ref);
    assert.equal(Date.parse(grant.server_time), initial, "controlled Core window clock");
  }
  if (config.windowsGrant === "unsupported") {
    await waitFor(() => evidence.window_grants.find(unsupported),
      "definitive unsupported response");
  }
  await page.clock.runFor(500);
  await page.bringToFront();
  await focus(page, true);
  await page.locator(".dv-tab").filter({ hasText: "71 Теория" }).first().click({ force: true, timeout: 15000 });
  evidence.active_tabs = await page.locator(".dv-active-tab").allTextContents();
  await theory.waitFor({ state: "visible", timeout: 20000 });
  evidence.focus_start = await focus(page, true);
  await page.keyboard.press("Shift");
  for (let elapsed = 0; elapsed < 60000; elapsed += 5000) {
    await page.clock.runFor(5000);
    await transportIdle();
  }
  await distractor.bringToFront();
  evidence.focus_end = await focus(page, false);
  assert.equal(Date.parse(evidence.focus_end.utc) - Date.parse(evidence.focus_start.utc), 60000);

  if (config.mode === "rollback-drain") {
    await waitFor(() => evidence.aborted.length, "intentionally dropped first v3 request");
    const draft = await waitFor(async () => (await readOutbox(page))?.windows
      .find(row => sameKey(row.key, config.key) && row.pending), "same-browser durable pending body");
    const pendingHash = sha256(draft.pending);
    evidence.before_drain = { hash: pendingHash, postgres: config.database(), opened_lesson_still_focused: false };
    assert(evidence.aborted.includes(pendingHash));
    assert.equal(evidence.before_drain.postgres.windows.length, 0, "abort occurs before any v3 commit");
    await writeFile(config.output + ".drain-ready.json", JSON.stringify({
      run: evidence.run, target: evidence.postgres_before.database, key: config.key, pending_sha256: pendingHash,
      action: "switch only the serving Web to current build with generation off; keep Core/Collector on",
    }, null, 2) + "\n", { flag: "wx" });
    // HMR is disabled in the dedicated Vite launcher. The old hidden page can drain
    // its already-captured body while new page loads use the new Web deployment.
    const withdrawn = await waitFor(async () => {
      try {
        const observation = JSON.parse(await readFile(config.output + ".generation-off.json", "utf8"));
        return observation.run === evidence.run && observation.pending_sha256 === pendingHash
          && observation.generation_off === true && observation.http_status === 200 ? observation : null;
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
        return null;
      }
    }, "Web generation flag off while original page stays hidden", 120000);
    evidence.generation_off = withdrawn;
    assert.equal((await readFocus(page)).focused, false);
    assert.equal(await page.evaluate(() => performance.timeOrigin), initialDocumentClock,
      "the existing gated document survives the Web deployment");
    evidence.pending_before_retry = { attempts: draft.attempts, next_at_ms: draft.next_at_ms };
    letThrough = true;
    await context.setOffline(true);
    for (let elapsed = 0; elapsed < 20000; elapsed += 5000) {
      await page.clock.runFor(5000);
      await transportIdle();
    }
    await context.setOffline(false);
    await page.bringToFront();
    await focus(page, true);
    await distractor.bringToFront();
    await focus(page, false);
    evidence.network_restored = await page.evaluate(() => navigator.onLine);
    assert.equal(evidence.network_restored, true);
    await transportIdle();
    for (let attempt = 0; attempt < 6 && !evidence.window_batches.some(batch => batch.sha256 === pendingHash
        && batch.items.some(item => item.status === "committed")); attempt++) {
      await page.clock.runFor(5000);
      await transportIdle();
    }
    const committed = await waitFor(() => evidence.window_batches.find(batch => batch.sha256 === pendingHash
      && batch.items.some(item => item.status === "committed")), "drain ACK after PostgreSQL commit");
    const ack = committed.items.find(item => item.status === "committed");
    assert(sameKey(ack, config.key));
    assert.deepEqual(ack.confirmed.stats, windowStats);
    await waitFor(async () => (await readOutbox(page))?.windows.some(row => sameKey(row.key, config.key)
      && !row.pending && row.confirmed?.payload_hash === ack.payload_hash), "exact ACK cleared IndexedDB outbox");
    const [row] = config.database().windows;
    assert(row && sameKey(row, config.key));
    assert.equal(row.revision, ack.confirmed.revision);
    assert.deepEqual(row.stats, windowStats);
    evidence.after_drain = { ack, confirmed_request_sha256: pendingHash, outbox_drained: true, postgres: row };
  }
  evidence.report_after = await reportAt60(context);
  verifyReport(evidence.report_after);
  assert(evidence.v2.every(event => event.status === 200
    && ["accepted", "duplicate"].includes(event.ack?.status)), "v2 ACKs prove accepted ordinary lesson activity");
  evidence.postgres_after = config.database();
  evidence.facts = matchingFacts(evidence.postgres_after, evidence);
  if (config.mode !== "rollback-drain") assert.equal(evidence.postgres_after.windows.length, 0);
  if (config.mode !== "rollback-drain") {
    // Leave the v2 grant clock stable until its HTTP request and delivery finish.
    // Observe the v3 retry budget afterward in the same mounted browser instance.
    const initialGrants = evidence.window_grants.length;
    for (let elapsed = 0; elapsed < 90000; elapsed += 5000) {
      await page.clock.runFor(5000);
      await transportIdle();
    }
    if (config.windowsGrant === "unsupported") {
      await delay(500);
      evidence.unsupported_probe_counts = { before_steady_observation: initialGrants,
        after_steady_observation: evidence.window_grants.length };
      evidence.unsupported_observation_ms = 90000;
      assert(initialGrants >= 1 && initialGrants <= 8, "mounted SPA probes are bounded");
      assert.equal(evidence.window_grants.length, initialGrants, "terminal unsupported stops steady-state retries");
      assert(evidence.window_grants.every(unsupported));
    } else {
      assert.equal(evidence.window_grants.length, 0, "this Web instance does not request a v3 grant");
    }
    assert.equal(evidence.window_batches.length, 0, "no browser v3 snapshot submitted");
  }
}

async function catchup(context, evidence, cfg, seedPath) {
  const seed = JSON.parse(await readFile(seedPath, "utf8"));
  assert.equal(seed.outcome, "PASS");
  assert.equal(seed.mode, "rollback-gap");
  assert.equal(seed.run, evidence.run);
  assert.equal(seed.learner, evidence.learner);
  assert.equal(seed.postgres_after.subject_ref, cfg.learner.subject_ref);
  const key = seed.predeclared.key;
  evidence.predeclared = seed.predeclared;
  assert(Date.now() >= endOfKey(key), "wait until the evidenced window closes before running catch-up");
  const before = seed.postgres_after.control.find(row => sameKey(row, key));
  assert(before?.source_generation > 0);
  evidence.before_repair = { key, control: before, expected_stats: windowStats, actual_stats: [] };
  const after = await waitFor(() => {
    const current = cfg.database();
    return current.windows.some(row => sameKey(row, key)) ? current : null;
  }, "dirty window repair by resumed processor", 60000);
  assert.equal(after.windows.length, 1);
  const [window] = after.windows;
  assert(sameKey(window, key));
  assert.deepEqual(window.stats, windowStats);
  assert.equal(window.revision, 1);
  const control = after.control.find(row => sameKey(row, key));
  assert.equal(control.source_generation, before.source_generation);
  assert.equal(control.reconciled_generation, before.source_generation);
  assert.equal(control.server_epoch, 2);
  assert(["repaired", "verified"].includes(control.phase));
  await delay(2500);
  const repeated = cfg.database();
  assert.deepEqual(repeated.windows, after.windows, "continued worker operation preserves repaired business revision");
  evidence.after_repair = { window, control, repeated: repeated.windows };
  evidence.report_after = await reportAt60(context);
  assert.deepEqual(withoutTime(evidence.report_after), withoutTime(seed.report_after), "v2 report survives recovery");
}

function checkDebugPort() {
  return new Promise((resolveCheck, reject) => {
    const socket = createConnection({ port: debugPort, host: "127.0.0.1" });
    socket.setTimeout(1500);
    socket.once("connect", () => {
      socket.destroy();
      reject(Error("browser debug port already belongs to a listener"));
    });
    socket.once("timeout", () => {
      socket.destroy();
      reject(Error("could not prove the browser debug port is free"));
    });
    socket.once("error", error => {
      if (error.code === "ECONNREFUSED") resolveCheck();
      else reject(Error("could not prove the browser debug port is free"));
    });
  });
}

async function main() {
  const [mode, run, learnerName, output, seedPath] = process.argv.slice(2);
  assert(modes[mode], "specify a supported mixed scenario");
  assert(syntheticUuid(run), "specify the allocated synthetic run UUID");
  assert(/^[a-z_]+$/.test(learnerName ?? ""), "specify one named fixture learner");
  assert(output, "specify a fresh evidence file");
  const ownerRoot = resolve(".local/local-live", run);
  const participants = JSON.parse(await readFile(resolve(ownerRoot, "corp607-learners.json"), "utf8"));
  try {
    Object.assign(participants, JSON.parse(await readFile(resolve(ownerRoot, "corp607-mixed-learners.json"), "utf8")));
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  const learner = participants[learnerName];
  assert(syntheticUuid(learner?.subject_ref));
  const browserModule = process.env.CORP607_PLAYWRIGHT_MODULE;
  assert(browserModule, "CORP607_PLAYWRIGHT_MODULE must point to installed Playwright");
  const { chromium } = createRequire(import.meta.url)(browserModule);
  const database = () => processTarget(run, learnerName);
  const expectedClock = process.env.CORP607_MIXED_GRANT_CLOCK ? Date.parse(process.env.CORP607_MIXED_GRANT_CLOCK) : undefined;
  if (mode === "rollback-drain") assert(Number.isFinite(expectedClock), "matches the server test-only UTC grant clock");
  const expectedStart = Number.isFinite(expectedClock) ? expectedClock + 500 : Date.now();
  const key = keyAt(expectedStart);
  const evidence = { mode, run, learner: learnerName, subject_ref: learner.subject_ref, origin, reportPath,
    fixture: { course_id: "backend", lesson_id: lessonId, active_ms: 60000 },
    predeclared: { key, expected_stats: windowStats },
    window_grants: [], window_batches: [], v2_grants: [], v2: [], aborted: [], unreadable: [] };
  const config = { ...modes[mode], mode, learner, key, expectedClock, database, output };
  const priorGap = mode === "rollback-gap" && seedPath ? JSON.parse(await readFile(seedPath, "utf8")) : null;
  if (priorGap) {
    assert.equal(priorGap.run, run);
    assert.equal(priorGap.subject_ref, learner.subject_ref);
    assert.equal(priorGap.mode, mode);
    assert.deepEqual(priorGap.predeclared, evidence.predeclared);
    assert.equal(Date.parse(priorGap.focus_end.utc) - Date.parse(priorGap.focus_start.utc), 60000);
    assert.equal(priorGap.postgres_before.facts.length, 0);
    assert(priorGap.v2.length && priorGap.v2.every(event => event.ack?.code === "committed"));
    assert.equal(priorGap.window_grants.length, 0);
  }
  await checkDebugPort();
  const outer = await chromium.launch({ headless: true, channel: "chromium",
    args: ["--remote-debugging-address=127.0.0.1", "--remote-debugging-port=" + debugPort] });
  let browser;
  try {
    browser = await chromium.connectOverCDP("http://127.0.0.1:" + debugPort, { noDefaults: true });
    evidence.chromium = browser.version();
    const context = browser.contexts()[0];
    await context.route("**/*", route => {
      const url = new URL(route.request().url());
      return url.origin === origin ? route.fallback() : route.abort("blockedbyclient");
    });
    evidence.postgres_before = database();
    assert.equal(evidence.postgres_before.schema, config.schema);
    assert.equal(evidence.postgres_before.subject_ref, learner.subject_ref);
    if (mode !== "rollback-catchup" && !priorGap) {
      assert.equal(evidence.postgres_before.facts.length, 0, "use a fresh subject for each measured browser scenario");
      assert.equal(evidence.postgres_before.windows.length, 0);
    }
    if (mode === "rollback-gap") {
      assert(expectedStart + 150000 < endOfKey(key), "use a slot with at least 150 seconds before its boundary");
      const t0 = process.env.CORP607_MIXED_T0;
      assert(Number.isFinite(Date.parse(t0)), "use the same declared cohort T0 for each rollback phase");
      assert(Date.parse(t0) <= endOfKey(key) - 900000, "cohort starts before the expected gap key");
      evidence.proof = { protocol_version: 1, cohort_t0: t0,
        windows: [{ key: { student_id: learner.subject_ref, ...key }, expected_stats: windowStats,
          v2_source_complete: true }] };
      await writeFile(output + ".plan.json", JSON.stringify(evidence.proof, null, 2) + "\n", { flag: "wx" });
    }
    if (mode === "old-web-new-server" && Number.isFinite(expectedClock)) {
      const plan = { run, subject_ref: learner.subject_ref, timezone: "Europe/Moscow",
        utc_start: new Date(expectedStart).toISOString(), utc_end: new Date(expectedStart + 60000).toISOString(),
        ...evidence.predeclared, expected_v3_rows: 0, old_web_only: true };
      await writeFile(output + ".plan.json", JSON.stringify(plan, null, 2) + "\n", { flag: "wx" });
    }
    await login(context, learner);
    evidence.report_before = priorGap?.report_before ?? await (mode === "rollback-catchup" ? reportAt60(context) : report(context));
    if (mode === "rollback-catchup") {
      assert(seedPath, "catchup requires an observed rollback-gap report");
      await catchup(context, evidence, config, seedPath);
    } else {
      if (mode === "old-web-new-server") evidence.driver_probe = await syntheticEndpointProbe(context, learner.subject_ref);
      if (priorGap) {
        evidence.v2 = priorGap.v2;
        evidence.focus_start = priorGap.focus_start;
        evidence.focus_end = priorGap.focus_end;
        evidence.report_before = priorGap.report_before;
        evidence.report_after = await reportAt60(context);
        verifyReport(evidence.report_after);
        evidence.postgres_after = database();
        evidence.facts = matchingFacts(evidence.postgres_after, evidence);
        assert.equal(evidence.postgres_after.windows.length, 0);
        evidence.resumed_without_replaying_activity = true;
      } else {
        await activity(context, evidence, config);
      }
      if (mode === "rollback-gap") {
        assert(evidence.facts.every(row => sameKey(keyAt(Date.parse(row.started_at)), key)
          && sameKey(keyAt(Date.parse(row.ended_at) - 1), key)), "v2 facts cover the declared key");
        assert(evidence.postgres_after.control.some(row => sameKey(row, key) && row.source_generation > 0),
          "v2 writes kept the dirty generation while intake/worker were stopped");
        await writeFile(output + ".proof.json", JSON.stringify(evidence.proof, null, 2) + "\n", { flag: "wx" });
      }
    }
    const baseline = process.env.CORP607_MIXED_BASELINE;
    if (baseline) {
      const prior = JSON.parse(await readFile(baseline, "utf8"));
      assert.deepEqual(withoutTime(evidence.report_after), withoutTime(prior.report_after.body ?? prior.report_after),
        "complete ordinary report matches the declared baseline");
      evidence.baseline_comparison = "matched except as_of";
    }
    evidence.outcome = "PASS";
  } catch (error) {
    evidence.outcome = "FAIL";
    // Compare-safe evidence only. Playwright requests, credentials and raw headers are excluded.
    evidence.error = error.message.split("\n")[0];
    throw Error("Mixed scenario failed; see the redacted evidence file");
  } finally {
    await browser?.close().catch(() => {});
    await outer.close();
    await writeFile(output, JSON.stringify(evidence, null, 2) + "\n", { flag: "wx" });
  }
}

if (process.argv[2] === "--self-check") {
  assert.deepEqual(keyAt(Date.parse("2026-11-27T07:00:00Z")), { activity_date: "2026-11-27", slot_no: 41 });
  assert.deepEqual(keyAt(Date.parse("2026-11-27T21:00:00Z")), { activity_date: "2026-11-28", slot_no: 1 });
  assert.equal(endOfKey({ activity_date: "2026-11-27", slot_no: 96 }), Date.parse("2026-11-27T21:00:00Z"));
  verifyReport({ schema_version: 1, scope: { course_id: "backend", activity: "platform" },
    summary: { active_seconds: 60, theory_seconds: 60, practice_seconds: null, completed_lessons_period: 0 } });
  assert.deepEqual(withoutTime({ as_of: "any", amount: 1 }), { amount: 1 });
  console.log("5 offline assertions passed; no browser, HTTP or PostgreSQL opened");
} else {
  await main();
}
