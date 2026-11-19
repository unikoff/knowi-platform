import { useEffect, useState } from "react";
import { AnalyticsReportError, fetchActivityReport } from "../../features/analytics-reporting/client";
import { getMoscowDateKey } from "../../shared/lib/dates";
import { cacheableDays, firstUncachedDay, parseCache, reportBounds, reportDays, weekDates, type CacheDay, type DayValue } from "./weeklyLearningTime";

type Identity = { id: number; accountRef: string; authRevision: number };
type State = { key: string; days: DayValue[]; status: "loading" | "ready" | "partial" | "error" };
const cachePrefix = "pymentor:weekly-learning-time:v1:";

function readCache(key: string, dates: string[]): Map<string, CacheDay> {
  try {
    return parseCache(localStorage.getItem(key), dates, Date.now());
  } catch { return new Map(); }
}

export function useWeeklyLearningTime(identity: Identity | null, enabled: boolean, refresh: number) {
  const [today, setToday] = useState(getMoscowDateKey);
  const [state, setState] = useState<State>({ key: "", days: [], status: "loading" });
  const key = identity && enabled ? JSON.stringify([identity.id, identity.accountRef, identity.authRevision, today]) : "";
  useEffect(() => {
    const update = () => setToday(getMoscowDateKey());
    const timer = window.setInterval(update, 60000);
    window.addEventListener("focus", update);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", update); };
  }, []);
  useEffect(() => {
    if (!identity || !enabled) return;
    const controller = new AbortController();
    let active = true;
    const dates = weekDates(today);
    const cacheKey = `${cachePrefix}${identity.accountRef}:${identity.authRevision}`;
    const cached = readCache(cacheKey, dates.slice(0, -1));
    const initial = dates.map(date => ({ date, minutes: cached.get(date)?.minutes ?? null }));
    setState({ key, days: initial, status: "loading" });
    const firstMissing = firstUncachedDay(dates, cached);
    const requested = dates.filter(date => date >= firstMissing);
    fetchActivityReport({ ...reportBounds(firstMissing, today), user_id: identity.id, expected_target_ref: identity.accountRef }, identity.accountRef, controller.signal)
      .then(report => {
        if (!active) return;
        if (report.status === "unavailable" || report.totals === null) { setState({ key, days: initial, status: "error" }); return; }
        const values = reportDays(report, requested);
        const all = new Map(initial.map(day => [day.date, day.minutes]));
        for (const day of values) if (!cached.has(day.date)) all.set(day.date, day.minutes);
        for (const day of cacheableDays(report, values, today)) if (!cached.has(day.date)) cached.set(day.date, { minutes: day.minutes, savedAt: Date.now(), partial: report.quality !== "complete" });
        try { localStorage.setItem(cacheKey, JSON.stringify([...cached])); } catch { /* Storage is optional. */ }
        setState({ key, days: dates.map(date => ({ date, minutes: all.get(date) ?? null })),
          status: report.status === "stale" || report.quality !== "complete" || [...cached.values()].some(day => day.partial) ? "partial" : "ready" });
      }, error => { if (active && error?.name !== "AbortError") {
        const denied = error instanceof AnalyticsReportError && error.kind === "denied";
        if (denied) try { localStorage.removeItem(cacheKey); } catch { /* Storage is optional. */ }
        setState({ key, days: denied ? dates.map(date => ({ date, minutes: null })) : initial, status: "error" });
      } });
    return () => { active = false; controller.abort(); };
  }, [key, refresh]);
  const empty = weekDates(today).map(date => ({ date, minutes: null }));
  return key && state.key === key ? state : { key, days: empty, status: "loading" as const };
}
