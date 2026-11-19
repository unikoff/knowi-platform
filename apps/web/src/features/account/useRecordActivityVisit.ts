import { useEffect, useRef } from "react";
import { recordActivityVisit } from "./api";
import { getLocalDateKey } from "../../shared/lib/dates";

// Workspace activity is recorded even when the account tab stays closed.
export function useRecordActivityVisit(userId: number | null, dateMode: "local" | "server" = "local", identityKey?: string) {
  const scope = JSON.stringify([userId, dateMode, identityKey]);
  const currentScope = useRef(scope);
  currentScope.current = scope;
  useEffect(() => {
    let active = true;
    if (userId !== null) {
      void recordActivityVisit(dateMode === "server" ? undefined : getLocalDateKey())
        .then((result) => {
          if (active && currentScope.current === scope) window.dispatchEvent(new CustomEvent("pymentor:activity-updated", { detail: { activity_date: result.activity_date } }));
        })
        .catch(() => undefined);
    }
    return () => { active = false; };
  }, [userId, dateMode, identityKey, scope]);
}
