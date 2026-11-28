import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { setTimeout as delay } from 'node:timers/promises';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.CORP604_PLAYWRIGHT_MODULE ?? 'playwright');
const { version: playwright } = require(process.env.CORP604_PLAYWRIGHT_PACKAGE ?? 'playwright/package.json');
const base = new URL('/src/features/analytics-tracker/windows-fixture.html', process.argv[2] ?? 'http://127.0.0.1:5514');
assert.equal(base.hostname, '127.0.0.1');
const port = Number(process.env.CORP604_DEBUG_PORT ?? 9556);
assert.ok(Number.isInteger(port) && port > 1024 && port <= 65535);
const now = Date.parse('2026-11-27T10:08:00+03:00');
const key = '2026-11-27/41';
const subjects = { A: 'c3333333-3333-4333-8333-333333333333', B: 'd4444444-4444-4444-8444-444444444444' };
const writer = { writer_id: 'a1111111-1111-4111-8111-111111111111', writer_generation: 1 };
const stats = [{ tool_id: 'theory', course_id: 'backend', lesson_id: '71', active_ms: 480_000 }];
const original = {
  activity_date: '2026-11-27', slot_no: 41, ...writer, server_epoch: 1, seq: 4,
  stats, catalog_versions: { backend: 'backend-v1' }, client_final: false,
};
const grantPath = '/api/analytics/windows/grant';
const batchPath = '/api/analytics/windows/batch';
const legacyBatch = '/api/analytics/events/batch';

function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') {
    return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
  }
  return JSON.stringify(value);
}

function ack(snapshot, status = 'committed', revision = 1) {
  const { activity_date, slot_no, writer_id, writer_generation, server_epoch, seq, client_final, stats } = snapshot;
  const payload_hash = createHash('sha256').update(canonical(snapshot)).digest('hex');
  const tuple = { writer_id, writer_generation, server_epoch, seq, payload_hash };
  return {
    index: 0, activity_date, slot_no, ...tuple, status, code: status === 'duplicate' ? 'same_payload' : 'committed',
    confirmed: { ...tuple, revision, stats, client_final, rebase_at: null },
  };
}

async function until(read, check) {
  const deadline = Date.now() + 5000;
  let result;
  do {
    result = await read();
    if (check(result)) return result;
    await delay(25);
  } while (Date.now() < deadline);
  assert.fail('Waited for browser state: ' + JSON.stringify(result));
}

async function scenario(name, test) {
  const launched = await chromium.launch({
    channel: 'chromium', args: ['--remote-debugging-port=' + port, '--remote-debugging-address=127.0.0.1'],
  });
  try {
    const browser = await chromium.connectOverCDP('http://127.0.0.1:' + port, { noDefaults: true, timeout: 5000 });
    const profile = browser.contexts()[0];
    const pages = [];
    const requests = [];
    const errors = [];
    const setup = { learner: 'A', legacyFault: false, offline: false, grant: null, batch: null };
    let elapsed = 0;
    await profile.addInitScript(() => {
      Math.random = () => 1;
      if (new URLSearchParams(location.search).has('noStorage')) {
        Object.defineProperty(window, 'indexedDB', { get() { throw new DOMException('disabled by scenario', 'SecurityError'); } });
      }
    });
    await profile.route('**/api/analytics/**', async route => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      const bodyRaw = request.postDataBuffer().toString('utf8');
      const body = JSON.parse(bodyRaw);
      const learner = setup.learner;
      const browserTime = await request.frame().page().evaluate(() => Date.now()).catch(() => now + elapsed);
      const entry = { path, elapsed_ms: browserTime - now, request: body, body: bodyRaw, learner };
      requests.push(entry);
      try {
        const windowCall = path === grantPath || path === batchPath;
        let response;
        if (windowCall && setup.offline) response = { drop: 'offline' };
        else if (path === grantPath && setup.grant) response = await setup.grant(entry);
        else if (path === batchPath && setup.batch) response = await setup.batch(entry);
        if (!response) {
          if (path === batchPath) response = { body: { protocol_version: 1, items: [ack(body.windows[0])] } };
          else if (path === legacyBatch) response = setup.legacyFault
            ? { drop: 'legacy-offline' }
            : { body: { items: body.events.map((event, index) => ({
              index, event_id: event.event_id, status: 'accepted', code: 'committed',
            })) } };
          else {
            assert.ok(path === grantPath || path === '/api/analytics/grants/collect');
            const time = await request.frame().page().evaluate(() => Date.now());
            response = { body: {
              ...(path === grantPath ? body : {}),
              subject_ref: subjects[entry.learner],
              grant: path === grantPath ? 'window-grant-' + entry.learner : 'legacy-grant-' + entry.learner,
              server_time: new Date(time).toISOString(), expires_at: new Date(time + 300_000).toISOString(),
            } };
          }
        }
        if (path === batchPath) assert.equal(await request.headerValue('authorization'), 'Bearer window-grant-' + entry.learner);
        entry.outcome = response.drop ?? (response.status ?? 200);
        if (response.headers?.Allow) entry.allow = response.headers.Allow;
        if (path === batchPath) entry.ack = response.body;
        if (response.drop) await route.abort();
        else await route.fulfill({ status: response.status ?? 200, json: response.body ?? {}, headers: response.headers });
      } catch (error) {
        if (!String(error).includes('Target page, context or browser has been closed') &&
          !String(error).includes('Execution context was destroyed') && !String(error).includes('route is already handled')) {
          errors.push(String(error));
        }
        await route.abort().catch(() => undefined);
      }
    });

    async function open(context = 'denied', learner = 'A', noStorage = false) {
      const page = await profile.newPage();
      pages.push(page);
      page.on('pageerror', error => errors.push(String(error)));
      if (pages.length === 1) {
        await page.clock.install({ time: new Date(now - 1000) });
        await page.clock.pauseAt(new Date(now));
      }
      const url = new URL(base);
      url.searchParams.set('context', context);
      url.searchParams.set('learner', learner);
      if (noStorage) url.searchParams.set('noStorage', '1');
      await page.goto(url.href);
      await page.locator('#context').waitFor();
      return page;
    }
    async function clockFor(ms) {
      for (let offset = 0; offset < ms; offset += 5000) {
        const step = Math.min(5000, ms - offset);
        await pages.find(page => !page.isClosed()).clock.runFor(step);
        elapsed += step;
        await delay(30);
      }
    }
    async function activate(page) {
      await page.bringToFront();
      const openPages = pages.filter(candidate => !candidate.isClosed());
      const browser = await until(() => Promise.all(openPages.map(item => item.evaluate(() => ({
        focused: document.hasFocus(), visible: document.visibilityState === 'visible',
      })))), states => states.every((state, index) => state.focused === (openPages[index] === page) &&
        state.visible === (openPages[index] === page)));
      await delay(30);
      return browser;
    }
    const start = page => page.locator('#start').evaluate(button => button.click());
    const inspect = page => page.evaluate(() => window.windowFixture.inspect());
    const ready = page => until(() => inspect(page), state => state?.ready);
    const seed = (page, { snapshot = original, due = now, confirmed } = {}) =>
      page.evaluate(({ snapshot, due, confirmed }) => window.windowFixture.seed(snapshot, due, confirmed), { snapshot, due, confirmed });
    const select = (page, context) => page.evaluate(kind => window.windowFixture.select(kind), context);
    const events = page => page.evaluate(() => window.windowFixture.events());
    const v3 = () => requests.filter(row => row.path === batchPath);
    const v2 = () => requests.filter(row => row.path === legacyBatch);
    const outcome = await test({ setup, open, clockFor, activate, start, seed, select, inspect, ready, events, requests, v3, v2 });
    assert.deepEqual(errors, []);
    return { name, browser: browser.version(), outcome, requests };
  } finally {
    await launched.close();
  }
}

const checks = {
  '604-В': async h => {
    const pages = [];
    for (let count = 0; count < 5; count++) pages.push(await h.open());
    const leader = pages[0];
    await h.seed(leader);
    let business;
    h.setup.batch = entry => {
      const snapshot = entry.request.windows[0];
      assert.deepEqual(snapshot, original);
      business = ack(snapshot);
      h.setup.offline = true;
      return { drop: 'ACK lost after mock commit' };
    };
    await h.activate(leader);
    await h.start(leader);
    const first = await until(h.v3, rows => rows[0]?.outcome === 'ACK lost after mock commit');
    const originalBody = first[0].body;
    const before = await until(() => h.inspect(leader), state => state?.partition?.windows[key]?.pending?.attempts === 1);
    assert.equal(before.partition.windows[key].pending.body, originalBody);
    for (const peer of pages.slice(1)) { await h.start(peer); }
    await leader.reload();
    await h.start(leader);
    const offline = await until(() => h.inspect(leader), state => state?.partition?.windows[key]?.pending?.attempts === 1);
    assert.equal(offline.partition.windows[key].pending.body, originalBody);
    assert.equal(offline.ready, false);
    assert.equal(pages.filter(page => !page.isClosed()).length, 5);

    h.setup.offline = false;
    let replays = 0;
    h.setup.batch = entry => {
      assert.equal(entry.body, originalBody);
      const same = ack(entry.request.windows[0], 'duplicate');
      replays += 1;
      return { body: { protocol_version: 1, items: [replays === 1 ? { ...same, slot_no: 42 } : same] } };
    };
    await h.clockFor(5000);
    await until(h.v3, rows => rows.some(row => row.ack?.items?.[0]?.slot_no === 42));
    const foreign = await until(() => h.inspect(leader), state => state?.partition?.windows[key]?.pending?.attempts === 2);
    assert.equal(foreign.partition.windows[key].pending.body, originalBody);
    assert.equal(foreign.partition.windows[key].confirmed, undefined);
    await h.clockFor(5000);
    const after = await until(() => h.inspect(leader), state => state?.partition?.windows[key]?.confirmed?.status === 'duplicate');
    assert.equal(after.partition.windows[key].pending, undefined);
    assert.deepEqual(Object.keys(after.partition.windows), [key]);
    assert.equal(after.partition.windows[key].confirmed.confirmed.revision, business.confirmed.revision);
    assert.deepEqual(after.partition.windows[key].stats, stats);
    assert.equal(replays, 2);
    return { tabs: 5, offline_seq: 4, foreign_ack_preserved: true, exact_replays: replays,
      confirmed: after.partition.windows[key].confirmed, coverage_reasons: after.partition.reasons };
  },

  '604-Г/E1': h => handoff(h, false),
  '604-Г/E2': h => handoff(h, true),

  '604-Д/account': async h => {
    const old = await h.open();
    await h.seed(old);
    let unblock;
    const gate = new Promise(resolve => { unblock = resolve; });
    h.setup.grant = entry => entry.learner === 'A' ? gate.then(() => ({ body: {
      ...entry.request, subject_ref: subjects.A, grant: 'window-grant-A',
      server_time: new Date(now).toISOString(), expires_at: new Date(now + 300_000).toISOString(),
    } })) : undefined;
    await h.start(old);
    const first = await until(() => h.requests, rows => rows.some(row => row.path === grantPath && row.learner === 'A'));
    assert.equal(first.find(row => row.path === grantPath).outcome, undefined);
    await old.evaluate(() => window.windowFixture.authBoundary());
    h.setup.learner = 'B';
    const current = await h.open('theory', 'B');
    await h.activate(current);
    await h.start(current);
    const b = await h.ready(current);
    unblock();
    assert.equal(b.partition.subject, subjects.B);
    assert.notEqual(b.partition.writer.writer_id, writer.writer_id);
    assert.deepEqual(b.partition.windows, {});
    assert.ok(b.losses.includes('account_change'));
    await h.clockFor(60_000);
    const fresh = await until(h.v3, rows => rows.some(row => row.learner === 'B'));
    assert.deepEqual(fresh.map(row => row.learner), ['B']);
    assert.equal(fresh[0].request.windows[0].stats[0].active_ms, 60_000);
    assert.notEqual(fresh[0].request.windows[0].writer_id, writer.writer_id);
    return { old_grant_in_flight_at_boundary: true, old_grant_late_response_offered: true, old_batches_under_B: 0,
      writer_changed: true, new_theory_ms: 60_000, reasons: b.losses };
  },

  '604-Д/storage': async h => {
    const page = await h.open('theory', 'A', true);
    await h.start(page);
    const losses = await until(() => h.events(page), rows => rows.some(event => event.reason === 'storage_denied'));
    await h.clockFor(60_000);
    const legacy = await until(h.v2, rows => rows.some(row => row.outcome === 200));
    assert.equal(legacy.filter(row => row.outcome === 200).flatMap(row => row.request.events).reduce((sum, row) => sum + row.active_ms, 0), 60_000);
    assert.deepEqual(h.v3(), []);
    return { coverage_reasons: losses.map(event => event.reason), v3_batches: 0, v2_active_ms: 60_000 };
  },

  '604-Д/grant-403': async h => {
    const page = await h.open('theory');
    h.setup.grant = () => ({ status: 403, body: { code: 'forbidden' } });
    await h.start(page);
    await until(() => h.events(page), rows => rows.some(event => event.reason === 'v3_disabled'));
    await h.clockFor(60_000);
    const legacy = await until(h.v2, rows => rows.some(row => row.outcome === 200));
    const grants = h.requests.filter(row => row.path === grantPath);
    assert.deepEqual(grants.map(row => row.outcome), [403]);
    assert.equal(h.v3().length, 0);
    const state = await h.inspect(page);
    assert.ok(state.partition.reasons.includes('v3_disabled'));
    assert.equal(legacy[0].request.events[0].active_ms, 60_000);
    return { grant_attempts: 1, v3_batches: 0, v2_active_ms: 60_000, coverage_reasons: state.partition.reasons };
  },

  '604-Д/grant-405': async h => {
    const page = await h.open('theory');
    h.setup.grant = () => ({ status: 405, body: { detail: 'Method Not Allowed' }, headers: { Allow: 'GET' } });
    await h.start(page);
    await until(() => h.events(page), rows => rows.some(event => event.reason === 'unsupported'));
    await h.start(page);
    await h.clockFor(150_000);
    const legacy = await until(h.v2, rows => rows.some(row => row.outcome === 200));
    const grants = h.requests.filter(row => row.path === grantPath);
    assert.deepEqual(grants.map(row => [row.outcome, row.allow]), [[405, 'GET']]);
    assert.equal(h.v3().length, 0);
    assert(legacy.flatMap(row => row.request.events).some(event => event.active_ms > 0));
    return { status: 405, allow: 'GET', grant_attempts: 1, v3_batches: 0, v2_batches: legacy.length };
  },

  '604-Д/grant-503': async h => {
    const page = await h.open();
    h.setup.grant = () => ({ status: 503, body: { code: 'unavailable' } });
    await h.start(page);
    await until(() => h.requests, rows => rows.some(row => row.outcome === 503));
    await h.clockFor(210_000);
    const state = await until(() => h.inspect(page), value => value?.partition?.reasons.includes('retry_exhausted'));
    const grants = h.requests.filter(row => row.path === grantPath);
    assert.equal(grants.length, 8);
    assert.equal(new Set(grants.map(row => row.body)).size, 1);
    assert.equal(h.v3().length, 0);
    return { grant_attempts: grants.length, at_ms: grants.map(row => row.elapsed_ms), coverage_reasons: state.partition.reasons };
  },

  '604-Д/429': async h => {
    const page = await h.open();
    await h.seed(page);
    let attempts = 0;
    h.setup.batch = entry => ++attempts === 1
      ? { status: 429, body: { code: 'rate_limited' }, headers: { 'Retry-After': '20' } }
      : { body: { protocol_version: 1, items: [ack(entry.request.windows[0])] } };
    await h.start(page);
    await until(h.v3, rows => rows.some(row => row.outcome === 429));
    await h.clockFor(19_000);
    assert.equal(h.v3().length, 1);
    await h.clockFor(6000);
    const rows = await until(h.v3, entries => entries.some(row => row.outcome === 200));
    assert.equal(rows.length, 2);
    assert.ok(rows[1].elapsed_ms - rows[0].elapsed_ms >= 20_000);
    assert.equal(rows[0].body, rows[1].body);
    return { retry_after_ms: 20_000, actual_delay_ms: rows[1].elapsed_ms - rows[0].elapsed_ms, exact_retry: true };
  },

  '604-Д/503': async h => {
    const page = await h.open();
    await h.seed(page);
    h.setup.batch = () => ({ status: 503, body: { code: 'unavailable' } });
    await h.start(page);
    await until(h.v3, rows => rows.some(row => row.outcome === 503));
    await h.clockFor(170_000);
    const outbox = await until(() => h.inspect(page), state =>
      state?.partition?.windows[key]?.pending?.quarantine === 'retry_exhausted');
    const rows = h.v3();
    assert.equal(rows.length, 8);
    assert.equal(new Set(rows.map(row => row.body)).size, 1);
    assert.equal(outbox.partition.windows[key].pending.attempts, 8);
    assert.equal(outbox.partition.windows[key].pending.body, rows[0].body);
    return { request_count: rows.length, exact_body: true, coverage_reasons: outbox.partition.reasons,
      elapsed_ms: rows.map(row => row.elapsed_ms) };
  },

  '604-Д/401': async h => {
    const page = await h.open();
    await h.seed(page);
    h.setup.batch = () => ({ status: 401, body: { code: 'unauthorized' } });
    await h.start(page);
    await until(h.v3, rows => rows.some(row => row.outcome === 401));
    await h.clockFor(20_000);
    const outbox = await until(() => h.inspect(page), state =>
      state?.partition?.windows[key]?.pending?.quarantine === 'v3_unconfirmed');
    const rows = h.v3(), grants = h.requests.filter(row => row.path === grantPath);
    assert.equal(rows.length, 2);
    assert.equal(grants.length, 2);
    assert.deepEqual(grants.map(row => row.request.writer_id), [writer.writer_id, writer.writer_id]);
    assert.equal(rows[0].body, rows[1].body);
    return { grant_attempts_including_warm: grants.length, v3_batch_attempts: rows.length,
      coverage_reasons: outbox.partition.reasons, same_lifecycle_and_writer: true };
  },

  '604-Д/404': async h => {
    const page = await h.open('theory');
    const idleTab = await h.open();
    await h.seed(page);
    h.setup.batch = () => ({ status: 404, body: { code: 'capability_off' } });
    await h.activate(page);
    await h.start(page);
    await h.ready(page);
    await h.clockFor(5000);
    await until(h.v3, rows => rows[0]?.outcome === 404);
    await h.clockFor(5000);
    await h.select(page, 'practice');
    await h.clockFor(10_000);
    await h.select(page, 'theory');
    await h.clockFor(10_000);
    h.setup.legacyFault = true;
    await h.activate(idleTab);
    await until(h.v2, rows => rows.some(row => row.outcome === 'legacy-offline'));
    h.setup.legacyFault = false;
    await h.clockFor(30_000);
    const losses = await h.events(page);
    assert.ok(losses.some(event => event.reason === 'unsupported'));
    assert.equal(h.v3().length, 1);
    const legacy = h.v2(), accepted = legacy.filter(row => row.outcome === 200).flatMap(row => row.request.events);
    assert.deepEqual(accepted.map(row => [row.section, row.active_ms]), [['theory', 10_000], ['practice', 10_000], ['theory', 10_000]]);
    const failed = legacy.find(row => row.outcome === 'legacy-offline');
    assert.ok(legacy.some(row => row.outcome === 200 && row.body === failed.body));
    return { capability_off_batches: 1, v2_active_ms: 30_000, v2_same_body_retry: true, coverage_reasons: ['unsupported'] };
  },

  '604-Д/course-revoked': async h => {
    const page = await h.open();
    const sql = { tool_id: 'theory', course_id: 'sql', lesson_id: '1', active_ms: 10_000 };
    const mixed = { ...original, stats: [...stats, sql], catalog_versions: { ...original.catalog_versions, sql: 'sql-v1' } };
    await h.seed(page, { snapshot: mixed });
    h.setup.batch = entry => {
      const rejected = ack(entry.request.windows[0]);
      return { body: { protocol_version: 1, items: [
        { ...rejected, status: 'rejected', code: 'course_forbidden', payload_hash: null, confirmed: null },
      ] } };
    };
    await h.start(page);
    const state = await until(() => h.inspect(page), value =>
      value?.partition?.windows[key]?.pending?.quarantine === 'v3_unconfirmed');
    await h.clockFor(20_000);
    assert.equal(h.v3().length, 1);
    assert.deepEqual(h.v3()[0].request.windows[0], mixed);
    assert.deepEqual(state.partition.windows[key].pending.snapshot, mixed);
    assert.equal(state.partition.windows[key].confirmed, undefined);
    return { scopes_in_rejected_key: ['backend', 'sql'], result: 'course_forbidden', attempts: 1, retained_whole_key: true };
  },

  '604-Д/expired': async h => {
    const page = await h.open();
    const expired = { ...original, activity_date: '2026-11-25' };
    await h.seed(page, { snapshot: expired, due: now - 1000 });
    await h.start(page);
    const state = await until(() => h.inspect(page), value => value?.partition?.reasons.includes('expired'));
    assert.deepEqual(state.partition.windows, {});
    assert.equal(h.v3().length, 0);
    return { expired_snapshot_sent: false, coverage_reasons: state.partition.reasons };
  },
};

async function handoff(h, repair) {
  const leader = await h.open('theory');
  const successor = await h.open();
  const initialAck = ack(original);
  await h.seed(leader, { due: now + 60_000, confirmed: initialAck });
  let authoritative = repair
    ? { ...initialAck.confirmed, server_epoch: 2, revision: 2, seq: null, payload_hash: null, rebase_at: new Date(now).toISOString() }
    : initialAck.confirmed;
  h.setup.batch = entry => {
    const snap = entry.request.windows[0];
    assert.equal(snap.seq, 5);
    assert.equal(snap.server_epoch, 1);
    assert.equal(snap.stats[0].active_ms, 510_000);
    const result = ack(snap, 'committed', 2);
    if (repair) {
      return { body: { protocol_version: 1, items: [{ ...result, status: 'stale', code: 'stale_epoch', confirmed: authoritative }] } };
    }
    authoritative = result.confirmed;
    return { body: { protocol_version: 1, items: [result] } };
  };
  await h.start(successor);
  await h.ready(successor);
  await h.activate(leader);
  await h.start(leader);
  await h.ready(leader);
  await h.clockFor(30_000);
  const checkpoint = await until(() => h.inspect(leader), state =>
    state?.partition?.windows[key]?.stats[0]?.active_ms === 510_000 && state.partition.lease);
  const predecessor = checkpoint.partition.lease.token;
  await leader.close();
  await h.activate(successor);
  await h.clockFor(15_000);
  await h.select(successor, 'theory');
  const taken = await until(() => h.inspect(successor), state =>
    state?.partition?.lease?.token && state.partition.lease.token !== predecessor);
  await h.select(successor, 'denied');
  await h.clockFor(20_000);
  const settled = await until(() => h.inspect(successor), state => repair
    ? state?.partition?.windows[key]?.pending?.quarantine === 'epoch_rebase_required'
    : state?.partition?.windows[key]?.confirmed?.seq === 5);
  assert.equal(settled.partition.windows[key].stats[0].active_ms, 510_000);
  assert.equal(h.v3().length, 1);
  assert.deepEqual(Object.keys(settled.partition.windows), [key]);
  assert.equal(authoritative.stats[0].active_ms, repair ? 480_000 : 510_000);
  if (repair) assert.equal(authoritative.server_epoch, 2);
  return {
    saved_before_leader_close_ms: 510_000, one_successor: true, transferred_token: taken.partition.lease.token,
    attempted_epoch: 1, attempted_seq: 5, authoritative,
    outcome: repair ? 'stale_epoch' : 'committed', coverage_reasons: settled.partition.reasons,
  };
}

const selected = process.argv[3] ? Object.entries(checks).filter(([name]) => name === process.argv[3]) : Object.entries(checks);
assert.ok(selected.length);
const results = [];
for (const [name, check] of selected) {
  try { results.push(await scenario(name, check)); }
  catch (error) { throw new Error('Scenario ' + name + ' failed', { cause: error }); }
}
console.log(JSON.stringify({ playwright, fixture: base.href, results }, null, 2));
