export const accentPresets = {
  lime: { label: "Лайм", lightSolid: "#b7ff5a", lightOnSolid: "#202020", darkSolid: "#b7ff5a", darkOnSolid: "#202020", lightText: "#365c17", darkText: "#b7ff5a", lightSurface: "#edf8df", darkSurface: "#28391d" },
  blue: { label: "Синий", lightSolid: "#315bd6", lightOnSolid: "#ffffff", darkSolid: "#85c5ff", darkOnSolid: "#202020", lightText: "#13548c", darkText: "#85c5ff", lightSurface: "#e6f3ff", darkSurface: "#1d3449" },
  violet: { label: "Фиолетовый", lightSolid: "#7241c7", lightOnSolid: "#ffffff", darkSolid: "#d1adff", darkOnSolid: "#202020", lightText: "#613594", darkText: "#d1adff", lightSurface: "#f2eaff", darkSurface: "#392b4b" },
  orange: { label: "Оранжевый", lightSolid: "#b34511", lightOnSolid: "#ffffff", darkSolid: "#ffbd80", darkOnSolid: "#202020", lightText: "#8b4200", darkText: "#ffbd80", lightSurface: "#fff0e0", darkSurface: "#49301d" },
  yellow: { label: "Жёлтый", lightSolid: "#f6d75c", lightOnSolid: "#202020", darkSolid: "#f6d75c", darkOnSolid: "#202020", lightText: "#6a5500", darkText: "#f6d75c", lightSurface: "#fff8d9", darkSurface: "#40391c" },
  red: { label: "Красный", lightSolid: "#b62e45", lightOnSolid: "#ffffff", darkSolid: "#ffaaaa", darkOnSolid: "#202020", lightText: "#a02d2d", darkText: "#ffaaaa", lightSurface: "#ffeded", darkSurface: "#4a2929" },
} as const;

export type AccentId = keyof typeof accentPresets;
export const accentIds = Object.keys(accentPresets) as AccentId[];
export const DEFAULT_ACCENT: AccentId = "lime";
export const ACCENT_STORAGE_KEY = "pymentor-accent";
export function isAccentId(value: unknown): value is AccentId {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(accentPresets, value);
}
export function resolveAccent(value: unknown): AccentId { return isAccentId(value) ? value : DEFAULT_ACCENT; }

export function applyAccent(id: AccentId, scheme: "dark" | "light") {
  const preset = accentPresets[id];
  const root = document.documentElement;
  const solid = scheme === "light" ? preset.lightSolid : preset.darkSolid;
  const onSolid = scheme === "light" ? preset.lightOnSolid : preset.darkOnSolid;
  const text = scheme === "light" ? preset.lightText : preset.darkText;
  const surface = scheme === "light" ? preset.lightSurface : preset.darkSurface;
  const values: Record<string, string> = {
    "accent-solid": solid,
    "accent-solid-hover": `color-mix(in srgb, ${solid} 85%, #000)`,
    "accent-solid-pressed": `color-mix(in srgb, ${solid} 72%, #000)`,
    "on-accent": onSolid,
    "accent-text": text,
    "accent-surface": surface,
    "accent-surface-hover": `color-mix(in srgb, ${surface} 85%, ${text})`,
    "on-accent-surface": text,
    "accent-border": text,
    "focus-ring": text,
  };
  root.dataset.accent = id;
  for (const [name, value] of Object.entries(values)) root.style.setProperty(`--${name}`, value);
}
