import { Moon, Sun } from "lucide-react";
import { accentIds, accentPresets, isAccentId, themeIds, themes, useTheme } from "../../theme";
import "./theme-toggle.css";

export function ThemeToggle() {
  const { theme, setTheme, colorScheme, accent, setAccent } = useTheme();
  const isDark = colorScheme === "dark";

  const themeControl = themeIds.length > 2 ? (
    <select className="theme-select" aria-label="Тема оформления" value={theme}
      onChange={(event) => {
        const next = themeIds.find((id) => id === event.target.value);
        if (next) setTheme(next);
      }}>
      {themeIds.map((id) => <option key={id} value={id}>{themes[id].label}</option>)}
    </select>
  ) : null;
  const nextTheme = themeIds[(themeIds.indexOf(theme) + 1) % themeIds.length];

  return (
    <div className="theme-controls">
    {themeControl ?? <button
      className="theme-switch"
      type="button"
      role="switch"
      aria-checked={!isDark}
      onClick={() => setTheme(nextTheme)}
      aria-label="Светлая тема"
      title={isDark ? "Включить светлую тему" : "Включить тёмную тему"}
    >
      <span className="theme-switch__thumb" aria-hidden="true" />
      <span className="theme-switch__option" aria-hidden="true"><Moon size={14} /></span>
      <span className="theme-switch__option" aria-hidden="true"><Sun size={14} /></span>
    </button>}
    <select className="theme-select" aria-label="Акцентный цвет" value={accent} onChange={event => { if (isAccentId(event.target.value)) setAccent(event.target.value); }}>
      {accentIds.map(id => <option key={id} value={id}>{accentPresets[id].label}</option>)}
    </select>
    </div>
  );
}
