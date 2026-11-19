import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import ts from "typescript";

const source=fs.readFileSync(new URL("./useCourseChapterAnalytics.ts",import.meta.url),"utf8");
const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
const data={tracksLoaded:true,courses:[{id:"Глава 1",blocks:[{lesson_ids:["one","two"],lessons:[]}]},{id:"chapter-2",blocks:[]}]};
const catalog=[{id:"Глава 1",lessons:[{id:"one"}]},{id:"chapter-2",lessons:[{id:"three"}]}];
const defaultCourseId="backend";
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const opened="pymentor:personal-analytics-opened";
const closed="pymentor:personal-analytics-closed";
const measured=(active_ms=0)=>({status:"ok",chapters:[{chapter_id:"Глава 1",active_ms}],lessons:[{lesson_id:"one",active_ms}]});

function setup() {
  let state, previousEffect;
  const refs=[];
  let refIndex=0;
  const surface=new EventTarget();
  const calls=[];
  let now=0, timerId=0;
  const timers=new Map();
  const schedule=(run,delay)=>{timers.set(++timerId,{run,at:now+delay});return timerId;};
  const clear=id=>timers.delete(id);
  const advance=ms=>{
    now+=ms;
    for (const [id,timer] of timers) {
      if (timer.at>now) continue;
      timers.delete(id);
      timer.run();
    }
  };
  class AnalyticsReportError extends Error {constructor(kind) {super(kind);this.kind=kind;}}
  const react={
    useMemo:fn=>fn(),
    useState:initial=>[state ??= initial,next=>{state=next;}],
    useRef:initial=>refs[refIndex++] ??= {current:initial},
    useEffect:(fn,[key])=>{
      if (previousEffect?.key===key) return;
      previousEffect?.cleanup?.();
      previousEffect={key,cleanup:fn()};
    },
  };
  const client={AnalyticsReportError,fetchLifetimeActivityOutline:(body,actor,signal)=>new Promise((resolve,reject)=>{
    calls.push({body,actor,signal,resolve,reject});
  })};
  const adapter={courseReportFilters:params=>({course_id:params.courseId,from_time:params.from,to_time:params.to})};
  const imports={react,"../analytics-reporting/client":client,"./course-statistics-analytics":adapter};
  const module={exports:{}};
  new Function("require","module","exports","window","setTimeout","clearTimeout",code)(name=>imports[name],module,module.exports,surface,schedule,clear);
  const draw=({enabled=true,actor="actor",courseId=defaultCourseId,course=data,attempt=0}={})=>{
    refIndex=0;
    return module.exports.useCourseChapterAnalytics(enabled,actor,courseId,course,catalog,attempt);
  };
  return {calls,draw,advance,AnalyticsReportError,dispatch:event=>surface.dispatchEvent(new Event(event)),dispose:()=>previousEffect?.cleanup?.()};
}

test("complete catalog membership reaches one self-scoped request even without lesson cards in blocks", t => {
  const {calls,draw,dispose}=setup();
  t.after(dispose);
  assert.equal(draw({enabled:false}).status,"pending");
  assert.equal(draw({course:{tracksLoaded:false,courses:[]}}).status,"pending");
  assert.equal(calls.length,0);
  assert.equal(draw().status,"loading");
  draw();
  assert.equal(calls.length,1);
  assert.equal(calls[0].actor,"actor");
  assert.deepEqual(Object.keys(calls[0].body).sort(), ["chapters", "course_id"]);
  assert.deepEqual(calls[0].body.chapters,[{chapter_id:"Глава 1",lesson_ids:["one","two"]},{chapter_id:"chapter-2",lesson_ids:["three"]}]);
});

test("outline ignores delayed responses after account or course changes and aborts on leaving personal mode", async t => {
  const {calls,draw,dispose}=setup();
  t.after(dispose);
  draw();
  assert.equal(draw({actor:"new"}).status,"loading");
  assert.equal(calls[0].signal.aborted,true);
  const courseId="another-course";
  assert.equal(draw({actor:"new",courseId}).status,"loading");
  assert.equal(calls[1].signal.aborted,true);
  const report={status:"ok",lessons:[{lesson_id:"one",active_ms:45}]};
  calls[2].resolve(report);
  await flush();
  calls[0].resolve({status:"ok",lessons:[{lesson_id:"two",active_ms:60}]});
  calls[1].resolve({status:"ok",lessons:[{lesson_id:"three",active_ms:60}]});
  await flush();
  const current=draw({actor:"new",courseId});
  assert.equal(current.status,"ready");
  assert.equal(current.report,report);
  assert.deepEqual(draw({enabled:false,actor:"new",courseId}),{status:"pending",report:null});
  assert.equal(calls[2].signal.aborted,true);
});

test("reports distinguish stale results and separate transport failure states", async t => {
  const {calls,draw,AnalyticsReportError,dispose}=setup();
  t.after(dispose);
  draw();
  const report={status:"stale",lessons:[]};
  calls[0].resolve(report);
  await flush();
  assert.equal(draw().status,"stale");
  assert.equal(draw().report,report);
  for (const [index,[kind,status]] of [["unavailable","unavailable"],["denied","denied"],["invalid_response","error"]].entries()) {
    const attempt=index+1;
    assert.equal(draw({attempt}).status,"loading");
    calls[attempt].reject(new AnalyticsReportError(kind));
    await flush();
    assert.equal(draw({attempt}).status,status);
    assert.equal(draw({attempt}).report,null);
  }
});

test("opening the mounted account refreshes the outline immediately and twice while the projection catches up", async t => {
  const {calls,draw,dispatch,advance,dispose}=setup();
  t.after(dispose);
  draw();
  calls[0].resolve(measured());
  await flush();
  assert.equal(draw().report.lessons[0].active_ms,0);

  dispatch(opened);
  assert.equal(calls.length,2);
  calls[1].resolve(measured());
  await flush();
  advance(3000);
  assert.equal(calls.length,3);
  calls[2].resolve(measured(30000));
  await flush();
  assert.equal(draw().report.chapters[0].active_ms,30000);
  advance(12000);
  assert.equal(calls.length,4);
  calls[3].resolve(measured(60000));
  await flush();
  assert.equal(draw().report.lessons[0].active_ms,60000);
  advance(60000);
  assert.equal(calls.length,4);
});

test("closing cancels an in-flight read and late results, and keeps account or course changes dormant until reopened", async t => {
  const {calls,draw,dispatch,advance,dispose}=setup();
  t.after(dispose);
  draw();
  dispatch(closed);
  assert.equal(calls[0].signal.aborted,true);
  calls[0].resolve(measured(60000));
  await flush();
  assert.equal(draw().report,null);

  const current={actor:"new",courseId:"another-course"};
  assert.equal(draw(current).report,null);
  advance(20000);
  assert.equal(calls.length,1);
  dispatch(opened);
  assert.equal(calls[1].actor,"new");
  assert.equal(calls[1].body.course_id,"another-course");
  advance(3000);
  assert.equal(calls[1].signal.aborted,true);
  calls[2].resolve(measured(10000));
  calls[1].resolve(measured(20000));
  await flush();
  assert.equal(draw(current).report.lessons[0].active_ms,10000);

  dispose();
  assert.equal(calls[2].signal.aborted,true);
  dispatch(opened);
  advance(60000);
  assert.equal(calls.length,3);
});

test("denial cancels scheduled self-report reads, and disabling turns off reopen requests", async t => {
  const {calls,draw,dispatch,advance,AnalyticsReportError,dispose}=setup();
  t.after(dispose);
  draw();
  calls[0].reject(new AnalyticsReportError("denied"));
  await flush();
  assert.equal(draw().status,"denied");
  advance(60000);
  assert.equal(calls.length,1);

  draw({attempt:1});
  assert.equal(calls.length,2);
  assert.equal(draw({enabled:false,attempt:1}).status,"pending");
  assert.equal(calls[1].signal.aborted,true);
  dispatch(opened);
  advance(60000);
  assert.equal(calls.length,2);
});
