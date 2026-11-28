import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { setTimeout as delay } from 'node:timers/promises';

const baseUrl = process.argv[2] ?? 'http://127.0.0.1:5514';
const debuggerPort = Number(process.env.CORP604_DEBUG_PORT ?? 9554);
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.CORP604_PLAYWRIGHT_MODULE ?? 'playwright');
const { version: playwrightVersion } = require(process.env.CORP604_PLAYWRIGHT_PACKAGE ?? 'playwright/package.json');
const fixtureUrl = new URL('/src/features/analytics-tracker/fixture.html', baseUrl);
assert.equal(fixtureUrl.hostname, '127.0.0.1', 'The synthetic fixture runs only on local loopback.');
assert.ok(Number.isInteger(debuggerPort) && debuggerPort > 1024 && debuggerPort <= 65535);
const grantPath = '/api/analytics/grants/collect';
const batchPath = '/api/analytics/events/batch';
const initialTime = new Date('2026-11-27T07:00:00.000Z');

async function readLog(page, selector) {
  const value = await page.locator(selector).textContent();
  return value ? JSON.parse(value) : [];
}

async function waitFor(read, ready) {
  const deadline = Date.now() + 5000;
  let value;
  do {
    value = await read();
    if (ready(value)) return value;
    await delay(25);
  } while (Date.now() < deadline);
  assert.fail('State did not arrive: ' + JSON.stringify(value));
}

const launched = await chromium.launch({
  channel: 'chromium',
  args: ['--remote-debugging-port=' + debuggerPort, '--remote-debugging-address=127.0.0.1'],
});
try {
  // A separate CDP attachment keeps Chromium's native tab visibility and focus.
  const browser = await chromium.connectOverCDP('http://127.0.0.1:' + debuggerPort, { noDefaults: true, timeout: 5000 });
  const profile = browser.contexts()[0];
  const pages = [];
  const pageErrors = [];
  const analyticsNetwork = [];
  profile.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/analytics/')) analyticsNetwork.push(request.url());
  });
  for (let index = 0; index < 5; index++) {
    const page = await profile.newPage();
    page.on('pageerror', error => pageErrors.push(String(error)));
    if (index === 0) {
      // Playwright's clock belongs to the browser context, even when called via a page.
      await page.clock.install({ time: new Date(initialTime.valueOf() - 1000) });
      await page.clock.pauseAt(initialTime);
    }
    await page.goto(fixtureUrl.href);
    await page.locator('#start').click();
    pages.push(page);
  }

  let elapsed = 0;
  const steps = [];
  const expectedFocus = index => pages.map((_, pageIndex) => ({
    visibility: pageIndex === index ? 'visible' : 'hidden',
    focus: pageIndex === index,
  }));
  async function state() {
    return Promise.all(pages.map(page => page.evaluate(() => ({
      visibility: document.visibilityState,
      focus: document.hasFocus(),
    }))));
  }
  async function step(name) {
    steps.push({
      name, elapsed_ms: elapsed, at: new Date(initialTime.valueOf() + elapsed).toISOString(),
      browsers: await state(),
      contexts: await Promise.all(pages.map(page => page.locator('#state').textContent())),
    });
  }
  async function switchTo(index) {
    await pages[index].bringToFront();
    const expected = expectedFocus(index);
    const observed = await waitFor(state, value => JSON.stringify(value) === JSON.stringify(expected));
    assert.deepEqual(observed, expected);
    await step('activate tab ' + (index + 1));
  }
  async function advance(ms) {
    await pages[0].clock.runFor(ms);
    elapsed += ms;
    const times = await Promise.all(pages.map(page => page.evaluate(() => Date.now())));
    assert.deepEqual(times, pages.map(() => initialTime.valueOf() + elapsed));
  }
  const selected = pages[0];
  const batches = async page => (await readLog(page, '#attempts')).filter(row => row.path === batchPath);
  const accepted = page => readLog(page, '#events');
  const sum = rows => rows.reduce((total, row) => total + row.active_ms, 0);

  await switchTo(0);
  await advance(10000);
  await selected.locator('#practice').click();
  await waitFor(() => accepted(selected), rows => rows.length === 1);
  await step('theory to practice');

  await advance(10000);
  await selected.locator('#theory').click();
  await waitFor(() => accepted(selected), rows => rows.length === 2);
  await step('practice to theory');

  await advance(10000);
  await selected.locator('#transport').click();
  await switchTo(1);
  const failed = await waitFor(() => batches(selected), rows => rows.some(row => row.response.error));
  const missed = failed.find(row => row.response.error);
  assert.deepEqual(missed.response, { error: 'synthetic offline' });
  assert.equal(await selected.locator('#transport').getAttribute('aria-pressed'), 'true');
  assert.equal(sum(await accepted(selected)), 20000);
  await step('focused theory cut on hide, first delivery failed');

  await selected.locator('#transport').evaluate(button => button.click());
  await advance(15000);
  await waitFor(() => accepted(selected), rows => rows.length === 3);
  const resumed = (await batches(selected)).find(row =>
    row.response.status === 200 && row.request.events.some(event => event.event_id === missed.request.events[0].event_id));
  assert.deepEqual(resumed.request, missed.request);
  assert.deepEqual(resumed.response.ack.items, missed.request.events.map((event, index) => ({
    index, event_id: event.event_id, status: 'accepted', code: 'committed',
  })));
  assert.equal((await state())[0].visibility, 'hidden');
  assert.equal(sum(await accepted(selected)), 30000);
  await step('hidden tab retries the queued v2 request and receives its ACK');

  await switchTo(2);
  await advance(5000);
  await switchTo(3);
  await advance(5000);
  await switchTo(4);

  await advance(300000);
  await step('idle deadline of the active fifth tab');
  // The next 15-second watchdog tick cuts the last segment at the true idle deadline.
  await advance(5000);
  const idleTotal = sum(await waitFor(() => accepted(pages[4]), rows => sum(rows) === 300000));
  await step('watchdog closes activity at the earlier idle deadline');
  await advance(30000);
  assert.equal(sum(await accepted(pages[4])), idleTotal);
  await step('35 seconds beyond the idle deadline');

  await switchTo(0);
  await selected.locator('#denied').click();
  await step('denied lesson selected');
  await advance(20000);
  await switchTo(1);
  assert.equal(sum(await accepted(selected)), 30000);
  await step('20 seconds on the denied lesson yielded no event');

  const tabs = await Promise.all(pages.map(async (page, index) => ({
    tab: index + 1,
    lifecycle: await readLog(page, '#lifecycle'),
    request_ack: await readLog(page, '#attempts'),
    accepted: await accepted(page),
  })));
  assert.deepEqual(tabs[0].accepted.map(row => [row.section, row.active_ms]),
    [['theory', 10000], ['practice', 10000], ['theory', 10000]]);
  assert.deepEqual(tabs.map(tab => sum(tab.accepted)), [30000, 15000, 5000, 5000, 300000]);
  const activeBounds = [[[0, 10000], [10000, 20000], [20000, 30000]], [[30000, 45000]],
    [[45000, 50000]], [[50000, 55000]], [[55000, 355000]]];
  tabs.forEach((tab, index) => {
    assert.ok(tab.lifecycle.some(row => row.visibility === 'visible' && row.focus));
    assert.ok(tab.lifecycle.some(row => row.visibility === 'hidden' && !row.focus));
    tab.request_ack.forEach(row => {
      assert.equal(row.method, 'POST');
      assert.ok(row.path === grantPath || row.path === batchPath);
      if (row.path === grantPath) {
        assert.deepEqual(row.request, { course_id: 'fixture-one' });
        return;
      }
      if (row.response.status === 200) {
        assert.equal(row.response.ack.items.length, row.request.events.length);
        row.request.events.forEach((event, eventIndex) => {
          assert.equal(event.schema_version, 2);
          assert.equal(event.course_id, 'fixture-one');
          assert.equal(event.lesson_id, 'fixture-lesson');
          assert.equal(event.visible, true);
          assert.equal(event.focused, true);
          assert.ok(event.active_ms > 0);
          const start = Date.parse(event.started_at) - initialTime.valueOf();
          const end = Date.parse(event.ended_at) - initialTime.valueOf();
          assert.equal(end - start, event.active_ms);
          assert.ok(activeBounds[index].some(([low, high]) => low <= start && end <= high),
            'Activity outside the focused interval of tab ' + tab.tab + ': ' + start + '–' + end);
          assert.deepEqual(row.response.ack.items[eventIndex],
            { index: eventIndex, event_id: event.event_id, status: 'accepted', code: 'committed' });
        });
      }
    });
  });
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(analyticsNetwork, []);
  console.log(JSON.stringify({
    baseline_web: '399df854',
    browser: browser.version(),
    playwright: playwrightVersion,
    fixture: fixtureUrl.href,
    profiles: 1,
    v2_http: 'synthetic fetch boundary',
    summary: {
      accepted_ms_by_tab: tabs.map(tab => sum(tab.accepted)),
      hidden_or_idle_ms: 0,
      denied_ms: 0,
      failed_attempts: 1,
      identical_retry_acknowledged: true,
      events: tabs.reduce((total, tab) => total + tab.accepted.length, 0),
    },
    steps,
    tabs,
  }, null, 2));
} finally {
  await launched.close();
}
