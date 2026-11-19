import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const source = fs.readFileSync(new URL("./AnalyticsReportState.tsx", import.meta.url), "utf8");
const code = ts.transpileModule(source, {compilerOptions: {jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
const output = {exports: {}};
new Function("require", "module", "exports", code)(require, output, output.exports);
const render = (state) => renderToStaticMarkup(React.createElement(output.exports.AnalyticsReportState, {title: "Учебная активность", state}, React.createElement("strong", null, "123")));

test("loading and errors do not show stale report values", () => {
  const loading = render({status: "loading"});
  assert.match(loading, /role="status"/);
  assert.doesNotMatch(loading, /123/);
  const error = render({status: "error", message: "Источник недоступен", retry() {}});
  assert.match(error, /role="alert"/);
  assert.match(error, /Повторить/);
  assert.doesNotMatch(error, /123/);
});

test("empty and unavailable never pretend to be zero or show old values", () => {
  for (const state of [{status: "empty", message: "Нет событий"}, {status: "unavailable", message: "Данные недоступны"}]) {
    const html = render(state);
    assert.ok(html.includes(state.message));
    assert.doesNotMatch(html, /123|<strong>0<\/strong>/);
  }
});

test("ready content can expose caller-provided freshness and partial coverage", () => {
  const html = render({status: "ready", freshness: "Обновлено в 12:00", partial: "Данные неполные"});
  assert.match(html, /123/);
  assert.match(html, /Обновлено в 12:00/);
  assert.match(html, /Данные неполные/);
});
