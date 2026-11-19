import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const range = {courseId: "backend", from: "2026-11-13", to: "2026-11-19"};

function load(filename, {modules = {}, globals = {}} = {}) {
  const source = fs.readFileSync(new URL(filename, import.meta.url), "utf8")
    .replace("import.meta.env.BASE_URL", '"/stage/"');
  const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
  const module = {exports: {}};
  new Function("require", "module", "exports", ...Object.keys(globals), code)(name => modules[name], module, module.exports, ...Object.values(globals));
  return module.exports;
}

test("the client preserves zero, validates request scope, and rejects inaccessible or malformed reports", async () => {
  const empty = {course_id: range.courseId, from: range.from, to: range.to, completed_lessons: 0};
  let reply = Response.json(empty);
  const calls = [];
  const {fetchPeriodCompletion} = load("./period-completion-api.ts", {globals: {fetch: async (url, options) => {
    calls.push({url, options});
    return reply;
  }}});
  const signal = new AbortController().signal;
  assert.equal(await fetchPeriodCompletion(range, signal), 0);
  const {url, options} = calls[0];
  const endpoint = new URL(url, "https://example.test");
  assert.equal(endpoint.pathname, "/stage/api/learning/progress/completed-count");
  assert.deepEqual(Object.fromEntries(endpoint.searchParams), {course_id: range.courseId, from: range.from, to: range.to});
  assert.equal(options.credentials, "include");
  assert.equal(options.cache, "no-store");
  assert.equal(options.signal, signal);
  for (const payload of [{...empty, completed_lessons: -1}, {...empty, completed_lessons: "0"}, {...empty, to: "2026-11-20"}, {...empty, course_id: "backend_copy"}, null]) {
    reply = Response.json(payload);
    await assert.rejects(fetchPeriodCompletion(range, signal));
  }
  reply = Response.json(empty, {status: 403});
  await assert.rejects(fetchPeriodCompletion(range, signal));
});

test("the hook isolates course, dates and account and refreshes after returning to statistics", async () => {
  const states = [];
  const effects = [];
  const refs = [];
  let stateIndex = 0, effectIndex = 0, refIndex = 0;
  const calls = [];
  const surface = new EventTarget();
  const react = {
    useState(initial) {
      const index = stateIndex++;
      if (!(index in states)) states[index] = initial;
      return [states[index], next => { states[index] = next; }];
    },
    useRef(initial) {
      const index = refIndex++;
      return refs[index] ??= {current: initial};
    },
    useEffect(effect, deps) {
      const index = effectIndex++;
      const previous = effects[index];
      if (previous && deps.every((dependency, position) => Object.is(dependency, previous.deps[position]))) return;
      previous?.cleanup();
      effects[index] = {deps, cleanup: effect()};
    },
  };
  const client = {fetchPeriodCompletion: (scope, signal) => new Promise((resolve, reject) => calls.push({scope, signal, resolve, reject}))};
  const {usePeriodCompletion} = load("./usePeriodCompletion.ts", {modules: {react, "./period-completion-api": client}, globals: {window: surface}});
  const render = (scope, actor = "student-a", enabled = true, attempt = 0) => {
    stateIndex = effectIndex = refIndex = 0;
    return usePeriodCompletion(enabled, actor, scope, attempt);
  };

  try {
    assert.equal(render(range, "student-a", false), undefined);
    assert.equal(calls.length, 0);
    assert.deepEqual(render(range), {from: range.from, to: range.to, count: null, loading: true});
    const previousWeek = {...range, from: "2026-11-06", to: "2026-11-12"};
    assert.equal(render(previousWeek).loading, true);
    assert.equal(calls[0].signal.aborted, true);
    const otherCourse = {...previousWeek, courseId: "backend_copy"};
    assert.equal(render(otherCourse).loading, true);
    assert.equal(calls[1].signal.aborted, true);
    assert.equal(render(otherCourse, "student-b").loading, true);
    assert.equal(calls[2].signal.aborted, true);
    calls[3].resolve(0);
    calls[0].resolve(20);
    await Promise.resolve();
    assert.equal(render(otherCourse, "student-b").count, 0);
    surface.dispatchEvent(new Event("pymentor:personal-analytics-closed"));
    assert.equal(calls[3].signal.aborted, true);
    assert.equal(render(range, "student-b").loading, true);
    assert.equal(calls.length, 4);
    surface.dispatchEvent(new Event("pymentor:personal-analytics-opened"));
    assert.deepEqual(calls[4].scope, range);
    calls[4].reject(new Error("503"));
    await Promise.resolve();
    assert.deepEqual(render(range, "student-b"), {from: range.from, to: range.to, count: null});
    surface.dispatchEvent(new Event("pymentor:personal-analytics-opened"));
    calls[5].resolve(1);
    await Promise.resolve();
    assert.equal(render(range, "student-b").count, 1);
    const month = {...range, from: "2026-11-01", to: "2026-11-30"};
    assert.equal(render(month, "student-b").loading, true);
    assert.equal(calls.length, 7);
    calls[6].resolve(5); await Promise.resolve();
    assert.equal(render(month, "student-b").count, 5);
    assert.equal(render(range, "student-b").count, 1);
    assert.equal(render(month, "student-b").count, 5);
    assert.equal(calls.length, 7);
    assert.equal(render(range, "student-b", true, 1).loading, true);
    assert.equal(calls.length, 8);
    calls[7].resolve(2); await Promise.resolve();
    assert.equal(render(range, "student-b", true, 1).count, 2);
    assert.equal(render(range, "student-a", true, 1).loading, true);
    assert.equal(calls.length, 9);
    render(range, "student-a", false, 1);
    assert.equal(render(range, "student-a", true, 1).loading, true);
    assert.equal(calls.length, 10);
    calls[8].resolve(99); await Promise.resolve();
    assert.equal(render(range, "student-a", true, 1).loading, true);
  } finally {
    effects.forEach(effect => effect.cleanup());
  }
});
