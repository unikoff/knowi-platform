import { AnalyticsReportError, fetchCalendarActivityReport } from "../analytics-reporting/client";
import type { ActivityReportV1 } from "../analytics-reporting/types";
import { calendarAuthLockAvailable, readCalendarAuthSignal, subscribeCalendarAuthSignal, withCalendarAuthLock } from "../auth/calendar-auth-boundary";
import { calendarDayWindow, calendarLeaseCurrent, openCalendarHistory, updateCalendarHistory, type CalendarCacheLease, type CalendarDay } from "./calendar-cache";
import { mergeCalendarDelta } from "./calendar-delta";
import { courseReportFilters, type CourseReportRange } from "./course-statistics-analytics";
import { moscowToday, type CourseTimeSnapshot } from "./course-statistics-model";

export { subscribeCalendarAuthSignal };
export const calendarPersistenceEnabled = import.meta.env.VITE_ANALYTICS_CALENDAR_CACHE_ENABLED === "true";
export const getCalendarPersistenceSignal = () => calendarPersistenceEnabled && calendarAuthLockAvailable()
  ? readCalendarAuthSignal() : null;

type OpenedHistory = {lease: CalendarCacheLease; today: string; days: CalendarDay[]; generation?: string};
export type OpenCalendarDays = Map<string, OpenedHistory>;

const unknown = {activeMs: null, theoryMs: null, practiceMs: null};
function preview(range: CourseReportRange, dates: string[], days: CalendarDay[]): CourseTimeSnapshot | null {
  const saved = new Map(days.map(day => [day.date, day]));
  if (!dates.some(date => saved.has(date))) return null;
  return {courseId: range.courseId, from: range.from, to: range.to, quality: "unknown", asOf: null,
    totals: {...unknown}, lessons: [], days: dates.map(date => {
      const day = saved.get(date);
      return day ? {date, activeMs: day.activeMs, theoryMs: day.theoryMs, practiceMs: day.practiceMs} : {date, ...unknown};
    })};
}

export async function loadCalendarReport({range, dates, actor, authSignal, signal, isCurrent, opened, onPreview, onHistoryReset}: {
  range: CourseReportRange; dates: string[]; actor: string; authSignal: string; signal: AbortSignal;
  isCurrent: () => boolean; opened: OpenCalendarDays; onPreview: (snapshot: CourseTimeSnapshot) => void;
  onHistoryReset: () => void;
}): Promise<ActivityReportV1> {
  const today = moscowToday();
  const {from, to} = calendarDayWindow(today);
  const closedDates = dates.filter(date => date >= from && date <= to);
  const closed = new Set(closedDates);
  const current = () => isCurrent() && !signal.aborted && getCalendarPersistenceSignal() === authSignal && moscowToday() === today;
  let history: OpenedHistory | null = null;
  const result = await fetchCalendarActivityReport(courseReportFilters(range), actor, signal, async sessionMarker => {
    if (!current()) return null;
    return withCalendarAuthLock(async () => {
      if (!current()) return null;
      const previous = opened.get(range.courseId);
      if (previous?.today === today && previous.lease.account.sessionMarker === sessionMarker) {
        history = previous;
      } else {
        const found = await openCalendarHistory({accountId: actor.toLowerCase(), sessionMarker}, authSignal, range.courseId, today);
        if (!current() || !found) return null;
        history = {...found, today};
        opened.set(range.courseId, history);
      }
      const snapshot = preview(range, dates, history.days);
      if (snapshot && current()) onPreview(snapshot);
      return {
        known_generation: history.generation ?? null,
        known_revisions: history.days.filter(day => closed.has(day.date)).map(day => ({date: day.date, token: day.revision})),
      };
    });
  });
  const saved = history as OpenedHistory | null;
  if (!current()) throw new DOMException("Calendar request expired", "AbortError");
  if ("schema_version" in result) {
    if (saved && await withCalendarAuthLock(() => calendarLeaseCurrent(saved.lease)) !== true) {
      opened.delete(range.courseId);
      throw new AnalyticsReportError("unavailable");
    }
    return result;
  }
  if (!saved) throw new AnalyticsReportError("invalid_response");
  const merged = mergeCalendarDelta(result, range, dates, closedDates, saved.days, saved.generation);
  if (!merged) throw new AnalyticsReportError("invalid_response");

  const confirmed = await withCalendarAuthLock(async () => {
    if (!current()) return false;
    const generation = result.history_generation;
    if (saved.generation === generation && !merged.replacedDates.length) {
      return calendarLeaseCurrent(saved.lease, {courseId: range.courseId, generation});
    }
    if (saved.generation && saved.generation !== generation) onHistoryReset();
    const updated = await updateCalendarHistory(saved.lease, range.courseId, generation,
      merged.replacedDates, merged.rows, saved.generation);
    if (!updated || !current()) return false;
    const covered = new Set(merged.replacedDates);
    const retained = saved.generation === generation
      ? saved.days.filter(day => day.date >= from && day.date <= to && !covered.has(day.date)) : [];
    saved.days = [...retained, ...merged.rows];
    saved.generation = generation;
    return true;
  });
  if (!confirmed) {
    opened.delete(range.courseId);
    throw new AnalyticsReportError("unavailable");
  }
  return merged.report;
}
