import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { accentIds, accentPresets, applyAccent, resolveAccent } from "./accents.ts";
import { darkPalette, lightPalette } from "./palettes.ts";

function luminance(hex) {
  const channels = hex.slice(1).match(/../g).map(value => parseInt(value, 16) / 255).map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}
function contrast(a, b) { const values = [luminance(a), luminance(b)].sort((x, y) => y - x); return (values[0] + 0.05) / (values[1] + 0.05); }
function mix(a, b, weight) {
  const channels = color => color.slice(1).match(/../g).map(value => parseInt(value, 16));
  const first = channels(a), second = channels(b);
  return "#" + first.map((value, i) => Math.round(value * weight + second[i] * (1 - weight)).toString(16).padStart(2, "0")).join("");
}

test("failed test labels retain readable neutral foreground and background in both schemes", () => {
  const css = readFileSync(new URL("../../features/learning/code-workbench/results.css", import.meta.url), "utf8");
  const rule = css.match(/\.test-list li\.is-failed > span\s*\{([^}]+)\}/)?.[1];
  assert.ok(rule, "failed test label rule must exist");
  const foreground = rule.match(/(?:^|;)\s*color:\s*var\(--([\w-]+)\)/)?.[1];
  const background = rule.match(/(?:^|;)\s*background:\s*var\(--([\w-]+)\)/)?.[1];
  for (const palette of [lightPalette, darkPalette]) {
    const resolve = role => {
      const value = palette[role];
      return value.startsWith("var(") ? resolve(value.slice(6, -1)) : value;
    };
    assert.ok(contrast(resolve(foreground), resolve(background)) >= 4.5, "failed test label text must meet AA");
  }
});

test("all light syntax roles remain neutral, including newly introduced token types", () => {
  const resolve = role => {
    const value = lightPalette[role];
    return value.startsWith("var(") ? resolve(value.slice(6, -1)) : value;
  };
  for (const role of Object.keys(lightPalette).filter(role => role.startsWith("syntax-"))) {
    const color = resolve(role);
    assert.match(color, /^#([0-9a-f]{2})\1\1$/i, `${role} must remain grayscale`);
    assert.ok(contrast(color, lightPalette["code-bg"]) >= 4.5, `${role} must be readable`);
  }
});
test("each scheme keeps accent text readable across solid and soft states", () => {
  assert.equal(accentIds.length, 6);
  for (const id of accentIds) {
    const p = accentPresets[id];
    for (const scheme of ["light", "dark"]) {
      const solid = p[`${scheme}Solid`], onSolid = p[`${scheme}OnSolid`];
      const text = p[`${scheme}Text`], surface = p[`${scheme}Surface`];
      const pairs = [[onSolid, solid], [onSolid, mix(solid, "#000000", 0.85)], [onSolid, mix(solid, "#000000", 0.72)], [text, surface], [text, mix(surface, text, 0.85)], [text, scheme === "light" ? "#ffffff" : "#202020"]];
      for (const [foreground, background] of pairs) {
        assert.ok(contrast(foreground, background) >= 4.5, `${id} ${scheme}: ${foreground} / ${background} = ${contrast(foreground, background)}`);
      }
    }
  }
});
test("invalid stored accent falls back and switching scheme retains selection without changing neutral roles", () => {
  assert.equal(resolveAccent("missing"), "lime");
  assert.equal(resolveAccent(null), "lime");
  assert.equal(resolveAccent("blue"), "blue");
  const properties = new Map();
  globalThis.document = { documentElement: { dataset: {}, style: { setProperty: (key, value) => properties.set(key, value) } } };
  for (const scheme of ["light", "dark", "light"]) {
    applyAccent("blue", scheme);
    assert.equal(document.documentElement.dataset.accent, "blue");
    assert.equal(properties.get("--accent-text"), accentPresets.blue[`${scheme}Text`]);
    assert.equal(properties.get("--accent-solid"), accentPresets.blue[`${scheme}Solid`]);
    assert.equal(properties.get("--on-accent"), accentPresets.blue[`${scheme}OnSolid`]);
    assert.ok(!properties.has("--bg"));
  }
  delete globalThis.document;
  for (const palette of [darkPalette, lightPalette]) {
    for (const role of ["negative", "info", "success", "success-border", "danger", "error", "warning", "accent-secondary", "accent-secondary-glow"]) {
      let value = palette[role];
      const seen = new Set();
      while (value.startsWith("var(")) {
        assert.ok(!seen.has(value), `cyclic neutral role ${role}`);
        seen.add(value);
        value = palette[value.slice(6, -1)];
        assert.ok(value, `unresolved neutral role ${role}`);
      }
      assert.match(value, /^#([0-9a-f]{2})\1\1$/i, `${role} must remain grayscale`);
    }
  }
});
