export type SidebarPinStorage = Pick<Storage, "getItem" | "setItem">;

const STORAGE_PREFIX = "pymentor:sidebar-pins:v1:";
const REQUIRED_PINS = new Set(["lessons", "theory", "practice", "admin", "account"]);

export function isRequiredSidebarPin(id: string): boolean {
  return REQUIRED_PINS.has(id);
}

export function isSidebarToolPinned(id: string, pins: readonly string[]): boolean {
  return isRequiredSidebarPin(id) || pins.includes(id);
}

export function toggleSidebarPin(id: string, pins: readonly string[]): string[] {
  if (isRequiredSidebarPin(id)) return [...pins];
  return pins.includes(id) ? pins.filter((pin) => pin !== id) : [...pins, id];
}

function browserStorage(): SidebarPinStorage | undefined {
  try {
    return typeof window === "undefined" ? undefined : window.localStorage;
  } catch {
    return undefined;
  }
}

function uniqueIds(values: readonly unknown[]): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    if (typeof value !== "string" || value.length === 0 || seen.has(value)) continue;
    seen.add(value);
    result.push(value);
  }
  return result;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A single preference key per account, shared by every course and viewport. */
export function sidebarPinsKey(owner: string): string {
  return `${STORAGE_PREFIX}${encodeURIComponent(owner)}`;
}

/** Read this user's pinned tool IDs, falling back to defaults when storage is absent or invalid. */
export function readSidebarPins(
  owner: string,
  defaults: readonly string[],
  storage: SidebarPinStorage | null | undefined = browserStorage(),
): string[] {
  const fallback = uniqueIds(defaults);
  if (!storage) return fallback;

  try {
    const raw = storage.getItem(sidebarPinsKey(owner));
    if (raw === null) return fallback;
    const saved: unknown = JSON.parse(raw);
    if (!isRecord(saved) || saved.version !== 1 || !Array.isArray(saved.pins) ||
      !saved.pins.every((pin) => typeof pin === "string" && pin.length > 0)) return fallback;
    return uniqueIds(saved.pins);
  } catch {
    return fallback;
  }
}

/** Persist this user's pinned tool IDs; returns false when browser storage is unavailable. */
export function writeSidebarPins(
  owner: string,
  pins: readonly string[],
  storage: SidebarPinStorage | null | undefined = browserStorage(),
): boolean {
  if (!storage) return false;
  try {
    storage.setItem(sidebarPinsKey(owner), JSON.stringify({ version: 1, pins: uniqueIds(pins) }));
    return true;
  } catch {
    return false;
  }
}
