// Optional browser storage must never prevent editing or opening the workspace.
export function readStoredString(key: string, fallback: string, legacyKey?: string): string {
  try {
    const current = window.localStorage.getItem(key);
    if (current !== null) return current;
    const legacy = legacyKey ? window.localStorage.getItem(legacyKey) : null;
    if (legacy !== null && legacyKey) {
      try { window.localStorage.setItem(key, legacy); } catch { /* The legacy value remains readable. */ }
    }
    return legacy ?? fallback;
  }
  catch { return fallback; }
}

export function readStoredNumber(key: string, fallback: number, legacyKey?: string): number {
  const stored = readStoredString(key, "", legacyKey);
  const value = stored.trim() ? Number(stored) : NaN;
  return Number.isFinite(value) ? value : fallback;
}

export function writeStoredValue(key: string, value: string | number) {
  try { window.localStorage.setItem(key, String(value)); }
  catch { /* Keep the current state when storage is unavailable. */ }
}
