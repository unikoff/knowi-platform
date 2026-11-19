import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import {createRequire} from "node:module";
import ts from "typescript";
const require=createRequire(import.meta.url);
function compile(path,imports={}) {
  const code=ts.transpileModule(fs.readFileSync(new URL(path,import.meta.url),"utf8"),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
  const out={exports:{}}; new Function("require","module","exports",code)((name)=>imports[name]??require(name),out,out.exports); return out.exports;
}
const request=compile("./courseActivityRequest.ts");
const {startCourseActivityDetailsRequest}=compile("./courseActivityDetailsRequest.ts",{"./courseActivityRequest":request});
const filters={course_id:"backend",from_time:"2026-11-01T00:00:00Z",to_time:"2026-11-02T00:00:00Z",timezone:"Europe/Moscow",lesson_id:"l1",offset:0,limit:20};
const report={course_id:"backend",from_time:filters.from_time,to_time:filters.to_time,timezone:filters.timezone,status:"ok"};
const details={report,lesson_id:"l1",rows:[],next_offset:null};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test("details propagate lesson, pagination and target lifecycle",async()=>{
  const calls=[]; const scoped={...filters,user_id:7,expected_target_ref:"target"};
  startCourseActivityDetailsRequest({filters:scoped,expectedActorRef:"actor",fetchDetails:async(f,a)=>{calls.push([f.lesson_id,f.offset,f.limit,f.user_id,f.expected_target_ref,a]);return details;},onDetails:()=>calls.push("ready"),onFailure:(e)=>calls.push(e)});
  await tick(); assert.deepEqual(calls,[["l1",0,20,7,"target","actor"],"ready"]);
});
test("mismatched lesson or invalid pagination fails instead of rendering",async()=>{
  for(const [scope,result] of [[filters,{...details,lesson_id:"other"}],[filters,{...details,next_offset:21}],[{...filters,offset:20001},details],[{...filters,limit:101},details]]){
    const calls=[]; startCourseActivityDetailsRequest({filters:scope,expectedActorRef:"actor",fetchDetails:async()=>result,onDetails:()=>calls.push("ready"),onFailure:(e)=>calls.push(e)});
    await tick(); assert.deepEqual(calls,["error"]);
  }
});
test("filter switch aborts the request and ignores a late details result",async()=>{
  let resolve, signal; const calls=[];
  const cancel=startCourseActivityDetailsRequest({filters,expectedActorRef:"actor",fetchDetails:(_f,_a,s)=>{signal=s;return new Promise(r=>{resolve=r;});},onDetails:()=>calls.push("ready"),onFailure:(e)=>calls.push(e)});
  await tick(); cancel(); assert.equal(signal.aborted,true); resolve(details); await tick(); assert.deepEqual(calls,[]);
});

test("a 21-row result is fetched as two scoped pages without losing or repeating rows", async () => {
  const rows = Array.from({length:21}, (_, index) => ({level:"lesson",course_id:"backend",lesson_id:`lesson-${index + 1}`,unique_visitors:1,active_ms:1000,theory_ms:1000,practice_ms:0}));
  const {lesson_id: _lesson, ...allLessons} = filters;
  const requested = [];
  const seen = [];
  const fetchDetails = async (scope, actor) => {
    requested.push({offset:scope.offset, limit:scope.limit, course:scope.course_id, actor});
    return {...details, lesson_id:null, rows:rows.slice(scope.offset, scope.offset + scope.limit), next_offset:scope.offset + scope.limit < rows.length ? scope.offset + scope.limit : null};
  };
  let offset = 0;
  for (let page = 0; page < 2; page++) {
    let result;
    startCourseActivityDetailsRequest({filters:{...allLessons,offset},expectedActorRef:"actor",fetchDetails,onDetails:value=>{result=value;},onFailure:failure=>assert.fail(`unexpected ${failure}`)});
    await tick();
    assert.ok(result);
    seen.push(...result.rows.map(row=>row.lesson_id));
    offset = result.next_offset;
  }
  assert.equal(offset, null);
  assert.deepEqual(seen, rows.map(row=>row.lesson_id));
  assert.deepEqual(requested,[{offset:0,limit:20,course:"backend",actor:"actor"},{offset:20,limit:20,course:"backend",actor:"actor"}]);
});

test("details access denial is surfaced once without retry or a report", async () => {
  let attempts = 0;
  const events = [];
  startCourseActivityDetailsRequest({filters,expectedActorRef:"actor",fetchDetails:async()=>{attempts++;throw {status:403};},onDetails:()=>events.push("ready"),onFailure:failure=>events.push(failure)});
  await tick();
  assert.equal(attempts,1);
  assert.deepEqual(events,["access"]);
});
