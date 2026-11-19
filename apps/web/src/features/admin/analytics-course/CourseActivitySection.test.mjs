import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
const require = createRequire(import.meta.url);
function compile(path, imports = {}) {
  const code = ts.transpileModule(fs.readFileSync(new URL(path, import.meta.url), "utf8"), {compilerOptions: {jsx:ts.JsxEmit.ReactJSX, module:ts.ModuleKind.CommonJS, target:ts.ScriptTarget.ES2020}}).outputText;
  const output = {exports:{}};
  new Function("require", "module", "exports", code)((name) => name.endsWith(".css") ? {} : (imports[name] ?? require(name)), output, output.exports);
  return output.exports;
}
const panel = compile("./CourseAnalyticsPanel.tsx");
const report = compile("./CourseActivityReport.tsx", {"./CourseAnalyticsPanel":panel});
const period = compile("./courseActivityPeriod.ts");
const details = compile("./CourseActivityDetails.tsx");
const {CourseActivitySection, activityScopeDescription} = compile("./CourseActivitySection.tsx", {"./CourseAnalyticsPanel":panel, "./CourseActivityReport":report, "./CourseActivityDetails":details, "./courseActivityPeriod":period, "./courseActivityDetailsRequest":{}, "./courseLessonCatalog":{}});
const render = (props) => renderToStaticMarkup(React.createElement(CourseActivitySection, {courseId:"backend", actorRef:"actor", actorRole:"leader", authRevision:1, fetchDetails:()=>{}, ...props}));

test("missing current identity fails closed", () => {
  assert.match(render({actorRef:undefined}), /Нет доступа к статистике/);
  assert.match(render({authRevision:undefined}), /Нет доступа к статистике/);
  assert.match(render({target:{userId:5, accountRef:""}}), /Нет доступа к статистике/);
});

test("admin and leader can load their scoped aggregate; roster member remains targeted", () => {
  assert.match(render({actorRole:"admin"}), /Учебная активность курса/);
  assert.match(render({actorRole:"admin"}), /Загружаем статистику/);
  assert.match(render({actorRole:"admin", target:{userId:5,accountRef:"target"}}), /Учебная активность ученика/);
  assert.match(render({actorRole:"leader"}), /Учебная активность курса/);
  assert.match(render({actorRole:"leader"}), /Загружаем статистику/);
});


test("scope descriptions distinguish administrator, leader and selected learner", () => {
  assert.equal(activityScopeDescription("admin", false), "Все пользователи курса");
  assert.equal(activityScopeDescription("leader", false), "Люди с сохранёнными записями на курс");
  assert.equal(activityScopeDescription("leader", true), "Выбранный ученик курса");
});


test("period choices and lesson selector are available for both aggregate and roster target", () => {
  for (const props of [{actorRole:"leader"}, {actorRole:"leader", target:{userId:5,accountRef:"target"}}]) {
    const html = render(props);
    assert.match(html, /Период активности \(Москва\)/); assert.match(html, />День<\/button>/);
    assert.match(html, />7 дней<\/button>/); assert.match(html, />30 дней<\/button>/);
    assert.match(html, /Все уроки/); assert.match(html, /Загружаем список уроков/);
  }
});
