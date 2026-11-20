import { useEffect, useState } from "react";
import { apiRequest } from "./client";
import type { UserProfile } from "./contracts";

type BookingMode = "legacy" | "scoped";
type Result = { scope: string; mode: BookingMode | null };

export function useBookingMode(user: UserProfile | null) {
  const scope = user && JSON.stringify([user.id, user.account_ref, user.role, user.auth_revision]);
  const [result, setResult] = useState<Result | null>(null);

  useEffect(() => {
    if (!scope) return;
    const requestScope = scope;
    let current = true;
    async function load() {
      try {
        const response = await apiRequest<{ mode?: unknown } | null>("/user/booking-mode");
        const mode = response?.mode;
        if (current) setResult({ scope: requestScope, mode: mode === "legacy" || mode === "scoped" ? mode : null });
      } catch {
        if (current) setResult({ scope: requestScope, mode: null });
      }
    }
    void load();
    return () => { current = false; };
  }, [scope]);

  const resolved = scope && result?.scope === scope ? result : null;
  return { mode: resolved?.mode ?? null, loading: Boolean(scope && !resolved) };
}
