import type { CalendarSessionIdentity } from "../account/calendar-cache";
import { announceAuthChange } from "./session-coordination";
import { WINDOWS_AUTH_BOUNDARY } from "../analytics-tracker/windows";

const LOCK_NAME = "knowi:calendar:session:v1";
const SIGNAL_KEY = "knowi:calendar:auth-signal:v1";
const SIGNAL_EVENT = "knowi:calendar:auth-signal";

export function readCalendarAuthSignal(): string | null {
  try {
    const value = window.localStorage.getItem(SIGNAL_KEY);
    if (value === null) return "";
    return /^(ready|blocked):[a-zA-Z0-9-]{8,128}$/.test(value) ? value : null;
  } catch { return null; }
}

function setSignal(value: string): boolean {
  try {
    // Test the full round trip; storage can be present but unusable.
    if (typeof window.localStorage.getItem !== "function") return false;
    window.localStorage.setItem(SIGNAL_KEY, value);
    if (window.localStorage.getItem(SIGNAL_KEY) !== value) return false;
    window.dispatchEvent(new Event(SIGNAL_EVENT));
    return true;
  } catch { return false; }
}

export function subscribeCalendarAuthSignal(listener: () => void): () => void {
  const storage = (event: StorageEvent) => { if (event.key === SIGNAL_KEY || event.key === null) listener(); };
  window.addEventListener("storage", storage);
  window.addEventListener(SIGNAL_EVENT, listener);
  return () => { window.removeEventListener("storage", storage); window.removeEventListener(SIGNAL_EVENT, listener); };
}

export function calendarAuthLockAvailable(): boolean {
  return typeof navigator !== "undefined" && typeof navigator.locks?.request === "function";
}

export async function withCalendarAuthLock<T>(operation: () => Promise<T>): Promise<T | null> {
  if (!calendarAuthLockAvailable()) return null;
  try { return await navigator.locks.request(LOCK_NAME, { mode: "exclusive" }, operation); }
  catch { return null; }
}

async function confirmSession(): Promise<CalendarSessionIdentity | null> {
  try {
    const base = import.meta.env.BASE_URL.replace(/\/$/, "");
    const response = await fetch(`${base}/api/user/me`, { credentials: "include", cache: "no-store", headers: { Accept: "application/json" } });
    if (response.status === 401) return { guest: true };
    if (!response.ok || !response.headers.get("Content-Type")?.includes("application/json")) return null;
    const body = await response.json();
    const accountId = body?.user?.account_ref;
    const sessionMarker = response.headers.get("X-Calendar-Session-Marker");
    return typeof accountId === "string" && accountId && typeof sessionMarker === "string" && sessionMarker
      ? { accountId, sessionMarker } : null;
  } catch { return null; }
}

/** Serialize managed cookie changes with calendar operations. An uncertain
 * mutation leaves both the durable boundary and the shared signal blocked. */
export async function runCalendarAuthTransition<T>(change: () => Promise<T>): Promise<T> {
  if (import.meta.env.VITE_ANALYTICS_WINDOWS_ENABLED === "true" && typeof window !== "undefined") {
    window.dispatchEvent(new Event(WINDOWS_AUTH_BOUNDARY));
    if (import.meta.env.VITE_ANALYTICS_CALENDAR_CACHE_ENABLED !== "true") announceAuthChange();
  }
  if (import.meta.env.VITE_ANALYTICS_CALENDAR_CACHE_ENABLED !== "true") return change();
  const nonce = globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  const blocked = `blocked:${nonce}`;
  let executed = false;
  let outcome: T | undefined;
  let failure: unknown;
  const perform = async (locked: boolean) => {
    executed = true;
    const priorSignal = typeof window !== "undefined" ? readCalendarAuthSignal() : null;
    const signalled = typeof window !== "undefined" && setSignal(blocked);
    // BroadcastChannel also reaches a sibling when this tab's storage is blocked.
    announceAuthChange();
    let generation: string | null = null;
    if (locked) {
      try { generation = await (await import("../account/calendar-cache")).beginCalendarAuthTransition(); }
      catch { /* auth remains usable, calendar remains blocked */ }
    }
    try { outcome = await change(); }
    catch (error) { failure = error; return; }
    if (!locked || !generation || !signalled || priorSignal === null || priorSignal.startsWith("blocked:")) return;
    const identity = await confirmSession();
    if (!identity) return;
    try {
      const stored = await (await import("../account/calendar-cache")).finishCalendarAuthTransition(generation, identity);
      if (stored && readCalendarAuthSignal() === blocked) setSignal(`ready:${generation}`);
    } catch { /* Keep blocked on an uncertain boundary. */ }
  };
  if (calendarAuthLockAvailable()) await withCalendarAuthLock(() => perform(true));
  // Authentication is still available when the browser lacks these APIs.
  if (!executed) await perform(false);
  if (failure !== undefined) throw failure;
  return outcome as T;
}
