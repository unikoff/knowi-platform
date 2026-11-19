import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import { createRequire } from "node:module";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const source = fs.readFileSync(new URL("./OperationRow.tsx", import.meta.url), "utf8");
const code = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const output = { exports: {} };
new Function("require", "module", "exports", code)(require, output, output.exports);
const { OperationRow } = output.exports;
const item = { command_id: "internal-command-id", actor_id: 4, action: "", course_id: "internal-course-id", reason: "По обращению", created_at: "2026-11-15T10:00:00Z", anonymized: false };
const actions = new Map([
  ["quota.grant", "Выдача занятий"], ["subscription.extend.course", "Продление общей подписки"],
  ["subscription.extend.platform", "Продление общей подписки"], ["subscription.reset", "Снятие общей подписки"],
  ["assignment.grant", "Назначение ведущего"], ["assignment.revoke", "Отзыв ведущего"], ["admin.role", "Изменение роли администратора"],
  ["slot.create", "Создание слота"], ["slot.archive", "Архивация слота"], ["slot.transfer", "Перенос слотов"],
  ["booking.book", "Запись на занятие"], ["booking.cancel", "Отмена записи"],
]);

test("audit renders the actual R4/R5 action labels, scope title and human-entered reason", () => {
  for (const [action, label] of actions) {
    const html = renderToStaticMarkup(React.createElement(OperationRow, { item: { ...item, action }, courseTitle: "Курс по Python" }));
    assert.ok(html.includes(label), action); assert.ok(html.includes("Курс по Python")); assert.ok(html.includes("По обращению"));
    assert.ok(!html.includes(action)); assert.ok(!html.includes(item.command_id)); assert.ok(!html.includes(item.course_id));
  }
});

test("unknown and anonymized audit records do not expose internal identifiers or redacted reason", () => {
  const html = renderToStaticMarkup(React.createElement(OperationRow, { item: { ...item, action: "__proto__", reason: "secret", anonymized: true } }));
  assert.match(html, /Другое действие/); assert.match(html, /Курс недоступен/); assert.match(html, /Данные удалены/);
  assert.doesNotMatch(html, /__proto__|internal-course-id|internal-command-id|secret/);
});
