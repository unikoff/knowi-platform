import type { UserProfile } from "../../shared/api/contracts/user";

export const AUTH_CHANGE_KEY = "pymentor:auth-change:v1";
const AUTH_CHANNEL = "pymentor:auth-change:v1";

// Only an opaque nonce crosses tabs. Cookies and /user/me remain authoritative.
export function createAuthChangeBus(browser: Window & typeof globalThis) {
  const listeners = new Set<() => void>();
  const seen = new Set<string>();
  let channel: BroadcastChannel | undefined;
  function remember(nonce: string) {
    seen.add(nonce);
    if (seen.size > 32) seen.delete(seen.values().next().value!);
  }
  function receive(value: unknown) {
    if (typeof value !== "string" || !/^[a-zA-Z0-9:-]{8,128}$/.test(value) || seen.has(value)) return;
    remember(value);
    listeners.forEach((listener) => listener());
  }
  const storageChanged = (event: StorageEvent) => { if (event.key === AUTH_CHANGE_KEY) receive(event.newValue); };
  const messageReceived = (event: MessageEvent) => receive(event.data);
  return {
    publish() {
      const nonce = browser.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}:${Math.random().toString(36).slice(2)}`;
      remember(nonce);
      try { browser.localStorage.setItem(AUTH_CHANGE_KEY, nonce); } catch { /* BroadcastChannel still works without storage. */ }
      try {
        const sender = channel ?? new browser.BroadcastChannel(AUTH_CHANNEL);
        sender.postMessage(nonce);
        if (sender !== channel) sender.close();
      } catch { /* Focus refresh also covers unavailable cross-tab transports. */ }
    },
    subscribe(listener: () => void) {
      if (!listeners.size) {
        browser.addEventListener("storage", storageChanged);
        try {
          channel = new browser.BroadcastChannel(AUTH_CHANNEL);
          channel.addEventListener("message", messageReceived);
        } catch { /* Storage events remain available. */ }
      }
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
        if (!listeners.size) {
          browser.removeEventListener("storage", storageChanged);
          channel?.removeEventListener("message", messageReceived);
          channel?.close();
          channel = undefined;
        }
      };
    },
  };
}

let authBus: ReturnType<typeof createAuthChangeBus> | undefined;
function getAuthBus() { return authBus ??= createAuthChangeBus(window); }

export function announceAuthChange() {
  if (typeof window !== "undefined") getAuthBus().publish();
}

export function listenForAuthRefresh(refresh: (announceIdentityChange: boolean) => void) {
  const unsubscribe = getAuthBus().subscribe(() => refresh(false));
  const focused = () => refresh(true);
  const visible = () => { if (document.visibilityState === "visible") refresh(true); };
  window.addEventListener("focus", focused);
  document.addEventListener("visibilitychange", visible);
  return () => {
    unsubscribe();
    window.removeEventListener("focus", focused);
    document.removeEventListener("visibilitychange", visible);
  };
}

export type AuthSessionSnapshot = Readonly<{
  user: UserProfile | null;
  checking: boolean;
  initialized: boolean;
  revision: number;
}>;

export function createAuthSession(
  probe: () => Promise<{ user: UserProfile | null }>,
  announceIdentityChange: () => void = () => {},
) {
  let snapshot: AuthSessionSnapshot = { user: null, checking: true, initialized: false, revision: 0 };
  let generation = 0;
  let active = false;
  let lastIdentity: number | null | undefined;
  const listeners = new Set<() => void>();
  function publish(next: AuthSessionSnapshot) {
    snapshot = next;
    listeners.forEach((listener) => listener());
  }
  async function refresh(announceChange = false) {
    if (!active) return;
    const request = ++generation;
    // The view gates Workspace while retaining email-action component state.
    publish({ ...snapshot, checking: true, revision: request });
    let user: UserProfile | null;
    try { user = (await probe()).user ?? null; }
    catch { user = null; }
    if (!active || request !== generation) return;
    const identity = user?.id ?? null;
    const changed = lastIdentity !== undefined && lastIdentity !== identity;
    lastIdentity = identity;
    publish({ user, checking: false, initialized: true, revision: request });
    // A focus-discovered change is shared once. Remote probes never rebroadcast.
    if (announceChange && changed) announceIdentityChange();
  }
  return {
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    start() { active = true; return refresh(); },
    stop() { active = false; generation += 1; },
    refresh,
  };
}
