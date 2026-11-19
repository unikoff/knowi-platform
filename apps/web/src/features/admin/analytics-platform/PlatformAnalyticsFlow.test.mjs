import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import ts from "typescript";
import {createServer} from "vite";

// Runs the real component functions with a small deterministic hook scheduler; not a browser or React DOM.
const vite = await createServer({configFile:false,optimizeDeps:{noDiscovery:true,include:[]},server:{middlewareMode:true,hmr:false}});
const client = await vite.ssrLoadModule("/src/features/analytics-reporting/client.ts");
test.after(async()=>vite.close());
const source = fs.readFileSync(new URL("./PlatformAnalytics.tsx", import.meta.url),"utf8");
const code = ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
const actor="01234567-89ab-4cde-8fab-0123456789ab";
const grant={grant:"fixture-token",subject_ref:actor,server_time:"2026-11-17T10:00:00Z",expires_at:"2026-11-17T10:01:00Z"};
const response=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{"content-type":"application/json"}});
const jsx=(type,props)=>({type,props:props??{}});
const marker=name=>Object.defineProperty(()=>null,"name",{value:name});
const Report=marker("ActivityReport"), Breakdown=marker("ActivityBreakdown"), Filters=marker("ActivityReportFilters"), State=marker("AnalyticsReportState");
function createHarness() {
  const slots=[], effects=[]; let cursor=0, queued=[];
  const hooks={
    useState(initial) {const index=cursor++;if (!(index in slots)) slots[index]=typeof initial==="function"?initial():initial;return [slots[index],value=>{slots[index]=typeof value==="function"?value(slots[index]):value;}];},
    useEffect(callback,deps) {const index=cursor++;const old=effects[index];if (!old || deps.some((value,i)=>!Object.is(value,old.deps[i]))) queued.push({index,callback,deps});},
  };
  const modules={"react":hooks,"react/jsx-runtime":{jsx,jsxs:jsx,Fragment:Symbol("Fragment")},"../../analytics-reporting/client":client,
    "./ActivityReport":{ActivityReport:Report},"./ActivityBreakdown":{ActivityBreakdown:Breakdown},
    "./ActivityReportFilters":{ActivityReportFilters:Filters},"./AnalyticsReportState":{AnalyticsReportState:State}};
  const require=id=>{if (!(id in modules)) throw Error(`Unexpected ${id}`);return modules[id];};
  const output={exports:{}};new Function("require","module","exports",code)(require,output,output.exports);
  let props={actorRef:actor,courseId:"",scopeLabel:"Все курсы",courses:[{id:"backend",title:"Python"},{id:"backend_extra",title:"Extra"}]};
  return {
    render(next={}) {props={...props,...next};cursor=0;queued=[];const tree=output.exports.PlatformAnalytics(props);for(const item of queued){effects[item.index]?.cleanup?.();effects[item.index]={deps:item.deps,cleanup:item.callback()};}return tree;},
    close(){for(const effect of effects) effect?.cleanup?.();},
  };
}
function find(tree,type) {
  if (!tree || typeof tree!=="object") return undefined;
  if (tree.type===type) return tree;
  const children=tree.props?.children;
  for(const child of Array.isArray(children)?children:[children]) {const found=find(child,type);if(found) return found;}
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function detailsFor(url, count=20, next=null) {
  const p=new URL(url,"http://local").searchParams, course=p.get("course_id");
  const zero={unique_visitors:0,active_ms:0,theory_ms:0,practice_ms:0};
  return {report:{schema_version:1,status:"ok",quality:"unknown",as_of:null,lag_seconds:null,timezone:"Europe/Moscow",metric_version:"activity-v1",course_id:course,from_time:p.get("from_time"),to_time:p.get("to_time"),totals:zero,daily:[],trusted_progress:null},lesson_id:null,
    rows:Array.from({length:count},(_,i)=>({level:"lesson",course_id:course??"backend",lesson_id:`lesson_${Number(p.get("offset"))+i}`,...zero})),next_offset:next};
}

test("forced report 401/403 hide old values and offer no retry; 503 offers retry",async()=>{
  const original=globalThis.fetch;
  try {for(const status of [401,403,503]) {
    const calls=[];globalThis.fetch=async(url,init)=>{calls.push({url:String(url),init});return calls.length===1?response(grant):response({},status);};
    const h=createHarness();let tree=h.render();assert.equal(find(tree,State).props.state.status,"loading");
    await flush();tree=h.render();const state=find(tree,State).props.state;
    assert.equal(state.status,"error");assert.equal(typeof state.retry,status===503?"function":"undefined");
    assert.equal(find(tree,Report),undefined);assert.equal(find(tree,Breakdown),undefined);assert.equal(calls.length,2);
    if(status===503){state.retry();tree=h.render();assert.equal(find(tree,State).props.state.status,"loading");}
    h.close();
  }} finally {globalThis.fetch=original;}
});

test("more than 20 rows paginate, clear old content, and reset offset on scope change",async()=>{
  const original=globalThis.fetch, calls=[];
  globalThis.fetch=async(url,init)=>{const path=String(url);calls.push({url:path,init});if(path.includes("/grants/")) return response(grant);const offset=Number(new URL(path,"http://local").searchParams.get("offset"));return response(detailsFor(path,offset===0?20:5,offset===0?20:null));};
  const h=createHarness();
  try {
    let tree=h.render();await flush();tree=h.render();let breakdown=find(tree,Breakdown);assert.equal(breakdown.props.data.rows.length,20);assert.equal(breakdown.props.data.next_offset,20);
    breakdown.props.onNext(20);tree=h.render();assert.equal(find(tree,State).props.state.status,"loading");assert.equal(find(tree,Breakdown),undefined);
    await flush();tree=h.render();breakdown=find(tree,Breakdown);assert.equal(breakdown.props.offset,20);assert.equal(breakdown.props.data.rows.length,5);assert.equal(breakdown.props.data.next_offset,null);
    breakdown.props.onPrevious();tree=h.render();assert.equal(find(tree,State).props.state.status,"loading");await flush();tree=h.render();assert.equal(find(tree,Breakdown).props.offset,0);
    find(tree,Breakdown).props.onNext(20);h.render();await flush();h.render();
    tree=h.render({courseId:"backend_extra",scopeLabel:"Extra"});assert.equal(find(tree,State).props.state.status,"loading");assert.equal(find(tree,Breakdown),undefined);
    await flush();tree=h.render();assert.equal(find(tree,Breakdown).props.offset,0);assert.equal(find(tree,Breakdown).props.data.rows[0].course_id,"backend_extra");
    const queries=calls.filter(x=>x.url.includes("/details?")).map(x=>new URL(x.url,"http://local").searchParams);
    assert.deepEqual(queries.map(x=>x.get("offset")),["0","20","0","20","0"]);assert.equal(queries.at(-1).get("course_id"),"backend_extra");
  } finally {h.close();globalThis.fetch=original;}
});

test("late previous course reply cannot replace the new scope",async()=>{
  const original=globalThis.fetch;let releaseOld;const old=new Promise(resolve=>{releaseOld=resolve;});let oldSignal;
  globalThis.fetch=async(url,init)=>{const path=String(url);if(path.includes("/grants/")) return response(grant);const q=new URL(path,"http://local").searchParams;if(q.get("course_id")==="backend"){oldSignal=init.signal;await old;}return response(detailsFor(path,1,null));};
  const h=createHarness();
  try {
    let tree=h.render({courseId:"backend",scopeLabel:"Python"});await flush();
    tree=h.render({courseId:"backend_extra",scopeLabel:"Extra"});assert.equal(find(tree,State).props.state.status,"loading");assert.equal(oldSignal.aborted,true);
    await flush();tree=h.render();assert.equal(find(tree,Breakdown).props.data.rows[0].course_id,"backend_extra");
    releaseOld();await flush();tree=h.render();assert.equal(find(tree,Breakdown).props.data.rows[0].course_id,"backend_extra");
  } finally {releaseOld();h.close();globalThis.fetch=original;}
});

test("scope change followed by denial or outage never renders previous data; retry recovers only outage",async()=>{
  const original=globalThis.fetch;
  try {for(const status of [401,403,503]) {
    let fail=true, queryCalls=0;
    globalThis.fetch=async(url)=>{const path=String(url);if(path.includes("/grants/"))return response(grant);queryCalls++;const course=new URL(path,"http://local").searchParams.get("course_id");if(course==="backend_extra"&&fail)return response({},status);return response(detailsFor(path,1,null));};
    const h=createHarness();
    try {
      let tree=h.render({courseId:"backend",scopeLabel:"Python"});await flush();tree=h.render();assert.equal(find(tree,Breakdown).props.data.rows[0].course_id,"backend");
      tree=h.render({courseId:"backend_extra",scopeLabel:"Extra"});assert.equal(find(tree,Breakdown),undefined);assert.equal(find(tree,State).props.state.status,"loading");
      await flush();tree=h.render();const state=find(tree,State).props.state;assert.equal(state.status,"error");assert.equal(find(tree,Breakdown),undefined);
      assert.equal(typeof state.retry,status===503?"function":"undefined");
      if(status===503){fail=false;state.retry();tree=h.render();assert.equal(find(tree,State).props.state.status,"loading");await flush();tree=h.render();assert.equal(find(tree,Breakdown).props.data.rows[0].course_id,"backend_extra");assert.equal(queryCalls,3);}
      else assert.equal(queryCalls,2);
    } finally {h.close();}
  }} finally {globalThis.fetch=original;}
});

test("editing period clears page data and applies Moscow dates as a bounded UTC interval",async()=>{
  const original=globalThis.fetch,calls=[];
  globalThis.fetch=async(url,init)=>{const path=String(url);calls.push({url:path,init});if(path.includes("/grants/"))return response(grant);const offset=Number(new URL(path,"http://local").searchParams.get("offset"));return response(detailsFor(path,offset===0?20:5,offset===0?20:null));};
  const h=createHarness();
  try {
    let tree=h.render();await flush();tree=h.render();find(tree,Breakdown).props.onNext(20);h.render();await flush();tree=h.render();assert.equal(find(tree,Breakdown).props.offset,20);
    const edited={...find(tree,Filters).props.values,fromDate:"2026-11-01",toDateExclusive:"2026-11-03"};
    find(tree,Filters).props.onChange(edited);tree=h.render();assert.equal(find(tree,Breakdown),undefined);assert.equal(find(tree,State).props.state.status,"unavailable");
    find(tree,Filters).props.onApply(edited);tree=h.render();assert.equal(find(tree,State).props.state.status,"loading");await flush();tree=h.render();assert.equal(find(tree,Breakdown).props.offset,0);
    const last=new URL(calls.filter(x=>x.url.includes("/details?")).at(-1).url,"http://local").searchParams;
    assert.equal(last.get("from_time"),"2026-10-31T21:00:00.000Z");assert.equal(last.get("to_time"),"2026-11-02T21:00:00.000Z");assert.equal(last.get("offset"),"0");
  } finally {h.close();globalThis.fetch=original;}
});

test("reapplying the same period starts one request, aborts the previous one and ignores its late reply",async()=>{
  const original=globalThis.fetch;let releaseOld;const pending=new Promise(resolve=>{releaseOld=resolve;});const reports=[];
  globalThis.fetch=async(url,init)=>{
    const path=String(url);if(path.includes("/grants/"))return response(grant);
    reports.push({path,signal:init.signal});const n=reports.length;
    if(n===2) await pending;
    const data=detailsFor(path,1,null);data.rows[0].lesson_id=`request_${n}`;return response(data);
  };
  const h=createHarness();
  try {
    let tree=h.render();await flush();tree=h.render();assert.equal(find(tree,Breakdown).props.data.rows[0].lesson_id,"request_1");
    find(tree,Filters).props.onApply(find(tree,Filters).props.values);tree=h.render();
    assert.equal(find(tree,State).props.state.status,"loading");assert.equal(find(tree,Breakdown),undefined);
    await flush();assert.equal(reports.length,2);
    find(tree,Filters).props.onApply(find(tree,Filters).props.values);tree=h.render();
    assert.equal(find(tree,State).props.state.status,"loading");assert.equal(reports[1].signal.aborted,true);
    await flush();tree=h.render();assert.equal(reports.length,3);assert.equal(find(tree,Breakdown).props.data.rows[0].lesson_id,"request_3");
    releaseOld();await flush();tree=h.render();assert.equal(find(tree,Breakdown).props.data.rows[0].lesson_id,"request_3");assert.equal(reports.length,3);
  } finally {releaseOld();h.close();globalThis.fetch=original;}
});

test("reapplying the same period retries a stale or unavailable report",async()=>{
  const original=globalThis.fetch;
  try {for(const status of ["stale","unavailable"]) {
    let calls=0;
    globalThis.fetch=async(url)=>{const path=String(url);if(path.includes("/grants/"))return response(grant);calls++;const data=detailsFor(path,0,null);if(calls===1){data.report.status=status;data.report.totals=null;}return response(data);};
    const h=createHarness();
    try {
      let tree=h.render();await flush();tree=h.render();assert.equal(find(tree,Report).props.report.status,status);
      find(tree,Filters).props.onApply(find(tree,Filters).props.values);tree=h.render();assert.equal(find(tree,State).props.state.status,"loading");assert.equal(find(tree,Report),undefined);
      await flush();tree=h.render();assert.equal(find(tree,Report).props.report.status,"ok");assert.equal(calls,2);
    } finally {h.close();}
  }} finally {globalThis.fetch=original;}
});
