import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import {createRequire} from "node:module";
import ts from "typescript";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
const require=createRequire(import.meta.url);
const source=fs.readFileSync(new URL("./CourseActivityDetails.tsx",import.meta.url),"utf8");
const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
const output={exports:{}}; new Function("require","module","exports",code)(require,output,output.exports);
const {CourseActivityDetails}=output.exports;
const row={level:"lesson",course_id:"backend",lesson_id:"l1",unique_visitors:1,active_ms:30000,theory_ms:30000,practice_ms:0};
const render=(report,rows=[row],next_offset=null,offset=0) => renderToStaticMarkup(React.createElement(CourseActivityDetails,{details:{report,rows,next_offset},lessons:[{id:"l1",title:"Введение"}],selectedLesson:"",onSelectLesson:()=>{},offset,onPrevious:()=>{},onNext:()=>{}}));
test("lesson drilldown renders named, bounded page without treating unknown zero as fact",()=>{
  const html=render({status:"ok",quality:"unknown"},[row],20);
  assert.match(html,/Введение/); assert.match(html,/Активное: 30 с/); assert.match(html,/Практика: —/);
  assert.match(html,/отсутствие времени не подтверждает отсутствие активности/);
  assert.match(html,/Назад/); assert.match(html,/Далее/);
});
test("stale and unavailable reports never show previous detail rows",()=>{
  for(const status of ["stale","unavailable"]) assert.equal(render({status,quality:"unknown"}),"");
});
test("empty unknown and partial results do not assert measured zero",()=>{
  assert.match(render({status:"ok",quality:"unknown"},[]),/полнота данных неизвестна/);
  assert.match(render({status:"ok",quality:"partial"},[]),/данные неполные/);
});


test("course row in a lesson-filtered report is not labelled as the whole course",()=>{
  const courseRow={...row,level:"course",lesson_id:null};
  const html=renderToStaticMarkup(React.createElement(CourseActivityDetails,{details:{report:{status:"ok",quality:"complete"},rows:[courseRow],next_offset:null},lessons:[],selectedLesson:"l1",onSelectLesson:()=>{},offset:0,onPrevious:()=>{},onNext:()=>{}}));
  assert.match(html,/Итог по выбранному уроку/); assert.doesNotMatch(html,/Весь курс/);
});

test("page controls enable the appropriate direction and pass the next offset", () => {
  const events=[];
  const makePage = (offset,next_offset) => CourseActivityDetails({details:{report:{status:"ok",quality:"complete"},rows:[row],next_offset},lessons:[],selectedLesson:"",offset,onSelectLesson:()=>{},onPrevious:()=>events.push("previous"),onNext:value=>events.push(value)});
  const controls = element => {
    const nav = React.Children.toArray(element.props.children).find(child=>child.type === "nav");
    return React.Children.toArray(nav.props.children);
  };
  const [firstPrevious,firstNext] = controls(makePage(0,20));
  assert.equal(firstPrevious.props.disabled,true); assert.equal(firstNext.props.disabled,false);
  firstNext.props.onClick();
  const [lastPrevious,lastNext] = controls(makePage(20,null));
  assert.equal(lastPrevious.props.disabled,false); assert.equal(lastNext.props.disabled,true);
  lastPrevious.props.onClick();
  assert.deepEqual(events,[20,"previous"]);
});
