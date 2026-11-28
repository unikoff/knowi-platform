import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { setTimeout as delay } from 'node:timers/promises';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.CORP604_PLAYWRIGHT_MODULE ?? 'playwright');
const base = new URL('/src/features/analytics-tracker/windows-fixture.html', process.argv[2] ?? 'http://127.0.0.1:5514');
assert.equal(base.hostname, '127.0.0.1');
const port = Number(process.env.CORP604_DEBUG_PORT ?? 9557);
assert.ok(Number.isInteger(port) && port > 1024 && port <= 65535);
const start = Date.parse('2026-11-27T10:00:00+03:00');
const subject = 'c3333333-3333-4333-8333-333333333333';
const key = '2026-11-27/41';

function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') {
    return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
  }
  return JSON.stringify(value);
}

async function until(read, check) {
  const deadline = Date.now() + 5000;
  let value;
  do {
    value = await read();
    if (check(value)) return value;
    await delay(20);
  } while (Date.now() < deadline);
  assert.fail('Timed out: ' + JSON.stringify(value));
}

const launched = await chromium.launch({
  channel: 'chromium', args: ['--remote-debugging-port=' + port, '--remote-debugging-address=127.0.0.1'],
});
try {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:' + port, { noDefaults: true, timeout: 5000 });
  const profile = browser.contexts()[0];
  const windows = [];
  const legacy = [];
  const errors = [];
  await profile.route('**/api/analytics/**', async route => {
    try {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      const page = request.frame().page();
      const browserTime = await page.evaluate(() => Date.now());
      const data = request.postDataJSON();
      if (path.endsWith('/grant') || path.endsWith('/grants/collect')) {
        const grant = { grant: 'synthetic', subject_ref: subject, server_time: new Date(browserTime).toISOString(),
          expires_at: new Date(browserTime + 300_000).toISOString(), ...data };
        await route.fulfill({ status: 200, json: grant });
      } else if (path.endsWith('/windows/batch')) {
        const snapshot = data.windows[0];
        const { activity_date, slot_no, writer_id, writer_generation, server_epoch, seq, client_final, stats } = snapshot;
        const digest = createHash('sha256').update(canonical(snapshot)).digest('hex');
        const receipt = { writer_id, writer_generation, server_epoch, seq, payload_hash: digest };
        const ack = {
          index: 0, activity_date, slot_no, ...receipt, code: 'committed', status: 'committed',
          confirmed: { ...receipt, stats, client_final, revision: seq, rebase_at: null },
        };
        windows.push({ elapsed_ms: browserTime - start, snapshot, ack });
        await route.fulfill({ status: 200, json: { protocol_version: 1, items: [ack] } });
      } else {
        assert.ok(path.endsWith('/events/batch'));
        const items = data.events.map((event, index) => ({ index, event_id: event.event_id, code: 'committed', status: 'accepted' }));
        legacy.push(...data.events);
        await route.fulfill({ status: 200, json: { items } });
      }
    } catch (error) {
      errors.push(String(error));
      await route.abort().catch(() => undefined);
    }
  });

  const lesson = await profile.newPage();
  await lesson.clock.install({ time: new Date(start - 1500) });
  await lesson.clock.pauseAt(new Date(start - 500));
  await lesson.goto(base.href);
  const other = await profile.newPage();
  await other.goto('about:blank');
  const visible = () => lesson.evaluate(() => document.hasFocus() && document.visibilityState === 'visible');
  const inspect = () => lesson.evaluate(() => window.windowFixture.inspect());
  assert.equal(await visible(), false);
  await lesson.locator('#start').evaluate(button => button.click());
  await until(inspect, value => value?.ready);
  assert.deepEqual((await inspect()).partition.windows, {});
  await lesson.clock.runFor(500);
  await lesson.bringToFront();
  await until(visible, Boolean);
  const focusedAt = await lesson.evaluate(() => Date.now());
  for (let walked = 0; walked < 60_000; walked += 5000) {
    await lesson.clock.runFor(5000);
    await delay(25);
  }
  assert.equal(await lesson.evaluate(() => Date.now()), start + 60_000);
  const before = (await inspect()).partition.windows[key].stats[0].active_ms;
  assert.equal(before, 59_500);

  // React can reapply an unchanged Dockview context during a course or auth refresh.
  await lesson.evaluate(() => window.windowFixture.select('theory'));
  await delay(150);
  const beforeBlur = windows.map(row => row.snapshot.stats[0].active_ms);
  await other.bringToFront();
  await until(visible, value => !value);
  const blurredAt = await lesson.evaluate(() => Date.now());
  const durable = await until(inspect, value => value?.partition?.windows[key]?.stats[0]?.active_ms === 60_000);

  for (let walked = 0; walked < 70_000; walked += 5000) {
    await lesson.clock.runFor(5000);
    await delay(25);
  }
  const after = await until(inspect, value => value?.partition?.windows[key]?.confirmed?.confirmed?.stats[0]?.active_ms === 60_000);
  const captured = windows.filter(row => row.snapshot.activity_date === '2026-11-27' && row.snapshot.slot_no === 41);
  const report = {
    chromium: browser.version(), first_heartbeat_offset_ms: -500,
    focused_at: new Date(focusedAt).toISOString(), blurred_at: new Date(blurredAt).toISOString(),
    before_blur_durable_ms: before, sent_before_blur_ms: beforeBlur, after_blur_durable_ms: durable.partition.windows[key].stats[0].active_ms,
    v3: captured, v2_active_ms: legacy.reduce((sum, event) => sum + event.active_ms, 0),
    settled: after.partition.windows[key].confirmed, hidden_after_blur_ms: 70_000, errors,
  };
  console.log(JSON.stringify(report, null, 2));
  assert.deepEqual(errors, []);
  assert.equal(focusedAt, start);
  assert.equal(blurredAt, start + 60_000);
  assert.equal(report.v2_active_ms, 60_000);
  assert.deepEqual(captured.map(row => row.snapshot.stats[0].active_ms), [60_000]);
  assert.equal(captured[0].ack.confirmed.revision, 1);
} finally {
  await launched.close();
}
