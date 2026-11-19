import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
const require = createRequire(import.meta.url);
function load(name, extra = {}) {
  const code = ts.transpileModule(fs.readFileSync(new URL(name, import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const module = { exports: {} };
  new Function('require', 'module', 'exports', code)(id => extra[id] ?? require(id), module, module.exports);
  return module.exports;
}
const { AnalyticsTracker, VolatileQueue, isToolId } = load('./tracker.ts');
const { BrowserLifecycle } = load('./browser.ts', { './tracker': { AnalyticsTracker } });
const { ServerClock, BrowserTransport } = load('./transport.ts');
const { BrowserTrackerController } = load('./controller.ts', { './tracker': { AnalyticsTracker, isToolId }, './browser': { BrowserLifecycle }, './transport': { ServerClock, BrowserTransport } });
const { activitySources, isTrackableLesson } = load('./integration.ts');
const { parsePublishedCatalog } = load('./catalog.ts');
const { observeDockview } = load('./dockview.ts');
const context = { courseId: 'backend', catalogVersion: 'catalog-1', lessonId: 'lesson-1', section: 'theory' };
const subjectRef = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
function fixture(send = async (_grant, body) => ({ status: 200, body: { items: body.events.map((e, index) => ({ index, event_id: e.event_id, status: 'accepted', code: 'committed' })) } })) {
  let mono = 0, wall = Date.parse('2026-11-16T10:00:00Z'), serial = 0;
  const clock = { now: () => ({ mono, wall }), uuid: () => `00000000-0000-4000-8000-${String(++serial).padStart(12, '0')}`, random: () => 0 };
  const queue = new VolatileQueue();
  const tracker = new AnalyticsTracker({ clock, queue, grant: async id => `grant-${id}`, send }, true);
  const advance = (ms, wallMs = ms) => { mono += ms; wall += wallMs; };
  const start = () => { tracker.start(); tracker.setIdentity('A', 'user'); tracker.setPresence(true, true); tracker.setContext(context); };
  return { tracker, queue, advance, start, clock };
}

function emit(t, f, course = 'backend') { t.setContext({ ...context, courseId: course }); f.advance(1); t.setContext(null); }

test('disabled and staff never collect; visits and event fields conform to v2', () => {
  const f = fixture();
  const disabled = new AnalyticsTracker({ clock: f.clock, grant: async () => { throw Error(); }, send: async () => { throw Error(); }, queue: f.queue });
  disabled.start(); disabled.setIdentity('A', 'user'); disabled.setPresence(true, true);
  assert.equal(f.queue.read().length, 0);
  f.tracker.start(); f.tracker.setIdentity('staff', 'admin'); f.tracker.setPresence(true, true); emit(f.tracker, f);
  assert.equal(f.queue.read().length, 0);
  f.start(); f.advance(15000); f.tracker.setContext(null);
  const [segment] = f.queue.read().map(x => x.event);
  assert.equal(f.queue.read().length, 1); assert.equal(segment.type, 'activity_segment');
  assert.equal(segment.active_ms, 15000); assert.deepEqual(segment.payload, {});
  assert.equal(Date.parse(segment.ended_at) - Date.parse(segment.started_at), segment.active_ms);
  assert.deepEqual(Object.keys(segment).sort(), ['schema_version','event_id','type','course_id','catalog_version','lesson_id','section','session_id','started_at','ended_at','last_action_at','visible','focused','active_ms','payload'].sort());
});

test('idle, hide, focus, context switch, sleep and clock jumps never fill gaps', () => {
  const f = fixture(); f.start();
  for (let i = 0; i < 22; i++) { f.advance(15000); f.tracker.tick(); }
  assert.equal(f.queue.read().reduce((s, x) => s + x.event.active_ms, 0), 300000);
  f.advance(30000); f.tracker.actionObserved(); f.advance(5000); f.tracker.setPresence(false, false);
  f.advance(60000); f.tracker.setPresence(true, true); f.advance(5000); f.tracker.setContext({ ...context, section: 'practice' });
  f.advance(90000); f.tracker.tick(); f.advance(15000, 90000); f.tracker.tick(); f.tracker.actionObserved(); f.advance(4000); f.tracker.setContext(null);
  const items = f.queue.read().map(x => x.event);
  assert.equal(items.reduce((s, x) => s + x.active_ms, 0), 314000);
  assert.equal(items.at(-1).section, 'practice');
  assert.ok(items.every(x => x.active_ms <= 60000 && Date.parse(x.ended_at) <= Date.parse(x.last_action_at) + 300000));
});

test('minute watchdog preserves partial context and delayed timer remainder', () => {
  const f = fixture(); f.start();
  for (let i = 0; i < 3; i++) { f.advance(15000); f.tracker.tick(); }
  assert.equal(f.queue.read().length, 0);
  f.advance(17000); f.tracker.tick();
  assert.equal(f.queue.read()[0].event.active_ms, 60000);
  f.advance(3000); f.tracker.setContext({ ...context, section: 'practice' });
  assert.equal(f.queue.read()[1].event.active_ms, 5000);
  f.advance(4000); f.tracker.setPresence(false, false);
  assert.deepEqual(f.queue.read().map(x => [x.event.section, x.event.active_ms]), [['theory',60000], ['theory',5000], ['practice',4000]]);
});

test('watchdog phase emits whole minutes and retains the remainder without gaps', () => {
  const f = fixture(); f.start();
  for (const step of [19000, 14000, 14000, 14000, 14000]) { f.advance(step); f.tracker.tick(); }
  let events = f.queue.read().map(x => x.event);
  assert.deepEqual(events.map(x => x.active_ms), [60000]);
  for (let i = 0; i < 4; i++) { f.advance(15000); f.tracker.tick(); }
  events = f.queue.read().map(x => x.event);
  assert.deepEqual(events.map(x => x.active_ms), [60000, 60000]);
  assert.equal(events[0].ended_at, events[1].started_at);
  f.tracker.setContext(null);
  events = f.queue.read().map(x => x.event);
  assert.deepEqual(events.map(x => x.active_ms), [60000, 60000, 15000]);
  assert.equal(events[1].ended_at, events[2].started_at);
});

test('lost ACK retries same payload; out-of-order and nullable per-index ACKs', async () => {
  const calls = []; let n = 0;
  const f = fixture(async (_grant, body) => {
    calls.push(JSON.stringify(body)); if (!n++) throw Error('lost response');
    return { status: 200, body: { items: [
      { index: 1, event_id: null, status: 'rejected', code: 'invalid' },
      { index: 0, event_id: body.events[0].event_id, status: 'duplicate', code: 'same_payload' },
    ] } };
  });
  f.start(); emit(f.tracker, f); emit(f.tracker, f);
  await f.tracker.flush(); assert.equal(f.queue.read().length, 2);
  f.advance(1000); await f.tracker.flush();
  assert.equal(calls[0], calls[1]); assert.equal(f.queue.read().length, 0); assert.equal(f.tracker.testCounters().rejected, 1);
});

test('pending and malformed ACK remain pending while conflict is terminal', async () => {
  const f = fixture(async () => ({ status: 200, body: { items: [
    { index: 2, event_id: null, status: 'conflict', code: 'payload_conflict' },
    { index: 0, event_id: null, status: 'pending', code: 'unknown_catalog' },
    { index: 1, event_id: 'wrong', status: 'accepted', code: 'committed' },
  ] } }));
  f.start(); for (let i = 0; i < 3; i++) emit(f.tracker, f);
  await f.tracker.flush(); assert.equal(f.queue.read().length, 2);
  assert.equal(f.tracker.testCounters().conflict, 1); assert.equal(f.tracker.testCounters().malformedAck, 1);
});

test('account switch aborts pending grant and 401 clears old queue', async () => {
  let resolveGrant; const calls = [];
  const f = fixture(async (g, body) => { calls.push([g, body]); return { status: 401 }; });
  const t = new AnalyticsTracker({ clock: f.clock, queue: f.queue, grant: id => id === 'A' ? new Promise(r => { resolveGrant = r; }) : Promise.resolve('B-grant'), send: async (g, b) => { calls.push([g, b]); return { status: 401 }; } }, true);
  t.start(); t.setIdentity('A', 'user'); t.setPresence(true, true); emit(t, f);
  const pending = t.flush(); t.setIdentity('B', 'user'); resolveGrant('A-grant'); await pending;
  assert.equal(calls.length, 0); assert.equal(f.queue.read().length, 0);
  emit(t, f); await t.flush(); assert.equal(calls[0][0], 'B-grant'); assert.equal(f.queue.read().length, 0);
  emit(t, f); assert.equal(f.queue.read().length, 0);
});

test('buffer bounds and age drop oldest without fabricated ACK', () => {
  const f = fixture(); f.start();
  for (let i = 0; i < 205; i++) emit(f.tracker, f);
  assert.equal(f.queue.read().length, 200); assert.equal(f.tracker.testCounters().dropped, 5);
  f.advance(24 * 3600000 + 1); emit(f.tracker, f);
  assert.equal(f.queue.read().length, 1); assert.equal(f.tracker.testCounters().dropped, 205);
});

test('frequent input only updates idle deadline, not event volume', () => {
  const f = fixture(); f.start();
  for (let i = 0; i < 150; i++) { f.advance(100); f.tracker.actionObserved(); }
  assert.equal(f.queue.read().length, 0); f.tracker.setContext(null);
  assert.equal(f.queue.read().length, 1); assert.equal(f.queue.read()[0].event.active_ms, 15000);
});

test('batch is bounded and forbidden clears remaining items', async () => {
  const sizes = [];
  const f = fixture(async (_grant, body) => {
    sizes.push(body.events.length);
    return sizes.length === 1 ? { status: 200, body: { items: body.events.map((e, index) => ({ index, event_id: e.event_id, status: 'accepted', code: 'committed' })) } } : { status: 403 };
  });
  f.start(); for (let i = 0; i < 101; i++) emit(f.tracker, f);
  await f.tracker.flush(); assert.equal(f.queue.read().length, 1); await f.tracker.flush();
  assert.deepEqual(sizes, [100, 1]); assert.equal(f.queue.read().length, 0);
});

test('retry budget stops repeatedly unavailable deliveries', async () => {
  let calls = 0; const f = fixture(async () => { calls++; return { status: 503 }; });
  f.start(); emit(f.tracker, f);
  for (let i = 0; i < 9; i++) { await f.tracker.flush(); f.advance(60001); }
  assert.equal(calls, 8); assert.equal(f.queue.read().length, 0); assert.equal(f.tracker.testCounters().dropped, 1);
});

test('Retry-After is respected while expired events are purged', async () => {
  let calls = 0; const f = fixture(async () => { calls++; return { status: 429, retryAfterMs: 25 * 3600000 }; });
  f.start(); emit(f.tracker, f); await f.tracker.flush();
  f.advance(24 * 3600000 + 1); await f.tracker.flush();
  assert.equal(calls, 1); assert.equal(f.queue.read().length, 0); assert.equal(f.tracker.testCounters().dropped, 1);
});

test('grant denial clears queue and duplicated ACK index cannot acknowledge', async () => {
  const f = fixture();
  const t = new AnalyticsTracker({ clock: f.clock, queue: f.queue, grant: async () => ({ status: 403 }), send: async () => { throw Error('must not send'); } }, true);
  t.start(); t.setIdentity('A', 'user'); t.setPresence(true, true); emit(t, f);
  await t.flush(); assert.equal(f.queue.read().length, 0);
  const g = fixture(async () => ({ status: 200, body: { items: [
    { index: 0, event_id: null, status: 'accepted', code: 'committed' },
    { index: 0, event_id: null, status: 'accepted', code: 'committed' },
  ] } }));
  g.start(); emit(g.tracker, g); await g.tracker.flush();
  assert.equal(g.queue.read().length, 1); assert.equal(g.tracker.testCounters().malformedAck, 2);
});

test('course scoped grants deliver separately and 403 only discards the denied course', async () => {
  const grants = [], batches = [], f = fixture(async (grant, body) => {
    batches.push([grant, body.events.map(x => x.course_id)]);
    return { status: 200, body: { items: body.events.map((e, index) => ({ index, event_id: e.event_id, status: 'accepted', code: 'committed' })) } };
  });
  const t = new AnalyticsTracker({ clock: f.clock, queue: f.queue,
    grant: async (_identity, _signal, course) => { grants.push(course); return course === 'backend' ? { status: 403 } : `grant-${course}`; },
    send: async (grant, body) => { batches.push([grant, body.events.map(x => x.course_id)]); return { status: 200, body: { items: body.events.map((e, index) => ({ index, event_id: e.event_id, status: 'accepted', code: 'committed' })) } }; } }, true);
  t.start(); t.setIdentity('A', 'user'); t.setPresence(true, true);
  emit(t, f); emit(t, f, 'other'); emit(t, f);
  await t.flush(); assert.deepEqual(f.queue.read().map(x => x.event.course_id), ['other']);
  await t.flush(); assert.deepEqual(grants, ['backend', 'other']); assert.deepEqual(batches, [['grant-other', ['other']]]);
  assert.equal(f.queue.read().length, 0); emit(t, f); assert.equal(f.queue.read().length, 0);
});

test('new server subject clears old identity events before delivery even when local key is unchanged', async () => {
  let subject = 'old', sends = 0; const f = fixture(); let t;
  t = new AnalyticsTracker({ clock: f.clock, queue: f.queue,
    grant: async () => { if (!t.bindSubject(subject)) throw Error('changed'); return 'grant'; },
    send: async () => { sends++; return { status: 503 }; } }, true);
  t.start(); t.setIdentity('same-local-key', 'user'); t.setPresence(true, true); t.bindSubject('old');
  emit(t, f); subject = 'new'; await t.flush();
  assert.equal(sends, 0); assert.equal(f.queue.read().length, 0);
});

test('server grant clock validates UTC, RTT, expiry and clock jumps; fetch binds every fresh grant', async () => {
  let mono = 1000, local = 1_000_000, count = 0; const bound = [];
  const source = { now: () => ({mono, wall: local}), uuid: () => 'id', random: () => 0 };
  const clock = new ServerClock(source);
  let payload = { grant: 'opaque', subject_ref: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', server_time: '2026-11-16T10:00:00Z', expires_at: '2026-11-16T10:05:00+00:00' };
  const transport = new BrowserTransport(clock, ref => { bound.push(ref); return true; }, async (url, init) => {
    count++; assert.match(url, /\/api\/analytics\/grants\/collect$/); assert.equal(init.credentials, 'include');
    assert.deepEqual(JSON.parse(init.body), { course_id: 'backend' }); mono += 100; local += 100;
    return new Response(JSON.stringify(payload), {status: 200, headers: {'Content-Type': 'application/json'}});
  });
  assert.equal(clock.isValid(), false);
  await transport.grant('A', new AbortController().signal, 'backend');
  assert.equal(clock.isValid(), true); assert.equal(clock.now().wall, Date.parse(payload.server_time) + 50);
  mono += 1000; local += 1000; assert.equal(clock.now().wall, Date.parse(payload.server_time) + 1050);
  local += 5000; assert.equal(clock.isValid(), false);
  payload = {...payload, server_time: '2026-02-31T10:00:00Z'};
  await assert.rejects(transport.grant('A', new AbortController().signal, 'backend'));
  payload = {...payload, server_time: '2026-11-16T10:00:00Z', expires_at: '2026-11-16T09:59:59Z'};
  await assert.rejects(transport.grant('A', new AbortController().signal, 'backend'));
  assert.equal(count, 3); assert.equal(bound.length, 1);
});

test('native-style fetch is called without a transport receiver for grants and batches', async () => {
  const stamp = Date.parse('2026-11-17T08:00:00Z');
  const clock = new ServerClock({ now: () => ({mono: 1000, wall: stamp}), uuid: () => 'id', random: () => 0 });
  const routes = [];
  const transport = new BrowserTransport(clock, () => true, async function (url, init) {
    // Window.fetch rejects BrowserTransport as its receiver in Chromium.
    assert.equal(this, undefined);
    routes.push(url);
    if (url.endsWith('/grants/collect')) {
      return new Response(JSON.stringify({ grant: 'synthetic-grant', subject_ref: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        server_time: '2026-11-17T08:00:00Z', expires_at: '2026-11-17T08:05:00Z' }), {status: 200});
    }
    assert.equal(init.credentials, 'omit');
    return new Response(JSON.stringify({items: []}), {status: 200});
  });
  const abort = new AbortController();
  assert.equal(await transport.grant('synthetic-local-key', abort.signal, 'backend'), 'synthetic-grant');
  assert.equal((await transport.send('synthetic-grant', {events: []}, abort.signal)).status, 200);
  assert.deepEqual(routes, ['/api/analytics/grants/collect', '/api/analytics/events/batch']);
});

test('collection grant is reused past one minute until expiry and is scoped to the identity and course', async () => {
  const start = Date.parse('2026-11-17T08:00:00Z');
  let mono = 0, requests = 0;
  const clock = new ServerClock({ now: () => ({ mono, wall: start + mono }), uuid: () => 'id', random: () => 0 });
  const transport = new BrowserTransport(clock, () => true, async () => new Response(JSON.stringify({
    grant: `grant-${++requests}`, subject_ref: subjectRef,
    server_time: new Date(start + mono).toISOString(), expires_at: new Date(start + mono + 300000).toISOString(),
  }), { status: 200 }));
  const signal = new AbortController().signal;
  assert.equal(await transport.grant('A', signal, 'backend'), 'grant-1');
  mono = 120000;
  assert.equal(await transport.grant('A', signal, 'backend'), 'grant-1');
  const aborted = new AbortController();
  aborted.abort();
  await assert.rejects(transport.grant('A', aborted.signal, 'backend'), { name: 'AbortError' });
  mono = 300000;
  assert.equal(await transport.grant('A', signal, 'backend'), 'grant-2');
  assert.equal(await transport.grant('A', signal, 'other'), 'grant-3');
  assert.equal(await transport.grant('B', signal, 'other'), 'grant-4');
  assert.equal(requests, 4);
});

test('a grant completing after a scope reset does not repopulate the cache', async () => {
  const stamp = Date.parse('2026-11-17T08:00:00Z'), pending = [];
  const clock = new ServerClock({ now: () => ({ mono: 0, wall: stamp }), uuid: () => 'id', random: () => 0 });
  const transport = new BrowserTransport(clock, () => true, () => new Promise(resolve => pending.push(resolve)));
  const reply = grant => new Response(JSON.stringify({
    grant, subject_ref: subjectRef,
    server_time: new Date(stamp).toISOString(), expires_at: new Date(stamp + 300000).toISOString(),
  }), { status: 200 });
  const signal = new AbortController().signal;
  const old = transport.grant('A', signal, 'backend');
  transport.clearGrant();
  pending[0](reply('old'));
  assert.equal(await old, 'old');
  const fresh = transport.grant('A', signal, 'backend');
  assert.equal(pending.length, 2);
  pending[1](reply('fresh'));
  assert.equal(await fresh, 'fresh');
  assert.equal(await transport.grant('A', signal, 'backend'), 'fresh');
});

test('Collector credential challenge clears the stored grant and leaves response status intact', async () => {
  const stamp = Date.parse('2026-11-17T08:00:00Z');
  let grants = 0;
  const clock = new ServerClock({ now: () => ({ mono: 0, wall: stamp }), uuid: () => 'id', random: () => 0 });
  const transport = new BrowserTransport(clock, () => true, async url => url.endsWith('/grants/collect')
    ? new Response(JSON.stringify({
      grant: `grant-${++grants}`, subject_ref: subjectRef,
      server_time: new Date(stamp).toISOString(), expires_at: new Date(stamp + 300000).toISOString(),
    }), { status: 200 })
    : new Response('', { status: 503, headers: { 'WWW-Authenticate': 'Bearer' } }));
  const signal = new AbortController().signal;
  const token = await transport.grant('A', signal, 'backend');
  assert.equal((await transport.send(token, { events: [] }, signal)).status, 503);
  assert.equal(await transport.grant('A', signal, 'backend'), 'grant-2');
});

test('server clock rejects oversized RTT and negative monotonic progression', () => {
  let sample = {mono: 0, wall: 0}; const clock = new ServerClock({now: () => sample, uuid: () => 'id', random: () => 0});
  assert.equal(clock.sync(100_000, {mono: 0, wall: 0}, {mono: 5001, wall: 5001}, 200_000), false);
  assert.equal(clock.sync(100_000, {mono: 10, wall: 10}, {mono: 5, wall: 5}, 200_000), false);
  assert.equal(clock.isValid(), false);
});

test('out-of-order grant responses cannot restore an older lifecycle; grant 429 retains Retry-After', async () => {
  const stamp = Date.parse('2026-11-16T10:00:00Z'), bound = [], gates = [];
  const clock = new ServerClock({now: () => ({mono: 1000, wall: stamp}), uuid: () => 'id', random: () => 0});
  const transport = new BrowserTransport(clock, ref => { bound.push(ref); return true; }, () => new Promise(resolve => gates.push(resolve)));
  const first = transport.grant('A', new AbortController().signal, 'backend');
  const second = transport.grant('A', new AbortController().signal, 'other');
  const response = ref => new Response(JSON.stringify({grant:'synthetic', subject_ref:ref, server_time:new Date(stamp).toISOString(), expires_at:new Date(stamp + 300000).toISOString()}), {status:200});
  gates[1](response('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')); await second;
  gates[0](response('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')); await assert.rejects(first);
  assert.deepEqual(bound, ['bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb']);
  const limited = new BrowserTransport(clock, () => true, async () => new Response('', {status:429, headers:{'Retry-After':'120'}}));
  await assert.rejects(limited.grant('A', new AbortController().signal, 'backend'), error => error.retryAfterMs === 120000);
});

test('source selection preserves baseline and keeps legacy visits off during cutover', () => {
  for (const [tracker,cutover] of [[false,false],[false,true]]) {
    for (const role of ['user','leader','admin',null]) {
      for (const version of [null,'published']) {
        assert.deepEqual(activitySources(tracker,cutover,version,role), {tracker:false, legacy:true});
      }
    }
  }
  for (const role of ['leader','admin',null]) {
    assert.deepEqual(activitySources(true,false,'published',role), {tracker:false,legacy:true});
  }
  assert.deepEqual(activitySources(true,false,null,'user'), {tracker:false,legacy:true});
  assert.deepEqual(activitySources(true,false,'published','user'), {tracker:true,legacy:true});
  for (const role of ['leader','admin',null]) {
    for (const version of [null,'published']) {
      assert.deepEqual(activitySources(true,true,version,role), {tracker:false, legacy:false});
    }
  }
  for (const invalid of [null,'','bad version','../bad','v'.repeat(129)]) {
    assert.deepEqual(activitySources(true,true,invalid,'user'), {tracker:false, legacy:false});
  }
  assert.deepEqual(activitySources(true,true,'published-1','user'), {tracker:true, legacy:false});
  assert.deepEqual(activitySources(true,true,'v'.repeat(128),'user'), {tracker:true, legacy:false});
});

test('metadata loss and recovery do not restart legacy visits during cutover', () => {
  assert.deepEqual(activitySources(true,true,null,'user'), {tracker:false, legacy:false});
  assert.deepEqual(activitySources(true,true,'published-1','user'), {tracker:true, legacy:false});
});

test('only loaded and authorized lesson content is trackable', () => {
  const target = {trackId:'track', lessonId:'one'}, summary = {id:'one', access:'free'}, material = {lesson:{id:'one'}, loading:false};
  const course = {tracksLoaded:true, courses:{track:{lessons:[summary], loading:false}}, materials:{one:material}};
  assert.equal(isTrackableLesson(target, course, {}), true);
  assert.equal(isTrackableLesson(target, {...course, tracksLoaded:false}, {}), false);
  assert.equal(isTrackableLesson(target, {...course, materials:{one:{...material, error:'failed'}}}, {}), false);
  assert.equal(isTrackableLesson(target, {...course, courses:{track:{lessons:[{...summary, is_available:false}], loading:false}}}, {}), false);
  const paid = {...course, courses:{track:{lessons:[{...summary, access:'subscription'}], loading:false}}};
  assert.equal(isTrackableLesson(target, paid, {}), false); assert.equal(isTrackableLesson(target, paid, {has_active_subscription:true}), true);
});

test('catalog metadata is course-bound, strict and fail-closed without published version', () => {
  const valid = {course_id:'backend', catalog_version:'published-v2', lesson_ids:['one', 'two']};
  const result = parsePublishedCatalog(valid, 'backend');
  assert.equal(result.version, 'published-v2'); assert.deepEqual([...result.lessons], ['one', 'two']);
  for (const bad of [
    {...valid, course_id:'other'}, {...valid, catalog_version:null, lesson_ids:[]}, {...valid, catalog_version:'bad version'},
    {...valid, lesson_ids:['one', 'one']}, {...valid, lesson_ids:['../private']}, {...valid, lesson_ids:'one'},
  ]) assert.equal(parsePublishedCatalog(bad, 'backend'), null);
});

test('Dockview observation follows active visible panel, lesson parameters and cleanup', () => {
  const emitter = () => {
    const listeners = new Set();
    return { on: callback => { listeners.add(callback); return {dispose: () => listeners.delete(callback)}; }, emit: () => listeners.forEach(fn => fn()), count: () => listeners.size };
  };
  const active = emitter(), layout = emitter(), params = emitter(), visible = emitter(), updates = [];
  const theory = {id:'theory', params:{trackId:'track', lessonId:'one'}, api:{isVisible:true, onDidParametersChange:params.on, onDidVisibilityChange:visible.on}};
  const api = {activePanel:theory, onDidActivePanelChange:active.on, onDidLayoutChange:layout.on};
  const dispose = observeDockview(api, value => updates.push(value));
  assert.equal(updates.at(-1).target.lessonId, 'one');
  theory.params = {trackId:'track', lessonId:'two'}; params.emit(); assert.equal(updates.at(-1).target.lessonId, 'two');
  theory.api.isVisible = false; visible.emit(); assert.equal(updates.at(-1), undefined);
  const accountParams = emitter(), accountVisible = emitter();
  api.activePanel = {id:'account', params:{}, api:{isVisible:true, onDidParametersChange:accountParams.on, onDidVisibilityChange:accountVisible.on}};
  active.emit(); assert.equal(updates.at(-1).id, 'account'); assert.equal(params.count(), 0); assert.equal(visible.count(), 0);
  dispose(); assert.equal(updates.at(-1), undefined); assert.equal(active.count(), 0); assert.equal(layout.count(), 0); assert.equal(accountParams.count(), 0);
});

test('browser controller requests a grant only after an allowed segment is queued', async t => {
  class Surface extends EventTarget { visibilityState = 'visible'; hasFocus() { return true; } }
  const win = new Surface(), doc = new Surface(), requests = [];
  let mono = 0, wall = Date.parse('2026-11-16T10:00:00Z');
  const timeSource = { now: () => ({mono, wall}), uuid: () => crypto.randomUUID(), random: () => 0 };
  const offset = 2 * 60 * 60 * 1000;
  const fetcher = async (url, init) => {
    requests.push([url, init]);
    if (url.endsWith('/grants/collect')) {
      const server = new Date(wall + offset).toISOString(), expiry = new Date(wall + offset + 300000).toISOString();
      return new Response(JSON.stringify({grant: 'opaque', subject_ref: subjectRef, server_time: server, expires_at: expiry}), {status: 200});
    }
    const body = JSON.parse(init.body);
    return new Response(JSON.stringify({items: body.events.map((e, index) => ({index, event_id:e.event_id, status:'accepted', code:'committed'}))}), {status: 200});
  };
  const controller = new BrowserTrackerController({identityKey:'A', subjectRef, role:'user', win, doc, fetcher, timeSource, canTrackLesson: t => t.lessonId === 'allowed'});
  t.after(() => controller.stop());
  controller.start(); controller.setCourse({courseId:'backend', catalogVersion:'catalog-1'});
  controller.onActivePanel({id:'theory', kind:'theory', target:{trackId:'track-1', lessonId:'allowed'}});
  win.dispatchEvent(new Event('online')); await new Promise(setImmediate);
  assert.equal(requests.length, 0);
  mono += 2000; wall += 2000; win.dispatchEvent(new Event('blur')); await new Promise(setImmediate); await new Promise(setImmediate);
  const segment = requests.filter(([url]) => url.endsWith('/events/batch')).flatMap(([, init]) => JSON.parse(init.body).events).find(e => e.type === 'activity_segment');
  assert.equal(segment.lesson_id, 'allowed'); assert.equal(segment.section, 'theory'); assert.equal(segment.active_ms, 2000);
  assert.equal(segment.started_at, '2026-11-16T12:00:00.000Z');
  assert.equal(segment.ended_at, '2026-11-16T12:00:02.000Z');
  win.dispatchEvent(new Event('focus')); controller.onActivePanel({id:'practice', kind:'practice', target:{trackId:'track-1', lessonId:'locked'}});
  mono += 2000; wall += 2000; win.dispatchEvent(new Event('blur')); await new Promise(setImmediate);
  assert.equal(requests.length, 2);
  controller.onActivePanel({id:'account'}); controller.stop();
  const disabled = new BrowserTrackerController({identityKey:'A', subjectRef, role:'admin', win, doc, fetcher, canTrackLesson: () => true});
  disabled.start(); disabled.setCourse({courseId:'backend', catalogVersion:'catalog-1'});
  const before = requests.length; await new Promise(setImmediate); assert.equal(requests.length, before); disabled.stop();
});

test('a first grant for a different lifecycle cannot deliver buffered activity', async t => {
  class Surface extends EventTarget { visibilityState = 'visible'; hasFocus() { return true; } }
  const win = new Surface(), doc = new Surface(), calls = [];
  const start = Date.parse('2026-11-16T10:00:00Z');
  let mono = 0;
  const controller = new BrowserTrackerController({
    identityKey: 'A', subjectRef, role: 'user', win, doc, canTrackLesson: () => true,
    timeSource: { now: () => ({ mono, wall: start + mono }), uuid: () => crypto.randomUUID(), random: () => 0 },
    fetcher: async url => {
      calls.push(url);
      return new Response(JSON.stringify({
        grant: 'opaque', subject_ref: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
        server_time: new Date(start + mono).toISOString(), expires_at: new Date(start + mono + 60000).toISOString(),
      }), { status: 200 });
    },
  });
  t.after(() => controller.stop());
  controller.start(); controller.setCourse({ courseId: 'backend', catalogVersion: 'catalog-1' });
  controller.onActivePanel({ id: 'theory', kind: 'theory', target: { trackId: 'track', lessonId: 'lesson' } });
  mono += 2000;
  win.dispatchEvent(new Event('blur'));
  await new Promise(setImmediate); await new Promise(setImmediate);
  assert.deepEqual(calls, ['/api/analytics/grants/collect']);
  assert.equal(controller.running, false);
});

test('lifecycle start and stop are idempotent, including StrictMode remount', () => {
  class Surface extends EventTarget { visibilityState = 'visible'; hasFocus() { return true; } }
  const f = fixture(); const win = new Surface(), doc = new Surface();
  const adapter = new BrowserLifecycle(f.tracker, win, doc);
  adapter.start(); adapter.start(); f.tracker.setIdentity('A', 'user'); adapter.setContext(context);
  f.advance(1000); win.dispatchEvent(new Event('blur')); f.advance(1000); win.dispatchEvent(new Event('focus'));
  f.advance(1000); adapter.stop(); adapter.stop(); adapter.start(); f.advance(1000); adapter.stop();
  assert.equal(f.queue.read().reduce((s, x) => s + x.event.active_ms, 0), 3000);
});

test('tool segments share boundaries with lessons and retry with identical IDs', async () => {
  const calls=[]; let count=0;
  const f=fixture(async (_grant,body)=>{calls.push(JSON.stringify(body)); if (!count++) throw Error('lost'); return {status:200,body:{items:body.events.map((e,index)=>({index,event_id:e.event_id,status:'accepted',code:'committed'}))}};});
  f.start(); f.advance(5000); f.tracker.setContext({courseId:'backend',toolId:'account-statistics'});
  f.advance(7000); f.tracker.setContext({courseId:'backend',toolId:'interpreter'});
  f.advance(3000); f.tracker.setContext(null);
  const events=f.queue.read().map(item=>item.event);
  assert.deepEqual(events.map(e=>[e.type,e.active_ms]),[['activity_segment',5000],['tool_segment',7000],['tool_segment',3000]]);
  assert.deepEqual([events[1].catalog_version,events[1].lesson_id,events[1].section,events[1].payload],['platform-tools-v1',null,null,{tool_id:'account-statistics'}]);
  await f.tracker.flush(); f.advance(1000); await f.tracker.flush(); assert.equal(calls[0],calls[1]);
});

test('tool queue is discarded on identity or server lifecycle change', () => {
  const f=fixture(); f.start(); f.tracker.setContext({courseId:'platform-tools',toolId:'catalog'}); f.advance(2000); f.tracker.setContext(null);
  assert.equal(f.queue.read().length,1); f.tracker.setIdentity(null,null); assert.equal(f.queue.read().length,0);
  f.tracker.setIdentity('A','user'); f.tracker.bindSubject('old'); f.tracker.setContext({courseId:'platform-tools',toolId:'catalog'}); f.advance(1000); f.tracker.setContext(null);
  f.tracker.bindSubject('new'); assert.equal(f.queue.read().length,0);
});

test('controller selects opt-in tools, valid lessons and reserved platform context', () => {
  class Surface extends EventTarget {visibilityState='visible'; hasFocus(){return true;}}
  const now={mono:0,wall:Date.parse('2026-11-16T10:00:00Z')};
  const timeSource={now:()=>({...now}),uuid:()=>crypto.randomUUID(),random:()=>0};
  const make=toolsEnabled=>new BrowserTrackerController({identityKey:'A',subjectRef,role:'user',toolsEnabled,win:new Surface(),doc:new Surface(),timeSource,canTrackLesson:t=>t.lessonId==='allowed'});
  const c=make(true);
  c.setCourse({courseId:'backend',catalogVersion:null}); c.onActivePanel({id:'theory',kind:'theory'});
  assert.deepEqual(c.tracker.context,{courseId:'backend',toolId:'theory'});
  c.onActivePanel({id:'untrusted-panel'}); assert.deepEqual(c.tracker.context,{courseId:'backend',toolId:'workspace'});
  c.onActiveTool('account-statistics'); assert.deepEqual(c.tracker.context,{courseId:'backend',toolId:'account-statistics'});
  c.setCourse({courseId:'backend',catalogVersion:'catalog-1'}); c.onActivePanel({id:'practice',kind:'practice',target:{trackId:'track',lessonId:'allowed'}});
  assert.equal(c.tracker.context.toolId,'account-statistics'); c.onActiveTool();
  assert.deepEqual(c.tracker.context,{courseId:'backend',catalogVersion:'catalog-1',lessonId:'allowed',section:'practice'});
  c.onActivePanel({id:'account'}); assert.deepEqual(c.tracker.context,{courseId:'backend',toolId:'account'});
  c.setCourse({courseId:'platform-tools',catalogVersion:null}); c.onActiveTool('catalog'); assert.deepEqual(c.tracker.context,{courseId:'platform-tools',toolId:'catalog'});
  const legacy=make(false); legacy.setCourse({courseId:'platform-tools',catalogVersion:null}); legacy.onActiveTool('catalog'); assert.equal(legacy.tracker.context,null);
});
