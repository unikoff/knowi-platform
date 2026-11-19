import type { ActivityReportV1 } from "../../features/analytics-reporting/types";

export type DayValue = { date: string; minutes: number | null };
export type CacheDay = { minutes: number | null; savedAt: number; partial: boolean };
const cacheTtlMs = 24 * 60 * 60 * 1000;

export function parseCache(serialized: string | null, dates: string[], now: number): Map<string, CacheDay> {
  try {
    const raw: unknown = JSON.parse(serialized ?? "null");
    if (!Array.isArray(raw)) return new Map();
    return new Map(raw.filter((item): item is [string, CacheDay] => {
      if (!Array.isArray(item) || item.length !== 2 || typeof item[0] !== "string" || !dates.includes(item[0])) return false;
      const value = item[1];
      return value && typeof value === "object" && (value.minutes === null || (typeof value.minutes === "number" && Number.isFinite(value.minutes) && value.minutes >= 0)) &&
        typeof value.partial === "boolean" && typeof value.savedAt === "number" && value.savedAt <= now && now - value.savedAt < cacheTtlMs;
    }));
  } catch { return new Map(); }
}

export function firstUncachedDay(dates: string[], cached: Map<string, CacheDay>): string {
  return dates.find((date, index) => index === dates.length - 1 || !cached.has(date))!;
}

export function shiftDay(date: string, days: number): string {
  const value = new Date(`${date}T12:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

export function weekDates(today: string): string[] {
  return Array.from({ length: 7 }, (_, index) => shiftDay(today, index - 6));
}

export function reportBounds(from: string, to: string) {
  return { from_time: new Date(`${from}T00:00:00+03:00`).toISOString(),
    to_time: new Date(`${shiftDay(to, 1)}T00:00:00+03:00`).toISOString() };
}

/** Partial reports contain observations, not proof that missing days were zero. */
export function reportDays(report: ActivityReportV1, dates: string[]): DayValue[] {
  const measured = new Map(report.daily.map(day => [day.date, day.active_ms]));
  const complete = report.quality === "complete" && report.status !== "unavailable" && report.status !== "stale" && report.totals !== null;
  return dates.map(date => {
    const value = measured.get(date);
    return { date, minutes: value !== undefined && (value > 0 || complete) ? value / 60000 : complete ? 0 : null };
  });
}

export function cacheableDays(report: ActivityReportV1, days: DayValue[], today: string): DayValue[] {
  if (!["ok", "no_data"].includes(report.status) || report.totals === null) return [];
  return days.filter(day => day.date < today);
}
