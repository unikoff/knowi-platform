import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
const require=createRequire(import.meta.url);
const source=fs.readFileSync(new URL("./DeletionStatus.tsx",import.meta.url),"utf8");
const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
const output={exports:{}}; new Function("require","module","exports",code)(require,output,output.exports);
const {DeletionStatus}=output.exports;
const render=(state)=>renderToStaticMarkup(React.createElement(DeletionStatus,{state,onCheck:()=>{},onBack:()=>{}}));
const target={id:123,accountRef:"opaque-ref"};

test("pending is not complete and offers only a manual same-target check",()=>{
  const html=render({target,status:"pending"});
  assert.match(html,/Аккаунт удалён/); assert.match(html,/Ожидаем подтверждения удаления статистики/);
  assert.match(html,/Проверить статус удаления/); assert.doesNotMatch(html,/удаление завершено/i);
});
test("unknown outcome stays an error and no reference means no retry",()=>{
  const html=render({target:{id:123},status:"unknown",error:"Нет ответа"});
  assert.match(html,/role="alert"/); assert.match(html,/Результат удаления неизвестен/);
  assert.match(html,/Идентификатор учётной записи недоступен/); assert.doesNotMatch(html,/Проверить статус удаления/);
});
test("in-flight status exposes no extra retry",()=>{
  const html=render({target,status:"checking"});
  assert.match(html,/role="status"/); assert.doesNotMatch(html,/Проверить статус удаления/);
});


test("a failed status check retains the known pending state",()=>{
  const html=render({target,status:"pending",error:"Нет ответа"});
  assert.match(html,/Аккаунт удалён/); assert.match(html,/Ожидаем подтверждения удаления статистики/);
  assert.match(html,/Не удалось проверить статус/); assert.match(html,/Проверить статус удаления/);
});
