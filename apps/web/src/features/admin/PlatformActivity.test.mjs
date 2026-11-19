import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const source = fs.readFileSync(new URL("./PlatformActivity.tsx", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const output = { exports: {} };
new Function("require", "module", "exports", code)(id => id === "./moscowDay" ? { moscowDate: () => "2026-11-16" } : require(id), output, output.exports);
const render = (activity) => renderToStaticMarkup(React.createElement(output.exports.PlatformActivity, { activity, scope: "Все курсы" }));

test("today's unique count marks partial Moscow coverage", () => {
  const html = render({date: "2026-11-16", unique_users: 3, coverage_since: "2026-11-15T22:00:00Z", partial_day: true});
  assert.match(html, /<strong>3<\/strong>/);
  assert.match(html, /Данные с 16\.11\.2026, 01:00 \(Москва\)/);
});

test("unavailable activity is not presented as zero", () => {
  const html = render({date: "2026-11-16", unique_users: null, coverage_since: null, partial_day: false});
  assert.match(html, /<strong>—<\/strong>/);
  assert.match(html, /недоступны/);
});

test("complete coverage can show a real zero", () => {
  const html = render({date: "2026-11-16", unique_users: 0, coverage_since: "2026-11-15T00:00:00Z", partial_day: false});
  assert.match(html, /<strong>0<\/strong>/);
  assert.match(html, /Уникальные посетители за сегодня/);
});

 test("global activity remains visible when coverage metadata is absent", () => {
  const html = render({date: "2026-11-16", unique_users: 3, coverage_since: null, partial_day: false});
  assert.match(html, /<strong>3<\/strong>/);
});

test("yesterday's count is unavailable rather than today's value", () => {
  const html = render({date: "2026-11-15", unique_users: 42, coverage_since: null, partial_day: false});
  assert.match(html, /<strong>—<\/strong>/);
  assert.match(html, /недоступны/);
  assert.doesNotMatch(html, /<strong>42<\/strong>/);
});
