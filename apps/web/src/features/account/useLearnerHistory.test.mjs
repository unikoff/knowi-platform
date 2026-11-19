import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import ts from "typescript";

test("range, actor and leaving cancel reads and late results cannot replace current history", async () => {
  const states = [], effects = [], calls = []; let si = 0, ei = 0;
  const react = {
    useState(initial) {const i = si++; if (!(i in states)) states[i] = initial; return [states[i], value => {states[i] = typeof value === "function" ? value(states[i]) : value;}];},
    useEffect(fn, deps) {const i = ei++, old = effects[i]; if (old && deps.every((v, n) => Object.is(v, old.deps[n]))) return; old?.cleanup?.(); effects[i] = {deps, cleanup: fn()};},
  };
  const source = fs.readFileSync(new URL("./useLearnerHistory.ts", import.meta.url), "utf8");
  const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
  const module = {exports: {}}, originalWindow = globalThis.window;
  globalThis.window = new EventTarget();
  const client = {fetchLearnerHistory: (from, to, signal) => new Promise((resolve, reject) => calls.push({signal, resolve, reject}))};
  new Function("require", "module", "exports", code)(name => name === "react" ? react : client, module, module.exports);
  const render = (...args) => {si = 0; ei = 0; return module.exports.useLearnerHistory(...args);};
  const args = [true, "actor-a", "2025-01-01", "2025-01-07", 0];
  try {
    assert.equal(render(...args).status, "loading");
    render(true, "actor-a", "2025-01-02", "2025-01-07", 0);
    assert.equal(calls[0].signal.aborted, true);
    render(true, "actor-b", "2025-01-02", "2025-01-07", 0);
    assert.equal(calls[1].signal.aborted, true);
    calls[2].resolve([{date: "current"}]); await new Promise(resolve => setImmediate(resolve));
    calls[0].resolve([{date: "stale"}]); await new Promise(resolve => setImmediate(resolve));
    assert.equal(render(true, "actor-b", "2025-01-02", "2025-01-07", 0).days[0].date, "current");
    globalThis.window.dispatchEvent(new Event("pymentor:personal-analytics-closed"));
    assert.equal(calls[2].signal.aborted, true);
    render(false, "actor-b", "2025-01-02", "2025-01-07", 0);
    assert.equal(calls.length, 3);
  } finally {effects.forEach(effect => effect?.cleanup?.()); globalThis.window = originalWindow;}
});
