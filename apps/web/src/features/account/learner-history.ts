import { validateStudyRange } from "./course-statistics-model";

export type LegacyHistoryDay = {date: string; theory_ms: 3600000; practice_ms: 3600000; provenance: "core_legacy_conditional"};

export function parseLearnerHistory(value: unknown, from: string, to: string): LegacyHistoryDay[] {
  if (!validateStudyRange(from, to).valid || !value || typeof value !== "object" || !Array.isArray((value as {days?: unknown}).days)) throw new Error("invalid history");
  const days = (value as {days: unknown[]}).days;
  if (days.length > 31) throw new Error("invalid history");
  const seen = new Set<string>();
  for (const item of days) {
    if (!item || typeof item !== "object") throw new Error("invalid history");
    const day = item as Record<string, unknown>;
    if (typeof day.date !== "string" || !validateStudyRange(day.date, day.date).valid || day.date < from || day.date > to || seen.has(day.date) || day.theory_ms !== 3600000 || day.practice_ms !== 3600000 || day.provenance !== "core_legacy_conditional") throw new Error("invalid history");
    seen.add(day.date);
  }
  return (days as LegacyHistoryDay[]).slice().sort((a, b) => a.date.localeCompare(b.date));
}

export async function fetchLearnerHistory(from: string, to: string, signal: AbortSignal): Promise<LegacyHistoryDay[]> {
  if (!validateStudyRange(from, to).valid) throw new Error("invalid range");
  const query = new URLSearchParams({start: from, end: to});
  const prefix = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api`;
  const response = await fetch(`${prefix}/user/activity/history?${query}`, {credentials: "include", cache: "no-store", redirect: "error", headers: {Accept: "application/json"}, signal});
  if (!response.ok || !response.headers.get("content-type")?.toLowerCase().includes("application/json")) throw new Error("history unavailable");
  return parseLearnerHistory(await response.json(), from, to);
}
