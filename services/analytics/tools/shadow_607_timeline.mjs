import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { access, readFile, rename, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

const lesson = { course_id: "backend", lesson_id: "lesson-64c9d597a164bbdb4f145d5a", title: "Создаём и заменяем задачу в SQLite" };
const epochOffset = 3 * 60 * 60 * 1000;
const windowsGrant = "/api/analytics/windows/grant";
const windowsBatch = "/api/analytics/windows/batch";
const legacyBatch = "/api/analytics/events/batch";
const legacyGrant = "/api/analytics/grants/collect";
const expectations = JSON.parse(await readFile(new URL("../tests/student_windows/fixtures/timeline-607-v1.json", import.meta.url), "utf8"));
const extraExpectations = JSON.parse(await readFile(new URL("../tests/student_windows/fixtures/timeline-607-final-v1.json", import.meta.url), "utf8"));
const plans = {
  "account-switch": { start: "2026-11-27T07:30:00.000Z", drain: 10_000, steps: [["theory", 60_000]] },
  "device-first": { start: "2026-11-27T07:45:00.000Z", drain: 10_000, steps: [["theory", 60_000]] },
  "device-second": { start: "2026-11-27T07:45:00.000Z", drain: 10_000, steps: [["theory", 60_000]] },
  first: { start: "2026-11-27T07:00:00.000Z", drain: 10_000, steps: [["theory", 60_000]] },
  "both-lost": { start: "2026-11-27T08:00:00.000Z", drain: 65_000, steps: [["theory", 40_000]] },
  five: {
    start: "2026-11-27T07:00:00.000Z", drain: 10_000,
    steps: [["theory", 60_000], ["practice", 60_000], ["catalog", 30_000], ["theory", 30_000], ["practice", 60_000]],
  },
  quarter: { start: "2026-11-27T12:14:50.000Z", drain: 65_000, steps: [["theory", 30_000]] },
  midnight: { start: "2026-11-27T20:59:50.000Z", drain: 65_000, steps: [["theory", 30_000]] },
  reload: { start: "2026-11-27T07:00:00.000Z", drain: 90_000, steps: [["theory", 480_000]] },
};
const [name, mode, output] = process.argv.slice(2);
assert(Object.hasOwn(plans, name), "Choose first, five, quarter, midnight, reload, both-lost, account-switch, device-first or device-second");
assert(["--plan", "--live"].includes(mode), "Choose --plan or --live <private-json>");
const plan = plans[name];
const start = Date.parse(process.env.CORP607_ACTIVITY_START_UTC ?? plan.start);
assert(Number.isFinite(start), "Activity start is a UTC timestamp");
assert.equal((start + epochOffset) % 86_400_000, (Date.parse(plan.start) + epochOffset) % 86_400_000,
  "A translated calendar date must preserve the Moscow time of day");
const initialAnchor = Date.parse(process.env.CORP607_SERVER_ANCHOR_UTC ?? new Date(start - 5000).toISOString());
const warmup = start - initialAnchor;
assert(warmup >= 500 && warmup <= 15_000, "Server anchor starts 0.5–15 seconds before focus");
const anchorEvidence = { warmup_ms: warmup, server_anchor_utc: iso(initialAnchor), activity_start_utc: iso(start) };
const duration = plan.steps.reduce((total, [, ms]) => total + ms, 0);
const expected = (expectations.scenarios[name] ?? extraExpectations.scenarios[name]).map(row => ({
  activity_date: iso(start + epochOffset + row.local_date_offset * 86_400_000).slice(0, 10), slot_no: row.slot_no, stats: row.stats,
}));

if (mode === "--plan") {
  console.log(JSON.stringify({
    result: "PROPOSED", scenario: name, ...anchorEvidence, duration_ms: duration,
    steps: plan.steps, drain_ms: plan.drain, expected_windows: expected,
    ...(name === "account-switch" ? { second_learner: "privacy", second_learner_focus_ms: 60_000 } : {}),
  }, null, 2));
} else {
  assert(output, "Supply a new private evidence path");
  await run();
}

function iso(ms) { return new Date(ms).toISOString(); }
function sha(bytes) { return createHash("sha256").update(bytes).digest("hex"); }

async function until(observe, predicate, description, timeout = 15_000) {
  const deadline = Date.now() + timeout;
  do {
    const state = await observe();
    if (predicate(state)) return state;
    await delay(75);
  } while (Date.now() < deadline);
  throw new Error("Timed out waiting for " + description);
}

async function readFocus(page) {
  return page.evaluate(() => ({
    utc: new Date().toISOString(), mono_ms: performance.now(),
    focused: document.hasFocus(), visibility: document.visibilityState,
    active_panels: Array.from(document.querySelectorAll(".dv-active-tab"), node => node.textContent?.trim()),
  }));
}

async function run() {
  const runId = process.env.CORP607_RUN;
  assert.match(runId ?? "", /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i);
  const pause = new URL("../../../docs/analytics/evidence-598/607/timeline/.pause-" + runId, import.meta.url);
  await until(() => access(pause).then(() => false, () => true), Boolean, "the acceptance operator to resume the timeline", 600_000);
  const origin = new URL(process.env.CORP607_WEB_ORIGIN ?? "http://127.0.0.1:52707");
  assert.equal(origin.hostname, "127.0.0.1", "Use only the isolated loopback Web");
  assert.equal(origin.pathname, "/");
  assert.equal(origin.username + origin.password, "");
  const versionsPath = process.env.CORP607_VERSIONS_FILE;
  assert(versionsPath, "Supply the accepted Web/Core/Collector/schema identity JSON");
  const buildBytes = await readFile(versionsPath);
  const build = JSON.parse(buildBytes.toString("utf8").replace(/^\uFEFF/, ""));
  const components = build.components ?? build;
  const collector = components.analytics_runtime_including_collector_and_worker ?? components.collector;
  const versions = {
    head: build.head, web: components.web, core: components.core, collector,
    worker: components.worker ?? collector, schema: components.schema,
  };
  for (const component of ["web", "core", "collector", "schema"]) assert(versions[component], "Missing version key " + component);

  const defaultLearner = {
    first: "first", reload: "new_lifecycle", "both-lost": "lost", "account-switch": "scope",
    "device-first": "outside_cohort", "device-second": "outside_cohort",
  }[name] ?? "multi";
  const learnerName = process.env.CORP607_LEARNER ?? defaultLearner;
  assert.match(learnerName, /^[a-z_]+$/);
  const ownerFile = resolve(".local", "local-live", runId, "corp607-learners.json");
  const relayFiles = new Map();
  const grantsToRelay = [
    [legacyGrant, process.env.CORP607_V2_GRANT_CLOCK_FILE, "corp607-v2-clock.json"],
    [windowsGrant, process.env.CORP607_WINDOWS_GRANT_CLOCK_FILE, "corp607-windows-clock.json"],
  ];
  for (const [path, input, file] of grantsToRelay) {
    if (!input) continue;
    const clockFile = resolve(input);
    assert.equal(clockFile, resolve(".local", "local-live", runId, file), "Use only the run owner's designated clock file");
    relayFiles.set(path, clockFile);
  }
  const learners = JSON.parse(await readFile(ownerFile, "utf8"));
  const learner = learners[learnerName];
  assert(learner?.email && learner.password && learner.subject_ref, "Selected learner is missing from the private owner record");
  const secondLearner = name === "account-switch" ? learners.privacy : null;
  if (name === "account-switch") assert(secondLearner?.email && secondLearner.password &&
    secondLearner.subject_ref !== learner.subject_ref, "Fresh B lifecycle in the private owner record");
  let confirmedSubject = learner.subject_ref;
  let authPhase = name === "account-switch" ? "A" : "learner";
  let priorAccountV2;
  const priorAccountPath = process.env.CORP607_ACCOUNT_A_EVIDENCE;
  if (name === "account-switch" && priorAccountPath) {
    const priorBytes = await readFile(priorAccountPath);
    const prior = JSON.parse(priorBytes.toString("utf8").replace(/^\uFEFF/, ""));
    assert.equal(prior.scenario, "account-switch");
    assert.equal(prior.run_id, runId);
    assert.equal(prior.versions_sha256, sha(buildBytes));
    assert.equal(prior.student_id, learner.subject_ref);
    const facts = confirmedLegacy(prior.requests).filter(row => row.phase === "A");
    assert.equal(facts.reduce((ms, row) => ms + row.event.active_ms, 0), 60_000,
      "The previous attempt already recorded exactly one minute for A");
    priorAccountV2 = { trace_sha256: sha(priorBytes), facts };
  }
  const playwrightModule = process.env.CORP607_PLAYWRIGHT_MODULE;
  assert(playwrightModule, "Point CORP607_PLAYWRIGHT_MODULE at the installed Playwright");
  const { chromium } = createRequire(import.meta.url)(playwrightModule);
  const port = Number(process.env.CORP607_TIMELINE_DEBUG_PORT ?? 52709);
  assert(Number.isInteger(port) && port > 1024 && port <= 65535);
  const launched = await chromium.launch({ channel: "chromium",
    args: ["--remote-debugging-address=127.0.0.1", "--remote-debugging-port=" + port] });
  const evidence = {
    schema_version: 1, run_id: runId, versions, versions_sha256: sha(buildBytes), scenario: name,
    learner_alias: learnerName, student_id: learner.subject_ref,
    browser: { origin: origin.origin, lesson_tabs: plan.steps.length, ...anchorEvidence },
    browser_steps: [], catalog: [], grants: [], requests: [], browser_faults: [], windows: [], grant_clock_relays: [],
  };

  const pages = [];
  const requests = new Map();
  const pendingReads = new Set();
  let outcome = "FAIL";
  let routeState = "normal";
  let lost;
  let clockAt = initialAnchor;

  function captureResponse(response) {
    const entry = requests.get(response.request());
    if (!entry) return;
    const job = response.json().then(body => {
      entry.status = response.status();
      if (entry.path === windowsGrant) {
        const { grant, ...publicEnvelope } = body;
        entry.response = publicEnvelope;
        entry.catalog_scope = false;
        if (typeof grant === "string") {
          const parts = grant.split(".");
          const claims = parts.length === 3 ? JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8")) : {};
          entry.catalog_scope = claims.tool_scopes?.some(scope => scope.tool_id === "catalog" && scope.course_id === null) === true;
        }
        evidence.grants.push(entry);
      } else if (entry.path === legacyGrant) {
        entry.response = { server_time: body.server_time, expires_at: body.expires_at };
      } else if (entry.path === "/api/learning/analytics-catalog") {
        entry.response = { published: body.lesson_ids?.includes(lesson.lesson_id) ?? false,
          collection_mode: response.headers()["x-analytics-collection-mode"] };
        evidence.catalog.push(entry);
      } else entry.response = body;
    }).catch(() => { entry.status = response.status(); entry.unreadable = true; });
    pendingReads.add(job);
    void job.finally(() => pendingReads.delete(job));
  }

  try {
    const browser = await chromium.connectOverCDP("http://127.0.0.1:" + port, { noDefaults: true, timeout: 5000 });
    evidence.browser.chromium = browser.version();
    const profile = browser.contexts()[0];
    profile.on("request", request => {
      const url = new URL(request.url());
      if (url.origin !== origin.origin || ![windowsGrant, windowsBatch, legacyBatch, legacyGrant,
        "/api/learning/analytics-catalog"].includes(url.pathname)) return;
      const raw = request.postDataBuffer();
      const entry = { path: url.pathname, clock_utc: iso(clockAt), phase: authPhase };
      if (raw) {
        try { entry.body = JSON.parse(raw); } catch { entry.invalid_json = true; }
        entry.body_sha256 = sha(raw);
      }
      requests.set(request, entry);
    });
    profile.on("response", captureResponse);
    profile.on("requestfinished", request => {
      const entry = requests.get(request);
      if (entry) entry.finished = true;
    });
    profile.on("requestfailed", request => {
      const entry = requests.get(request);
      if (entry) {
        entry.finished = true;
        entry.transport_failure = request.failure()?.errorText ?? "failed";
      }
    });

    if (relayFiles.size) {
      let relay = Promise.resolve();
      await profile.route(url => url.origin === origin.origin && relayFiles.has(url.pathname), route => {
        if (route.request().method() !== "POST") return route.fallback();
        const path = new URL(route.request().url()).pathname;
        const clockFile = relayFiles.get(path);
        const send = relay.then(async () => {
          const at = await route.request().frame().page().evaluate(() => new Date().toISOString());
          const temporary = clockFile + "." + randomUUID() + ".tmp";
          try {
            await writeFile(temporary, JSON.stringify({ at }) + "\n", { mode: 0o600, flag: "wx" });
            await rename(temporary, clockFile);
            const response = await route.fetch({ maxRedirects: 0 });
            evidence.grant_clock_relays.push({ path, at, status: response.status() });
            await route.fulfill({ response });
          } finally {
            await rm(temporary, { force: true });
          }
        });
        relay = send.catch(() => undefined);
        return send.catch(() => route.abort("failed"));
      });
    }

    if (name === "reload") {
      // Keep the real published catalog available during transport-only outages.
      await profile.route(url => url.origin === origin.origin && url.pathname === "/api/learning/analytics-catalog",
        async route => {
          const response = await route.fetch({ maxRedirects: 0 });
          await route.fulfill({ response });
        });
      await profile.route(url => url.origin === origin.origin && [windowsGrant, windowsBatch].includes(url.pathname), async route => {
        const path = new URL(route.request().url()).pathname;
        if (routeState === "offline") { await route.abort("internetdisconnected"); return; }
        if (routeState !== "drop-first-ack" || path !== windowsBatch) { await route.fallback(); return; }
        routeState = "relaying";
        try {
          const raw = route.request().postDataBuffer();
          const response = await route.fetch({ maxRedirects: 0 });
          const ack = await response.json();
          if (response.ok() && ack.items?.some(item => item.status === "committed" && item.server_epoch === 1 &&
            item.seq === 4 && item.confirmed?.stats?.[0]?.active_ms === 480_000)) {
            lost = { body_sha256: sha(raw), body: JSON.parse(raw), status: response.status(), ack };
            routeState = "offline";
            await route.abort("internetdisconnected");
          } else {
            routeState = "drop-first-ack";
            await route.fulfill({ response });
          }
        } catch {
          routeState = "drop-first-ack";
          await route.abort("failed");
        }
      });
    }

    if (name === "account-switch") {
      await profile.route(url => url.origin === origin.origin && url.pathname === windowsBatch,
        route => authPhase === "A" ? route.abort("internetdisconnected") : route.fallback());
      if (priorAccountV2) await profile.route(url => url.origin === origin.origin && url.pathname === legacyBatch,
        route => authPhase === "A" ? route.abort("internetdisconnected") : route.fallback());
    }
    if (name === "both-lost") {
      await profile.route(url => url.origin === origin.origin && [windowsBatch, legacyBatch].includes(url.pathname),
        route => route.abort("internetdisconnected"));
    }

    const login = await profile.request.post(origin.origin + "/api/user/login", {
      headers: { Origin: origin.origin, Referer: origin.origin + "/backend", "Idempotency-Key": randomUUID() },
      data: { email: learner.email, password: learner.password },
    });
    assert.equal(login.status(), 200, "Synthetic learner login");
    const who = await profile.request.get(origin.origin + "/api/user/me");
    assert.equal(who.status(), 200, "Current browser session");
    const { user } = await who.json();
    assert.equal(user.account_ref, learner.subject_ref, "Expected lifecycle");
    assert.equal(user.role, "user", "Learner role");

    const control = profile.pages()[0] ?? await profile.newPage();
    await control.clock.install({ time: new Date(initialAnchor - 1000) });
    await control.clock.pauseAt(new Date(initialAnchor));
    for (const [kind] of plan.steps) {
      const page = await profile.newPage();
      pages.push({ page, kind });
      page.on("pageerror", error => evidence.browser_faults.push(error.name));
      await page.setViewportSize({ width: 1440, height: 980 });
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.goto(origin.origin + (kind === "catalog" ? "/catalog" : "/backend"), { waitUntil: "domcontentloaded" });
      if (kind !== "catalog") await prepareTab(page, kind);
      if (name === "account-switch") {
        await page.getByRole("button", { name: "Открыть личный кабинет" }).click({ force: true });
        await page.locator(".dv-tab").filter({ hasText: "Личный кабинет" }).first().click({ force: true });
      }
    }
    await control.bringToFront();
    await checkFocus(-1);
    await until(() => evidence.grants, rows => rows.some(row => row.status === 200 &&
      row.response?.subject_ref === learner.subject_ref && Date.parse(row.response?.server_time) === initialAnchor),
    "the trusted managed Core clock");
    if (name === "five") assert(evidence.grants.some(row => row.status === 200 && row.catalog_scope),
      "The managed multi learner must be granted the global catalog/null scope");
    if (name === "account-switch") {
      const page = pages[0].page, accountWarmup = Math.floor(warmup / 2);
      await advance(accountWarmup);
      await page.getByRole("tab", { name: "Настройки", includeHidden: true }).waitFor({ state: "attached", timeout: 15_000 });
      await page.bringToFront();
      await clickLessonTab(page, "theory");
      await control.bringToFront();
      await checkFocus(-1);
      await advance(warmup - accountWarmup);
    } else await advance(warmup);

    for (let index = 0; index < pages.length; index++) {
      const { page, kind } = pages[index];
      await page.bringToFront();
      await checkFocus(index);
      if (name === "account-switch") await lessonPanel(page, "theory").waitFor({ state: "visible", timeout: 20_000 });
      else await selectPanel(page, kind);
      if (index === 0) await control.close();
      const begin = await readFocus(page);
      assert.equal(Date.parse(begin.utc), clockAt);
      assert(begin.focused && begin.visibility === "visible");
      if (kind !== "catalog") await page.keyboard.press("Shift");
      if (name === "reload") await prepareReplayTimeline(profile, page);
      else await advance(plan.steps[index][1]);
      evidence.browser_steps.push({ tab: index + 1, kind, duration_ms: plan.steps[index][1], start: begin, end: await readFocus(page) });
    }

    const distractor = await profile.newPage();
    await distractor.bringToFront();
    await checkFocus(-1);
    assert.equal(clockAt, start + duration, "Exact final focus boundary");
    evidence.focus_left_at = iso(clockAt);
    await drainTransport();
    if (name === "account-switch") await switchAccount(profile, pages[0].page, distractor);

    if (name === "reload") {
      routeState = "drop-first-ack";
      await profile.setOffline(false);
      await advance(5000);
      await until(() => lost, Boolean, "a real committed 480000 response to drop");
      evidence.lost_ack = lost;
      const resumeFile = process.env.CORP607_RELOAD_WAIT_FILE;
      if (resumeFile) {
        const checkpoint = output + ".before-reload.json";
        await writeFile(checkpoint, JSON.stringify({ run_id: runId, versions_sha256: evidence.versions_sha256,
          student_id: learner.subject_ref, lost_ack: lost }) + "\n", { mode: 0o600, flag: "wx" });
        await until(async () => access(resumeFile).then(() => true, () => false), Boolean, "owner PostgreSQL checkpoint", 90_000);
      }
      await pages[0].page.reload({ waitUntil: "domcontentloaded" });
      await distractor.bringToFront();
      await checkFocus(-1);
      routeState = "normal";
    }
    await advance(plan.drain);
    if (name === "both-lost") {
      await until(() => Array.from(requests.values()), rows =>
        [windowsBatch, legacyBatch].every(path => rows.some(row => row.path === path && row.transport_failure)),
      "both pre-commit transport failures");
    } else {
      const expectedStatuses = name === "device-second" ? ["pending"] : ["committed", "duplicate"];
      await until(() => Array.from(requests.values()), rows => rows.some(row => row.path === windowsBatch &&
        row.response?.items?.some(item => expectedStatuses.includes(item.status))), "a matching window acknowledgement");
    }
    await Promise.all(pendingReads);
    evidence.requests = Array.from(requests.values());
    const normalized = new Map();
    for (const request of evidence.requests) {
      if (request.path !== windowsBatch || request.status !== 200) continue;
      for (const ack of request.response?.items ?? []) {
        if (!["committed", "duplicate"].includes(ack.status) || !ack.confirmed) continue;
        const identity = { student_id: confirmedSubject, activity_date: ack.activity_date, slot_no: ack.slot_no };
        const key = identity.activity_date + "/" + identity.slot_no;
        const previous = normalized.get(key);
        if (!previous || ack.confirmed.revision >= previous.ack.confirmed.revision) {
          normalized.set(key, { key: identity, ack, delivery: ack.status, request_body_sha256: request.body_sha256 });
        }
      }
    }
    evidence.windows = [...normalized.values()];
    evidence.v2_confirmed = [...(priorAccountV2?.facts ?? []), ...confirmedLegacy(evidence.requests)];
    if (name === "account-switch") {
      const oldWriter = evidence.account_boundary.a.writer_id;
      const newWriter = evidence.account_boundary.b.writer_id;
      for (const request of evidence.requests.filter(row => row.path === windowsBatch)) {
        if (request.phase === "A") {
          assert(request.transport_failure && request.body.windows.every(row => row.writer_id === oldWriter));
        } else {
          assert.equal(request.phase, "B");
          assert(request.body.windows.every(row => row.writer_id === newWriter && row.writer_id !== oldWriter));
        }
      }
      assert.equal(evidence.v2_confirmed.filter(row => row.phase === "B").reduce((ms, row) => ms + row.event.active_ms, 0), 60_000);
    }
    if (name === "first") {
      const initial = evidence.requests.flatMap(request => request.path === windowsBatch
        ? request.response?.items ?? [] : []).find(item => item.status === "committed");
      assert.equal(initial?.seq, 1);
      assert.deepEqual(initial.confirmed.stats, expected[0].stats, "The first committed ordinary lesson is one exact minute");
    }
    const sortStats = stats => [...stats].sort((a, b) => statKey(a).localeCompare(statKey(b)));
    const byKey = rows => [...rows].sort((a, b) =>
      a.activity_date.localeCompare(b.activity_date) || a.slot_no - b.slot_no);
    if (name === "device-second") {
      assert.deepEqual(evidence.windows, []);
      const firstPath = process.env.CORP607_DEVICE_FIRST_EVIDENCE;
      assert(firstPath, "Provide the accepted first independent Chromium evidence");
      const firstBytes = await readFile(firstPath);
      const first = JSON.parse(firstBytes.toString("utf8").replace(/^\uFEFF/, ""));
      assert.equal(first.scenario, "device-first");
      assert.equal(first.run_id, runId);
      assert.equal(first.versions_sha256, evidence.versions_sha256);
      assert.equal(first.student_id, learner.subject_ref);
      const reported = evidence.requests.find(row => row.path === windowsBatch &&
        row.response?.items?.some(item => item.status === "pending"));
      const snapshot = reported.body.windows[0], ack = reported.response.items[0];
      assert.equal(reported.status, 200);
      assert.equal(ack.code, "ownership_conflict");
      assert.equal(ack.writer_id, snapshot.writer_id);
      assert.notEqual(ack.writer_id, first.windows[0].ack.writer_id);
      assert.deepEqual({ activity_date: snapshot.activity_date, slot_no: snapshot.slot_no, stats: snapshot.stats }, expected[0]);
      evidence.device_conflict = { predecessor_trace_sha256: sha(firstBytes), predecessor: first.windows[0], snapshot, ack };
    } else if (name === "both-lost") {
      assert.deepEqual(evidence.windows, []);
      const firstWindowAttempt = evidence.requests.find(row => row.path === windowsBatch).body.windows[0];
      assert.deepEqual({
        activity_date: firstWindowAttempt.activity_date, slot_no: firstWindowAttempt.slot_no, stats: firstWindowAttempt.stats,
      }, expected[0], "The blocked v3 attempt is the independently expected window");
      const firstLegacyAttempt = evidence.requests.find(row => row.path === legacyBatch).body.events;
      assert.equal(firstLegacyAttempt.reduce((sum, row) => sum + row.active_ms, 0), 40_000);
      assert(evidence.requests.filter(row => [windowsBatch, legacyBatch].includes(row.path)).every(row => row.transport_failure),
        "Neither telemetry path received a server response");
    } else {
      assert.deepEqual(byKey(evidence.windows.map(({ key, ack }) => ({
        activity_date: key.activity_date, slot_no: key.slot_no, stats: sortStats(ack.confirmed.stats),
      }))), byKey(expected.map(row => ({ ...row, stats: sortStats(row.stats) }))), "Window ACKs match the independent scenario");
    }
    function statKey(row) { return JSON.stringify([row.tool_id, row.course_id, row.lesson_id]); }
    if (name === "reload") {
      assert(evidence.requests.some(request => request.path === windowsBatch && request.body_sha256 === lost.body_sha256 &&
        request.response?.items?.some(item => item.status === "duplicate")), "Reload must retry the exact committed lost-ACK bytes");
    }
    evidence.observation = "Browser HTTP only. The acceptance owner reads PostgreSQL before repair and joins it by full key.";
    outcome = "PASS_BROWSER_CAPTURE";
  } catch (error) {
    evidence.error = error.message.split("\n")[0];
    evidence.requests = Array.from(requests.values());
    evidence.diagnostics = await Promise.all(pages.map(async ({ page, kind }) => ({
      kind, observed: await page.evaluate(() => ({
        url: location.pathname,
        tabs: [...document.querySelectorAll(".dv-tab")].map(item => ({ name: item.textContent, class: item.className })),
        panels: [...document.querySelectorAll("article.dock-lesson")].map(item => ({
          label: item.getAttribute("aria-label"), visible: Boolean(item.getClientRects().length),
        })),
        selected: [...document.querySelectorAll(".lesson-row.is-active")].map(item => item.textContent?.trim()),
      })).catch(() => null),
    })));
    throw error;
  } finally {
    evidence.result = outcome;
    try {
      await writeFile(output, JSON.stringify(evidence, null, 2) + "\n", { mode: 0o600, flag: "wx" });
    } finally {
      await launched.close();
    }
  }

  async function switchAccount(profile, page, distractor) {
    const attempts = () => Array.from(requests.values()).filter(row => row.path === windowsBatch);
    const before = await until(() => readStoredWindowPartition(page), state =>
      state?.subject === learner.subject_ref && state.pending.length === 1, "durable A pending body");
    const pendingSha = sha(Buffer.from(before.pending[0]));
    assert(attempts().some(row => row.transport_failure && row.body_sha256 === pendingSha));
    await page.bringToFront();
    await checkFocus(0);
    const account = page.getByRole("button", { name: "Открыть личный кабинет" });
    await page.locator(".dv-tab").filter({ hasText: "Личный кабинет" }).first().click({ force: true });
    await distractor.bringToFront();
    await checkFocus(-1);
    await advance(500);
    await page.bringToFront();
    await checkFocus(0);
    await page.getByRole("tab", { name: "Настройки" }).click();
    const loggedOut = page.waitForResponse(response =>
      new URL(response.url()).pathname === "/api/user/logout" && response.request().method() === "POST");
    await page.getByRole("button", { name: "Выйти", exact: true }).click();
    const logout = await loggedOut;
    assert(logout.ok(), "Ordinary UI logout");
    authPhase = "guest";
    const guest = await profile.request.get(origin.origin + "/api/user/me");
    assert(guest.status() === 401 || (guest.ok() && !(await guest.json()).user), "A's cookie is detached");
    await account.click({ force: true });
    const form = page.locator("form.auth-form");
    await form.getByLabel("Email").fill(secondLearner.email);
    await form.getByLabel("Пароль", { exact: true }).fill(secondLearner.password);
    const loggedIn = page.waitForResponse(response =>
      new URL(response.url()).pathname === "/api/user/login" && response.request().method() === "POST");
    authPhase = "B";
    await form.getByRole("button", { name: "Войти", exact: true }).click();
    assert((await loggedIn).ok(), "Ordinary UI login for B");
    const who = await profile.request.get(origin.origin + "/api/user/me");
    assert.equal(who.status(), 200);
    assert.equal((await who.json()).user.account_ref, secondLearner.subject_ref);

    await prepareTab(page, "theory");
    await clickLessonTab(page, "theory");
    await distractor.bringToFront();
    await checkFocus(-1);
    await advance(500);
    await page.bringToFront();
    await checkFocus(0);
    await lessonPanel(page, "theory").waitFor({ state: "visible", timeout: 20_000 });
    const after = await until(() => readStoredWindowPartition(page), state =>
      state?.subject === secondLearner.subject_ref, "the verified new lifecycle in the same IndexedDB");
    assert.notEqual(after.writer_id, before.writer_id);
    assert.deepEqual(after.pending, []);
    await drainTransport();
    const reportUrl = origin.origin + "/api/user/analytics/statistics?days=7&course_id=backend";
    const beforeReport = await profile.request.get(reportUrl);
    assert.equal(beforeReport.status(), 200);
    const baseline = (await beforeReport.json()).summary?.theory_seconds ?? 0;

    confirmedSubject = secondLearner.subject_ref;
    const startB = await readFocus(page);
    await page.keyboard.press("Shift");
    await advance(60_000);
    evidence.browser_steps.push({ phase: "B", tab: 1, kind: "theory", duration_ms: 60_000, start: startB, end: await readFocus(page) });
    await distractor.bringToFront();
    await checkFocus(-1);
    await drainTransport();
    const report = await until(async () => {
      const response = await profile.request.get(reportUrl);
      return { status: response.status(), summary: (await response.json()).summary };
    }, row => row.status === 200 && row.summary?.theory_seconds === baseline + 60, "B's ordinary v2 learner report");
    evidence.account_boundary = {
      logout: { status: logout.status(), guest_status: guest.status() }, same_partition: true,
      a: { student_id: before.subject, writer_id: before.writer_id, pending_body_sha256: pendingSha,
        prior_v2_trace_sha256: priorAccountV2?.trace_sha256 },
      b: { student_id: after.subject, writer_id: after.writer_id, pending_at_login: after.pending.length,
        report_before_theory_seconds: baseline, report_after: report },
    };
  }

  async function prepareReplayTimeline(profile, page) {
    const committed = seq => Array.from(requests.values()).some(row => row.path === windowsBatch &&
      row.response?.items?.some(item => item.status === "committed" && item.server_epoch === 1 && item.seq === seq &&
        item.confirmed?.stats?.[0]?.active_ms === seq * 120_000));
    await profile.setOffline(true);
    for (let seq = 1; seq <= 3; seq++) {
      if (seq === 3) {
        await advance(45_000);
        const prior = evidence.grants.filter(row => row.status === 200).length;
        await profile.setOffline(false);
        await until(() => evidence.grants, rows => rows.filter(row => row.status === 200).length > prior,
          "grant renewal while the third snapshot is still below its next deadline");
        await page.keyboard.press("Shift");
        await profile.setOffline(true);
        await advance(75_000);
      } else await advance(120_000);
      await profile.setOffline(false);
      await until(() => committed(seq), Boolean, "cumulative upstream ACK " + seq);
      await profile.setOffline(true);
    }
    await advance(120_000);
  }

  async function checkFocus(index) {
    return until(() => Promise.all(pages.map(({ page }) => readFocus(page))), states =>
      states.every((state, i) => state.focused === (i === index) && state.visibility === (i === index ? "visible" : "hidden")),
    "exclusive native browser focus");
  }

  async function drainTransport() {
    let quietSince;
    const deadline = Date.now() + 12_000;
    while (Date.now() < deadline) {
      const pending = [...requests.values()].some(entry => !entry.finished);
      quietSince = pending ? undefined : quietSince ?? Date.now();
      if (quietSince !== undefined && Date.now() - quietSince >= 175) return;
      await delay(50);
    }
    throw new Error("Real Analytics HTTP remained in flight with the browser clock frozen");
  }

  async function advance(ms) {
    for (let advanced = 0; advanced < ms; advanced += 5000) {
      const piece = Math.min(ms - advanced, 5000);
      clockAt += piece;
      await pages[0].page.clock.runFor(piece);
      await drainTransport();
    }
  }
}

function confirmedLegacy(requests) {
  const events = new Map();
  for (const request of requests) {
    if (request.path !== legacyBatch || request.status !== 200) continue;
    for (const ack of request.response?.items ?? []) {
      const event = request.body?.events?.[ack.index];
      if (!event || ack.event_id !== event.event_id || !["accepted", "duplicate"].includes(ack.status)) continue;
      if (!events.has(event.event_id)) events.set(event.event_id, { event, ack, phase: request.phase, body_sha256: request.body_sha256 });
    }
  }
  return [...events.values()];
}

function readStoredWindowPartition(page) {
  return page.evaluate(() => new Promise((resolve, reject) => {
    const open = indexedDB.open("knowi:analytics:windows:v1", 1);
    open.onupgradeneeded = () => { open.transaction.abort(); resolve(null); };
    open.onerror = () => reject(open.error);
    open.onsuccess = () => {
      const db = open.result;
      try {
        const transaction = db.transaction("partition", "readonly");
        const read = transaction.objectStore("partition").get("current");
        read.onsuccess = () => {
          const data = read.result;
          resolve(data ? {
            subject: data.subject, writer_id: data.writer.writer_id,
            pending: Object.values(data.windows).flatMap(window => window.pending ? [window.pending.body] : []),
          } : null);
        };
        read.onerror = () => reject(read.error);
        transaction.oncomplete = () => db.close();
      } catch (error) {
        db.close();
        reject(error);
      }
    };
  }));
}

async function prepareTab(page, kind) {
  const chapter = page.getByRole("button", { name: "Выбрать главу" });
  const lessonDrawer = page.getByRole("button", { name: "Открыть список занятий" });
  await chapter.or(lessonDrawer).first().waitFor({ timeout: 20_000 });
  if (!(await chapter.isVisible())) await lessonDrawer.click({ force: true });
  await chapter.click({ force: true });
  await page.getByRole("dialog", { name: "Главы курса" }).getByRole("button", { name: /FastAPI и базы данных/ })
    .click({ force: true });
  const row = page.locator("button.lesson-row").filter({ hasText: lesson.title });
  await row.waitFor({ state: "attached", timeout: 20_000 });
  const expander = page.locator(".module-group").filter({ has: row }).locator("button.module-toggle");
  if (await expander.getAttribute("aria-expanded") !== "true") await expander.press("Enter");
  assert.doesNotMatch(await row.getAttribute("class"), /is-locked/);
  await row.press("Enter");
  await page.getByRole("button", { name: kind === "theory" ? "Открыть теорию" : "Открыть практику" }).press("Enter");
}

async function selectPanel(page, kind) {
  if (kind === "catalog") {
    await page.getByRole("main", { name: "Каталог курсов" }).waitFor({ timeout: 20_000 });
    return;
  }
  await clickLessonTab(page, kind);
  await lessonPanel(page, kind).waitFor({ state: "visible", timeout: 20_000 });
}

function clickLessonTab(page, kind) {
  const title = kind === "theory" ? "Теория" : "Практика";
  return page.locator(".dv-tab").filter({ hasText: "71 " + title }).first().click({ force: true, timeout: 15_000 });
}

function lessonPanel(page, kind) {
  const title = kind === "theory" ? "Теория" : "Практика";
  return page.locator("article.dock-lesson").and(page.locator(
    'article[aria-label^="' + title + ':"][aria-label*="' + lesson.title + '"]'));
}
