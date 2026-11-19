import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import {createRequire} from "node:module";
import ts from "typescript";
const require=createRequire(import.meta.url);
const source=fs.readFileSync(new URL("./courseLessonCatalog.ts",import.meta.url),"utf8").replace("import.meta.env.BASE_URL", '"/platform/"');
const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
const out={exports:{}}; class ApiError extends Error { constructor(message, status) {super(message);this.status=status;} }
new Function("require","module","exports",code)((name)=>name === "../../../shared/api/client" ? {ApiError} : require(name),out,out.exports);
const {fetchCourseLessons}=out.exports;
const json=(body)=>new Response(JSON.stringify(body),{status:200,headers:{"Content-Type":"application/json"}});
test("loads and deduplicates named lessons for the explicit course",async()=>{
  const original=globalThis.fetch; let url,opts;
  globalThis.fetch=async (u,o)=>{url=String(u);opts=o;return json({tracks:[{id:"ch1"},{id:"ch2"}],courses:{ch1:[{id:"l1",title:" Урок 1 "}],ch2:[{id:"l1",title:"Повтор"},{id:"l2",title:"Урок 2"}]}});};
  try {const got=await fetchCourseLessons("backend_copy",new AbortController().signal); assert.equal(url,"/platform/api/learning/catalog?course_id=backend_copy"); assert.equal(opts.credentials,"include"); assert.equal(opts.cache,"no-store"); assert.deepEqual(got,[{id:"l1",title:"Урок 1"},{id:"l2",title:"Урок 2"}]);}
  finally {globalThis.fetch=original;}
});
test("aborted and malformed catalogue never become lesson options",async()=>{
  const original=globalThis.fetch; let calls=0, release;
  try {
    globalThis.fetch=async()=>{calls++;return json({tracks:[{id:"ch"}],courses:{}});};
    await assert.rejects(fetchCourseLessons("backend",new AbortController().signal),/Некорректный каталог/);
    const stopped=new AbortController();stopped.abort();await assert.rejects(fetchCourseLessons("backend",stopped.signal),{name:"AbortError"});assert.equal(calls,1);
    globalThis.fetch=()=>new Promise(resolve=>{release=resolve;});
    const controller=new AbortController(), pending=fetchCourseLessons("backend",controller.signal);controller.abort();release(json({tracks:[],courses:{}}));
    await assert.rejects(pending,{name:"AbortError"});
  } finally {globalThis.fetch=original;}
});


test("access failure is preserved for the UI and does not retry",async()=>{
  const original=globalThis.fetch; let calls=0;
  globalThis.fetch=async()=>{calls++;return new Response(null,{status:403});};
  try {await assert.rejects(fetchCourseLessons("backend",new AbortController().signal),e=>e instanceof ApiError && e.status===403);assert.equal(calls,1);}
  finally {globalThis.fetch=original;}
});
