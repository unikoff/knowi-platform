import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import {createRequire} from "node:module";
import ts from "typescript";
const require=createRequire(import.meta.url);
const source=fs.readFileSync(new URL("./courseActivityPeriod.ts",import.meta.url),"utf8");
const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
const output={exports:{}}; new Function("require","module","exports",code)(require,output,output.exports);
const {courseActivityPeriod}=output.exports;
test("periods are bounded Moscow calendar days including today",()=>{
  const before=Date.parse("2026-11-16T20:59:59.999Z"), after=Date.parse("2026-11-16T21:00:00.000Z");
  assert.deepEqual(courseActivityPeriod(1,before),{from_time:"2026-11-15T21:00:00.000Z",to_time:"2026-11-16T21:00:00.000Z"});
  assert.deepEqual(courseActivityPeriod(1,after),{from_time:"2026-11-16T21:00:00.000Z",to_time:"2026-11-17T21:00:00.000Z"});
  for(const days of [1,7,30]) {const p=courseActivityPeriod(days,after); assert.equal(Date.parse(p.to_time)-Date.parse(p.from_time),days*86400000);}
});
