import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function load(name, imports = {}) {
  const source = readFileSync(new URL(name, import.meta.url), 'utf8');
  const js = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const module = { exports: {} };
  new Function('require', 'module', 'exports', js)(id => imports[id] ?? require(id), module, module.exports);
  return module.exports;
}

const windows = load('./windows.ts');
const { backoff, checkWindowAck, snapshotHash, splitWindowSpan } = windows;
const { claimSend, emptyPartition, settleSend, updateLease, WindowOutbox, WindowStorageError } = load('./windows-outbox.ts', { './windows': windows });
const legacyTransport = load('./transport.ts');
const { ServerClock } = legacyTransport;
const { WindowAccountChanged, WindowHttpError, WindowTransport } =
  load('./windows-transport.ts', { './windows': windows, './transport': legacyTransport });
const expected = JSON.parse(readFileSync(new URL('./windows.expected.json', import.meta.url), 'utf8'));
const contract = JSON.parse(readFileSync(new URL('../../../../../services/analytics/contracts/fixtures/windows-v1/scenarios.json', import.meta.url), 'utf8'));
const subject = contract.student_id;
const writer = { writer_id: contract.grants.A.writer_id, writer_generation: 1 };
const theory = { position: expected.boundaries.context, catalogVersion: 'backend-v1' };

test('604-Б matches the independent quarter-hour and Moscow midnight boundaries', () => {
  const rows = expected.boundaries.intervals.flatMap(({ start, end }) =>
    splitWindowSpan({ start: Date.parse(start), end: Date.parse(end), context: theory }));
  assert.deepEqual(rows.map(row => ({
    activity_date: row.activity_date, slot_no: row.slot_no, active_ms: row.end - row.start,
  })), expected.boundaries.windows);
});

test('604-А produces one cumulative snapshot and retries the immutable body after a lost ACK', async () => {
  const partition = emptyPartition(subject, writer);
  const start = Date.parse(expected.five_tabs.start);
  for (const step of expected.five_tabs.steps) {
    const { tool_id, course_id, lesson_id } = step;
    const spanStart = start + step.start_offset_ms;
    const end = spanStart + step.duration_ms;
    const context = { position: { tool_id, course_id, lesson_id }, catalogVersion: lesson_id ? 'backend-v1' : undefined };
    // Checkpoints extend the lease, then release at the same switching boundary.
    for (let cursor = spanStart; cursor < end; cursor += 5000) {
      const cut = Math.min(cursor + 5000, end);
      const result = updateLease(partition, String(step.tab), cut, spanStart, [{ start: cursor, end: cut, context }], cut === end);
      assert.equal(result.consumed, true);
    }
  }
  const now = Date.parse(expected.five_tabs.leave_focus_at);
  const first = claimSend(partition, now, false);
  assert.ok(first);
  assert.equal(first.key, '2026-11-27/41');
  assert.deepEqual(first.pending.snapshot.stats, windows.sortStats(expected.five_tabs.windows[0].stats));
  const digest = await snapshotHash(first.pending.snapshot);
  settleSend(partition, first, { kind: 'retry', reason: 'v3_unconfirmed' }, now + 1000);
  assert.equal(claimSend(partition, now + 999, false), null);
  const replay = claimSend(partition, now + 1000, false);
  assert.equal(replay.pending.body, first.pending.body);
  assert.equal(replay.pending.snapshot.seq, first.pending.snapshot.seq);
  const snapshot = replay.pending.snapshot;
  const ack = {
    ...snapshot, index: 0, payload_hash: digest, status: 'duplicate', code: 'same_payload',
    confirmed: { ...snapshot, payload_hash: digest, revision: 1, rebase_at: null },
  };
  const outcome = checkWindowAck({ protocol_version: 1, items: [ack] }, snapshot, digest);
  assert.equal(outcome.kind, 'accepted');
  settleSend(partition, replay, outcome, now + 1000);
  assert.equal(partition.windows[first.key].pending, undefined);
  assert.equal(partition.windows[first.key].confirmed.confirmed.revision, 1);
  assert.equal(claimSend(partition, now + 61_000, false), null);
});

test('matches contract digests and removes only the ACK for the exact index, key and version', async () => {
  const fixture = contract.cases['602-Е'], snapshot = fixture.offline_body.windows[0];
  const digest = await snapshotHash(snapshot);
  assert.equal(digest, fixture.correct_ack.payload_hash);
  const check = ack => checkWindowAck({ protocol_version: 1, items: [ack] }, snapshot, digest);
  assert.equal(check(fixture.correct_ack).kind, 'accepted');
  assert.equal(check(fixture.foreign_ack).kind, 'retry');
  assert.equal(check({ ...fixture.correct_ack, index: 1 }).kind, 'retry');
  assert.equal(check({ ...fixture.correct_ack, writer_id: contract.grants.B.writer_id }).kind, 'retry');
  assert.equal(check({ ...fixture.correct_ack, server_epoch: snapshot.server_epoch + 1 }).kind, 'retry');
  assert.equal(check({ ...fixture.correct_ack, seq: snapshot.seq + 1 }).kind, 'retry');
  assert.equal(check({ ...fixture.correct_ack, payload_hash: 'a'.repeat(64) }).kind, 'retry');
  assert.equal(check({ ...fixture.correct_ack, confirmed: null }).kind, 'retry');
  assert.equal(check(fixture.pending_ack).reason, 'writer_conflict');
  assert.equal(check({ ...fixture.correct_ack, status: 'stale', code: 'stale_epoch' }).reason, 'epoch_rebase_required');
  assert.equal(await snapshotHash(contract.cases['602-А'].batch.windows[0]), contract.cases['602-А'].expected.digest);
});

test('lease fencing prevents overlap and marks time lost when a crashed leader misses lease expiry', () => {
  const partition = emptyPartition(subject, writer);
  const start = Date.parse(expected.five_tabs.start);
  const span = (before, after) => ({ start: start + before, end: start + after, context: theory });
  updateLease(partition, 'first', start + 5000, start, [span(0, 5000)], false);
  assert.deepEqual(updateLease(partition, 'second', start + 6000, start + 4000, [span(4000, 6000)], false),
    { owned: false, consumed: false });
  updateLease(partition, 'first', start + 6000, start, [span(5000, 6000)], true);
  updateLease(partition, 'second', start + 8000, start + 4000, [span(4000, 8000)], false);
  assert.equal(partition.windows['2026-11-27/41'].stats[0].active_ms, 8000);
  updateLease(partition, 'third', start + 24_000, start + 10_000, [span(10_000, 24_000)], false);
  assert.ok(partition.reasons.includes('lease_lost'));
  assert.equal(partition.windows['2026-11-27/41'].stats[0].active_ms, 8000);
  updateLease(partition, 'third', start + 29_000, start + 24_000, [span(24_000, 29_000)], false);
  assert.equal(partition.windows['2026-11-27/41'].stats[0].active_ms, 13_000);
});

test('a hidden sender cannot stage a new snapshot ahead of the active lease checkpoint', () => {
  const partition = emptyPartition(subject, writer);
  const start = Date.parse(expected.five_tabs.start);
  for (let offset = 0; offset < 55_000; offset += 5000) {
    updateLease(partition, 'active', start + offset + 5000, start, [
      { start: start + offset, end: start + offset + 5000, context: theory },
    ], false);
  }
  const boundary = start + 60_000;
  assert.equal(claimSend(partition, boundary, false, 'hidden'), null);
  updateLease(partition, 'active', boundary, start,
    [{ start: boundary - 5000, end: boundary, context: theory }], false);
  assert.equal(claimSend(partition, boundary, false, 'active').pending.snapshot.stats[0].active_ms, 60_000);
});

test('the first minute waits for its durable tail when a no-op flush lands between heartbeats', () => {
  const partition = emptyPartition(subject, writer);
  const start = Date.parse(expected.five_tabs.start);
  let last = start;
  for (let at = start + 4500; at <= start + 59_500; at += 5000) {
    updateLease(partition, 'focused', at, start, [{ start: last, end: at, context: theory }], false);
    last = at;
  }
  const boundary = start + 60_000;
  assert.equal(partition.cursor, start + 59_500);
  assert.equal(claimSend(partition, boundary, false, 'focused'), null);
  updateLease(partition, 'focused', boundary, start, [{ start: last, end: boundary, context: theory }], true);
  const dispatch = claimSend(partition, boundary, false, 'focused');
  assert.equal(dispatch.pending.snapshot.seq, 1);
  assert.equal(dispatch.pending.snapshot.stats[0].active_ms, 60_000);
});

test('window finalization keeps client-final seq and stops sending unchanged confirmed snapshots', async () => {
  const partition = emptyPartition(subject, writer);
  const end = Date.parse('2026-11-27T10:15:00+03:00');
  updateLease(partition, 'one', end, end - 10_000, [{ start: end - 10_000, end, context: theory }], true);
  const send = claimSend(partition, end, false);
  assert.equal(send.pending.snapshot.client_final, true);
  const digest = await snapshotHash(send.pending.snapshot);
  const ack = {
    ...send.pending.snapshot, index: 0, payload_hash: digest, status: 'committed', code: 'committed',
    confirmed: { ...send.pending.snapshot, payload_hash: digest, revision: 1, rebase_at: null },
  };
  settleSend(partition, send, checkWindowAck({ protocol_version: 1, items: [ack] }, send.pending.snapshot, digest), end);
  assert.equal(partition.windows[send.key].clientFinalSeq, send.pending.snapshot.seq);
  assert.equal(claimSend(partition, end + 60_000, false), null);
});

test('offline defers the body and retries honor server delay and bounded attempts', () => {
  const partition = emptyPartition(subject, writer);
  const start = Date.parse(expected.five_tabs.start);
  updateLease(partition, 'one', start + 5000, start, [{ start, end: start + 5000, context: theory }], true);
  let now = start + 60_000;
  assert.equal(claimSend(partition, now, true), null);
  const exact = claimSend(partition, now, false);
  let current = exact;
  for (let attempt = 1; attempt <= 8; attempt++) {
    assert.equal(current.pending.body, exact.pending.body);
    assert.equal(current.pending.attempts, attempt);
    const next = now + backoff(attempt, 1, 90_000);
    settleSend(partition, current, { kind: 'retry', reason: 'v3_unconfirmed' }, next);
    assert.equal(claimSend(partition, next - 1, false), null);
    now = next;
    current = claimSend(partition, now, false);
  }
  assert.equal(current, null);
  assert.equal(partition.windows[exact.key].pending.quarantine, 'retry_exhausted');
});

test('trusted projection uses server UTC and rejects wall jumps', () => {
  let stamp = { mono: 0, wall: 100 };
  const clock = new ServerClock({ now: () => stamp, uuid: () => '', random: () => 0 });
  assert.equal(clock.projectSample(stamp), null);
  const server = Date.parse(expected.five_tabs.start);
  assert.equal(clock.sync(server, stamp, stamp, server + 300_000), true);
  const first = stamp;
  stamp = { mono: 15000, wall: 15100 };
  assert.equal(clock.projectSample(first), server);
  assert.equal(clock.projectSample(stamp), server + 15000);
  stamp = { mono: 16000, wall: 20000 };
  assert.equal(clock.projectSample(stamp), null);
});

test('602-Е preserves the persisted E1/seq4 body across reload and a foreign ACK', async () => {
  const fixture = contract.cases['602-Е'];
  const snapshot = fixture.offline_body.windows[0];
  const now = Date.parse(contract.clock);
  const partition = emptyPartition(subject, writer);
  const key = windows.windowId(snapshot);
  partition.windows[key] = {
    key: { activity_date: snapshot.activity_date, slot_no: snapshot.slot_no },
    stats: snapshot.stats, versions: snapshot.catalog_versions, seq: snapshot.seq - 1, nextDue: now, dirty: true,
  };
  const committedThenLost = claimSend(partition, now, false);
  const restored = structuredClone(partition);
  const replayTime = committedThenLost.pending.nextAt;
  const replay = claimSend(restored, replayTime, false);
  assert.deepEqual(replay.pending.snapshot, snapshot);
  assert.equal(replay.pending.body, committedThenLost.pending.body);
  const digest = await snapshotHash(snapshot);
  const foreign = checkWindowAck({ protocol_version: 1, items: [fixture.foreign_ack] }, snapshot, digest);
  settleSend(restored, replay, foreign, replayTime + 5000);
  assert.equal(restored.windows[key].pending.body, committedThenLost.pending.body);
  const retry = claimSend(restored, replayTime + 5000, false);
  const confirmed = checkWindowAck({ protocol_version: 1, items: [fixture.correct_ack] }, snapshot, digest);
  settleSend(restored, retry, confirmed, replayTime + 5000);
  assert.equal(restored.windows[key].pending, undefined);
  assert.deepEqual(restored.windows[key].confirmed, fixture.correct_ack);
  assert.deepEqual(restored.reasons, []);
});

test('604-Г quarantines stale epochs while retaining old receipts and the unconfirmed tail', async () => {
  const fixture = contract.cases['602-Е'];
  const partition = emptyPartition(subject, writer);
  const snapshot = fixture.offline_body.windows[0];
  const key = windows.windowId(snapshot), now = Date.parse(contract.clock);
  partition.windows[key] = {
    key: { activity_date: snapshot.activity_date, slot_no: snapshot.slot_no },
    stats: structuredClone(snapshot.stats), versions: snapshot.catalog_versions, seq: snapshot.seq, confirmed: fixture.correct_ack,
    nextDue: now + 30_000, dirty: false,
  };
  for (let offset = 0; offset < 30_000; offset += 5000) {
    updateLease(partition, 'leader', now + offset + 5000, now,
      [{ start: now + offset, end: now + offset + 5000, context: theory }], offset === 25_000);
  }
  updateLease(partition, 'successor', now + 30_000, now + 30_000, [], false);
  const send = claimSend(partition, now + 30_000, false, 'successor');
  assert.equal(send.pending.snapshot.seq, 5);
  assert.equal(send.pending.snapshot.stats[0].active_ms, 510_000);
  const digest = await snapshotHash(send.pending.snapshot);
  const stale = { ...fixture.correct_ack, seq: 5, payload_hash: digest, status: 'stale', code: 'stale_epoch',
    confirmed: { ...fixture.correct_ack.confirmed, server_epoch: 2, seq: null, payload_hash: null,
      revision: 2, stats: fixture.repair_stats, rebase_at: new Date(now).toISOString() } };
  const outcome = checkWindowAck({ protocol_version: 1, items: [stale] }, send.pending.snapshot, digest);
  settleSend(partition, send, outcome, now + 30_000);
  assert.equal(partition.windows[key].pending.quarantine, 'epoch_rebase_required');
  assert.equal(partition.windows[key].pending.snapshot.stats[0].active_ms, 510_000);
  assert.equal(partition.windows[key].confirmed.confirmed.revision, 1);
  assert.equal(claimSend(partition, now + 61_000, false, 'successor'), null);
});

test('604-Д does not reuse grants from a different or canceled lifecycle', async () => {
  const source = { now: () => ({ wall: Date.parse(contract.clock), mono: 0 }), uuid: () => writer.writer_id, random: () => 1 };
  const reply = subject_ref => new Response(JSON.stringify({ protocol_version: 1, ...writer, grant: 'synthetic',
    subject_ref, server_time: contract.clock, expires_at: new Date(source.now().wall + 300_000).toISOString() }));
  let release;
  const fetcher = () => new Promise(resolve => { release = resolve; });
  const transport = new WindowTransport(source, subject, fetcher);
  const lifecycle = new AbortController();
  const canceled = transport.authorize(writer, lifecycle.signal);
  lifecycle.abort();
  release(reply(subject));
  await assert.rejects(canceled);
  assert.equal(transport.hasGrant(), false);

  const wrong = new WindowTransport(source, subject, async () => reply(contract.other_lifecycle));
  await assert.rejects(wrong.authorize(writer, new AbortController().signal), WindowAccountChanged);
  assert.equal(wrong.hasGrant(), false);
});

test('604-Д sends credentialed windows and parses server Retry-After and capability off', async () => {
  const source = { now: () => ({ wall: Date.parse(contract.clock), mono: 0 }), uuid: () => writer.writer_id, random: () => 1 };
  const lifecycle = new AbortController();
  const responses = [
    new Response(JSON.stringify({ ...writer, protocol_version: 1, grant: 'synthetic', subject_ref: subject,
      server_time: contract.clock, expires_at: new Date(source.now().wall + 300_000).toISOString() })),
    new Response(JSON.stringify({ code: 'rate_limited' }), { status: 429, headers: { 'Retry-After': '20' } }),
    new Response(JSON.stringify({ code: 'capability_off' }), { status: 404 }),
  ];
  const calls = [];
  const transport = new WindowTransport(source, subject, async (path, init) => {
    calls.push({ path, init });
    return responses.shift();
  }, '/test-http-errors');
  await transport.authorize(writer, lifecycle.signal);
  const body = JSON.stringify(contract.cases['602-Е'].offline_body);
  await assert.rejects(transport.send(body, lifecycle.signal, false), error =>
    error instanceof WindowHttpError && error.status === 429 && error.retryAfter === 20_000);
  await assert.rejects(transport.send(body, lifecycle.signal, true), error => error.unsupported);
  assert.deepEqual(calls.map(({ path, init }) => [path, init.credentials, init.keepalive]), [
    ['/test-http-errors/api/analytics/windows/grant', 'include', undefined],
    ['/test-http-errors/api/analytics/windows/batch', 'include', false],
    ['/test-http-errors/api/analytics/windows/batch', 'include', true],
  ]);
  assert.ok(calls.slice(1).every(({ init }) => init.body === body && init.headers.Authorization === 'Bearer synthetic'));
});

test('a legacy 405 on the v3 grant keeps the status and terminates unsupported POST', async () => {
  const source = { now: () => ({ wall: Date.parse(contract.clock), mono: 0 }), uuid: () => writer.writer_id, random: () => 1 };
  for (const [allow, unsupported] of [['GET', true], ['GET, POST', false], [undefined, true]]) {
    const transport = new WindowTransport(source, subject, async () => new Response(
      JSON.stringify({ detail: 'Method Not Allowed' }), { status: 405, headers: allow ? { Allow: allow } : undefined },
    ), '/test-405-' + String(allow));
    await assert.rejects(transport.authorize(writer, new AbortController().signal), error => error instanceof WindowHttpError &&
      error.status === 405 && error.allow === (allow ?? null) && error.unsupported === unsupported);
  }
});

test('terminal document capability survives a second controller and resets at a new endpoint or document', async () => {
  const source = { now: () => ({ wall: Date.parse(contract.clock), mono: 0 }), uuid: () => writer.writer_id, random: () => 1 };
  const options = new AbortController();
  let calls = 0;
  const off = async () => { calls++; return new Response(JSON.stringify({ code: 'capability_off' }), { status: 404 }); };
  const first = new WindowTransport(source, subject, off, '/test-remount');
  await assert.rejects(first.authorize(writer, options.signal), error => error.status === 404);
  const replacement = new WindowTransport(source, subject, off, '/test-remount');
  assert.equal(replacement.unsupportedCapability, true);
  await assert.rejects(replacement.authorize(writer, options.signal), error => error.status === 404);
  assert.equal(calls, 1);

  const anotherEndpoint = new WindowTransport(source, subject, off, '/test-other-endpoint');
  assert.equal(anotherEndpoint.unsupportedCapability, false);
  const { WindowTransport: NewDocumentTransport } = load('./windows-transport.ts',
    { './windows': windows, './transport': legacyTransport });
  const reloaded = new NewDocumentTransport(source, subject, off, '/test-remount');
  assert.equal(reloaded.unsupportedCapability, false);
  await assert.rejects(reloaded.authorize(writer, options.signal), error => error.status === 404);
  assert.equal(calls, 2);
});

test('604-Д carries one 401 refresh through storage and quarantines expired or denied state', async () => {
  const noStorage = new WindowOutbox(() => { throw new DOMException('disabled', 'SecurityError'); });
  await assert.rejects(noStorage.transact(() => ({ result: null })), WindowStorageError);
  const fixture = contract.cases['602-Е'];
  const partition = emptyPartition(subject, writer);
  const snapshot = fixture.offline_body.windows[0], key = windows.windowId(snapshot);
  const now = Date.parse(contract.clock);
  partition.windows[key] = { key: { activity_date: snapshot.activity_date, slot_no: snapshot.slot_no }, stats: snapshot.stats, versions: snapshot.catalog_versions,
    seq: snapshot.seq - 1, nextDue: now, dirty: true };
  const first = claimSend(partition, now, false);
  settleSend(partition, first, { kind: 'retry', reason: 'v3_unconfirmed', authRefresh: true }, now + 1000);
  const restored = structuredClone(partition);
  const second = claimSend(restored, now + 1000, false);
  assert.equal(second.pending.reauth, true);
  settleSend(restored, second, { kind: 'quarantine', reason: 'v3_unconfirmed' }, now + 1000);
  assert.equal(claimSend(restored, now + 60_000, false), null);
  const dropAt = windows.windowEnd(snapshot) + windows.OUTBOX_AGE + 1;
  assert.equal(claimSend(restored, dropAt, false), null);
  assert.deepEqual(restored.windows, {});
  assert.ok(restored.reasons.includes('expired'));
});
