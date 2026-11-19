/** Synthetic review only: fixed, bounded metadata; no request or response payload is persisted. */
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { performance } from 'node:perf_hooks';

const ROUTES = new Map([
  ['GET /api/learning/analytics-catalog', 'catalog'],
  ['POST /api/analytics/grants/collect', 'grant'],
  ['POST /api/analytics/grants/report', 'grant'],
  ['POST /api/analytics/events/batch', 'batch'],
  ['GET /api/analytics/reports/activity', 'report'],
  ['GET /api/analytics/reports/activity/details', 'details'],
  ['GET /api/user/me', 'me'],
  ['GET /api/admin-platform/overview', 'overview'],
]);
const HOSTS = new Set(['localhost:18782', '127.0.0.2:18782', '127.0.0.3:18782']);
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const utc = value => typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/i.test(value) && Number.isFinite(Date.parse(value));
const count = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
const object = value => value && typeof value === 'object' && !Array.isArray(value);

export function safeRoute(method, url, host) {
  if (!HOSTS.has(host) || typeof url !== 'string' || typeof method !== 'string') return null;
  const route = url.split('?', 1)[0], kind = ROUTES.get(`${method} ${route}`);
  return kind ? {host: host.slice(0, -6), method, route, kind} : null;
}

export function summarize(kind, body, rtt) {
  if (!object(body)) return {};
  if (kind === 'catalog') return {
    catalog_version_valid: typeof body.catalog_version === 'string' && IDENTIFIER.test(body.catalog_version),
    lesson_count: Array.isArray(body.lesson_ids) ? count(body.lesson_ids.length) : null,
  };
  if (kind === 'grant') {
    const serverValid = utc(body.server_time), expiresValid = utc(body.expires_at);
    const ttl = serverValid && expiresValid ? Date.parse(body.expires_at) - Date.parse(body.server_time) : NaN;
    return { grant_present: typeof body.grant === 'string' && body.grant.length > 0,
      subject_uuid_valid: typeof body.subject_ref === 'string' && UUID.test(body.subject_ref),
      server_time_valid: serverValid, expires_at_valid: expiresValid,
      ttl_valid_le_300s: Number.isFinite(ttl) && ttl > 0 && ttl <= 300000,
      rtt_ms: Number.isFinite(rtt) && rtt >= 0 ? Math.round(rtt) : null };
  }
  if (kind === 'report' || kind === 'details') {
    const report = kind === 'details' ? body.report : body;
    if (!object(report)) return {report_valid_object: false};
    const totals = object(report.totals) ? Object.fromEntries(['unique_visitors', 'active_ms', 'theory_ms', 'practice_ms']
      .map(key => [key, count(report.totals[key])])) : null;
    return { report_valid_object: true,
      report_status: ['ok', 'no_data', 'stale', 'unavailable'].includes(report.status) ? report.status : null,
      report_quality: ['complete', 'partial', 'unknown'].includes(report.quality) ? report.quality : null,
      as_of_null: report.as_of === null, totals,
      rows: kind === 'details' && Array.isArray(body.rows) ? count(body.rows.length) : null,
      daily_rows: Array.isArray(report.daily) ? count(report.daily.length) : null };
  }
  return {};
}

function same(a, b) {
  const normalize = value => process.platform === 'win32' ? path.resolve(value).toLowerCase() : path.resolve(value);
  return normalize(a) === normalize(b);
}
function safeDirectory(parent, name) {
  const dir = path.join(parent, name);
  try { fs.mkdirSync(dir, {mode: 0o700}); } catch (error) { if (error?.code !== 'EEXIST') throw error; }
  const stat = fs.lstatSync(dir), real = fs.realpathSync(dir);
  if (stat.isSymbolicLink() || !stat.isDirectory() || !same(real, dir) || !same(path.dirname(real), parent)) throw new Error('trace path');
  return real;
}
export function createTrace(webRoot) {
  const web = fs.realpathSync(webRoot);
  if (!same(web, webRoot) || fs.lstatSync(webRoot).isSymbolicLink()) throw new Error('trace root');
  const local = safeDirectory(web, '.local'), folder = safeDirectory(local, 'analytics-review-traces');
  const file = path.join(folder, `review-${randomUUID()}.jsonl`);
  const fd = fs.openSync(file, 'wx', 0o600);
  try {
    const stat = fs.lstatSync(file), opened = fs.fstatSync(fd), real = fs.realpathSync(file);
    if (stat.isSymbolicLink() || !stat.isFile() || !same(real, file) || !same(path.dirname(real), folder) ||
        stat.dev !== opened.dev || stat.ino !== opened.ino ||
        !same(fs.realpathSync(local), local) || !same(fs.realpathSync(folder), folder)) throw new Error('trace ownership');
  } catch (error) { fs.closeSync(fd); throw error; }
  let size = 0, closed = false;
  return { file, write(entry) {
    if (closed) return;
    const line = JSON.stringify(entry) + '\n', bytes = Buffer.byteLength(line);
    if (size + bytes > 5 * 1024 * 1024) return;
    try { fs.writeFileSync(fd, line); size += bytes; } catch { /* diagnostics must not break proxy traffic */ }
  }, close() { if (!closed) { closed = true; fs.closeSync(fd); } } };
}

export function configureTrace(proxy, trace, now = () => performance.now()) {
  const starts = new WeakMap();
  proxy.on('proxyReq', (_proxyReq, req) => { if (safeRoute(req.method, req.url, req.headers.host)) starts.set(req, now()); });
  proxy.on('proxyRes', (response, req) => {
    const info = safeRoute(req.method, req.url, req.headers.host);
    if (!info) return;
    const {kind, ...base} = info, status = Number.isInteger(response.statusCode) ? response.statusCode : null;
    const started = starts.get(req);
    starts.delete(req);
    const entry = { ...base, status };
    const contentType = String(response.headers['content-type'] ?? '').toLowerCase();
    const encoding = String(response.headers['content-encoding'] ?? 'identity').toLowerCase();
    if (status < 200 || status >= 300 || !['catalog', 'grant', 'report', 'details'].includes(kind) ||
        !contentType.includes('application/json') || encoding !== 'identity' ||
        typeof response.on !== 'function' || typeof response.once !== 'function') { trace.write(entry); return; }
    let bytes = 0, chunks = [], overflow = false, recorded = false;
    const finish = () => { if (!recorded) { recorded = true; chunks = []; trace.write(entry); } };
    response.on('data', chunk => {
      bytes += chunk.length;
      if (bytes <= 1024 * 1024 && !overflow) chunks.push(Buffer.from(chunk));
      else { chunks = []; overflow = true; }
    });
    response.once('end', () => {
      if (!overflow) { try { Object.assign(entry, summarize(kind, JSON.parse(Buffer.concat(chunks).toString('utf8')), started === undefined ? NaN : now() - started)); } catch { /* malformed or oversized body */ } }
      finish();
    });
    response.once('error', finish);
    response.once('aborted', finish);
  });
}

export function focusPlugin() {
  return { name: 'analytics-review-focus', transformIndexHtml() { return [
    {tag: 'output', attrs: {id: 'analytics-review-focus', hidden: true, 'aria-hidden': 'true'}, injectTo: 'body-prepend'},
    {tag: 'script', injectTo: 'body', children: `(() => {
      const output = document.getElementById('analytics-review-focus');
      if (!output) return;
      const update = () => { output.setAttribute('data-visibility', document.visibilityState); output.setAttribute('data-focused', String(document.hasFocus())); output.setAttribute('data-secure-context', String(window.isSecureContext)); output.setAttribute('data-random-uuid', typeof window.crypto?.randomUUID); };
      document.addEventListener('visibilitychange', update); window.addEventListener('focus', update); window.addEventListener('blur', update);
      update(); setInterval(update, 1000);
    })();`},
  ]; } };
}
