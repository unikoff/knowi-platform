import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import ts from 'typescript';

const require=createRequire(import.meta.url);
function load(path,replacements={}) {
  const source=fs.readFileSync(new URL(path,import.meta.url),'utf8');
  const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
  const module={exports:{}};
  new Function('require','module','exports',code)(id => replacements[id] ?? require(id),module,module.exports);
  return module.exports;
}
const catalog=load('./catalog.ts');
const {activitySources}=load('./integration.ts');
const flush=async () => {for(let i=0;i<12;i++) await Promise.resolve();};
const body={course_id:'backend',catalog_version:'published-v1',lesson_ids:['lesson-1']};
function response(value,{status=200,header='X-Analytics-Browser-Presence-Required'}={}) {
  const headers=new Headers();
  if(value!==null) headers.set(header,value);
  let reads=0;
  return {status,ok:status>=200&&status<300,headers,json:async () => {reads++; return body;},get reads(){return reads;}};
}
function harness() {
  let ref, state=null;
  const effects=[], updates=[];
  const react={useRef:value => ref ??= {current:value},useState:() => [state,value => {state=value; updates.push(value);}],
    useEffect:(effect,deps) => effects.push({effect,deps})};
  const {usePublishedCatalog}=load('./usePublishedCatalog.ts',{react,'./catalog':catalog});
  return {render:(identity='actor',policy='cutover') => usePublishedCatalog(true,identity,'backend','',policy),effects,updates};
}

for(const value of [null,'false','TRUE','1','true,false']) {
  test(`missing or non-exact requirement ${String(value)} pauses collection during cutover`,async t => {
    t.mock.timers.enable({apis:['setTimeout']});
    const old=globalThis.fetch, result=response(value), h=harness();
    globalThis.fetch=async () => result;
    try {
      assert.equal(h.render(),null);
      const stop=h.effects[0].effect(); await flush();
      const published=h.render();
      assert.equal(published,null); assert.equal(result.reads,0);
      assert.deepEqual(activitySources(true,true,published?.version ?? null,'user'),{tracker:false,legacy:false});
      stop();
    } finally {globalThis.fetch=old;}
  });
}

test('exact true plus valid published metadata enables tracker',async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const old=globalThis.fetch, result=response('true'), h=harness();
  globalThis.fetch=async () => result;
  try {
    h.render(); const stop=h.effects[0].effect(); await flush();
    const published=h.render();
    assert.equal(published.version,'published-v1'); assert.equal(result.reads,1);
    assert.deepEqual(activitySources(true,true,published.version,'user'),{tracker:true,legacy:false});
    stop();
  } finally {globalThis.fetch=old;}
});

for(const value of [null,'false','true','PARALLEL','parallel,other']) {
  test(`parallel mode rejects missing or non-exact collection header ${String(value)}`,async t => {
    t.mock.timers.enable({apis:['setTimeout']});
    const old=globalThis.fetch, result=response(value,{header:'X-Analytics-Collection-Mode'}), h=harness();
    globalThis.fetch=async () => result;
    try {
      h.render('actor','parallel'); const stop=h.effects[0].effect(); await flush();
      assert.equal(h.render('actor','parallel'),null); assert.equal(result.reads,0);
      assert.deepEqual(activitySources(true,false,null,'user'),{tracker:false,legacy:true}); stop();
    } finally {globalThis.fetch=old;}
  });
}

test('parallel header enables learner tracker while retaining legacy',async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const old=globalThis.fetch, result=response('parallel',{header:'X-Analytics-Collection-Mode'}), h=harness();
  globalThis.fetch=async () => result;
  try {
    h.render('actor','parallel'); const stop=h.effects[0].effect(); await flush();
    const published=h.render('actor','parallel');
    assert.equal(published.version,'published-v1');
    assert.deepEqual(activitySources(true,false,published.version,'user'),{tracker:true,legacy:true});
    assert.deepEqual(activitySources(true,false,published.version,'admin'),{tracker:false,legacy:true});
    stop();
  } finally {globalThis.fetch=old;}
});

test('the cutover header alone cannot enable parallel collection',async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const old=globalThis.fetch,h=harness();
  globalThis.fetch=async () => response('true');
  try {
    h.render('actor','parallel'); const stop=h.effects[0].effect(); await flush();
    assert.equal(h.render('actor','parallel'),null); stop();
  } finally {globalThis.fetch=old;}
});

test('cutover and parallel headers cannot authorize each other and policy switch hides old catalog',async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const old=globalThis.fetch,h=harness();
  globalThis.fetch=async () => response('parallel',{header:'X-Analytics-Collection-Mode'});
  try {
    h.render('actor','parallel'); const stop=h.effects[0].effect(); await flush();
    assert.equal(h.render('actor','parallel').version,'published-v1');
    assert.equal(h.render('actor','cutover'),null);
    stop(); const stopCutover=h.effects.at(-1).effect(); await flush();
    assert.equal(h.render('actor','cutover'),null); stopCutover();
  } finally {globalThis.fetch=old;}
});

test('exact header does not bypass existing catalog validation',async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const old=globalThis.fetch, result=response('true'), h=harness();
  result.json=async () => ({...body,course_id:'other'});
  globalThis.fetch=async () => result;
  try {
    h.render(); const stop=h.effects[0].effect(); await flush();
    assert.equal(h.render(),null);
    assert.deepEqual(activitySources(true,true,null,'user'),{tracker:false,legacy:false});
    stop();
  } finally {globalThis.fetch=old;}
});

for(const status of [401,403]) {
  test(`${status} still stops retries`,async t => {
    t.mock.timers.enable({apis:['setTimeout']});
    const old=globalThis.fetch,h=harness(); let calls=0;
    globalThis.fetch=async () => {calls++; return response('true',{status});};
    try {
      h.render(); const stop=h.effects[0].effect(); await flush();
      t.mock.timers.tick(120_000); await flush();
      assert.equal(calls,1); assert.equal(h.render(),null); stop();
    } finally {globalThis.fetch=old;}
  });
}

test('old Core response retries and a later required-header response enables cutover',async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const oldFetch=globalThis.fetch, oldRandom=Math.random, h=harness();
  const responses=[response(null),response('true')]; let calls=0;
  globalThis.fetch=async () => responses[calls++]; Math.random=() => 0;
  try {
    h.render(); const stop=h.effects[0].effect(); await flush();
    assert.equal(h.render(),null); assert.equal(calls,1);
    t.mock.timers.tick(999); await flush(); assert.equal(calls,1);
    t.mock.timers.tick(1); await flush();
    assert.equal(calls,2); assert.equal(h.render().version,'published-v1');
    stop();
  } finally {globalThis.fetch=oldFetch; Math.random=oldRandom;}
});

test('revoked header pauses collection during cutover and cleanup cancels retry',async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const oldFetch=globalThis.fetch, oldRandom=Math.random, h=harness();
  const responses=[response('true'),response('false')]; let calls=0;
  globalThis.fetch=async () => responses[calls++]; Math.random=() => 0;
  try {
    h.render(); const stop=h.effects[0].effect(); await flush(); assert.equal(h.render().version,'published-v1');
    t.mock.timers.tick(60_000); await flush();
    assert.equal(h.render(),null); assert.deepEqual(activitySources(true,true,null,'user'),{tracker:false,legacy:false});
    stop(); t.mock.timers.tick(120_000); await flush(); assert.equal(calls,2);
  } finally {globalThis.fetch=oldFetch; Math.random=oldRandom;}
});

test('cleanup cancels an in-flight catalog response before it changes the scope',async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const oldFetch=globalThis.fetch,h=harness(); let resolve;
  globalThis.fetch=() => new Promise(done => {resolve=done;});
  try {
    h.render(); const stop=h.effects[0].effect(); stop(); resolve(response('true')); await flush();
    assert.equal(h.render(),null); assert.equal(h.updates.length,0);
  } finally {globalThis.fetch=oldFetch;}
});
