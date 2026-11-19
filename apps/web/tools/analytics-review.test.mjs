import assert from 'node:assert/strict';
import test from 'node:test';
import path from 'node:path';
import fs from 'node:fs';
import os from 'node:os';
import { Duplex } from 'node:stream';
import { parseArguments, createHoldGuard, bridgeSockets, createReviewConfig, isolateReviewEnvironment, removeOwnedCache, closeOwnedServers } from './analytics-review.mjs';

const operation = 'a'.repeat(32), coreName = `analytics_delivery_test_${'b'.repeat(32)}`, analyticsName = `analytics_test_${'c'.repeat(32)}`;
function guardFixture() {
  const root = path.resolve('synthetic-analytics-root'), folder = path.join(root, '.local', 'cross-service', operation);
  const files = new Map(); let wall = 1_000_000, clock = 500, running = true;
  const owner = {token:'d'.repeat(64), state:'browser_hold', browser_holder_pid:1234, hold_expires_unix:1100,
    core_name:coreName, core_sentinel:'e'.repeat(64), analytics_resources:[{name:analyticsName, sentinel:'f'.repeat(64)}]};
  const fixture = {core_url:'http://127.0.0.1:18151', collector_url:'http://127.0.0.1:18152', query_url:'http://127.0.0.1:18153',
    databases:{core:{name:coreName, sentinel:'e'.repeat(64)}, analytics:{name:analyticsName, sentinel:'f'.repeat(64)}}};
  const ownerPath = path.join(folder, 'owner.json'), fixturePath = path.join(folder, 'fixture.json'), release = path.join(folder, 'release.request');
  const directories = new Set([root, path.join(root,'.local'), path.join(root,'.local','cross-service'), folder]);
  const stat = (directory, size = 0, link = false) => ({isSymbolicLink:() => link, isDirectory:() => directory, isFile:() => !directory, size});
  const fakeFs = {
    realpathSync(value) { if (directories.has(value) || files.has(value)) return value; throw Object.assign(Error(), {code:'ENOENT'}); },
    lstatSync(value) {
      if (directories.has(value)) return stat(true);
      if (files.has(value)) { const data = files.get(value); return stat(false, Buffer.byteLength(data)); }
      throw Object.assign(Error(), {code:'ENOENT'});
    },
    readFileSync(value) { return files.get(value); },
  };
  const update = () => { files.set(ownerPath, JSON.stringify(owner)); files.set(fixturePath, JSON.stringify(fixture)); };
  update();
  return { owner, fixture, files, release, fakeFs, update, root, setWall: value => { wall = value; }, setMono: value => { clock = value; },
    stopHolder: () => { running = false; }, make: () => createHoldGuard(operation, {fs:fakeFs, analyticsRoot:root, now:()=>wall, mono:()=>clock, alive:pid => running && pid === 1234}) };
}

test('CLI requires explicit action and exact operation UUID', () => {
  assert.deepEqual(parseArguments(['--check','--operation',operation]), {mode:'--check', operation});
  assert.deepEqual(parseArguments(['--run','--operation',operation]), {mode:'--run', operation});
  assert.deepEqual(parseArguments(['--run','--operation',operation,'--reports-off']), {mode:'--run', operation, reportsOff:true});
  for (const args of [[], ['--run'], ['--run','--operation','../other'], ['--run','--operation',operation,'extra']]) assert.throws(() => parseArguments(args));
});

test('guard admits owned active hold and refuses release, expiry, changed sentinel or holder', () => {
  for (const change of [
    f => { f.files.set(f.release, 'private'); },
    f => f.setWall(1_100_000),
    f => f.setMono(100_500),
    f => { f.owner.token = '0'.repeat(64); f.update(); },
    f => { f.owner.browser_holder_pid = 2222; f.update(); },
    f => { f.owner.hold_expires_unix += 1; f.update(); },
    f => { f.owner.state = 'acceptance_passed'; f.update(); },
    f => f.stopHolder(),
    f => { f.fixture.collector_url = 'http://127.0.0.1:18001'; f.update(); },
    f => { f.owner.core_sentinel = '0'.repeat(64); f.update(); },
  ]) {
    const fixture = guardFixture(), guard = fixture.make(); assert.doesNotThrow(guard);
    change(fixture); assert.throws(guard);
  }
});

test('hold horizon admits one hour plus timestamp tolerance and rejects larger or renewed holds', () => {
  const allowed = guardFixture(); allowed.owner.hold_expires_unix = 4601; allowed.update();
  const guard = allowed.make(); assert.doesNotThrow(guard);
  allowed.owner.hold_expires_unix = 4602; allowed.update(); assert.throws(guard);
  const tooLong = guardFixture(); tooLong.owner.hold_expires_unix = 4602; tooLong.update();
  assert.throws(() => tooLong.make());
});

test('guard rejects invalid hold before launch and symlinked private directory', () => {
  const expired = guardFixture(); expired.setWall(1_100_000); assert.throws(() => expired.make());
  const absent = guardFixture(); absent.stopHolder(); assert.throws(() => absent.make());
  const moved = guardFixture(), real = moved.fakeFs.lstatSync;
  moved.fakeFs.lstatSync = file => file === path.join(moved.root,'.local') ? {isSymbolicLink:()=>true} : real(file);
  assert.throws(() => moved.make());
});

test('review config pins loopback endpoints and keeps host and origin untouched', () => {
  const config = createReviewConfig({courses:[{frontendDir:'/synthetic/frontend'}], react:()=>({name:'react'}), courseFrontendWatch:dirs=>({dirs}), cache:'/isolated/cache'});
  assert.equal(config.configFile, false); assert.equal(config.envFile, false); assert.equal(config.cacheDir, '/isolated/cache');
  assert.equal(config.server.host, '127.0.0.1'); assert.equal(config.server.port, 18782); assert.equal(config.server.strictPort, true);
  assert.equal(config.server.proxy['^/api/analytics/events/batch(?:\\?|$)'].target, 'http://127.0.0.1:18152');
  assert.equal(config.server.proxy['/api/analytics/reports/'].target, 'http://127.0.0.1:18153');
  assert.equal(config.server.proxy['/api'].target, 'http://127.0.0.1:18151');
  assert.ok(Object.values(config.server.proxy).every(value => value.changeOrigin === false));
  assert.ok(config.server.fs.deny.includes('**/.local/**')); assert.equal(config.server.forwardConsole, false);
  const env = {VITE_SECRET:'not-public', VITE_ANALYTICS_REPORTS_ENABLED:'false', PATH:'unchanged'};
  isolateReviewEnvironment(env);
  assert.equal(env.VITE_SECRET, undefined); assert.equal(env.VITE_ANALYTICS_REPORTS_ENABLED, 'true');
  assert.equal(env.VITE_ANALYTICS_TRACKER_ENABLED, 'true'); assert.equal(env.VITE_ANALYTICS_LEGACY_VISIT_CUTOVER, 'true');
  assert.equal(env.VITE_ANALYTICS_PERSONAL_MEASUREMENTS_ENABLED, 'true'); assert.equal(env.PATH, 'unchanged');
});

test('reports-off review hides report features while keeping collection cutover isolated', () => {
  const env = {VITE_SECRET:'private', VITE_ANALYTICS_REPORTS_ENABLED:'true', PATH:'unchanged'};
  isolateReviewEnvironment(env, {reportsOff:true});
  assert.equal(env.VITE_SECRET, undefined);
  assert.equal(env.VITE_ANALYTICS_REPORTS_ENABLED, 'false');
  assert.equal(env.VITE_ANALYTICS_PERSONAL_MEASUREMENTS_ENABLED, 'false');
  assert.equal(env.VITE_ANALYTICS_TRACKER_ENABLED, 'true');
  assert.equal(env.VITE_ANALYTICS_LEGACY_VISIT_CUTOVER, 'true');
  assert.equal(env.PATH, 'unchanged');
});

test('TCP bridge forwards original HTTP and WebSocket bytes without opening listeners', async () => {
  class Socket extends Duplex {
    chunks = [];
    _read() {}
    _write(chunk, _encoding, done) { this.chunks.push(Buffer.from(chunk)); done(); }
    receive(data) { this.push(Buffer.from(data)); }
  }
  const client = new Socket(), upstream = new Socket();
  const dispose = bridgeSockets(client, upstream);
  const request = 'GET /@vite/client HTTP/1.1\r\nHost: 127.0.0.2:18782\r\nOrigin: http://127.0.0.2:18782\r\nUpgrade: websocket\r\n\r\n';
  const frame = Buffer.from([0x81, 0x02, 0x4f, 0x4b]);
  client.receive(request); upstream.receive(frame);
  await new Promise(setImmediate);
  assert.equal(Buffer.concat(upstream.chunks).toString(), request);
  assert.deepEqual(Buffer.concat(client.chunks), frame);
  dispose(); assert.equal(client.destroyed, true); assert.equal(upstream.destroyed, true);
});

test('cleanup removes only the explicitly owned cache and preserves a neighbor', () => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'analytics-review-unit-')));
  const cache = fs.mkdtempSync(path.join(root, '.analytics-review-'));
  const neighbor = path.join(root, 'neighbor.txt');
  try {
    fs.writeFileSync(path.join(cache, 'test.txt'), 'synthetic'); fs.writeFileSync(neighbor, 'preserve');
    assert.throws(() => removeOwnedCache({folder:cache, root:path.dirname(root)}));
    removeOwnedCache({folder:cache, root});
    assert.equal(fs.existsSync(cache), false); assert.equal(fs.readFileSync(neighbor, 'utf8'), 'preserve');
  } finally {
    fs.unlinkSync(neighbor);
    fs.rmdirSync(root);
  }
});

test('shutdown destroys owned connections and closes both proxies and Vite', async () => {
  const calls = [];
  const sockets = new Set([{destroy:() => calls.push('socket')}]);
  const proxies = [{close:done => {calls.push('proxy-a'); done();}}, {close:done => {calls.push('proxy-b'); done();}}];
  const results = await closeOwnedServers(sockets, proxies, {close:async () => {calls.push('vite');}});
  assert.deepEqual(calls, ['socket', 'proxy-a', 'proxy-b', 'vite']);
  assert.ok(results.every(result => result.status === 'fulfilled'));
  const failed = await closeOwnedServers(new Set(), [{close:done => done(Error('closed failed'))}], null);
  assert.equal(failed[0].status, 'rejected');
});
