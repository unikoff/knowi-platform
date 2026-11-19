import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
const props = { actorRef: "actor", courseId: "backend", lessonId: "lesson-31", from: "2026-11-30", to: "2026-12-01" };

function compile(file, imports = {}) {
  const source = fs.readFileSync(new URL(file, import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const module = { exports: {} };
  new Function("require", "module", "exports", code)(name => Object.hasOwn(imports, name) ? imports[name] : require(name), module, module.exports);
  return module.exports;
}

function setup() {
  const state = [];
  const calls = [];
  let stateIndex = 0;
  let effect;
  const react = {
    useState(initial) {
      const index = stateIndex++;
      if (!(index in state)) state[index] = initial;
      return [state[index], value => { state[index] = value; }];
    },
    useEffect(next) { effect = next; },
  };
  const { CourseStatisticsLessonDetails } = compile("./CourseStatisticsLessonDetails.tsx", {
    react,
    "./course-statistics-model": compile("./course-statistics-model.ts"),
    "./course-statistics-analytics": compile("./course-statistics-analytics.ts"),
    "../analytics-reporting/client": {
      AnalyticsReportError: class extends Error {},
      fetchActivityReport: (filters, actor, signal) => new Promise(resolve => calls.push({ filters, actor, signal, resolve })),
    },
  });
  return { calls, mount: () => effect(), render(input = props) {
    stateIndex = 0;
    return renderToStaticMarkup(CourseStatisticsLessonDetails(input));
  } };
}

const totals = (theory, practice) => ({ active_ms: theory + practice, theory_ms: theory, practice_ms: practice });

test("the expanded lesson requests self-scoped time over the selected Moscow period and discards a canceled response", async () => {
  const { calls, render, mount } = setup();
  const loading = render();
  assert.match(loading, /class="course-stats__sr-only" role="status">Загружаем время занятия<\/span>/);
  assert.match(loading, /<dl aria-busy="true">/);
  assert.equal((loading.match(/class="course-stats__loading"/g) ?? []).length, 3);
  const close = mount();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].actor, props.actorRef);
  assert.deepEqual(calls[0].filters, {
    course_id: "backend", lesson_id: "lesson-31", from_time: "2026-11-29T21:00:00.000Z", to_time: "2026-12-01T21:00:00.000Z",
  });
  close();
  assert.equal(calls[0].signal.aborted, true);
  calls[0].resolve({ status: "ok", quality: "complete", totals: totals(60000, 120000) });
  await Promise.resolve();
  assert.match(render(), /Загружаем время занятия/);
});

test("partial stale and complete no-data reports display the recorded zero", async () => {
  const stale = setup();
  stale.render();
  stale.mount();
  stale.calls[0].resolve({ status: "stale", quality: "partial", totals: totals(60000, 0) });
  await Promise.resolve();
  assert.match(stale.render(), /Доступны прошлые замеры.*Данные за период неполные/);
  assert.match(stale.render(), /<dt>Практика<\/dt><dd>0 мин<\/dd>/);
  assert.match(stale.render(), /<dt>Теория<\/dt><dd>1 мин<\/dd>/);

  const empty = setup();
  empty.render();
  empty.mount();
  empty.calls[0].resolve({ status: "no_data", quality: "complete", totals: totals(0, 0) });
  await Promise.resolve();
  assert.match(empty.render(), /За этот период учебное время не зафиксировано/);
  assert.match(empty.render(), /<dt>Всего<\/dt><dd>0 мин<\/dd>/);
});

test("an unavailable lesson report does not display zero", async () => {
  const {calls, mount, render} = setup();
  render();
  mount();
  calls[0].resolve({status: "unavailable", quality: "unknown", totals: null});
  await Promise.resolve();
  assert.match(render(), /Не удалось загрузить время занятия/);
  assert.doesNotMatch(render(), /0 мин/);
  assert.doesNotMatch(render(), /class="course-stats__loading"/);
});

test("non-personal details display the existing lesson time without a network request", () => {
  const { calls, mount, render } = setup();
  const html = render({ ...props, actorRef: undefined, fallbackTime: { activeMs: 120000, theoryMs: 0, practiceMs: 120000 } });
  mount();
  assert.equal(calls.length, 0);
  assert.match(html, /<dt>Всего<\/dt><dd>2 мин<\/dd>/);
});
