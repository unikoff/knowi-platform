import assert from "node:assert/strict";
import test, {after, before} from "node:test";
import {createServer} from "vite";
let vite, deleteStudentWithStatus;
const target = {id:123, accountRef:"00112233-4455-4677-8899-aabbccddeeff"};
before(async () => {
  vite = await createServer({configFile:false, base:"/platform/", server:{middlewareMode:true, hmr:false}});
  ({deleteStudentWithStatus} = await vite.ssrLoadModule("/src/features/admin/students/deleteStudentRequest.ts"));
});
after(async () => {await vite?.close();});
const json = (body, status) => new Response(JSON.stringify(body), {status, headers:{"Content-Type":"application/json"}});

test("202 pending then manual 204 checks the same lifecycle and sends no automatic retry", async () => {
  const original = globalThis.fetch, calls=[];
  globalThis.fetch = async (url, options) => {calls.push({url,options}); return calls.length === 1 ? json({status:"pending"},202) : new Response(null,{status:204});};
  try {
    const signal = new AbortController().signal;
    assert.equal(await deleteStudentWithStatus(target, signal),"pending");
    assert.equal(calls.length,1);
    assert.equal(await deleteStudentWithStatus(target, signal),"complete");
    assert.equal(calls.length,2);
    for (const call of calls) {
      assert.equal(call.url,"/platform/api/admin/users/123"); assert.equal(call.options.method,"DELETE");
      assert.equal(call.options.credentials,"include"); assert.equal(call.options.cache,"no-store");
      assert.equal(new Headers(call.options.headers).get("X-Account-Ref"),target.accountRef);
    }
  } finally {globalThis.fetch=original;}
});

test("legacy 200 is complete and does not require a reference", async () => {
  const original=globalThis.fetch; let headers;
  globalThis.fetch=async (_url, options) => {headers=new Headers(options.headers); return json({deleted:123},200);};
  try { assert.equal(await deleteStudentWithStatus({id:123},new AbortController().signal),"complete"); assert.equal(headers.has("X-Account-Ref"),false); }
  finally {globalThis.fetch=original;}
});

test("bad or failed responses never become success", async () => {
  const original=globalThis.fetch;
  try {
    for (const response of [json({status:"pending"},200),json({status:"done"},202),json({deleted:456},200),new Response(null,{status:503})]) {
      globalThis.fetch=async () => response;
      await assert.rejects(deleteStudentWithStatus(target,new AbortController().signal));
    }
    globalThis.fetch=async () => json({detail:"Account changed"},409);
    await assert.rejects(deleteStudentWithStatus(target,new AbortController().signal),/Account changed/);
  } finally {globalThis.fetch=original;}
});

test("abort ignores a late successful response even if transport ignores the signal", async () => {
  const original=globalThis.fetch; let release, observedSignal;
  globalThis.fetch=(_url, options) => {observedSignal=options.signal; return new Promise(resolve=>{release=resolve;});};
  try {
    const controller=new AbortController();
    const pending=deleteStudentWithStatus(target,controller.signal);
    controller.abort(); assert.equal(observedSignal.aborted,true);
    release(new Response(null,{status:204}));
    await assert.rejects(pending,{name:"AbortError"});
  } finally {globalThis.fetch=original;}
});

test("already aborted request never reaches the server", async () => {
  const original=globalThis.fetch; let calls=0;
  globalThis.fetch=async () => {calls++; return new Response(null,{status:204});};
  try {const controller=new AbortController(); controller.abort(); await assert.rejects(deleteStudentWithStatus(target,controller.signal),{name:"AbortError"}); assert.equal(calls,0);}
  finally {globalThis.fetch=original;}
});
