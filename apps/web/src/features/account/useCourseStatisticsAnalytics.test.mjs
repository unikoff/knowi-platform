import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import ts from "typescript";
function compile(file, imports = {}) {
  const source = fs.readFileSync(new URL(file, import.meta.url), "utf8");
  const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
  const module = {exports: {}};
  new Function("require", "module", "exports", code)(name => {if (!(name in imports)) throw new Error(name); return imports[name];}, module, module.exports);
  return module.exports;
}
const model = compile("./course-statistics-model.ts");
const adapter = compile("./course-statistics-analytics.ts");
function setup() {
  const states=[], refs=[], effectSlots=[];
  let stateIndex=0, refIndex=0, effectIndex=0;
  const originalWindow=globalThis.window, surface=new EventTarget();
  globalThis.window=surface;
  const calls = [];
  const react = {
    useSyncExternalStore: (_subscribe, snapshot) => snapshot(),
    useState: initial => {const index=stateIndex++; if (!(index in states)) states[index]=initial; return [states[index], next => {states[index]=typeof next === 'function' ? next(states[index]) : next;}];},
    useRef: initial => {const index=refIndex++; return refs[index] ??= {current:initial};},
    useEffect: (effect, deps) => {const index=effectIndex++, old=effectSlots[index];
      if (old && deps.length===old.deps.length && deps.every((value,i)=>Object.is(value,old.deps[i]))) return;
      old?.cleanup?.(); effectSlots[index]={deps,cleanup:effect()};},
  };
  class AnalyticsReportError extends Error {constructor(kind) {super(kind); this.kind = kind;}}
  const client = {AnalyticsReportError, fetchActivityReport: (filters, actor, signal) => new Promise((resolve, reject) => {calls.push({filters, actor, signal, resolve, reject});})};
  const persistence = {getCalendarPersistenceSignal: () => null, subscribeCalendarAuthSignal: () => () => {}};
  const {useCourseStatisticsAnalytics: hook} = compile("./useCourseStatisticsAnalytics.ts", {react, "../analytics-reporting/client": client, "./course-statistics-analytics": adapter, "./course-statistics-model": model, "./calendar-persistence": persistence});
  return {hook:(...args) => {stateIndex=0;refIndex=0;effectIndex=0;return hook(...args);}, calls, surface, AnalyticsReportError,
    close: () => {effectSlots.forEach(value => value?.cleanup?.()); if(originalWindow===undefined) delete globalThis.window; else globalThis.window=originalWindow;}};
}
const a = {courseId: "backend", from: "2026-11-16", to: "2026-11-17"};
const b = {...a, from: "2026-11-17"};
const makeResponse = value => ({status: "ok", quality: "partial", as_of: null, totals: {active_ms: value, theory_ms: value, practice_ms: 0, unique_visitors: 1}, daily: [{date: "2026-11-17", active_ms: value, theory_ms: value, practice_ms: 0, unique_visitors: 1}]});
const flush = () => new Promise(resolve => setImmediate(resolve));

test("initial open coalesces an in-flight mount read and retains projection catch-up", async t => {
  t.mock.timers.enable({apis:["setTimeout"]});
  const {hook,calls,surface,close}=setup();
  try {
    hook(true,"actor",a,0);
    surface.dispatchEvent(new Event("pymentor:personal-analytics-opened"));
    hook(true,"actor",a,0);
    assert.equal(calls.length,1);
    calls[0].resolve(makeResponse(60000)); await flush();
    hook(true,"actor",a,0);
    t.mock.timers.tick(3000); hook(true,"actor",a,0);
    assert.equal(calls.length,2);
    calls[1].resolve(makeResponse(90000)); await flush(); hook(true,"actor",a,0);
    t.mock.timers.tick(12000); hook(true,"actor",a,0);
    assert.equal(calls.length,3);
  } finally {close();}
});

test("reopening after close starts a fresh read even when old read remains in flight", () => {
  const {hook,calls,surface,close}=setup();
  try {
    hook(true,"actor",a,0);
    surface.dispatchEvent(new Event("pymentor:personal-analytics-closed"));
    surface.dispatchEvent(new Event("pymentor:personal-analytics-opened"));
    hook(true,"actor",a,0);
    assert.equal(calls.length,2);
    assert.equal(calls[0].signal.aborted,true);
  } finally {close();}
});

test("changing range cancels old request and a late result cannot replace current data", async () => {
  const {hook, calls, close} = setup();
  assert.equal(hook(true, "actor", a, 0).status, "loading");
  assert.deepEqual(calls[0].filters, {course_id:"backend", from_time:"2026-11-15T21:00:00.000Z", to_time:"2026-11-17T21:00:00.000Z"});
  assert.equal(hook(true, "actor", b, 0).status, "loading");
  assert.equal(calls[0].signal.aborted, true);
  calls[1].resolve(makeResponse(60000)); await flush();
  assert.equal(hook(true, "actor", b, 0).snapshot.totals.activeMs, 60000);
  calls[0].resolve(makeResponse(120000)); await flush();
  assert.equal(hook(true, "actor", b, 0).snapshot.totals.activeMs, 60000);
  close(); assert.equal(calls[1].signal.aborted, true);
});
test("disabled or demo mode aborts and does not initiate a personal request", () => {
  const {hook, calls, close} = setup();
  assert.equal(hook(false, "actor", a, 0).status, "pending"); assert.equal(calls.length, 0);
  hook(true, "actor", a, 0); assert.equal(calls.length, 1);
  assert.equal(hook(false, "actor", a, 0).status, "pending");
  assert.equal(calls[0].signal.aborted, true); assert.equal(calls.length, 1);
  close();
});

test("returning to account refreshes immediately and with bounded follow-ups", async t => {
  t.mock.timers.enable({apis:["setTimeout"]});
  const {hook,calls,surface,close}=setup();
  try {
    hook(true,"actor",a, 0); calls[0].resolve(makeResponse(60000)); await flush();
    assert.equal(hook(true,"actor",a, 0).snapshot.totals.activeMs,60000);
    surface.dispatchEvent(new Event("pymentor:personal-analytics-opened"));
    assert.equal(hook(true,"actor",a, 0).status,"loading"); assert.equal(calls.length,2);
    calls[1].resolve(makeResponse(90000)); await flush();
    assert.equal(hook(true,"actor",a, 0).snapshot.totals.activeMs,90000);
    t.mock.timers.tick(3000); hook(true,"actor",a, 0); assert.equal(calls.length,3);
    calls[2].resolve(makeResponse(120000)); await flush(); hook(true,"actor",a, 0);
    t.mock.timers.tick(12000); hook(true,"actor",a, 0); assert.equal(calls.length,4);
    calls[3].resolve(makeResponse(150000)); await flush();
    assert.equal(hook(true,"actor",a, 0).snapshot.totals.activeMs,150000);
    t.mock.timers.tick(60000); hook(true,"actor",a, 0); assert.equal(calls.length,4);
  } finally {close();}
});

test("leaving account cancels follow-ups and denied responses stop scheduled reads", async t => {
  t.mock.timers.enable({apis:["setTimeout"]});
  const {hook,calls,surface,close,AnalyticsReportError}=setup();
  try {
    hook(true,"actor",a, 0); surface.dispatchEvent(new Event("pymentor:personal-analytics-closed"));
    t.mock.timers.tick(20000); hook(true,"actor",a, 0); assert.equal(calls.length,1);
    surface.dispatchEvent(new Event("pymentor:personal-analytics-opened")); hook(true,"actor",a, 0);
    calls[1].reject(new AnalyticsReportError("denied")); await flush();
    assert.equal(hook(true,"actor",a, 0).status,"denied");
    t.mock.timers.tick(20000); hook(true,"actor",a, 0); assert.equal(calls.length,2);
  } finally {close();}
});

test("a temporary unavailable result is refreshed on the next bounded follow-up", async t => {
  t.mock.timers.enable({apis:["setTimeout"]});
  const {hook,calls,close,AnalyticsReportError}=setup();
  try {
    hook(true,"actor",a, 0);
    calls[0].reject(new AnalyticsReportError("unavailable")); await flush();
    assert.equal(hook(true,"actor",a, 0).status,"error");
    t.mock.timers.tick(3000); hook(true,"actor",a, 0); assert.equal(calls.length,2);
    calls[1].resolve(makeResponse(120000)); await flush();
    assert.equal(hook(true,"actor",a, 0).snapshot.totals.activeMs,120000);
  } finally {close();}
});

test("actor replacement cancels old timers and cannot expose a late old response", async t => {
  t.mock.timers.enable({apis:["setTimeout"]});
  const {hook,calls,close}=setup();
  try {
    hook(true,"actor-a",a, 0);
    hook(true,"actor-b",a, 0); assert.equal(calls[0].signal.aborted,true);
    calls[1].resolve(makeResponse(120000)); await flush();
    calls[0].resolve(makeResponse(60000)); await flush();
    assert.equal(hook(true,"actor-b",a, 0).snapshot.totals.activeMs,120000);
    t.mock.timers.tick(3000); hook(true,"actor-b",a, 0);
    assert.equal(calls.length,3); assert.equal(calls[2].actor,"actor-b");
  } finally {close();}
});

test("each period is fetched independently and revisiting it reuses its own report", async () => {
  const {hook,calls,close}=setup();
  const month = {...a, from:"2026-11-01", to:"2026-11-30"};
  try {
    hook(true,"actor",a,0); calls[0].resolve(makeResponse(60000)); await flush();
    assert.equal(hook(true,"actor",a,0).snapshot.totals.activeMs,60000);
    assert.equal(hook(true,"actor",month,0).status,"loading");
    assert.equal(calls.length,2);
    calls[1].resolve(makeResponse(180000)); await flush();
    assert.equal(hook(true,"actor",month,0).snapshot.totals.activeMs,180000);
    assert.equal(hook(true,"actor",a,0).snapshot.totals.activeMs,60000);
    assert.equal(hook(true,"actor",month,0).snapshot.totals.activeMs,180000);
    assert.equal(calls.length,2);
  } finally {close();}
});

test("cached reports cannot cross account sessions or courses", async () => {
  const {hook,calls,close}=setup();
  try {
    hook(true,"actor",a,0); calls[0].resolve(makeResponse(60000)); await flush();
    hook(true,"actor",a,0);
    assert.equal(hook(true,"actor",{...a,courseId:"other"},0).status,"loading");
    assert.equal(calls.length,2);
    hook(false,"actor",a,0);
    assert.equal(hook(true,"actor",a,0).status,"loading");
    assert.equal(calls.length,3);
    calls[2].resolve(makeResponse(90000)); await flush(); hook(true,"actor",a,0);
    assert.equal(hook(true,"other-actor",a,0).status,"loading");
    assert.equal(calls.length,4);
  } finally {close();}
});

test("a late result cannot overwrite a revisited cached period", async () => {
  const {hook,calls,close}=setup();
  try {
    hook(true,"actor",a,0); calls[0].resolve(makeResponse(60000)); await flush();
    hook(true,"actor",a,0); hook(true,"actor",b,0);
    assert.equal(hook(true,"actor",a,0).snapshot.totals.activeMs,60000);
    assert.equal(calls[1].signal.aborted,true);
    calls[1].resolve(makeResponse(120000)); await flush();
    assert.equal(hook(true,"actor",a,0).snapshot.totals.activeMs,60000);
    assert.equal(calls.length,2);
  } finally {close();}
});
