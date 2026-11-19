import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import ts from "typescript";

function compile(path, imports = {}) {
  const source = fs.readFileSync(new URL(path, import.meta.url), "utf8").replaceAll("import.meta.env.BASE_URL", '"/platform/"');
  const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
  const module = {exports: {}};
  new Function("require", "module", "exports", code)(name => imports[name], module, module.exports);
  return module.exports;
}
const model = compile("./course-statistics-model.ts");
const {parseLearnerHistory} = compile("./learner-history.ts", {"./course-statistics-model": model});
const day = date => ({date, theory_ms: 3600000, practice_ms: 3600000, provenance: "core_legacy_conditional"});
const parse = value => parseLearnerHistory(value, "2025-01-01", "2025-01-07");

test("accepts and orders distinct dates without combining measurements", () => {
  assert.deepEqual(parse({days: [day("2025-01-03"), day("2025-01-01")]}), [day("2025-01-01"), day("2025-01-03")]);
  assert.deepEqual(parse({days: []}), []);
});
test("rejects duplicate, invalid, out-of-range and nonconditional data", () => {
  for (const days of [[day("2025-01-01"), day("2025-01-01")], [day("2025-02-30")], [day("2025-01-08")], [{...day("2025-01-01"), theory_ms: 0}], [{...day("2025-01-01"), provenance: "measured"}]]) {
    assert.throws(() => parse({days}));
  }
});
