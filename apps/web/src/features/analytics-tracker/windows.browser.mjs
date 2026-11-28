import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { setTimeout as delay } from 'node:timers/promises';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.CORP604_PLAYWRIGHT_MODULE ?? 'playwright');
const { version: playwrightVersion } = require(process.env.CORP604_PLAYWRIGHT_PACKAGE ?? 'playwright/package.json');
const expected = JSON.parse(readFileSync(new URL('./windows.expected.json', import.meta.url), 'utf8')).five_tabs;
const fixture = new URL('/src/features/analytics-tracker/windows-fixture.html', process.argv[2] ?? 'http://127.0.0.1:5514');
assert.equal(fixture.hostname, '127.0.0.1');
const port = Number(process.env.CORP604_DEBUG_PORT ?? 9555);
assert.ok(Number.isInteger(port) && port > 1024 && port <= 65535);
const subjectRef = 'c3333333-3333-4333-8333-333333333333';
const start = Date.parse(expected.start);
const grantPath = '/api/analytics/windows/grant';
const batchPath = '/api/analytics/windows/batch';
const v2Grant = '/api/analytics/grants/collect';
const v2Batch = '/api/analytics/events/batch';
const order = rows => [...rows].sort((a, b) => a.tool_id.localeCompare(b.tool_id));
const total = rows => rows.reduce((sum, row) => sum + row.active_ms, 0);

async function until(read, check) {
  const deadline = Date.now() + 6000;
  let result;
  do {
    result = await read();
    if (check(result)) return result;
    await delay(20);
  } while (Date.now() < deadline);
  assert.fail('Timed out waiting for browser state: ' + JSON.stringify(result));
}

function orderedJson(value) {
  if (Array.isArray(value)) return '[' + value.map(orderedJson).join(',') + ']';
  if (value && typeof value === 'object') {
    return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + orderedJson(value[key])).join(',') + '}';
  }
  return JSON.stringify(value);
}

const launched = await chromium.launch({
  channel: 'chromium', args: ['--remote-debugging-port=' + port, '--remote-debugging-address=127.0.0.1'],
});
try {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:' + port, { noDefaults: true, timeout: 5000 });
  const profile = browser.contexts()[0];
  const requests = [];
  const errors = [];
  const receipts = new Map();
  const pages = [];
  const steps = [];
  let elapsed = 0;
  const json = (route, body) => route.fulfill({ status: 200, json: body });

  await profile.route('**/api/analytics/**', async route => {
    try {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      const page = request.frame().page();
      const tab = pages.indexOf(page) + 1;
      const now = await page.evaluate(() => Date.now());
      const raw = request.postDataBuffer().toString('utf8');
      const body = JSON.parse(raw);
      assert.equal(request.method(), 'POST');
      let response;
      if (path === grantPath || path === v2Grant) {
        if (path === grantPath) {
          assert.equal(body.protocol_version, 1);
          assert.equal(body.writer_generation, 1);
          assert.match(body.writer_id, /^[0-9a-f-]{36}$/);
        }
        response = {
          grant: path === grantPath ? 'synthetic-window-grant' : 'synthetic-v2-grant',
          subject_ref: subjectRef,
          server_time: new Date(now).toISOString(),
          expires_at: new Date(now + 300_000).toISOString(),
        };
        if (path === grantPath) Object.assign(response, body);
      } else if (path === v2Batch) {
        assert.equal(await request.headerValue('authorization'), 'Bearer synthetic-v2-grant');
        response = { items: body.events.map((event, index) => ({ index, event_id: event.event_id, code: 'committed', status: 'accepted' })) };
      } else {
        assert.equal(path, batchPath);
        assert.equal(await request.headerValue('authorization'), 'Bearer synthetic-window-grant');
        assert.equal(body.protocol_version, 1);
        assert.equal(body.windows.length, 1);
        const items = body.windows.map((row, index) => {
          const { activity_date, slot_no, writer_id, writer_generation, server_epoch, seq, stats, client_final } = row;
          const key = activity_date + '/' + slot_no;
          const payload_hash = createHash('sha256').update(orderedJson({ ...row, stats: order(stats) })).digest('hex');
          const prior = receipts.get(key);
          const duplicate = prior?.seq === seq;
          if (duplicate) assert.equal(prior.payload_hash, payload_hash);
          else if (prior) assert.equal(seq, prior.seq + 1);
          const confirmed = {
            writer_id, writer_generation, server_epoch, seq, payload_hash,
            revision: duplicate ? prior.revision : (prior?.revision ?? 0) + 1, stats, client_final, rebase_at: null,
          };
          receipts.set(key, confirmed);
          return {
            index, activity_date, slot_no, writer_id, writer_generation, server_epoch, seq, payload_hash,
            status: duplicate ? 'duplicate' : 'committed', code: duplicate ? 'same_payload' : 'committed', confirmed,
          };
        });
        response = { protocol_version: 1, items };
      }
      requests.push({
        tab, path, at: new Date(now).toISOString(), request: body,
        ...(path === batchPath ? { raw_body: raw } : {}),
        response: path === grantPath || path === v2Grant ? { status: 200 } : { status: 200, body: response },
      });
      await json(route, response);
    } catch (error) {
      errors.push(String(error));
      await route.abort();
    }
  });

  for (const segment of expected.steps) {
    const target = new URL(fixture);
    target.searchParams.set('context', segment.tool_id);
    const page = await profile.newPage();
    pages.push(page);
    page.on('pageerror', error => errors.push(String(error)));
    if (pages.length === 1) {
      await page.clock.install({ time: new Date(start - 1000) });
      await page.clock.pauseAt(new Date(start));
    }
    await page.goto(target.href);
    await page.locator('#start').click();
    await until(() => page.evaluate(() => window.windowFixture.inspect()), value => value?.ready);
  }

  const state = () => Promise.all(pages.map(page => page.evaluate(() => ({
    visibility: document.visibilityState, focus: document.hasFocus(),
  }))));
  async function activate(tab) {
    await pages[tab - 1].bringToFront();
    const browsers = await until(state, rows => rows.every((row, index) =>
      row.focus === (index + 1 === tab) && row.visibility === (index + 1 === tab ? 'visible' : 'hidden')));
    steps.push({ tab, elapsed_ms: elapsed, browsers });
    await delay(35);
  }
  async function advance(ms) {
    for (let offset = 0; offset < ms; offset += 5000) {
      const part = Math.min(5000, ms - offset);
      await pages[0].clock.runFor(part);
      elapsed += part;
      const times = await Promise.all(pages.map(page => page.evaluate(() => Date.now())));
      assert.ok(times.every(time => time === start + elapsed));
      await delay(30);
    }
  }

  for (const segment of expected.steps) {
    assert.equal(segment.start_offset_ms, elapsed);
    await activate(segment.tab);
    await advance(segment.duration_ms);
  }
  assert.equal(start + elapsed, Date.parse(expected.leave_focus_at));
  await pages[0].locator('#stop').evaluate(button => button.click());
  await activate(1);

  const key = expected.windows[0].activity_date + '/' + expected.windows[0].slot_no;
  const stats = order(expected.windows[0].stats);
  await advance(5000);
  const current = () => pages[4].evaluate(() => window.windowFixture.inspect());
  const stateAfter = await until(current, value => {
    const draft = value?.partition?.windows[key];
    return draft?.confirmed && !draft.pending && total(draft.confirmed.confirmed.stats) >= expected.windows[0].total_ms;
  });
  assert.deepEqual(Object.keys(stateAfter.partition.windows), [key]);
  assert.deepEqual(order(stateAfter.partition.windows[key].stats), stats);
  const batches = requests.filter(row => row.path === batchPath);
  assert.equal(receipts.size, 1);
  assert.deepEqual(receipts.get(key).stats, stats);
  const finalBatch = batches.at(-1);
  assert.deepEqual(finalBatch.request.windows[0].catalog_versions, { backend: 'backend-v1' });
  assert.equal(finalBatch.response.body.items[0].payload_hash, stateAfter.partition.windows[key].confirmed.payload_hash);
  assert.equal(new Set(batches.flatMap(row => row.request.windows.map(snapshot => snapshot.writer_id))).size, 1);
  assert.equal(requests.filter(row => row.path === grantPath).length, 5);
  assert.deepEqual(stateAfter.partition.reasons, []);
  const v2Events = requests.filter(row => row.path === v2Batch).flatMap(row => row.request.events);
  assert.equal(total(v2Events), expected.windows[0].total_ms);
  assert.equal(v2Events.filter(row => row.section === 'theory').reduce((sum, row) => sum + row.active_ms, 0), 90_000);
  assert.equal(v2Events.filter(row => row.section === 'practice').reduce((sum, row) => sum + row.active_ms, 0), 120_000);
  assert.equal(v2Events.filter(row => row.payload.tool_id === 'catalog').reduce((sum, row) => sum + row.active_ms, 0), 30_000);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({
    browser: browser.version(), playwright: playwrightVersion, fixture: fixture.href, profiles: 1, tabs: 5,
    expected: expected.windows[0], actual: receipts.get(key),
    persisted: stateAfter.partition.windows[key], steps, requests,
    v2_event_count: v2Events.length,
    v2_active_ms: total(v2Events),
  }, null, 2));
} finally {
  await launched.close();
}
