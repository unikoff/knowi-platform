import test from "node:test";
import assert from "node:assert/strict";
import { highlightPython } from "./python-highlight.ts";
import { darkPalette, lightPalette } from "../theme/palettes.ts";

test("Python declarations, calls and literals receive distinct syntax roles", () => {
  const html = highlightPython('class Greeter:\n    def greet(self):\n        print("Hello", 42) # note');
  assert.match(html, /class="tok-class">Greeter</);
  assert.match(html, /class="tok-fn">greet</);
  assert.match(html, /class="tok-fn">print</);
  assert.match(html, /class="tok-str">&quot;Hello&quot;</);
  assert.match(html, /class="tok-num">42</);
  assert.match(html, /class="tok-com"># note</);
  assert.match(html, /class="tok-kw">class</);
});

test("strings and comments stay escaped and are not parsed as code", () => {
  const html = highlightPython('print("<script>class Fake: def run()</script>") # class Other');
  assert.ok(!html.includes("<script>"));
  assert.doesNotMatch(html, /class="tok-class">(?:Fake|Other)</);
  assert.doesNotMatch(html, /class="tok-fn">run</);
});

test("syntax roles are distinct from each other and plain code in both schemes", () => {
  for (const palette of [darkPalette, lightPalette]) {
    const resolve = role => {
      const value = palette[role];
      return value.startsWith("var(") ? resolve(value.slice(6, -1)) : value;
    };
    const colors = ["code-text", "syntax-keyword", "syntax-function", "syntax-class", "syntax-string", "syntax-number"].map(resolve);
    assert.equal(new Set(colors).size, colors.length);
  }
});
