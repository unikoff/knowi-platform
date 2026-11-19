import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';

const require = createRequire(import.meta.url);
function load(path, replacements = {}, transform = value => value) {
  const source = transform(fs.readFileSync(new URL(path, import.meta.url), 'utf8'));
  const code = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
  const module = {exports:{}};
  new Function('require','module','exports',code)(id => replacements[id] ?? require(id),module,module.exports);
  return module.exports;
}
const dates = load('../../shared/lib/dates.ts');
function harness(request) {
  const effects = [];
  let ref;
  const {useRecordActivityVisit} = load('./useRecordActivityVisit.ts', {
    react:{useEffect:(effect,deps) => effects.push({effect,deps}),useRef:value => ref ??= {current:value}},
    './api':{recordActivityVisit:request}, '../../shared/lib/dates':dates,
  });
  return {useRecordActivityVisit,effects};
}
const flush = async () => { for (let i=0;i<4;i++) await Promise.resolve(); };
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve=done; });
  return {promise,resolve};
};

test('baseline keeps local date while fallback uses the server date despite a skewed clock', async () => {
  const oldDate=globalThis.Date, oldWindow=globalThis.window, oldTz=process.env.TZ;
  process.env.TZ='America/Los_Angeles';
  globalThis.Date=class extends oldDate {
    constructor(...args) { super(...(args.length ? args : ['2025-12-30T01:00:00Z'])); }
  };
  const dispatched=[]; globalThis.window={dispatchEvent:event => {dispatched.push(event); return true;}};
  try {
    const recorded=[];
    const {useRecordActivityVisit,effects}=harness(async value => {recorded.push(value); return {activity_date:'2026-01-02',visited_at:null};});
    useRecordActivityVisit(7); const stopDefault=effects[0].effect(); await flush();
    useRecordActivityVisit(7,'server','lifecycle-a'); const stopFallback=effects[1].effect(); await flush();
    assert.deepEqual(recorded,['2025-12-29',undefined]);
    assert.equal(dispatched[1].detail.activity_date,'2026-01-02');
    assert.deepEqual(effects[0].deps,[7,'local',undefined,'[7,"local",null]']);
    assert.deepEqual(effects[1].deps,[7,'server','lifecycle-a','[7,"server","lifecycle-a"]']);
    stopDefault(); stopFallback();
  } finally {
    globalThis.Date=oldDate;
    if (oldWindow === undefined) delete globalThis.window; else globalThis.window=oldWindow;
    if (oldTz === undefined) delete process.env.TZ; else process.env.TZ=oldTz;
  }
});

test('visit API sends an empty object only when the server chooses the day', async () => {
  const calls=[];
  const {recordActivityVisit}=load('./api.ts', {'../../shared/api/client':{apiRequest:async (...args) => {calls.push(args); return {};}}});
  await recordActivityVisit(); await recordActivityVisit('2025-12-29');
  assert.deepEqual(calls,[['/user/activity/visit',{}],['/user/activity/visit',{activity_date:'2025-12-29'}]]);
});

test('server-day fallback sends POST JSON with an empty object', async () => {
  const oldFetch=globalThis.fetch, requests=[];
  try {
    globalThis.fetch=async (url,init) => {
      requests.push({url,init});
      return new Response(JSON.stringify({activity_date:'2026-01-02',visited_at:null}),{headers:{'Content-Type':'application/json'}});
    };
    const client=load('../../shared/api/client.ts', {'../course-paths':{resolveCourseRoute:() => null}},
      source => source.replaceAll('import.meta.env.BASE_URL','"/"'));
    const {recordActivityVisit}=load('./api.ts', {'../../shared/api/client':client});
    assert.equal((await recordActivityVisit()).activity_date,'2026-01-02');
    assert.equal(requests[0].url,'/api/user/activity/visit');
    assert.equal(requests[0].init.method,'POST');
    assert.equal(requests[0].init.body,'{}');
    assert.equal(requests[0].init.headers['Content-Type'],'application/json');
  } finally { globalThis.fetch=oldFetch; }
});

test('late response after identity replacement or cleanup cannot dispatch an old activity update', async () => {
  const oldWindow=globalThis.window;
  let events=0; globalThis.window={dispatchEvent:() => {events++; return true;}};
  try {
    const requests=[];
    const {useRecordActivityVisit,effects}=harness(() => { const item=deferred(); requests.push(item); return item.promise; });
    useRecordActivityVisit(7,'server','lifecycle-a'); const stopOld=effects[0].effect();
    useRecordActivityVisit(7,'server','lifecycle-b');
    assert.notDeepEqual(effects[0].deps,effects[1].deps);
    requests[0].resolve({activity_date:'2026-01-01'}); await flush(); assert.equal(events,0);
    stopOld(); const stopNew=effects[1].effect();
    requests[1].resolve({activity_date:'2026-01-02'}); await flush(); assert.equal(events,1);
    stopNew();
    useRecordActivityVisit(null,'server','logged-out'); effects[2].effect()();
    assert.equal(requests.length,2);
  } finally { if (oldWindow === undefined) delete globalThis.window; else globalThis.window=oldWindow; }
});
