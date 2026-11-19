import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
const require = createRequire(import.meta.url);
const source = fs.readFileSync(new URL("./courseActivityRequest.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {compilerOptions: {module:ts.ModuleKind.CommonJS, target:ts.ScriptTarget.ES2020}}).outputText;
const output = {exports:{}};
new Function("require", "module", "exports", code)(require, output, output.exports);
const {startCourseActivityRequest} = output.exports;
const filters = {course_id:"backend", from_time:"2026-10-18T12:00:00.000Z", to_time:"2026-11-17T12:00:00.000Z", timezone:"Europe/Moscow"};
const response = {...filters, status:"ok"};
const tick = () => new Promise((resolve) => setImmediate(resolve));
function deferred() { let resolve, reject; const promise = new Promise((yes,no) => {resolve=yes; reject=no;}); return {promise, resolve, reject}; }

test("superseded scope is aborted and late replies are ignored", async () => {
  const old = deferred(), calls = []; let signal;
  const cancel = startCourseActivityRequest({filters, expectedActorRef:"actor-ref", fetchReport: (_filters, _actor, s) => {signal=s; return old.promise;}, onReport: (x) => calls.push(x), onFailure: (x) => calls.push(x)});
  await tick(); cancel(); assert.equal(signal.aborted, true);
  old.resolve(response); await tick(); assert.deepEqual(calls, []);
});

test("only matching course, period and timezone can render", async () => {
  for (const patch of [{course_id:"backend_copy"}, {from_time:"2026-10-19T12:00:00Z"}, {to_time:"invalid"}, {timezone:"UTC"}]) {
    const calls = [];
    startCourseActivityRequest({filters, expectedActorRef:"actor-ref", fetchReport: async () => ({...response,...patch}), onReport: () => calls.push("report"), onFailure: (x) => calls.push(x)});
    await tick(); assert.deepEqual(calls, ["error"]);
  }
  const calls = [];
  startCourseActivityRequest({filters, expectedActorRef:"actor-ref", fetchReport: async () => ({...response, from_time:"2026-10-18T12:00:00Z"}), onReport: () => calls.push("report"), onFailure: (x) => calls.push(x)});
  await tick(); assert.deepEqual(calls, ["report"]);
});

test("access failures do not trigger another grant request", async () => {
  for (const failure of [{status:401},{status:403},{kind:"denied"}]) {
    let attempts=0; const calls=[];
    startCourseActivityRequest({filters, expectedActorRef:"actor-ref", fetchReport: async () => {attempts++; throw failure;}, onReport: () => calls.push("report"), onFailure: (x) => calls.push(x)});
    await tick(); assert.equal(attempts,1); assert.deepEqual(calls,["access"]);
  }
});

test("timeout aborts once and discards a later successful response", async () => {
  const delayed=deferred(), calls=[]; let signal;
  startCourseActivityRequest({filters, expectedActorRef:"actor-ref", timeoutMs:10, fetchReport: (_filters,_actor,s) => {signal=s; return delayed.promise;}, onReport: () => calls.push("report"), onFailure: (x) => calls.push(x)});
  await new Promise((resolve) => setTimeout(resolve,30));
  assert.equal(signal.aborted,true); assert.deepEqual(calls,["timeout"]);
  delayed.resolve(response); await tick(); assert.deepEqual(calls,["timeout"]);
});

test("cancellation before dispatch never requests a grant", async () => {
  let attempts = 0;
  const cancel = startCourseActivityRequest({filters, expectedActorRef:"actor-ref", fetchReport: async () => {attempts++; return response;}, onReport: () => assert.fail("unexpected report"), onFailure: () => assert.fail("unexpected failure")});
  cancel(); await tick(); assert.equal(attempts, 0);
});


test("targeted request requires a roster lifecycle and passes both guards to the client", async () => {
  const calls = [];
  startCourseActivityRequest({filters:{...filters,user_id:5}, expectedActorRef:"actor-ref", fetchReport: async () => {calls.push("sent"); return response;}, onReport: () => calls.push("report"), onFailure: (x) => calls.push(x)});
  await tick(); assert.deepEqual(calls,["access"]);
  calls.length = 0;
  startCourseActivityRequest({filters:{...filters,user_id:5,expected_target_ref:"target-ref"}, expectedActorRef:"actor-ref", fetchReport: async (scope, actor, _signal) => {calls.push([scope.user_id,actor,scope.expected_target_ref]); return response;}, onReport: () => calls.push("report"), onFailure: (x) => calls.push(x)});
  await tick(); assert.deepEqual(calls,[[5,"actor-ref","target-ref"],"report"]);
});
