import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import type { UserProfile } from "../../shared/api/contracts/user";
import { getCurrentUser } from "./api";
import { announceAuthChange, createAuthSession, listenForAuthRefresh } from "./session-coordination";

export function useAuthSession() {
  const [session] = useState(() => createAuthSession(getCurrentUser, announceAuthChange));
  const snapshot = useSyncExternalStore(session.subscribe, session.getSnapshot);
  useEffect(() => {
    const unsubscribe = listenForAuthRefresh((announceChange) => { void session.refresh(announceChange); });
    void session.start();
    return () => { unsubscribe(); session.stop(); };
  }, [session]);
  const recheck = useCallback(() => { void session.refresh(); }, [session]);
  const onUserChange = useCallback((_next: UserProfile | null) => {
    // Mutation responses may arrive after another tab changed the cookie.
    announceAuthChange();
    void session.refresh();
  }, [session]);
  return { ...snapshot, recheck, onUserChange };
}
