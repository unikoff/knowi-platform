import { useEffect, useState } from "react";
import { coursePath } from "./scopedApi";

type TimeCohort = { active_ms: number; learners: number };
export type StudyTimeDay = {
  date: string;
  with_subscription: TimeCohort | null;
  without_subscription: TimeCohort | null;
};

export type CourseStudyTimeContext = {
  courseId: string;
  actorRef?: string;
  actorRole?: string;
  authRevision?: number;
};

type TimeReport = { status: "available"; quality: "unknown"; daily: StudyTimeDay[] };
type TimeView = TimeReport | { status: "loading" | "unavailable" | "denied" | "error" };

const apiPrefix = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api`;
const loading: TimeView = { status: "loading" };
const unavailable: TimeView = { status: "unavailable" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPositiveCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function isCohort(value: unknown): value is TimeCohort | null {
  return value === null || (isRecord(value) && isPositiveCount(value.active_ms) && isPositiveCount(value.learners));
}

function isDay(value: unknown): value is StudyTimeDay {
  if (!isRecord(value) || typeof value.date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value.date)
    || !isCohort(value.with_subscription) || !isCohort(value.without_subscription)) return false;

  const date = new Date(`${value.date}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value.date
    && Number.isSafeInteger((value.with_subscription?.active_ms ?? 0) + (value.without_subscription?.active_ms ?? 0))
    && Number.isSafeInteger((value.with_subscription?.learners ?? 0) + (value.without_subscription?.learners ?? 0));
}

export async function fetchCourseStudyTime(courseId: string, actorRef: string, authRevision: number, signal: AbortSignal): Promise<TimeView> {
  signal.throwIfAborted();
  const response = await fetch(`${apiPrefix}${coursePath(courseId)}/study-time`, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
    redirect: "error",
    headers: { Accept: "application/json", "X-Actor-Ref": actorRef, "X-Auth-Revision": String(authRevision) },
    signal,
  });
  signal.throwIfAborted();
  if (response.status === 404) return unavailable;
  if (response.status === 401 || response.status === 403) return { status: "denied" };
  if (!response.ok || !response.headers.get("content-type")?.toLowerCase().includes("application/json")) return { status: "error" };

  const payload: unknown = await response.json();
  signal.throwIfAborted();
  if (!isRecord(payload) || payload.quality !== "unknown" || !Array.isArray(payload.daily)) return { status: "error" };
  if (payload.status === "unavailable" && payload.daily.length === 0) return unavailable;
  const daily: unknown[] = payload.daily;
  if (payload.status !== "available" || daily.length !== 30 || !daily.every(isDay)) return { status: "error" };
  return daily.every((day, index) => index === 0 || Date.parse(day.date) - Date.parse(daily[index - 1].date) === 86_400_000)
    ? { status: "available", quality: "unknown", daily }
    : { status: "error" };
}

export function useCourseStudyTime(context?: CourseStudyTimeContext) {
  const { courseId, actorRef, actorRole, authRevision } = context ?? {};
  const allowed = Boolean(courseId && actorRef && (actorRole === "leader" || actorRole === "admin")
    && authRevision !== undefined && authRevision >= 0 && Number.isSafeInteger(authRevision));
  const [attempt, setAttempt] = useState(0);
  const key = allowed ? JSON.stringify([courseId, actorRef, actorRole, authRevision, attempt]) : null;
  const [snapshot, setSnapshot] = useState<{ key: string; view: TimeView }>();

  useEffect(() => {
    setSnapshot(undefined);
    if (!key || !courseId || !actorRef || authRevision === undefined) return;
    const controller = new AbortController();
    fetchCourseStudyTime(courseId, actorRef, authRevision, controller.signal).then(
      view => { if (!controller.signal.aborted) setSnapshot({ key, view }); },
      () => { if (!controller.signal.aborted) setSnapshot({ key, view: { status: "error" } }); },
    );
    return () => controller.abort();
  }, [key, courseId, actorRef, authRevision]);

  const view = !key ? unavailable : snapshot?.key === key ? snapshot.view : loading;
  return { view, retry: () => setAttempt(previous => previous + 1) };
}
