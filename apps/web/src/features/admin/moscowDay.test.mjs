import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync(new URL("./moscowDay.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
const output = {exports:{}};
new Function("module","exports",code)(output,output.exports);
const {moscowDate,watchMoscowDay} = output.exports;

test("Moscow midnight and returning from a hidden tab each refresh once without polling", () => {
  const original = {now:Date.now,setTimeout:globalThis.setTimeout,clearTimeout:globalThis.clearTimeout,document:globalThis.document};
  let now=Date.parse("2026-11-16T20:59:59Z"), id=0, refreshes=0, listener;
  const timers=new Map();
  Date.now=()=>now;
  globalThis.setTimeout=(fn,delay)=>{const handle=++id;timers.set(handle,{fn,due:now+delay});return handle;};
  globalThis.clearTimeout=handle=>timers.delete(handle);
  globalThis.document={visibilityState:"visible",addEventListener:(name,fn)=>{assert.equal(name,"visibilitychange");listener=fn;},removeEventListener:(name,fn)=>{assert.equal(name,"visibilitychange");assert.equal(fn,listener);listener=undefined;}};
  const advance=target=>{while(true){const next=[...timers.entries()].sort((a,b)=>a[1].due-b[1].due)[0];if(!next || next[1].due>target)break;timers.delete(next[0]);now=next[1].due;next[1].fn();}now=target;};
  try {
    const stop=watchMoscowDay(()=>refreshes++);
    assert.equal(moscowDate(),"2026-11-16");assert.equal(timers.size,1);
    advance(Date.parse("2026-11-16T21:00:00Z"));assert.equal(moscowDate(),"2026-11-17");assert.equal(refreshes,1);assert.equal(timers.size,1);
    document.visibilityState="hidden";listener();
    advance(Date.parse("2026-11-17T21:00:00Z"));assert.equal(refreshes,1);assert.equal(timers.size,1);
    document.visibilityState="visible";listener();assert.equal(refreshes,2);assert.equal(timers.size,1);
    advance(Date.parse("2026-11-18T20:59:59Z"));assert.equal(refreshes,2);
    stop();assert.equal(timers.size,0);assert.equal(listener,undefined);
  } finally {
    Date.now=original.now;globalThis.setTimeout=original.setTimeout;globalThis.clearTimeout=original.clearTimeout;
    if(original.document===undefined)delete globalThis.document;else globalThis.document=original.document;
  }
});
