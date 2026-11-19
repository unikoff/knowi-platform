import { createContext, useContext, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { applyTheme, DEFAULT_THEME, isThemeId, THEME_STORAGE_KEY, themes, type ThemeId } from "./catalog";
import { ACCENT_STORAGE_KEY, applyAccent, resolveAccent, type AccentId } from "./accents";
import { readStoredString, writeStoredValue } from "../lib/storage";

type ThemeContextValue = { theme: ThemeId; setTheme: (theme: ThemeId) => void; colorScheme: "dark" | "light"; accent: AccentId; setAccent: (accent: AccentId) => void };
const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<ThemeId>(() => {
    const stored = readStoredString(THEME_STORAGE_KEY, DEFAULT_THEME);
    return isThemeId(stored) ? stored : DEFAULT_THEME;
  });
  const [accent, setAccent] = useState<AccentId>(() => resolveAccent(readStoredString(ACCENT_STORAGE_KEY, "")));
  useLayoutEffect(() => {
    applyTheme(theme);
    applyAccent(accent, themes[theme].colorScheme);
    writeStoredValue(THEME_STORAGE_KEY, theme);
    writeStoredValue(ACCENT_STORAGE_KEY, accent);
  }, [theme, accent]);
  const value = useMemo(() => ({ theme, setTheme, colorScheme: themes[theme].colorScheme, accent, setAccent }), [theme, accent]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("ThemeProvider is missing");
  return context;
}
