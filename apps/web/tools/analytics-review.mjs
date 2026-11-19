/** Explicit, synthetic-only browser review. Importing this module starts nothing. */
import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { performance } from 'node:perf_hooks';
import { createTrace, configureTrace, focusPlugin } from './analytics-review-diagnostics.mjs';

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PROJECT = path.resolve(WEB, '../..');
const ANALYTICS = path.join(PROJECT, 'services', 'analytics');
const PORT = 18782;
const MAX_HOLD_HORIZON_MS = 3_601_000;
const UUID = /^[0-9a-f]{32}$/;
const TOKEN = /^[0-9a-f]{64}$/;
const CORE_NAME = /^analytics_delivery_test_[0-9a-f]{32}$/;
const ANALYTICS_NAME = /^analytics_test_[0-9a-f]{32}$/;

export function parseArguments(args) {
  if (![3, 4].includes(args.length) || !['--check', '--run'].includes(args[0]) || args[1] !== '--operation' || !UUID.test(args[2]) || (args.length === 4 && args[3] !== '--reports-off')) {
    throw new Error('usage');
  }
  return { mode: args[0], operation: args[2], ...(args.length === 4 ? { reportsOff: true } : {}) };
}
export function isolateReviewEnvironment(env, { reportsOff = false } = {}) {
  for (const key of Object.keys(env)) if (key.startsWith('VITE_')) delete env[key];
  Object.assign(env, { VITE_ANALYTICS_TRACKER_ENABLED: 'true', VITE_ANALYTICS_LEGACY_VISIT_CUTOVER: 'true',
    VITE_ANALYTICS_REPORTS_ENABLED: reportsOff ? 'false' : 'true', VITE_ANALYTICS_PERSONAL_MEASUREMENTS_ENABLED: reportsOff ? 'false' : 'true' });
}
function samePath(a, b) {
  const normalize = value => process.platform === 'win32' ? path.resolve(value).toLowerCase() : path.resolve(value);
  return normalize(a) === normalize(b);
}
function child(fsImpl, parent, name, directory) {
  const value = path.join(parent, name), stat = fsImpl.lstatSync(value);
  if (stat.isSymbolicLink() || (directory ? !stat.isDirectory() : !stat.isFile())) throw new Error('private path');
  const resolved = fsImpl.realpathSync(value);
  if (!samePath(path.dirname(resolved), parent) || !samePath(resolved, value)) throw new Error('private path');
  return resolved;
}
function readPrivateJson(fsImpl, folder, name) {
  const file = child(fsImpl, folder, name, false);
  const size = fsImpl.lstatSync(file).size;
  if (!Number.isSafeInteger(size) || size <= 0 || size > 131072) throw new Error('private data');
  return JSON.parse(fsImpl.readFileSync(file, 'utf8'));
}
function hasRelease(fsImpl, folder) {
  try { fsImpl.lstatSync(path.join(folder, 'release.request')); return true; }
  catch (error) { if (error?.code === 'ENOENT') return false; throw error; }
}
function isAlive(pid) {
  try { process.kill(pid, 0); return true; } catch { return false; }
}
function validFixture(owner, fixture) {
  const core = fixture?.databases?.core, analytics = fixture?.databases?.analytics;
  return fixture?.core_url === 'http://127.0.0.1:18151' && fixture?.collector_url === 'http://127.0.0.1:18152' &&
    fixture?.query_url === 'http://127.0.0.1:18153' && CORE_NAME.test(core?.name ?? '') && ANALYTICS_NAME.test(analytics?.name ?? '') &&
    TOKEN.test(core?.sentinel ?? '') && TOKEN.test(analytics?.sentinel ?? '') && owner.core_name === core.name &&
    owner.core_sentinel === core.sentinel && Array.isArray(owner.analytics_resources) &&
    owner.analytics_resources.filter(item => item?.name === analytics.name && item?.sentinel === analytics.sentinel).length === 1;
}

/** Check fixed private ancestry, the immutable hold identity, release, expiry and holder liveness. */
export function createHoldGuard(operation, options = {}) {
  if (!UUID.test(operation)) throw new Error('operation');
  const fsImpl = options.fs ?? fs, now = options.now ?? (() => Date.now()), mono = options.mono ?? (() => performance.now());
  const alive = options.alive ?? isAlive, analyticsRoot = fsImpl.realpathSync(options.analyticsRoot ?? ANALYTICS);
  const local = child(fsImpl, analyticsRoot, '.local', true);
  const root = child(fsImpl, local, 'cross-service', true);
  const folder = child(fsImpl, root, operation, true);
  const firstWall = now(), firstMono = mono();
  const inspect = () => {
    // Re-check ancestry and symlinks on every poll; never trust a replaced fixture path.
    if (!samePath(child(fsImpl, analyticsRoot, '.local', true), local) || !samePath(child(fsImpl, local, 'cross-service', true), root) ||
        !samePath(child(fsImpl, root, operation, true), folder) || hasRelease(fsImpl, folder)) throw new Error('hold ended');
    const owner = readPrivateJson(fsImpl, folder, 'owner.json');
    const fixture = readPrivateJson(fsImpl, folder, 'fixture.json');
    if (owner?.state !== 'browser_hold' || !TOKEN.test(owner?.token ?? '') || !Number.isSafeInteger(owner?.browser_holder_pid) ||
        owner.browser_holder_pid <= 0 || !Number.isSafeInteger(owner?.hold_expires_unix) || !validFixture(owner, fixture) || !alive(owner.browser_holder_pid)) {
      throw new Error('hold invalid');
    }
    return owner;
  };
  const initial = inspect();
  const expires = initial.hold_expires_unix * 1000;
  if (expires <= firstWall || expires - firstWall > MAX_HOLD_HORIZON_MS) throw new Error('hold expired');
  const deadline = firstMono + (expires - firstWall);
  return () => {
    const owner = inspect();
    if (owner.token !== initial.token || owner.browser_holder_pid !== initial.browser_holder_pid || owner.hold_expires_unix !== initial.hold_expires_unix ||
        now() >= expires || mono() >= deadline || mono() < firstMono) throw new Error('hold changed');
  };
}

/** Raw bidirectional TCP pipes preserve HTTP Host, Origin and WebSocket bytes unchanged. */
export function bridgeSockets(client, upstream) {
  const failClient = () => upstream.destroy();
  const failUpstream = () => client.destroy();
  client.once('error', failClient); upstream.once('error', failUpstream);
  client.pipe(upstream); upstream.pipe(client);
  return () => { client.destroy(); upstream.destroy(); };
}

/** Stop only handles created by this launcher; errors are returned to its shutdown path. */
export async function closeOwnedServers(sockets, proxies, vite) {
  for (const socket of sockets) socket.destroy();
  const closing = proxies.map(server => new Promise((resolve, reject) => {
    try { server.close(error => error ? reject(error) : resolve()); } catch (error) { reject(error); }
  }));
  if (vite) closing.push(Promise.resolve().then(() => vite.close()));
  return Promise.allSettled(closing);
}

function createProxy(host, sockets, onError) {
  const server = net.createServer({ pauseOnConnect: true }, client => {
    const upstream = net.createConnection({ host: '127.0.0.1', port: PORT });
    sockets.add(client); sockets.add(upstream);
    client.once('close', () => sockets.delete(client)); upstream.once('close', () => sockets.delete(upstream));
    bridgeSockets(client, upstream);
    upstream.once('connect', () => client.resume());
  });
  server.maxConnections = 128;
  server.on('error', onError);
  return new Promise((resolve, reject) => {
    const failed = () => reject(new Error('proxy bind'));
    server.once('error', failed);
    server.listen({ host, port: PORT, exclusive: true }, () => { server.off('error', failed); resolve(server); });
  });
}

function makeOwnedCache() {
  const cacheRoot = fs.realpathSync(path.join(WEB, '.cache'));
  if (!samePath(cacheRoot, path.join(WEB, '.cache')) || fs.lstatSync(cacheRoot).isSymbolicLink()) throw new Error('cache root');
  const folder = fs.mkdtempSync(path.join(cacheRoot, '.analytics-review-'));
  const real = fs.realpathSync(folder);
  if (!samePath(path.dirname(real), cacheRoot) || !samePath(real, folder)) throw new Error('cache path');
  return { folder: real, root: cacheRoot };
}
export function removeOwnedCache(cache) {
  if (!cache) return;
  let stat;
  try { stat = fs.lstatSync(cache.folder); } catch (error) { if (error?.code === 'ENOENT') return; throw error; }
  if (stat.isSymbolicLink() || !stat.isDirectory() || !samePath(path.dirname(fs.realpathSync(cache.folder)), cache.root) ||
      !samePath(fs.realpathSync(cache.folder), cache.folder) || !path.basename(cache.folder).startsWith('.analytics-review-')) throw new Error('cache ownership');
  fs.rmSync(cache.folder, { recursive: true });
}

export function createReviewConfig({ courses, react, courseFrontendWatch, cache, trace }) {
  const modules = path.join(WEB, 'node_modules');
  const proxyOptions = target => ({target, changeOrigin: false, ...(trace ? {configure: proxy => configureTrace(proxy, trace)} : {})});
  return {
    configFile: false, envFile: false, root: WEB, base: '/', cacheDir: cache,
    logLevel: 'silent', plugins: [focusPlugin(), react(), courseFrontendWatch(courses.map(course => course.frontendDir))],
    resolve: { alias: [
      { find: /^react$/, replacement: path.join(modules, 'react') },
      { find: 'react-dom', replacement: path.join(modules, 'react-dom') },
      { find: /^@course-ui$/, replacement: path.join(PROJECT, 'courses/shared/ui/index.ts') },
      { find: /^@course-ui\//, replacement: path.join(PROJECT, 'courses/shared/ui') + '/' },
      { find: /^@platform\//, replacement: path.join(WEB, 'src') + '/' },
      { find: 'video.js', replacement: path.join(modules, 'video.js') },
      { find: 'lucide-react', replacement: path.join(modules, 'lucide-react') },
    ], dedupe: ['react', 'react-dom'] },
    server: {
      host: '127.0.0.1', port: PORT, strictPort: true, open: false, forwardConsole: false,
      fs: { strict: true, allow: [WEB, ...courses.map(course => course.frontendDir),
        path.join(PROJECT, 'courses/shared/ui'), path.join(PROJECT, 'courses/shared/demo'), path.join(PROJECT, 'courses/shared/landing')],
      deny: ['.env', '.env.*', '*.{crt,pem,key,p12,pfx,cer,der}', '.npmrc', '.yarnrc.yml', '**/.git/**', '**/.local', '**/.local/**',
        '**/courses/*/backend/**', '**/courses/*/archive/**', '**/courses/shared/ai/**', '**/personal.settings.yaml', '**/course.yaml'] },
      proxy: {
        '^/api/analytics/events/batch(?:\\?|$)': proxyOptions('http://127.0.0.1:18152'),
        '/api/analytics/reports/': proxyOptions('http://127.0.0.1:18153'),
        '/api': proxyOptions('http://127.0.0.1:18151'),
      },
    },
  };
}

async function main(args) {
  const { mode, operation, reportsOff = false } = parseArguments(args);
  const guard = createHoldGuard(operation); guard();
  if (mode === '--check') { process.stdout.write('PASS active synthetic browser hold\n'); return; }
  let vite, cache, trace, interval, closing = false, finished;
  const sockets = new Set(), proxies = [];
  const stopped = new Promise(resolve => { finished = resolve; });
  const stop = async reason => {
    if (closing) return;
    closing = true; clearInterval(interval);
    const forceExit = setTimeout(() => process.exit(1), 5000);
    forceExit.unref();
    const outcomes = await closeOwnedServers(sockets, proxies, vite);
    const closeFailed = outcomes.some(outcome => outcome.status === 'rejected');
    if (closeFailed) { process.stderr.write('Review listener cleanup failed.\n'); process.exitCode = 1; }
    try { trace?.close(); } catch { process.stderr.write('Review trace close failed.\n'); process.exitCode = 1; }
    try { removeOwnedCache(cache); } catch { process.stderr.write('Review cache cleanup requires inspection.\n'); process.exitCode = 1; }
    if (!closeFailed) clearTimeout(forceExit);
    process.stdout.write(`Synthetic browser review stopped: ${reason}.\n`);
    finished();
  };
  const check = () => { try { guard(); } catch { void stop('hold ended'); } };
  interval = setInterval(check, 250);
  for (const signal of ['SIGINT', 'SIGTERM', ...(process.platform === 'win32' ? ['SIGBREAK'] : [])]) process.once(signal, () => void stop('signal'));
  try {
    // Vite exposes VITE_* to the browser; remove inherited values and opt in only review flags.
    isolateReviewEnvironment(process.env, { reportsOff });
    for (const name of ['course-ids.ts', 'generated-loaders.ts', 'generated-preview.ts']) {
      if (!fs.statSync(path.join(WEB, '.cache/course-registry', name)).isFile()) throw new Error('registry unavailable');
    }
    const [{ createServer }, { default: react }, { discoverCourses }, { courseFrontendWatch }] = await Promise.all([
      import('vite'), import('@vitejs/plugin-react'), import('./course-registry.mjs'), import('./course-frontend-watch.mjs'),
    ]);
    guard(); if (closing) return await stopped;
    cache = makeOwnedCache();
    trace = createTrace(WEB);
    const created = await createServer(createReviewConfig({ courses: discoverCourses(), react, courseFrontendWatch, cache: cache.folder, trace }));
    if (closing) {
      await created.close();
      try { removeOwnedCache(cache); } catch { process.exitCode = 1; }
      return await stopped;
    }
    vite = created;
    guard();
    await vite.listen();
    if (closing) { await vite.close(); return await stopped; }
    guard();
    for (const host of ['127.0.0.2', '127.0.0.3']) {
      const proxy = await createProxy(host, sockets, () => void stop('proxy error'));
      proxies.push(proxy);
      if (closing) { for (const socket of sockets) socket.destroy(); proxy.close(); return await stopped; }
      guard();
    }
    process.stdout.write('Synthetic browser review ready: localhost, 127.0.0.2, 127.0.0.3 on port 18782.\n');
    process.stdout.write(`Synthetic report flags: ${reportsOff ? 'off' : 'on'}; collection/cutover flags: on.\n`);
    process.stdout.write(`Synthetic review trace: ${trace.file}\n`);
    await stopped;
  } catch {
    process.stderr.write('Synthetic browser review failed its guard or startup.\n');
    process.exitCode = 1; await stop('startup failure');
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main(process.argv.slice(2)).catch(() => { process.stderr.write('Synthetic browser review refused.\n'); process.exitCode = 1; });
}
