import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import ts from "typescript";
function compile(file, imports = {}) {
  const code = ts.transpileModule(fs.readFileSync(new URL(file, import.meta.url), "utf8"), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
  const module = {exports:{}};
  new Function("require","module","exports",code)(name => imports[name],module,module.exports);
  return module.exports;
}
function setup() {
  const states=[],refs=[],effects=[],callbacks=[],calls=[]; let si=0,ri=0,ei=0,ci=0;
  const previous=globalThis.window; globalThis.window=new EventTarget();
  const react={useState:initial=>{const i=si++; if (!(i in states)) states[i]=initial; return [states[i],value=>{states[i]=typeof value==="function"?value(states[i]):value;}];},useRef:initial=>refs[ri++] ??= {current:initial},useCallback:(fn,deps)=>{const i=ci++,old=callbacks[i];if(old && deps.length===old.deps.length && deps.every((v,j)=>Object.is(v,old.deps[j])))return old.fn;callbacks[i]={fn,deps};return fn;},useEffect:(fn,deps)=>{const i=ei++,old=effects[i];if(old && deps.length===old.deps.length && deps.every((v,j)=>Object.is(v,old.deps[j])))return;old?.cleanup?.();effects[i]={deps,cleanup:fn()};}};
  const request=(name,...args)=>new Promise((resolve,reject)=>calls.push({name,args,resolve,reject}));
  const api={getActivitySummary:()=>Promise.resolve({}),getMeasuredActivity:()=>new Promise(()=>{}),getBookingContext:(...a)=>request("context",...a),getStudentSlots:(...a)=>request("scoped-list",...a),getLegacyStudentSlots:(...a)=>request("legacy-list",...a),bookLegacyStudentSlot:(...a)=>request("legacy-book",...a),cancelLegacyStudentBooking:(...a)=>request("legacy-cancel",...a)};
  const {createActivityRead}=compile("./activityRead.ts");
  const {useAccountSchedule}=compile("./useAccountSchedule.ts",{react,"./api":api,"../admin/scopedApi":{command:(...a)=>request("command",...a)},"../../shared/api/client":{ApiError:class extends Error{}},"./activityRead":{createActivityRead}});
  return {calls,render:(user=1,course="backend",mode="legacy",month=1)=>{si=ri=ei=ci=0;return useAccountSchedule(user,course,{year:2026,month},"actor",`auth:${user}`,true,mode);},close:()=>{effects.forEach(x=>x?.cleanup?.());globalThis.window=previous;}};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const slot={id:3,date:"2026-01-02",start_time:"12:00",duration_minutes:60,status:"free",can_cancel:false};
test("legacy backend lists, books and cancels through legacy endpoints",async()=>{const s=setup();try{ s.render();assert.equal(s.calls[0].name,"legacy-list");s.calls[0].resolve({slots:[slot],quota:null});await flush();let view=s.render();assert.deepEqual(view.bookingSlots,[slot]);const booking=view.bookSlot(3);assert.equal(s.calls[1].name,"legacy-book");s.calls[1].resolve({});await flush();assert.equal(s.calls[2].name,"legacy-list");s.calls[2].resolve({slots:[slot],quota:null});await booking;view=s.render();const cancel=view.cancelSlotBooking(3);assert.equal(s.calls[3].name,"legacy-cancel");s.calls[3].resolve({});await flush();s.calls[4].resolve({slots:[],quota:null});await cancel;}finally{s.close();}});
test("unknown mode and legacy secondary courses fail closed",async()=>{const s=setup();try{let view=s.render(1,"backend",null);await assert.rejects(view.bookSlot(3));view=s.render(1,"backend_copy","legacy");await assert.rejects(view.bookSlot(3));assert.deepEqual(s.calls,[]);assert.deepEqual(view.bookingSlots,[]);}finally{s.close();}});
test("legacy errors stay visible and never fall back to scoped",async()=>{const s=setup();try{s.render();s.calls[0].reject(Error("offline"));await flush();const view=s.render();assert.equal(view.bookingError,"offline");assert.deepEqual(view.bookingSlots,[]);assert.equal(s.calls.length,1);}finally{s.close();}});
test("late list and stale callbacks cannot cross user, course or mode",async()=>{const s=setup();try{const old=s.render();s.render(2,"backend","scoped");s.calls[1].resolve({active_owner_id:7,active_owner_name:"Owner",my_upcoming_bookings:[]});await flush();assert.equal(s.calls[2].name,"scoped-list");s.calls[2].resolve({slots:[],quota:null});s.calls[0].resolve({slots:[slot],quota:null});await flush();assert.deepEqual(s.render(2,"backend","scoped").bookingSlots,[]);await assert.rejects(old.bookSlot(3));s.render(2,"other","legacy");assert.equal(s.calls.length,3);}finally{s.close();}});
test("in-flight mutation does not reload after mode changes",async()=>{const s=setup();try{s.render();s.calls[0].resolve({slots:[slot],quota:null});await flush();const old=s.render();const pending=old.bookSlot(3);s.render(1,"backend",null);s.calls[1].resolve({});await pending;assert.equal(s.calls.length,2);}finally{s.close();}});
test("legacy API paths and request bodies match the existing contract",()=>{const calls=[];const api=compile("./api.ts",{"../../shared/api/client":{apiRequest:(...args)=>calls.push(args)}});api.getLegacyStudentSlots({year:2026,month:2});api.bookLegacyStudentSlot(3);api.cancelLegacyStudentBooking(3);assert.deepEqual(calls,[["/user/slots?year=2026&month=2"],["/user/slots/3/book",{}],["/user/slots/3/cancel",{}]]);});
