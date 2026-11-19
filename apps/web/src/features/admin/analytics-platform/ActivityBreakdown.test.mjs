import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {createServer} from "vite";
const vite = await createServer({configFile:false, optimizeDeps:{noDiscovery:true,include:[]}, server:{middlewareMode:true, hmr:false}});
const {ActivityBreakdown} = await vite.ssrLoadModule("/src/features/admin/analytics-platform/ActivityBreakdown.tsx");
test.after(async()=>{await vite.close();});
const metrics={unique_visitors:0,active_ms:0,theory_ms:0,practice_ms:0};
const report={status:"ok",quality:"unknown"};
const course={level:"course",course_id:"backend",lesson_id:null,...metrics};
const lesson={level:"lesson",course_id:"backend",lesson_id:"lesson_1",unique_visitors:2,active_ms:60000,theory_ms:60000,practice_ms:0};
const props={courses:[{id:"backend",title:"Python"}],offset:0,onPrevious(){},onNext(){}};
const render=(data,extra={})=>renderToStaticMarkup(React.createElement(ActivityBreakdown,{...props,...extra,data}));
test("course and lesson rows retain independent server counts and durations",()=>{
  const html=render({report,rows:[course,lesson],next_offset:null});
  assert.match(html,/Курс: Python/);assert.match(html,/Урок: lesson_1/);
  assert.match(html,/<dt>Посетители<\/dt><dd>—<\/dd>/);
  assert.match(html,/<dt>Посетители<\/dt><dd>2<\/dd>/);
  assert.match(html,/<dt>Теория<\/dt><dd>1 мин<\/dd>/);
  assert.match(html,/Посетители в разных строках могут совпадать/);
});
test("complete coverage displays proven zeros; unknown empty rows do not assert no activity",()=>{
  assert.match(render({report:{status:"ok",quality:"complete"},rows:[course],next_offset:null}),/<dt>Посетители<\/dt><dd>0<\/dd>/);
  assert.match(render({report,rows:[],next_offset:null}),/покрытие неполное или неизвестно/);
});
test("stale, unavailable and no_data hide all breakdown rows",()=>{
  for(const status of ["stale","unavailable","no_data"]) assert.equal(render({report:{status,quality:"unknown"},rows:[lesson],next_offset:null}),"");
});
test("page controls reflect server next offset and current position",()=>{
  const first=render({report,rows:[lesson],next_offset:20});
  assert.match(first,/Следующие/);assert.doesNotMatch(first,/Предыдущие/);
  const second=render({report,rows:[lesson],next_offset:null},{offset:20});
  assert.match(second,/Предыдущие/);assert.doesNotMatch(second,/Следующие/);
});
