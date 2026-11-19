import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
const require = createRequire(import.meta.url);
const source = fs.readFileSync(new URL("./CourseAnalyticsPanel.tsx", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const output = { exports: {} };
new Function("require", "module", "exports", code)(require, output, output.exports);
const { CourseAnalyticsPanel, CourseAnalyticsMetric } = output.exports;
const render = (state) => renderToStaticMarkup(React.createElement(CourseAnalyticsPanel, { title: "Статистика курса", state }));

test("loading, error, empty and unavailable remain distinct", () => {
  assert.match(render({kind: "loading"}), /role="status".*Загружаем/);
  const error = render({kind: "error", retry: () => {}});
  assert.match(error, /role="alert"/); assert.match(error, /Повторить/);
  assert.match(render({kind: "empty"}), /За выбранный период данных нет/);
  assert.match(render({kind: "unavailable"}), /Статистика пока недоступна/);
});

test("partial and unknown coverage remain visible even with a value", () => {
  const metric = React.createElement(CourseAnalyticsMetric, {label: "Посетители", value: "0"});
  assert.match(render({kind: "ready", coverage: "partial", children: metric}), /Данные за период неполные/);
  assert.match(render({kind: "ready", coverage: "unknown", children: metric}), /Полнота данных неизвестна/);
  assert.doesNotMatch(render({kind: "ready", coverage: "complete", children: metric}), /неполные|неизвестна/);
});

test("missing value differs from a measured zero", () => {
  const missing = renderToStaticMarkup(React.createElement(CourseAnalyticsMetric, {label: "Время", value: null}));
  const zero = renderToStaticMarkup(React.createElement(CourseAnalyticsMetric, {label: "Время", value: "0"}));
  assert.match(missing, /Данные недоступны/); assert.match(missing, />—</);
  assert.match(zero, />0</); assert.doesNotMatch(zero, /Данные недоступны/);
});
