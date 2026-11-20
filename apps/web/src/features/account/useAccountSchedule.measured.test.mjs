import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import ts from "typescript";
function compile(file, imports = {}) {
  const code = ts.transpileModule(fs.readFileSync(new URL(file, import.meta.url), "utf8"), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
  const module={exports:{}};
  new Function("require","module","exports",code)(name => {if (!(name in imports)) throw Error(name); return imports[name];},module,module.exports);
  return module.exports;
}
function setup() {
  const states=[], refs=[], effects=[], calls=[], legacy=[]; let si=0,ri=0,ei=0;
  const original=globalThis.window; globalThis.window=new EventTarget();
  const react={useState:initial=>{const i=si++;if (!(i in states)) states[i]=initial;return [states[i],value=>{states[i]=typeof value === "function"?value(states[i]):value;}];},useRef:initial=>refs[ri++] ??= {current:initial},useCallback:fn=>fn,useEffect:(fn,deps)=>{const i=ei++,old=effects[i];if(old && deps.every((v,j)=>Object.is(v,old.deps[j])) && deps.length===old.deps.length)return;old?.cleanup?.();effects[i]={deps,cleanup:fn()};}};
  const api={getMeasuredActivity:(month,courseId)=>new Promise((resolve,reject)=>calls.push({month,courseId,resolve,reject})),getActivityCalendar:month=>{legacy.push("calendar");return Promise.resolve({days:[`2026-${String(month.month).padStart(2,"0")}-03`],visits:[]});},getActivitySummary:()=>{legacy.push("summary");return Promise.resolve({});},getBookingContext:()=>new Promise(()=>{}),getStudentSlots:()=>new Promise(()=>{})};
  const {createActivityRead}=compile("./activityRead.ts");
  const {useAccountSchedule}=compile("./useAccountSchedule.ts",{react,"./api":api,"../admin/scopedApi":{},"../../shared/api/client":{ApiError:class extends Error {}},"./activityRead":{createActivityRead}});
  return {calls,legacy,render:(user,month,measured=true,courseId="course")=>{si=ri=ei=0;return useAccountSchedule(user,courseId,month,"actor",`user:${user}`,measured);},close:()=>{effects.forEach(x=>x?.cleanup?.());globalThis.window=original;}};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const month=month=>({year:2026,month});
const response=(days,courseId="course")=>({days,course_id:courseId,current_streak:2,streak_is_lower_bound:true,coverage:"partial",as_of:null});
test("measured mode preserves positive days and falls back on failure",async()=>{const s=setup();try{assert.equal(s.render(1,month(1)).measurementStatus,"loading");assert.deepEqual(s.legacy,[]);s.calls[0].resolve(response(["2026-01-02"]));await flush();let state=s.render(1,month(1));assert.equal(state.measurementStatus,"partial");assert.deepEqual([...state.activityDays],["2026-01-02"]);assert.equal(state.activitySummary,null);s.render(1,month(2));s.calls[1].reject(Error("down"));await flush();state=s.render(1,month(2));assert.equal(state.measurementStatus,"visits_loading");await flush();state=s.render(1,month(2));assert.equal(state.measurementStatus,"visits");assert.deepEqual(s.legacy,["calendar"]);}finally{s.close();}});
test("late response cannot cross month, account or mode",async()=>{const s=setup();try{s.render(1,month(1));s.render(2,month(2));s.calls[1].resolve(response(["2026-02-02"]));await flush();s.calls[0].resolve(response(["2026-01-01"]));await flush();assert.deepEqual([...s.render(2,month(2)).activityDays],["2026-02-02"]);s.render(2,month(3));s.render(2,month(3),false);s.calls[2].resolve(response(["2026-03-03"]));await flush();const disabled=s.render(2,month(3),false);assert.deepEqual([...disabled.activityDays],["2026-03-03"]);assert.equal(disabled.measurementStatus,"visits");assert.deepEqual(s.legacy,["summary","calendar"]);}finally{s.close();}});

test("the request encodes the selected course and both calendar parameters", () => {
  const {getMeasuredActivity} = compile("./api.ts", {"../../shared/api/client": {apiRequest: path => path}});
  const url = new URL(getMeasuredActivity(month(3), "another-course"), "http://localhost");
  assert.equal(url.pathname, "/user/activity/measured");
  assert.deepEqual(Object.fromEntries(url.searchParams), {year: "2026", month: "3", course_id: "another-course"});
});

test("switching courses clears loaded markers and ignores late data from the previous course", async () => {
  const s = setup();
  try {
    s.render(1, month(1), true, "backend");
    s.calls[0].resolve(response(["2026-01-02"], "backend"));
    await flush();
    assert.deepEqual([...s.render(1, month(1), true, "backend").activityDays], ["2026-01-02"]);

    globalThis.window.dispatchEvent(new Event("pymentor:activity-updated"));
    const switched = s.render(1, month(1), true, "backend_copy");
    assert.equal(switched.measurementStatus, "loading");
    assert.equal(switched.activityDays.size, 0);
    assert.deepEqual(s.calls.map(call => call.courseId), ["backend", "backend", "backend_copy"]);
    s.calls[2].resolve(response(["2026-01-04"], "backend_copy"));
    s.calls[1].resolve(response(["2026-01-05"], "backend"));
    await flush();
    assert.deepEqual([...s.render(1, month(1), true, "backend_copy").activityDays], ["2026-01-04"]);
  } finally { s.close(); }
});

test("a legacy unscoped or mismatched response cannot mark any course days", async () => {
  const s = setup();
  try {
    s.render(1, month(1), true, "backend");
    const {course_id, ...legacy} = response(["2026-01-03"]);
    s.calls[0].resolve(legacy);
    await flush();
    let state = s.render(1, month(1), true, "backend");
    assert.equal(state.measurementStatus, "visits_loading");
    assert.equal(state.activityDays.size, 0);

    s.render(1, month(1), true, "backend_copy");
    s.calls[1].resolve(response(["2026-01-05"], "backend"));
    await flush();
    state = s.render(1, month(1), true, "backend_copy");
    assert.equal(state.measurementStatus, "visits_loading");
    assert.equal(state.activityDays.size, 0);
    assert.deepEqual(s.legacy, ["calendar", "calendar"]);
  } finally { s.close(); }
});
