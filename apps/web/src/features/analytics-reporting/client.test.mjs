import assert from "node:assert/strict";
import test from "node:test";
import {createServer} from "vite";
const vite = await createServer({configFile: false, optimizeDeps: {noDiscovery:true, include:[]}, server: {middlewareMode:true}});
const {fetchActivityReport, fetchActivityDetails, fetchActivityChapters, fetchActivityOutline, fetchLifetimeActivityOutline,
  fetchStudentActivityReport, fetchStudentLifetimeActivityOutline, AnalyticsReportError} = await vite.ssrLoadModule("/src/features/analytics-reporting/client.ts");
test.after(async () => {await vite.close();});
const filters = {from_time:"2026-11-15T21:00:00.000Z", to_time:"2026-11-16T21:00:00.000Z", course_id:"backend"};
const actor = "01234567-89ab-4cde-8fab-0123456789ab";
const grant = {grant:"secret.jwt.token", subject_ref:actor, server_time:"2026-11-16T10:00:00Z", expires_at:"2026-11-16T10:01:00Z"};
const v = {unique_visitors: 2, active_ms: 60000, theory_ms: 60000, practice_ms: 0};
const report = {schema_version:1,status:"ok",quality:"unknown",as_of:null,lag_seconds:null,timezone:"Europe/Moscow",metric_version:"activity-v1",course_id:"backend",from_time:filters.from_time,to_time:filters.to_time,totals:v,daily:[{date:"2026-11-16",...v}],trusted_progress:null};
const response = (data, status = 200) => new Response(JSON.stringify(data), {status, headers:{"Content-Type":"application/json"}});

test("grant and report use correct scope, credentials and bearer without persistence", async () => {
  const original = globalThis.fetch; const calls=[];
  globalThis.fetch=async (url, init) => {calls.push({url:String(url), init}); return response(calls.length === 1 ? grant : report);};
  try {
    assert.deepEqual(await fetchActivityReport(filters,actor),report);
    assert.equal(calls.length,2); assert.match(calls[0].url,/\/api\/analytics\/grants\/report$/);
    assert.deepEqual(JSON.parse(calls[0].init.body),{course_id:"backend"}); assert.equal(calls[0].init.credentials,"include");
    assert.equal(calls[0].init.headers.Authorization,undefined); assert.equal(calls[1].init.credentials,"omit");
    assert.equal(calls[1].init.headers.Authorization,"Bearer secret.jwt.token");
    const query = new URL(calls[1].url,"http://localhost").searchParams;
    assert.equal(query.get("course_id"),"backend"); assert.equal(query.get("timezone"),"Europe/Moscow");
    assert.equal(calls[0].init.redirect,"error"); assert.equal(calls[1].init.cache,"no-store");
  } finally {globalThis.fetch=original;}
});
test("access denial stops immediately and never exposes token", async () => {
  const original=globalThis.fetch; let count=0; globalThis.fetch=async()=>{count++;return response({detail:"secret.jwt.token"},403);};
  try {await assert.rejects(fetchActivityReport(filters,actor), e=>e instanceof AnalyticsReportError && e.kind==="denied" && !e.message.includes("secret")); assert.equal(count,1);} finally {globalThis.fetch=original;}
});
test("mismatched scope or impossible data are rejected", async () => {
  const original=globalThis.fetch;
  try {for (const invalid of [{...report,course_id:"other"}, {...report,totals:{...v,active_ms:123}}, {...report,from_time:"2026-11-14T21:00:00Z"}, {...report,quality:"complete",status:"no_data"}]) {
    let count=0; globalThis.fetch=async()=>response(++count === 1 ? grant : invalid);
    await assert.rejects(fetchActivityReport(filters,actor), e=>e instanceof AnalyticsReportError && e.kind==="invalid_response");
  }} finally {globalThis.fetch=original;}
});
test("abort cancels the pending report and does not retry", async () => {
  const original=globalThis.fetch; let count=0; const controller=new AbortController();
  globalThis.fetch=async (_url,init)=>{if (++count===1) return response(grant); return await new Promise((_resolve,reject)=>init.signal.addEventListener("abort",()=>reject(new DOMException("aborted","AbortError")),{once:true}));};
  try {const task=fetchActivityReport(filters,actor,controller.signal); await new Promise(resolve=>setTimeout(resolve,10)); controller.abort(); await assert.rejects(task,{name:"AbortError"}); assert.equal(count,2);} finally {globalThis.fetch=original;}
});
test("invalid range is rejected before requesting a grant", async () => {
  const original=globalThis.fetch; let count=0; globalThis.fetch=async()=>{count++;throw new Error("unexpected");};
  try {await assert.rejects(fetchActivityReport({...filters,to_time:"2026-12-30T21:00:00Z"},actor), e=>e.kind==="invalid_response"); assert.equal(count,0);} finally {globalThis.fetch=original;}
});

test("pre-aborted requests never issue a grant request", async () => {
  const original=globalThis.fetch; let count=0; globalThis.fetch=async()=>{count++;throw new Error("unexpected");};
  const controller=new AbortController(); controller.abort();
  try {await assert.rejects(fetchActivityReport(filters,actor,controller.signal),{name:"AbortError"}); assert.equal(count,0);} finally {globalThis.fetch=original;}
});
test("overall timeout aborts a pending grant and reports unavailability", async () => {
  const originalFetch=globalThis.fetch, originalSetTimeout=globalThis.setTimeout, originalClearTimeout=globalThis.clearTimeout;
  globalThis.setTimeout=(fn)=>{queueMicrotask(fn); return 1;}; globalThis.clearTimeout=()=>{};
  globalThis.fetch=async (_url,init)=>await new Promise((_resolve,reject)=>init.signal.addEventListener("abort",()=>reject(new DOMException("aborted","AbortError")),{once:true}));
  try {await assert.rejects(fetchActivityReport(filters,actor), e=>e instanceof AnalyticsReportError && e.kind==="unavailable");}
  finally {globalThis.fetch=originalFetch;globalThis.setTimeout=originalSetTimeout;globalThis.clearTimeout=originalClearTimeout;}
});

test("a changed filter object cannot alter an in-flight scope", async () => {
  const original=globalThis.fetch; const changing={...filters}; let release; const pending=new Promise(resolve=>{release=resolve;}); const calls=[];
  globalThis.fetch=async (url,init)=>{calls.push({url:String(url),init}); if (calls.length===1) {await pending; return response(grant);} return response(report);};
  try {const task=fetchActivityReport(changing,actor); changing.course_id="other"; release(); assert.deepEqual(await task,report); assert.match(calls[1].url,/course_id=backend/);}
  finally {globalThis.fetch=original;}
});
test("report 401 does not request a replacement grant", async () => {
  const original=globalThis.fetch; let count=0; globalThis.fetch=async()=>response(++count===1 ? grant : {}, count===1 ? 200 : 401);
  try {await assert.rejects(fetchActivityReport(filters,actor), e=>e.kind==="denied"); assert.equal(count,2);} finally {globalThis.fetch=original;}
});

const target = "11111111-2222-4333-8444-555555555555";
test("numeric user maps through Core grant into lifecycle-scoped Query", async () => {
  const original=globalThis.fetch; const calls=[];
  globalThis.fetch=async(url,init)=>{calls.push({url:String(url),init});return response(calls.length===1 ? {...grant,target_subject_ref:target} : report);};
  try {
    await fetchActivityReport({...filters,user_id:7,expected_target_ref:target},actor);
    assert.deepEqual(JSON.parse(calls[0].init.body),{course_id:"backend",user_id:7});
    const query=new URL(calls[1].url,"http://localhost").searchParams;
    assert.equal(query.get("subject_ref"),target); assert.equal(query.has("user_id"),false); assert.equal(query.has("expected_target_ref"),false);
  } finally {globalThis.fetch=original;}
});
test("actor mismatch and invalid target mapping stop before Query", async () => {
  const original=globalThis.fetch; let count=0;
  try {
    globalThis.fetch=async()=>{count++;return response({...grant,subject_ref:target});};
    await assert.rejects(fetchActivityReport(filters,actor),e=>e.kind==="denied"); assert.equal(count,1);
    for (const responseGrant of [grant,{...grant,target_subject_ref:"not-uuid"},{...grant,target_subject_ref:target}]) {
      count=0;globalThis.fetch=async()=>{count++;return response(responseGrant);};
      const expected=responseGrant.target_subject_ref===target ? "denied" : "invalid_response";
      await assert.rejects(fetchActivityReport({...filters,user_id:7,expected_target_ref:actor},actor),e=>e.kind===expected); assert.equal(count,1);
    }
  } finally {globalThis.fetch=original;}
});
test("missing actor and contradictory targets fail before requesting a grant", async () => {
  const original=globalThis.fetch; let count=0; globalThis.fetch=async()=>{count++;throw Error("unexpected");};
  try {
    await assert.rejects(fetchActivityReport(filters,""),e=>e.kind==="denied");
    await assert.rejects(fetchActivityReport({...filters,user_id:7,subject_ref:target},actor),e=>e.kind==="invalid_response");
    assert.equal(count,0);
  } finally {globalThis.fetch=original;}
});

test("accepts canonical backend report fixtures without treating unknown coverage as zero", async () => {
  const {readFile} = await import("node:fs/promises");
  const original=globalThis.fetch;
  try {for (const name of ["reports-v1-unavailable.json","reports-v1-ok.json","reports-v1-observed.json"]) {
    const fixture=JSON.parse(await readFile(new URL(`../../../../../services/analytics/contracts/fixtures/${name}`,import.meta.url),"utf8"));
    const request={from_time:fixture.from_time,to_time:fixture.to_time,course_id:fixture.course_id};
    let calls=0;globalThis.fetch=async()=>response(++calls===1 ? grant : fixture);
    assert.deepEqual(await fetchActivityReport(request,actor),fixture);
  }} finally {globalThis.fetch=original;}
});

const detailRow = {level:"lesson",course_id:"backend",lesson_id:"lesson_42",...v};
const details = {report,lesson_id:"lesson_42",rows:[detailRow],next_offset:null};
test("details issues scoped grant and passes lesson and pagination only to Query", async () => {
  const original=globalThis.fetch;const calls=[];
  globalThis.fetch=async(url,init)=>{calls.push({url:String(url),init});return response(calls.length===1 ? {...grant,target_subject_ref:target} : details);};
  try {
    const query={...filters,lesson_id:"lesson_42",user_id:7,expected_target_ref:target,offset:0,limit:10};
    assert.deepEqual(await fetchActivityDetails(query,actor),details);
    assert.deepEqual(JSON.parse(calls[0].init.body),{course_id:"backend",user_id:7});
    const url=new URL(calls[1].url,"http://localhost");assert.match(url.pathname,/\/activity\/details$/);
    assert.equal(url.searchParams.get("lesson_id"),"lesson_42");assert.equal(url.searchParams.get("subject_ref"),target);
    assert.equal(url.searchParams.get("limit"),"10");assert.equal(url.searchParams.get("offset"),"0");
    assert.equal(url.searchParams.has("user_id"),false);assert.equal(calls[1].init.headers.Authorization,"Bearer secret.jwt.token");
  } finally {globalThis.fetch=original;}
});
test("details reject invalid paging, lesson without course and untrusted breakdown", async () => {
  const original=globalThis.fetch;let count=0;globalThis.fetch=async()=>{count++;return response(grant);};
  try {
    for (const query of [{...filters,offset:20001}, {...filters,limit:0}, {...filters,limit:101}, {...filters,course_id:null,lesson_id:"lesson_42"}])
      await assert.rejects(fetchActivityDetails(query,actor),e=>e.kind==="invalid_response");
    assert.equal(count,0);
    for (const invalid of [{...details,lesson_id:"other"},{...details,rows:[{...detailRow,course_id:"other"}]},{...details,rows:[{...detailRow,level:"course"}]},{...details,next_offset:900}]) {
      count=0;globalThis.fetch=async()=>response(++count===1 ? grant : invalid);
      await assert.rejects(fetchActivityDetails({...filters,lesson_id:"lesson_42",limit:10},actor),e=>e.kind==="invalid_response");assert.equal(count,2);
    }
  } finally {globalThis.fetch=original;}
});
test("legacy activity GET forwards an optional validated lesson", async () => {
  const original=globalThis.fetch;const calls=[];globalThis.fetch=async(url,init)=>{calls.push({url:String(url),init});return response(calls.length===1?grant:report);};
  try {await fetchActivityReport({...filters,lesson_id:"lesson_42"},actor);assert.equal(new URL(calls[1].url,"http://localhost").searchParams.get("lesson_id"),"lesson_42");}
  finally {globalThis.fetch=original;}
});

test("chapter report posts full scope using a verified grant and rejects duplicate membership", async () => {
  const original=globalThis.fetch, calls=[];
  const input={...filters,chapters:[{chapter_id:"chapter-1",lesson_ids:["lesson-1","lesson-2"]}]};
  const result={...filters,status:"ok",quality:"complete",as_of:"2026-11-16T10:00:00Z",chapters:[{chapter_id:"chapter-1",active_ms:60000,theory_ms:40000,practice_ms:20000}]};
  globalThis.fetch=async(url,init)=>{calls.push({url:String(url),init});return response(calls.length===1?grant:result);};
  try {
    assert.deepEqual(await fetchActivityChapters(input,actor),result);
    assert.match(calls[1].url,/reports\/activity\/chapters$/);
    assert.equal(calls[1].init.method,"POST"); assert.deepEqual(JSON.parse(calls[1].init.body),input);
    assert.equal(calls[1].init.headers.Authorization,"Bearer secret.jwt.token");
    await assert.rejects(fetchActivityChapters({...input,chapters:[...input.chapters,{chapter_id:"chapter-2",lesson_ids:["lesson-1"]}]},actor),e=>e.kind==="invalid_response");
    assert.equal(calls.length,2);
  } finally {globalThis.fetch=original;}
});

test("chapter report accepts opaque Unicode chapter IDs but retains lesson validation", async () => {
  const original=globalThis.fetch, calls=[];
  const input={...filters,chapters:[{chapter_id:"Глава 1 — основы",lesson_ids:["lesson-1"]}]};
  const result={...filters,status:"ok",quality:"complete",as_of:"2026-11-16T10:00:00Z",chapters:[{chapter_id:"Глава 1 — основы",active_ms:0,theory_ms:0,practice_ms:0}]};
  globalThis.fetch=async(url,init)=>{calls.push({url:String(url),init});return response(calls.length===1?grant:result);};
  try {
    assert.deepEqual(await fetchActivityChapters(input,actor),result);
    assert.deepEqual(JSON.parse(calls[1].init.body),input);
    await assert.rejects(fetchActivityChapters({...input,chapters:[{chapter_id:"Глава",lesson_ids:["урок"]}]},actor),e=>e.kind==="invalid_response");
    await assert.rejects(fetchActivityChapters({...input,chapters:[{chapter_id:"",lesson_ids:[]}]},actor),e=>e.kind==="invalid_response");
    assert.equal(calls.length,2);
  } finally {globalThis.fetch=original;}
});

const zeroOutlineTime = {active_ms:0,theory_ms:0,practice_ms:0};
const outlineTime = {active_ms:150000,theory_ms:90000,practice_ms:60000};
const outlineInput = {...filters,chapters:[{chapter_id:"Глава 1",lesson_ids:["lesson-1","lesson-2"]}]};
const outlineResult = {
  ...filters,
  status:"ok",quality:"partial",as_of:null,
  chapters:[{chapter_id:"Глава 1",...outlineTime}],
  lessons:[{lesson_id:"lesson-1",...outlineTime},{lesson_id:"lesson-2",...zeroOutlineTime}],
  unmapped_lessons:[{lesson_id:"retired",...outlineTime}],
};

test("lifetime outline is scoped to the course without sending chart dates", async () => {
  const original=globalThis.fetch, calls=[];
  const input={course_id:"backend",chapters:outlineInput.chapters};
  const result={...outlineResult,from_time:"2020-01-01T00:00:00Z",to_time:"2026-11-19T00:00:00Z"};
  globalThis.fetch=async(url,init)=>{calls.push({url:String(url),init});return response(calls.length===1 ? grant : result);};
  try {
    assert.deepEqual(await fetchLifetimeActivityOutline(input,actor),result);
    assert.deepEqual(JSON.parse(calls[0].init.body),{course_id:"backend"});
    assert.match(calls[1].url,/reports\/activity\/outline\/lifetime$/);
    assert.deepEqual(JSON.parse(calls[1].init.body),input);
    for (const invalid of [{...result,course_id:"other"},{...result,to_time:result.from_time},{...result,lessons:[]}]) {
      let count=0;
      globalThis.fetch=async()=>response(++count===1 ? grant : invalid);
      await assert.rejects(fetchLifetimeActivityOutline(input,actor),e=>e.kind==="invalid_response");
    }
  } finally {globalThis.fetch=original;}
});

test("the student dashboard gets separate no-store grants for the chosen learner and course", async () => {
  const original = globalThis.fetch, calls = [];
  const student = {user_id: 7, expected_target_ref: target};
  const selection = {course_id: filters.course_id, chapters: outlineInput.chapters};
  const outline = {...outlineResult, from_time: "2020-01-01T00:00:00Z"};
  globalThis.fetch = async (url, init) => {
    calls.push({url: String(url), init});
    if (calls.length % 2 === 1) return response({...grant, target_subject_ref: target.toUpperCase()});
    return response(calls.length === 2 ? report : outline);
  };
  try {
    assert.deepEqual(await fetchStudentActivityReport(filters, student, actor), report);
    assert.deepEqual(await fetchStudentLifetimeActivityOutline(selection, student, actor), outline);
    const [dailyGrant, dailyQuery, outlineGrant, outlineQuery] = calls;
    for (const request of [dailyGrant, outlineGrant]) {
      assert.match(request.url, /\/grants\/report$/);
      assert.deepEqual(JSON.parse(request.init.body), {course_id: "backend", ...student, expected_actor_ref: actor, student_statistics: true});
      assert.equal(request.init.credentials, "include");
    }
    const dailyUrl = new URL(dailyQuery.url, "http://localhost");
    assert.match(dailyUrl.pathname, /\/reports\/activity$/);
    assert.equal(dailyUrl.searchParams.get("subject_ref"), target.toUpperCase());
    assert.equal(dailyUrl.searchParams.get("course_id"), "backend");
    assert.match(outlineQuery.url, /\/reports\/activity\/outline\/lifetime\/student$/);
    assert.deepEqual(JSON.parse(outlineQuery.init.body), {...selection, subject_ref: target.toUpperCase()});
    for (const request of [dailyQuery, outlineQuery]) {
      assert.equal(request.init.credentials, "omit");
      assert.equal(request.init.headers.Authorization, "Bearer secret.jwt.token");
    }
    assert.ok(calls.every(({init}) => init.cache === "no-store"));
  } finally {globalThis.fetch = original;}
});

test("the student dashboard can never turn a missing target into a self-report", async () => {
  const original = globalThis.fetch;
  const student = {user_id: 7, expected_target_ref: target};
  let calls = 0;
  const requests = [
    target => fetchStudentActivityReport(filters, target, actor),
    target => fetchStudentLifetimeActivityOutline({course_id: "backend", chapters: outlineInput.chapters}, target, actor),
  ];
  try {
    for (const request of requests) {
      globalThis.fetch = async () => {calls++; return response(grant);};
      await assert.rejects(request(undefined), {kind: "invalid_response"});
      await assert.rejects(request({...student, expected_target_ref: ""}), {kind: "invalid_response"});
      assert.equal(calls, 0);
      for (const issued of [{...grant, target_subject_ref: actor}, {...grant, subject_ref: target, target_subject_ref: target}]) {
        globalThis.fetch = async () => {calls++; return response(issued);};
        await assert.rejects(request(student), {kind: "denied"});
        assert.equal(calls, 1);
        calls = 0;
      }
    }
  } finally {globalThis.fetch = original;}
});

test("one self-scoped outline returns exact observed timings including results beyond the old page limit", async () => {
  const original=globalThis.fetch, calls=[];
  const lessonIds=Array.from({length:150},(_,index)=>`lesson-${index}`);
  const input={...outlineInput,chapters:[{chapter_id:"Глава 1",lesson_ids:lessonIds}]};
  const result={...outlineResult,lessons:lessonIds.map((id,index)=>({lesson_id:id,...(index===149 ? outlineTime : zeroOutlineTime)}))};
  globalThis.fetch=async(url,init)=>{calls.push({url:String(url),init});return response(calls.length===1 ? grant : result);};
  try {
    const output=await fetchActivityOutline(input,actor);
    assert.deepEqual(output,result);
    assert.equal(output.lessons[149].practice_ms,60000);
    assert.equal(output.lessons[0].active_ms,0);
    assert.equal(output.unmapped_lessons[0].lesson_id,"retired");
    assert.equal(calls.length,2);
    assert.deepEqual(JSON.parse(calls[0].init.body),{course_id:"backend"});
    assert.match(calls[1].url,/reports\/activity\/outline$/);
    assert.equal(calls[1].init.method,"POST");
    assert.equal(calls[1].init.credentials,"omit");
    assert.deepEqual(JSON.parse(calls[1].init.body),input);
  } finally {globalThis.fetch=original;}
});

test("outline rejects incomplete, foreign or inconsistent results", async () => {
  const original=globalThis.fetch;
  try {
    const rows=outlineResult.lessons;
    for (const invalid of [
      {...outlineResult,course_id:"other"},
      {...outlineResult,to_time:"2026-11-17T21:00:00Z"},
      {...outlineResult,status:["ok"]},
      {...outlineResult,quality:["partial"]},
      {...outlineResult,lessons:[rows[0]]},
      {...outlineResult,lessons:[rows[0],rows[0]]},
      {...outlineResult,lessons:[rows[0],{...rows[1],lesson_id:"unexpected"}]},
      {...outlineResult,lessons:[rows[0],{...rows[1],active_ms:-1}]},
      {...outlineResult,lessons:[{...rows[0],active_ms:Number.MAX_SAFE_INTEGER+1},rows[1]]},
      {...outlineResult,lessons:[{...rows[0],theory_ms:0},rows[1]]},
      {...outlineResult,unmapped_lessons:[rows[1]]},
      {...outlineResult,unmapped_lessons:[outlineResult.unmapped_lessons[0],outlineResult.unmapped_lessons[0]]},
      {...outlineResult,chapters:[{chapter_id:"Глава 1",...zeroOutlineTime}]},
      {...outlineResult,chapters:[{chapter_id:"Глава 1",active_ms:null,theory_ms:null,practice_ms:null}]},
      {...outlineResult,status:"no_data",quality:"complete",as_of:grant.server_time},
    ]) {
      let count=0;
      globalThis.fetch=async()=>response(++count===1 ? grant : invalid);
      await assert.rejects(fetchActivityOutline(outlineInput,actor),e=>e.kind==="invalid_response");
      assert.equal(count,2);
    }
  } finally {globalThis.fetch=original;}
});

test("outline accepts explicit empty measurements and stops a changed identity before Query", async () => {
  const original=globalThis.fetch;
  const empty={...outlineResult,status:"no_data",quality:"complete",as_of:grant.server_time,
    chapters:[{chapter_id:"Глава 1",...zeroOutlineTime}],
    lessons:outlineResult.lessons.map(row=>({...row,...zeroOutlineTime})),unmapped_lessons:[]};
  try {
    let count=0;
    globalThis.fetch=async()=>response(++count===1 ? grant : empty);
    assert.deepEqual(await fetchActivityOutline(outlineInput,actor),empty);
    count=0;
    globalThis.fetch=async()=>{count++;return response({...grant,subject_ref:target});};
    await assert.rejects(fetchActivityOutline(outlineInput,actor),e=>e.kind==="denied");
    assert.equal(count,1);
  } finally {globalThis.fetch=original;}
});
