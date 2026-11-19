import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { AnalyticsReportError, fetchActivityReport } from "../analytics-reporting/client";
import { getCalendarPersistenceSignal, loadCalendarReport, subscribeCalendarAuthSignal, type OpenCalendarDays } from "./calendar-persistence";
import { courseReportFilters, courseSnapshot, type CourseReportRange } from "./course-statistics-analytics";
import { shiftDate, type CourseTimeSnapshot } from "./course-statistics-model";

type Status = "pending" | "loading" | "ready" | "stale" | "error" | "unavailable" | "denied";
type State = {key: string; status: Status; snapshot: CourseTimeSnapshot | null};
export function useCourseStatisticsAnalytics(enabled: boolean, actorRef: string | undefined, range: CourseReportRange, attempt: number) {
  const [revision, setRevision] = useState(0);
  const timers = useRef<Array<ReturnType<typeof setTimeout>>>([]);
  const inFlight = useRef(false);
  const closedSinceRead = useRef(false);
  const authSignal = useSyncExternalStore(subscribeCalendarAuthSignal, getCalendarPersistenceSignal, () => null);
  const blocked = authSignal?.startsWith("blocked:") === true;
  // Report snapshots belong to the mounted account and exact requested period.
  const reports = useRef<{scope: string; generation: number; entries: Map<string, State>; opened: OpenCalendarDays}>({
    scope: "", generation: 0, entries: new Map(), opened: new Map(),
  });
  const refresh = useRef({attempt, revision});
  const scope = JSON.stringify([enabled, actorRef, authSignal]);
  if (reports.current.scope !== scope) {
    reports.current = {scope, generation: reports.current.generation + 1, entries: new Map(), opened: new Map()};
  }
  const store = reports.current;
  const period = JSON.stringify([range.courseId, range.from, range.to]);
  const forceRefresh = refresh.current.attempt !== attempt || refresh.current.revision !== revision;
  const key = JSON.stringify([reports.current.generation, enabled, actorRef, range.courseId, range.from, range.to, attempt, revision]);
  const [state, setState] = useState<State>({key: "", status: "pending", snapshot: null});
  useEffect(() => {
    if (!enabled) return;
    const clear = () => { timers.current.forEach(clearTimeout); timers.current = []; };
    const followUp = () => {
      clear();
      timers.current = [3000, 15000].map(delay => setTimeout(() => setRevision(value => value + 1), delay));
    };
    const opened = () => {
      if (!inFlight.current || closedSinceRead.current) setRevision(value => value + 1);
      closedSinceRead.current = false;
      followUp();
    };
    const closed = () => { closedSinceRead.current = true; clear(); };
    followUp(); // The panel's first open event can precede this effect's mount.
    window.addEventListener("pymentor:personal-analytics-opened", opened);
    window.addEventListener("pymentor:personal-analytics-closed", closed);
    return () => {
      clear();
      window.removeEventListener("pymentor:personal-analytics-opened", opened);
      window.removeEventListener("pymentor:personal-analytics-closed", closed);
    };
  }, [enabled, actorRef, authSignal]);
  useEffect(() => {
    if (!enabled) return;
    refresh.current = {attempt, revision};
    const cached = store.entries.get(period);
    if (cached && !forceRefresh) {
      setState({...cached, key});
      return;
    }
    const controller = new AbortController(); let active = true;
    inFlight.current = true;
    const isCurrent = () => active && reports.current === store && getCalendarPersistenceSignal() === authSignal;
    const length = Math.round((Date.parse(`${range.to}T00:00:00Z`) - Date.parse(`${range.from}T00:00:00Z`)) / 86400000) + 1;
    const dates = Array.from({length}, (_, index) => shiftDate(range.from, index));
    const request = authSignal !== null && !blocked && actorRef
      ? loadCalendarReport({
        range, dates, actor: actorRef, authSignal, signal: controller.signal, isCurrent, opened: store.opened,
        onPreview: snapshot => {
          if (isCurrent() && !cached) setState({key, status: "stale", snapshot});
        },
        onHistoryReset: () => {
          if (!isCurrent()) return;
          for (const [cachedPeriod, entry] of store.entries) {
            if (entry.snapshot?.courseId === range.courseId) store.entries.delete(cachedPeriod);
          }
          setState({key, status: "loading", snapshot: null});
        },
      })
      : fetchActivityReport(courseReportFilters(range), actorRef ?? "", controller.signal);
    request.then(report => {
      if (!isCurrent()) return;
      inFlight.current = false;
      if (report.status === "unavailable") {
        store.entries.delete(period);
        setState({key, status: "unavailable", snapshot: null});
        return;
      }
      const next: State = {key, status: report.status === "stale" ? "stale" : "ready", snapshot: courseSnapshot(report, range, dates)};
      store.entries.delete(period);
      store.entries.set(period, next);
      if (store.entries.size > 32) store.entries.delete(store.entries.keys().next().value!);
      setState(next);
    }, error => {
      if (!isCurrent()) return;
      inFlight.current = false;
      if (error?.name !== "AbortError") {
        const denied = error instanceof AnalyticsReportError && error.kind === "denied";
        store.entries.delete(period);
        if (denied) { timers.current.forEach(clearTimeout); timers.current = []; store.entries.clear(); }
        setState({key, status: denied ? "denied" : "error", snapshot: null});
      }
    });
    return () => {active = false; inFlight.current = false; controller.abort();};
  }, [key]);
  if (!enabled) return {status: "pending" as const, snapshot: null};
  const cached = reports.current.entries.get(period);
  if (state.key !== key && cached && !forceRefresh) return {...cached, key};
  return state.key === key ? state : {status: "loading" as const, snapshot: null};
}
