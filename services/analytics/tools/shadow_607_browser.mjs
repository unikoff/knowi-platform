import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
import { readFile, rename, rm, writeFile } from "node:fs/promises";
import { setTimeout as delay } from "node:timers/promises";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";

const require = createRequire(import.meta.url);
const browserModule = process.env.CORP607_PLAYWRIGHT_MODULE;
assert(browserModule, "CORP607_PLAYWRIGHT_MODULE must point to the installed Playwright package");
const { chromium } = require(browserModule);
const run = process.env.CORP607_RUN;
assert.match(run ?? "", /^[0-9a-f-]{36}$/);
const fixture = process.argv[2] ?? "first";
const phase = process.env.CORP607_MODE ?? "baseline";
assert(["baseline", "shadow", "outside", "offphase"].includes(phase));
const shadow = phase === "shadow" || phase === "offphase";
const offphase = phase === "offphase";
const output = process.argv[3];
assert(output, "supply a new private evidence path");
const debugPort = 52708;
const origin = "http://127.0.0.1:52707";
const reportPath = "/api/user/analytics/statistics?days=7&course_id=backend";
const lessonTitle = "Создаём и заменяем задачу в SQLite";
const lessonId = "lesson-64c9d597a164bbdb4f145d5a";
const learnerPath = resolve(".local", "local-live", run, "corp607-learners.json");
const learner = JSON.parse(await readFile(learnerPath, "utf8"))[fixture];
assert(learner, "unknown named fixture");

const requestedDate = process.env.CORP607_FIRST_WINDOW_DATE;
const activityDate = requestedDate ?? "2026-11-27";
const startUtc = activityDate + "T07:00:00.000Z";
const endUtc = activityDate + "T07:01:00.000Z";
const stats = [{ tool_id: "theory", course_id: "backend", lesson_id: lessonId, active_ms: 60000 }];
const v2GrantPath = "/api/analytics/grants/collect";
const baselinePath = process.env.CORP607_BASELINE_EVIDENCE ??
  "../../docs/analytics/evidence-598/607/baseline-v2.json";
let expectationDigest;
let baselineDigest;
let baselineReport;
let v2ClockFile;
if (requestedDate) {
  assert(["baseline", "shadow"].includes(phase), "a translated date is reserved for ordinary 607.B");
  assert.match(activityDate, /^\d{4}-\d{2}-\d{2}$/);
  const startMs = Date.parse(startUtc);
  assert.equal(new Date(startMs).toISOString(), startUtc, "valid calendar date");
  const serverNow = Date.now();
  assert(Date.parse(endUtc) <= serverNow && serverNow <= startMs + (24 * 60 + 15) * 60000,
    "the minute must be past and within the normal admission horizon");
  const expectationFile = process.env.CORP607_FIRST_EXPECTED_FILE;
  assert(expectationFile, "supply the first-window expectation recorded before the run");
  const bytes = await readFile(expectationFile);
  const expected = JSON.parse(bytes.toString("utf8").replace(/^\uFEFF/, ""));
  assert.equal(expected.subject_ref, learner.subject_ref);
  assert.equal(expected.activity_date, activityDate);
  assert.equal(expected.slot_no, 41);
  assert.equal(expected.timezone, "Europe/Moscow");
  assert.equal(expected.start, activityDate + "T10:00:00+03:00");
  assert.equal(expected.end, activityDate + "T10:01:00+03:00");
  assert.deepEqual(expected.stats, stats);
  assert.deepEqual(expected.expected, { early_window_count: 1, first_revision: 1, first_outcome: "committed",
    replay_outcome: "duplicate", reconciliation_enabled: 0, ordinary_v2_active_seconds: 60 });
  expectationDigest = digest(bytes);
  assert(process.env.CORP607_V2_GRANT_CLOCK_FILE, "provide the owned Core metadata clock file");
  v2ClockFile = resolve(process.env.CORP607_V2_GRANT_CLOCK_FILE);
  assert.equal(v2ClockFile, resolve(dirname(learnerPath), "corp607-v2-clock.json"), "use this run's private clock file");
  if (shadow) assert(process.env.CORP607_BASELINE_EVIDENCE, "use the v2-only evidence for the same fixture date");
}
if (shadow && !offphase) {
  const bytes = await readFile(baselinePath);
  const baseline = JSON.parse(bytes.toString("utf8").replace(/^\uFEFF/, ""));
  baselineReport = baseline.report_after;
  assert.equal(baselineReport.status, 200, "the v2-only report succeeded before opening shadow");
  if (requestedDate) {
    assert.equal(baseline.first_expectation_sha256, expectationDigest, "baseline evidence must match the predeclared fixture");
    assert(baselineReport.body.days.some(row => row.date === activityDate && row.theory_seconds === 60),
      "matched baseline must show the expected minute at this date");
  }
  baselineDigest = digest(bytes);
}

const outer = await chromium.launch({ headless: true, channel: "chromium",
  args: ["--remote-debugging-address=127.0.0.1", "--remote-debugging-port=" + debugPort] });
const browser = await chromium.connectOverCDP("http://127.0.0.1:" + debugPort, { noDefaults: true });
const evidence = { run, fixture, origin, subject_ref: learner.subject_ref, browser: browser.version(),
  known_model_date: "2026-11-27", fixture_activity_date: activityDate, reportPath,
  first_expectation_sha256: expectationDigest, baseline_sha256: baselineDigest,
  lesson: { course_id: "backend", lesson_id: lessonId },
  observed: [], v2_acks: [], events: [] };
const windowAcks = [];
const windowGrants = [];

let page;
try {
  const context = browser.contexts()[0];
  const inFlight = new Set();
  const settleAnalytics = async () => {
    if (requestedDate) await waitUntil(() => inFlight.size === 0, 15000);
  };
  if (requestedDate) {
    const analytics = [v2GrantPath, "/api/analytics/events/batch", "/api/analytics/windows/grant",
      "/api/analytics/windows/batch"];
    context.on("request", request => {
      const url = new URL(request.url());
      if (url.origin === origin && analytics.includes(url.pathname)) inFlight.add(request);
    });
    context.on("requestfinished", request => inFlight.delete(request));
    context.on("requestfailed", request => inFlight.delete(request));
    let nextGrant = Promise.resolve();
    await context.route(origin + v2GrantPath, route => {
      if (route.request().method() !== "POST") return route.fallback();
      const send = nextGrant.then(async () => {
        const at = await route.request().frame().page().evaluate(() => new Date().toISOString());
        const temporary = v2ClockFile + "." + randomUUID() + ".tmp";
        try {
          await writeFile(temporary, JSON.stringify({ at }) + "\n", { mode: 0o600, flag: "wx" });
          await rename(temporary, v2ClockFile);
          const response = await route.fetch({ maxRedirects: 0 });
          await route.fulfill({ response });
        } finally {
          await rm(temporary, { force: true });
        }
      });
      nextGrant = send.catch(() => undefined);
      return send.catch(() => route.abort("failed"));
    });
  }
  page = context.pages()[0] ?? await context.newPage();
  await page.setViewportSize({ width: 1440, height: 980 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  const errs = new Set();
  page.on("pageerror", error => errs.add(error.name));
  page.on("response", async response => {
    const url = new URL(response.url());
    if (url.origin !== origin) return;
    try {
      if (url.pathname === "/api/learning/analytics-catalog" && response.ok()) {
        const catalog = await response.json();
        evidence.observed.push({ path: url.pathname, status: response.status(),
          collection_mode: response.headers()["x-analytics-collection-mode"], published: catalog.lesson_ids.includes(lessonId) });
      }
      if (url.pathname === "/api/analytics/grants/collect" && response.ok()) {
        const grant = await response.json();
        evidence.observed.push({ path: url.pathname, status: response.status(), server_time: grant.server_time });
      }
      if (url.pathname === "/api/analytics/windows/grant") {
        const { grant: unused, ...body } = await response.json();
        windowGrants.push({ status: response.status(), body });
      }
      if (url.pathname === "/api/analytics/windows/batch") {
        const raw = response.request().postDataBuffer();
        const body = await response.json();
        windowAcks.push({ status: response.status(), body, raw, request: response.request() });
      }
      if (url.pathname === "/api/analytics/events/batch") {
        const body = JSON.parse(response.request().postDataBuffer().toString("utf8"));
        evidence.events.push(...body.events.filter(event => event.type === "activity_segment"));
        evidence.v2_acks.push({ status: response.status(), body: await response.json() });
      }
    } catch {
      evidence.observed.push({ path: url.pathname, status: response.status(), readable: false });
    }
  });
  const login = await context.request.post(origin + "/api/user/login", {
    headers: { Origin: origin, Referer: origin + "/backend", "Idempotency-Key": randomUUID() },
    data: { email: learner.email, password: learner.password },
  });
  assert.equal(login.status(), 200, "synthetic learner login");
  const profile = await context.request.get(origin + "/api/user/me");
  assert.equal(profile.status(), 200, "session principal");
  const { user: principal } = await profile.json();
  assert.equal(principal.account_ref, learner.subject_ref, "fixture lifecycle");
  if (requestedDate && shadow) {
    evidence.readiness_before = { core: await readiness(18041, "/health/ready"), collector: await readiness(18042, "/ready"),
      query: await readiness(18043, "/ready"), privacy: await readiness(18045, "/ready") };
    assert.deepEqual(evidence.readiness_before, { core: 200, collector: 200, query: 200, privacy: 200 });
    const metrics = await fetch("http://127.0.0.1:18044/metrics", { signal: AbortSignal.timeout(5000) });
    assert.equal(metrics.status, 200);
    assert.match(await metrics.text(), /^analytics_windows_reconciliation_enabled 0(?:\.0)?$/m);
    evidence.worker_reconciliation_enabled = 0;
  }
  const before = await context.request.get(origin + reportPath);
  evidence.report_before = { status: before.status(), body: await before.json() };
  assert.equal(before.status(), 200, "normal learner report before activity");

  const firstClock = shadow || requestedDate ? new Date(Date.parse(startUtc) - 500) : new Date(Date.now() + 10000);
  await page.clock.install({ time: new Date(firstClock.getTime() - 1000) });
  await page.clock.pauseAt(firstClock);
  const catalogResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/learning/analytics-catalog" && response.ok());
  await page.goto(origin + "/backend", { waitUntil: "domcontentloaded" });
  const published = await catalogResponse;
  assert.equal(published.headers()["x-analytics-collection-mode"], "parallel");
  assert((await published.json()).lesson_ids.includes(lessonId), "real lesson has published Analytics identity");
  const chapters = page.getByRole("button", { name: "Выбрать главу" });
  const openLessons = page.getByRole("button", { name: "Открыть список занятий" });
  await chapters.or(openLessons).first().waitFor({ timeout: 20000 });
  if (!(await chapters.isVisible())) await openLessons.click({ force: true });
  await chapters.click({ force: true, timeout: 20000 });
  await page.getByRole("dialog", { name: "Главы курса" }).getByRole("button", { name: /FastAPI и базы данных/ })
    .click({ force: true, timeout: 15000 });
  const lesson = page.locator("button.lesson-row").filter({ hasText: lessonTitle });
  await lesson.waitFor({ state: "attached", timeout: 15000 });
  const lessonModule = page.locator(".module-group").filter({ has: lesson });
  const expander = lessonModule.locator("button.module-toggle");
  if (await expander.getAttribute("aria-expanded") !== "true") await expander.press("Enter");
  evidence.lesson_selection = { expanded: await expander.getAttribute("aria-expanded"), class: await lesson.getAttribute("class") };
  assert.doesNotMatch(evidence.lesson_selection.class, /is-locked/);
  await lesson.press("Enter");
  await page.getByRole("button", { name: "Открыть теорию" }).press("Enter");
  const theory = page.locator("article.dock-lesson").filter({ has: page.locator("h1,h2") })
    .and(page.locator(`article[aria-label*="${lessonTitle}"]`));
  const distractor = await context.newPage();
  await distractor.bringToFront();
  await waitFocus(page, false);
  if (shadow) {
    const grant = await waitUntil(() => windowGrants.find(item => item.status === 200), 20000);
    assert.equal(grant.body.subject_ref, learner.subject_ref);
    assert.equal(Date.parse(grant.body.server_time), firstClock.getTime(), "Core trusted clock anchor");
    evidence.window_grant = grant;
  }
  // Dockview can finish scheduling layout while the learner tab is unfocused.
  await page.clock.runFor(500);
  await page.bringToFront();
  await waitFocus(page, true);
  await page.locator(".dv-tab").filter({ hasText: "71 Теория" }).first().click({ force: true, timeout: 15000 });
  await theory.waitFor({ state: "visible", timeout: 20000 });
  evidence.focus_start = await readFocus(page);
  evidence.active_tabs = await page.locator(".dv-active-tab").allTextContents();
  // Register a learner input at the controlled instant before advancing timers.
  await page.keyboard.press("Shift");
  for (let elapsed = 0; elapsed < 60000; elapsed += 5000) {
    await page.clock.runFor(5000);
    await delay(80);
    await settleAnalytics();
  }
  await distractor.bringToFront();
  await waitFocus(page, false);
  evidence.focus_end = await readFocus(page);
  await settleAnalytics();
  if (requestedDate) {
    assert.equal(evidence.focus_start.utc, startUtc);
    assert.equal(evidence.focus_end.utc, endUtc);
  }
  if (shadow) {
    assert.equal(evidence.focus_start.utc, startUtc);
    assert.equal(evidence.focus_end.utc, endUtc);
    await page.clock.runFor(5000);
    const committed = await waitUntil(() => windowAcks.find(result =>
      result.status === 200 && result.body.items.some(item => item.status === "committed")), 20000);
    const body = JSON.parse(committed.raw.toString("utf8"));
    const initial = body.windows[0];
    assert.equal(body.windows.length, 1);
    assert.equal(initial.activity_date, activityDate);
    assert.equal(initial.slot_no, 41);
    evidence.commit = { status: committed.status, body: committed.body };
    if (offphase) {
      evidence.interim = { ...evidence.commit, raw_request: body,
        postgres: readWindow(learner.subject_ref, initial.activity_date).rows };
      assert.equal(initial.stats[0].active_ms, 59500, "reproduced early cumulative ACK");
      for (let elapsed = 0; elapsed < 65000; elapsed += 5000) {
        await page.clock.runFor(5000);
        await delay(80);
      }
      const full = await waitUntil(() => windowAcks.find(result => result.status === 200 &&
        result.body.items.some(item => item.status === "committed" && item.confirmed?.revision === 2 &&
          item.confirmed.stats.some(stat => stat.active_ms === 60000))), 16000);
      evidence.recovered = { status: full.status, body: full.body,
        postgres: readWindow(learner.subject_ref, initial.activity_date).rows };
      evidence.final_hidden = await readFocus(page);
      assert.equal(evidence.recovered.postgres[0].stats[0].active_ms, 60000);
      assert.equal(evidence.final_hidden.focused, false);
    } else {
      assert.deepEqual(initial.stats, stats);
      evidence.request_body_sha256 = digest(committed.raw);
      await writeFile(resolve(dirname(output), "first-window-wire.json"), committed.raw, { flag: "wx", mode: 0o600 });
      const beforeReplay = readWindow(learner.subject_ref, initial.activity_date);
      evidence.postgres_before_replay = beforeReplay.rows;
      evidence.postgres_subject_count = beforeReplay.subject_count;
      evidence.postgres_window_count = beforeReplay.window_count;
      if (requestedDate) {
        assert.equal(beforeReplay.window_count, 1);
        assert.equal(beforeReplay.subject_count, 1);
        assert.equal(committed.body.items[0].confirmed.revision, 1);
        assert.deepEqual(beforeReplay.rows, [{ student_id: learner.subject_ref, activity_date: activityDate,
          slot_no: 41, stats, revision: 1 }]);
      }
      evidence.shadow_report = await waitReportAt60(context);
      const reply = await context.request.post(origin + "/api/analytics/windows/batch", {
        data: committed.raw,
        headers: { Origin: origin, Referer: origin + "/backend", "Content-Type": "application/json",
          Authorization: await committed.request.headerValue("authorization") },
      });
      evidence.replayed_body_sha256 = digest(committed.raw);
      evidence.duplicate = { status: reply.status(), body: await reply.json() };
      evidence.postgres_after_replay = readWindow(learner.subject_ref, initial.activity_date).rows;
      assert.equal(reply.status(), 200);
      assert.equal(evidence.duplicate.body.items[0].status, "duplicate");
      if (requestedDate) assert.deepEqual(evidence.postgres_after_replay, evidence.postgres_before_replay);
    }
  }
  assert.equal(Date.parse(evidence.focus_end.utc) - Date.parse(evidence.focus_start.utc), 60000, "focused elapsed time");

  evidence.report_after = await waitReportAt60(context);
  const theoryEvents = evidence.events.filter(event =>
    event.section === "theory" && event.course_id === "backend" && event.lesson_id === lessonId);
  const recordedMs = theoryEvents.reduce((total, event) =>
    total + Date.parse(event.ended_at) - Date.parse(event.started_at), 0);
  assert.equal(recordedMs, 60000, "v2 wire telemetry for the rendered lesson");
  if (requestedDate) {
    const event = theoryEvents[0];
    assert.equal(theoryEvents.length, 1, "one ordinary v2 segment");
    assert.equal(new Date(event.started_at).toISOString(), startUtc);
    assert.equal(new Date(event.ended_at).toISOString(), endUtc);
    assert(evidence.v2_acks.some(ack => ack.status === 200 && ack.body.items?.some(item =>
      item.event_id === event.event_id && item.status === "accepted")), "ordinary v2 fact accepted");
  }
  assert(evidence.v2_acks.some(ack => ack.status === 200), "durable v2 ACK");
  assert.equal(evidence.report_after.status, 200, "normal learner report after activity");
  const summary = evidence.report_after.body.summary;
  evidence.page_errors = Array.from(errs);
  assert.equal(summary.active_seconds, 60);
  assert.equal(summary.theory_seconds, 60);
  assert.equal(summary.practice_seconds, null);
  assert.equal(summary.completed_lessons_period, 0);
  if (shadow && !offphase) {
    evidence.baseline_report = baselineReport;
    const normal = await context.request.get(origin + reportPath);
    evidence.duplicate_report = { status: normal.status(), body: await normal.json() };
    evidence.repair_enabled = false;
    evidence.readiness = { core: await readiness(18041, "/health/ready"), collector: await readiness(18042, "/ready"),
      query: await readiness(18043, "/ready"), privacy: await readiness(18045, "/ready") };
    evidence.versions = { analytics_schema: "a015", chromium: browser.version() };
    evidence.activity_date = activityDate;
    evidence.browser = { chromium: browser.version(), url: origin + "/backend",
      start_clock: activityDate + "T10:00:00+03:00", end_clock: activityDate + "T10:01:00+03:00",
      focused_at_start: evidence.focus_start.focused, focused_at_end: evidence.focus_end.focused };
    assert.equal(before.status(), 200);
  } else if (phase === "outside") {
    assert(windowGrants.some(grant => grant.status === 403), "outside cohort must be denied a v3 grant");
    assert.equal(windowAcks.length, 0, "outside cohort must produce no v3 delivery");
    evidence.window_grants = windowGrants;
    evidence.outside_window_count = readWindow(learner.subject_ref, new Date().toISOString().slice(0, 10)).rows.length;
    assert.equal(evidence.outside_window_count, 0);
  }
  evidence.outcome = "PASS";
} catch (error) {
  evidence.outcome = "FAIL";
  evidence.error = error.message.split("\n")[0];
  evidence.window_grants = windowGrants;
  evidence.window_replies = windowAcks.map(({ status, body }) => ({ status, body }));
  if (page) evidence.page_diagnostic = await page.evaluate(() => ({ title: document.title,
    text: document.body.innerText.substring(0, 1200),
    buttons: [...document.querySelectorAll("button")].slice(0, 25).map(button => button.getAttribute("aria-label") || button.innerText),
    articles: [...document.querySelectorAll("article")].map(article => ({ name: article.getAttribute("aria-label"),
      text: article.innerText.substring(0, 120), boxes: article.getClientRects().length,
      bounds: article.getBoundingClientRect().toJSON(), parent: article.parentElement.className,
      visibility: getComputedStyle(article).visibility })),
    active: [...document.querySelectorAll(".lesson-row.is-active")].map(lesson => lesson.innerText),
    main: document.querySelector(".dock-workbench")?.innerText.substring(0, 700),
  })).catch(() => null);
  throw error;
} finally {
  await writeFile(output, JSON.stringify(evidence, null, 2) + "\n", { flag: "wx", mode: 0o600 });
  await browser.close();
  await outer.close();
}

async function waitFocus(page, focused) {
  for (let attempt = 0; attempt < 30; attempt++) {
    if ((await readFocus(page)).focused === focused) return;
    await delay(100);
  }
  throw new Error("Chromium did not report the expected native focus");
}

function readFocus(page) {
  return page.evaluate(() => ({ utc: new Date().toISOString(), mono: performance.now(), visibility: document.visibilityState,
    focused: document.hasFocus() }));
}

async function waitReportAt60(context) {
  const deadline = Date.now() + 90000;
  let report;
  while (Date.now() < deadline) {
    const result = await context.request.get(origin + reportPath);
    const body = await result.json();
    report = { status: result.status(), body };
    if (result.status() === 200 && body.summary?.theory_seconds === 60) return report;
    await delay(1000);
  }
  return report;
}

async function readiness(port, path) {
  const response = await fetch("http://127.0.0.1:" + port + path, { signal: AbortSignal.timeout(5000) });
  return response.status;
}

async function waitUntil(read, timeout) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const result = read();
    if (result) return result;
    await delay(100);
  }
  throw new Error("No expected browser HTTP response within the bounded wait");
}

function digest(raw) {
  return createHash("sha256").update(raw).digest("hex");
}

function readWindow(subject, date) {
  const sql = "BEGIN READ ONLY;\n" +
    "SELECT student_id, activity_date, slot_no, stats, revision\n" +
    "FROM analytics.student_activity_windows\n" +
    "WHERE student_id = :'student_id'::uuid\n" +
    "  AND activity_date = :'activity_date'::date\n" +
    "  AND slot_no = 41;\n" +
    "SELECT count(DISTINCT student_id) AS subject_count, count(*) AS window_count FROM analytics.student_activity_windows;\n" +
    "COMMIT;\n";
  const dbName = "analytics_live_" + run.replaceAll("-", "");
  const result = spawnSync("docker", ["exec", "-i", "693d9afb4c42", "psql", "-X", "--csv", "-q",
    "-v", "ON_ERROR_STOP=1", "-v", "student_id=" + subject, "-v", "activity_date=" + date,
    "-U", "analytics_bootstrap", "-d", dbName], { input: sql, encoding: "utf8", timeout: 8000 });
  assert.equal(result.status, 0, "independent psql read of full key");
  const lines = result.stdout.trim().split(/\r?\n/);
  assert.equal(lines[0], "student_id,activity_date,slot_no,stats,revision");
  assert.equal(lines.at(-2), "subject_count,window_count");
  const [subject_count, window_count] = lines.at(-1).split(",").map(Number);
  const rows = lines.slice(1, -2).map(line => {
    const match = line.match(/^([^,]+),([^,]+),(\d+),"(.*)",(\d+)$/);
    assert(match, "psql emitted an unexpected window row");
    const [, student_id, activity_date, slot, stats, revision] = match;
    return { student_id, activity_date, slot_no: Number(slot), stats: JSON.parse(stats.replaceAll('""', '"')),
      revision: Number(revision) };
  });
  return { rows, subject_count, window_count };
}

