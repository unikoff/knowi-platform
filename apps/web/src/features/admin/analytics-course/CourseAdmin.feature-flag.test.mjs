import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import {createRequire} from "node:module";
import {createElement} from "react";
import {renderToStaticMarkup} from "react-dom/server";
import ts from "typescript";
const source = fs.readFileSync(new URL("../CourseAdmin.tsx", import.meta.url), "utf8");
const ast = ts.createSourceFile("CourseAdmin.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const panelsSource = fs.readFileSync(new URL("../students/CourseStudentPanels.tsx", import.meta.url), "utf8");
const panelsAst = ts.createSourceFile("CourseStudentPanels.tsx", panelsSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function visit(node, found) { found.push(node); ts.forEachChild(node, (child) => { visit(child, found); }); return found; }
const nodes = visit(ast, []);
test("the course admin uses the analytics opt-in and no longer imports the separate activity details", () => {
  const binding = nodes.find((node) => ts.isVariableDeclaration(node) && node.name.getText(ast) === "analyticsReportsEnabled");
  assert.ok(binding && ts.isVariableDeclarationList(binding.parent) && (binding.parent.flags & ts.NodeFlags.Const));
  assert.equal(binding.initializer.getText(ast), 'import.meta.env.VITE_ANALYTICS_REPORTS_ENABLED === "true"');
  assert.equal(nodes.filter((node) => ts.isIdentifier(node) && ["CourseActivitySection", "fetchActivityReport", "fetchActivityDetails"].includes(node.text)).length, 0);
  assert.ok(nodes.some((node) => ts.isJsxSelfClosingElement(node) && node.tagName.getText(ast) === "CourseOverviewDashboard"));
});

test("the student calendar requests measured activity only when both flags are enabled", () => {
  const flag = name => nodes.find(node => ts.isVariableDeclaration(node) && node.name.getText(ast) === name).getText(ast);
  const calendar = nodes.find(node => ts.isJsxSelfClosingElement(node) && node.tagName.getText(ast) === "CourseStudentPanels");
  const mode = calendar.attributes.properties.find(node => ts.isJsxAttribute(node) && node.name.getText(ast) === "visitsMode");
  const resolveMode = new Function("env", "analyticsNeedsSearch", `
    const ${flag("analyticsReportsEnabled").replaceAll("import.meta.env", "env")};
    const ${flag("calendarActivityEnabled").replaceAll("import.meta.env", "env")};
    return ${mode.initializer.expression.getText(ast)};
  `);
  for (const reports of ["true", undefined]) {
    for (const personal of ["true", undefined]) {
      const env = {VITE_ANALYTICS_REPORTS_ENABLED: reports, VITE_ANALYTICS_PERSONAL_MEASUREMENTS_ENABLED: personal};
      for (const needsSearch of [false, true]) {
        const expected = reports && personal ? (needsSearch ? "search-required" : "enabled") : "disabled";
        assert.equal(resolveMode(env, needsSearch), expected);
      }
    }
  }
});

test("the existing student actions render only inside the subscription disclosure", () => {
  const actions = nodes.filter(node => ts.isJsxSelfClosingElement(node) && node.tagName.getText(ast) === "StudentActions");
  assert.equal(actions.length, 1);
  const slot = actions[0].parent.parent;
  assert.ok(ts.isJsxAttribute(slot) && slot.name.getText(ast) === "subscriptionActions");
  assert.equal(slot.parent.parent.tagName.getText(ast), "CourseStudentPanels");
  assert.deepEqual(actions[0].attributes.properties.map(attribute => attribute.name.getText(ast)).sort(),
    ["base", "busy", "month", "perform", "person", "quota"]);

  const slots = visit(panelsAst, []).filter(node => ts.isJsxExpression(node) && node.expression
    && visit(node.expression, []).some(value => ts.isIdentifier(value) && value.text === "subscriptionActions"));
  assert.equal(slots.length, 1);
  let container = slots[0].parent;
  while (container && !(ts.isJsxElement(container) && container.openingElement.tagName.getText(panelsAst) === "StudentProfileDisclosure")) {
    container = container.parent;
  }
  const title = container?.openingElement.attributes.properties.find(attribute => ts.isJsxAttribute(attribute) && attribute.name.getText(panelsAst) === "title");
  assert.equal(title?.initializer?.text, "Подписка");
});

function createStudentActions() {
  const definition = nodes.find(node => ts.isFunctionDeclaration(node) && node.name?.text === "StudentActions");
  const {outputText} = ts.transpileModule(definition.getText(ast), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX},
  });
  const commands = [];
  let draft;
  const reason = {
    validationError: "",
    setCustomValidity(message) { this.validationError = message; },
    reportValidity() { return !this.validationError; },
  };
  const context = {
    useState: (initial) => [draft ??= initial, (value) => { draft = value; }],
    useRef: () => ({current: reason}),
    useId: () => "student-actions",
    getMonthTitle: () => "Ноябрь 2026 г.",
    State: () => null,
    subscriptionAndQuotaSuffix: "/subscription-and-quota",
  };
  const component = new Function("require", "exports", ...Object.keys(context), `${outputText}\nreturn StudentActions;`)(
    createRequire(import.meta.url), {}, ...Object.values(context),
  );
  function render({busy = false, quotaReady = true} = {}) {
    const tree = component({
      base: "/admin/courses/backend",
      person: {id: 11, account_ref: "learner-ref"},
      month: {year: 2026, month: 11},
      quota: {ready: quotaReady},
      busy,
      perform: async (...command) => { commands.push(command); },
    });
    const inputs = tree.props.children[1].props.children.map((label) => label.props.children[1].props);
    const submit = () => tree.props.onSubmit({preventDefault() {}});
    return {tree, inputs, submit, button: tree.props.children[3].props};
  }
  function fill(lessons, days, justification) {
    for (const [index, value] of [lessons, days, justification].entries()) {
      render().inputs[index].onChange({target: {value}, currentTarget: reason});
    }
  }
  return {commands, reason, render, fill};
}

test("student management validates both quantities and the reason before sending one combined command", () => {
  const actions = createStudentActions();
  const {tree, inputs} = actions.render();
  const html = renderToStaticMarkup(tree);
  assert.equal((html.match(/<form\b/g) ?? []).length, 1);
  assert.equal((html.match(/type="submit"/g) ?? []).length, 1);
  assert.doesNotMatch(html, /aria-expanded|<details/);
  assert.match(html, /Добавить занятия и продлить подписку/);
  assert.ok(inputs.every((input) => input.required && input.value === ""));
  assert.equal(inputs[0].max, 100);
  assert.equal(inputs[1].max, 3650);

  for (const [lessons, days] of [["", "17"], ["101", "17"], ["1.5", "17"], ["4", ""], ["4", "3651"], ["4", "1.5"]]) {
    actions.fill(lessons, days, "Решение ведущего");
    actions.render().submit();
  }
  actions.fill("4", "17", "   ");
  actions.render().submit();
  assert.equal(actions.reason.validationError, "Укажите причину.");
  assert.equal(actions.commands.length, 0);

  actions.fill("4", "17", "  Решение ведущего  ");
  actions.render().submit();
  assert.deepEqual(actions.commands.map(command => command.slice(0, 3)), [
    ["/admin/courses/backend/users/11/subscription-and-quota", {year: 2026, month: 11, add: 4, days: 17, reason: "Решение ведущего"}, "learner-ref"],
  ]);
});

test("the combined action locks submission while busy or quota is loading and retains the draft until success", () => {
  const actions = createStudentActions();
  actions.fill("4", "17", "Общее решение");

  const busy = actions.render({busy: true});
  assert.ok(busy.inputs.every((input) => input.readOnly));
  assert.equal(busy.button["aria-disabled"], true);
  busy.submit();
  assert.equal(actions.commands.length, 0);

  const loadingQuota = actions.render({quotaReady: false});
  assert.equal(loadingQuota.button["aria-disabled"], true);
  loadingQuota.submit();
  assert.equal(actions.commands.length, 0);

  actions.render().submit();
  assert.deepEqual(actions.render().inputs.map(({value}) => value), ["4", "17", "Общее решение"]);
  actions.render().submit();
  assert.deepEqual(actions.commands[1].slice(0, 3), actions.commands[0].slice(0, 3));
  actions.commands[1][3]();
  assert.deepEqual(actions.render().inputs.map(({value}) => value), ["", "", ""]);
  actions.render().submit();
  assert.equal(actions.commands.length, 2);
});

test("subscription commands stay available while loading or on a technical failure and disappear after access denial", () => {
  const definition = visit(panelsAst, []).find(node => ts.isFunctionDeclaration(node) && node.name?.text === "CourseStudentPanels");
  assert.ok(definition);
  const {outputText} = ts.transpileModule(definition.getText(panelsAst), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX},
  });
  class StudentProfileReadError extends Error {
    constructor(kind) {
      super(kind);
      this.kind = kind;
    }
  }
  const render = (result) => {
    const context = {
      useState: () => [false, () => {}],
      useCallback: callback => callback,
      useStudentPanelRead: () => ({result, retry: () => {}}),
      StudentProfileDisclosure: ({title, children}) => title === "Подписка" ? children : null,
      CourseStudentCalendar: () => null,
      StudentPanelFeedback: () => createElement("p", null, "Не удалось загрузить подписку"),
      StudentProfileReadError,
    };
    const module = {exports: {}};
    new Function("require", "exports", ...Object.keys(context), outputText)(createRequire(import.meta.url), module.exports, ...Object.values(context));
    return renderToStaticMarkup(createElement(module.exports.CourseStudentPanels, {
      month: {year: 2026, month: 11}, target: {id: 1, account_ref: "ref"},
      subscriptionActions: createElement("button", null, "Продлить подписку"),
    }));
  };
  assert.match(render({status: "loading"}), /Продлить подписку/);
  for (const kind of ["invalid", "unavailable"]) {
    assert.match(render({status: "error", error: new StudentProfileReadError(kind)}), /Продлить подписку/);
  }
  for (const kind of ["denied", "search-required"]) {
    const html = render({status: "error", error: new StudentProfileReadError(kind)});
    assert.match(html, /Не удалось загрузить подписку/);
    assert.doesNotMatch(html, /Продлить подписку/);
  }
});

test("the statistics disclosure mounts only after opening and retains its content when collapsed", () => {
  const disclosure = fs.readFileSync(new URL("../students/StudentProfileDisclosure.tsx", import.meta.url), "utf8");
  const module = {exports: {}};
  const {outputText} = ts.transpileModule(disclosure, {
    compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX},
  });
  const require = createRequire(import.meta.url);
  const mutableState = {value: undefined};
  const useState = (initial) => {
    mutableState.value ??= initial;
    return [mutableState.value, (value) => { mutableState.value = value; }];
  };
  new Function("require", "exports", outputText)(name => name === "react" ? {useState} : require(name), module.exports);
  const content = createElement("div", null, "Учебный прогресс");
  const render = () => module.exports.StudentProfileDisclosure({title: "Статистика ученика", children: content});

  const collapsed = render();
  const html = renderToStaticMarkup(collapsed);
  assert.match(html, /<details[^>]*><summary[^>]*><span>Статистика ученика<\/span>/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b|Учебный прогресс/);

  collapsed.props.onToggle({currentTarget: {open: true}});
  const firstOpen = render();
  assert.match(renderToStaticMarkup(firstOpen), /Учебный прогресс/);
  firstOpen.props.onToggle({currentTarget: {open: false}});
  assert.equal(render().props.children[1].props.children, content);
});
