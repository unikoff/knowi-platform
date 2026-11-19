import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { configureTrace, createTrace, focusPlugin, safeRoute } from './analytics-review-diagnostics.mjs';
import { createReviewConfig } from './analytics-review.mjs';

test('only exact route, verb and local host enter trace; query strings are removed', () => {
  assert.deepEqual(safeRoute('GET', '/api/learning/analytics-catalog?course_id=secret', 'localhost:18782'),
    {host:'localhost', method:'GET', route:'/api/learning/analytics-catalog', kind:'catalog'});
  for (const [method, url, host] of [
    ['GET', '/api/user/login?token=secret', 'localhost:18782'], ['POST', '/api/user/me', 'localhost:18782'],
    ['GET', '/api/user/me/anything', 'localhost:18782'], ['GET', '/api/user/me', 'example.com:18782'],
  ]) assert.equal(safeRoute(method, url, host), null);
});

test('proxy collects only whitelisted response summaries with bounded input', async () => {
  const proxy = new EventEmitter(), entries = [], trace = {write: entry => entries.push(entry)};
  let clock = 0; configureTrace(proxy, trace, () => clock);
  async function response(route, method, body, opts = {}) {
    const req = {method, url:route+'?secret=hidden', headers:{host:'localhost:18782', cookie:'cookie-secret', authorization:'authorization-secret'}};
    const stream = new PassThrough(); stream.statusCode = opts.status ?? 200; stream.headers = {'content-type':'application/json'};
    proxy.emit('proxyReq', {}, req); clock += 17; proxy.emit('proxyRes', stream, req); stream.resume(); stream.end(body);
    await new Promise(resolve => stream.once('end', resolve));
  }
  await response('/api/analytics/grants/collect', 'POST', JSON.stringify({grant:'JWT-TOKEN-SECRET', subject_ref:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    server_time:'2026-11-17T00:00:00Z', expires_at:'2026-11-17T00:05:00Z', extra:'BODY-SECRET'}));
  await response('/api/learning/analytics-catalog', 'GET', JSON.stringify({catalog_version:'sha256:abcdef', lesson_ids:['first','second'], secret:'BODY-SECRET'}));
  await response('/api/analytics/reports/activity/details', 'GET', JSON.stringify({report:{status:'ok', quality:'complete', as_of:null,
    totals:{unique_visitors:1,active_ms:3,theory_ms:1,practice_ms:2,secret:'BODY-SECRET'}, daily:[]}, rows:[{secret:'BODY-SECRET'}]}));
  await response('/api/user/me', 'GET', JSON.stringify({secret:'BODY-SECRET'}));
  await response('/api/analytics/grants/report', 'POST', JSON.stringify({detail:'BODY-SECRET'}), {status:403});
  await response('/api/analytics/grants/collect', 'POST', 'x'.repeat(1024*1024+1));
  assert.equal(entries.length, 6);
  assert.deepEqual(entries[0], {host:'localhost',method:'POST',route:'/api/analytics/grants/collect',status:200,
    grant_present:true,subject_uuid_valid:true,server_time_valid:true,expires_at_valid:true,ttl_valid_le_300s:true,rtt_ms:17});
  assert.equal(entries[1].catalog_version_valid, true); assert.equal(entries[1].lesson_count, 2);
  assert.equal(entries[2].rows, 1); assert.equal(entries[2].totals.active_ms, 3); assert.equal(entries[2].as_of_null, true);
  assert.deepEqual(Object.keys(entries[3]).sort(), ['host','method','route','status']);
  assert.deepEqual(Object.keys(entries[4]).sort(), ['host','method','route','status']);
  assert.deepEqual(Object.keys(entries[5]).sort(), ['host','method','route','status']);
  const req = {method:'POST', url:'/api/analytics/grants/report', headers:{host:'127.0.0.2:18782'}};
  proxy.emit('proxyReq', {}, req);
  assert.doesNotThrow(() => proxy.emit('proxyRes', {statusCode:200,headers:{'content-type':'application/json'}}, req));
  assert.deepEqual(entries[6], {host:'127.0.0.2',method:'POST',route:'/api/analytics/grants/report',status:200});
  const text = JSON.stringify(entries);
  for (const secret of ['JWT-TOKEN-SECRET','BODY-SECRET','cookie-secret','authorization-secret','?secret','aaaaaaaa-aaaa']) assert.ok(!text.includes(secret));
});

test('trace uses new owned file and refuses a redirected private directory', () => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'review-trace-unit-')));
  try {
    const trace = createTrace(root); trace.write({route:'/api/user/me',status:200}); trace.close();
    assert.equal(fs.readFileSync(trace.file, 'utf8'), '{"route":"/api/user/me","status":200}\n');
    const second = createTrace(root); assert.notEqual(second.file, trace.file); second.close();
    const other = path.join(root, 'other'); fs.mkdirSync(other);
    const wrong = path.join(root, 'redirected'); fs.mkdirSync(wrong);
    fs.symlinkSync(other, path.join(wrong, '.local'), 'junction');
    assert.throws(() => createTrace(wrong));
  } finally { fs.rmSync(root, {recursive:true,force:true}); }
});

test('focus observer exists only in review config and reflects DOM state', () => {
  const config = createReviewConfig({courses:[], react:()=>({name:'react'}), courseFrontendWatch:()=>({}), cache:'test'});
  const plugin = config.plugins.find(item => item.name === 'analytics-review-focus');
  const [output, script] = plugin.transformIndexHtml();
  assert.equal(output.attrs.id, 'analytics-review-focus'); assert.equal(output.attrs.hidden, true);
  const data = {}, listeners = {}, element = {setAttribute:(key,value) => {data[key] = value;}};
  const document = {visibilityState:'visible', getElementById:() => element, hasFocus:() => true,
    addEventListener:(name,fn) => {listeners[name]=fn;}};
  const window = {isSecureContext:true, crypto:{randomUUID:()=>''}, addEventListener:(name,fn) => {listeners[name]=fn;}};
  let timer; vm.runInNewContext(script.children, {document, window, setInterval:fn => {timer=fn;}});
  assert.deepEqual(data, {'data-visibility':'visible','data-focused':'true','data-secure-context':'true','data-random-uuid':'function'});
  document.visibilityState = 'hidden'; document.hasFocus = () => false; window.isSecureContext = false; delete window.crypto; timer();
  assert.deepEqual(data, {'data-visibility':'hidden','data-focused':'false','data-secure-context':'false','data-random-uuid':'undefined'});
  assert.deepEqual(Object.keys(listeners).sort(), ['blur','focus','visibilitychange']);
  assert.equal(focusPlugin().name, 'analytics-review-focus');
});
