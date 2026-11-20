import { STUDENT_SEARCH_REQUIRED } from "../../analytics-reporting/client";
import { coursePath } from "../scopedApi";

export type Target = { id: number; account_ref: string };
type StatisticsLesson = { id: string; title: string; completed: boolean; completed_on: string | null; counts_toward_statistics: boolean; historical_completion_credit?: boolean };
type StatisticsBlock = { id: string; title: string; completed: number; total: number; lesson_ids: string[] };
type StatisticsTrack = { id: string; title: string; lessons_completed: number; lessons_countable: number; blocks: StatisticsBlock[] };
export type StatisticsCatalog = { tracks: StatisticsTrack[]; courses: Record<string, StatisticsLesson[]> };

const apiPrefix = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api`;
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const isCount = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const isDate = (value: unknown): value is string => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);

function isBlock(value: unknown): value is StatisticsBlock {
  return isRecord(value) && typeof value.id === "string" && typeof value.title === "string"
    && isCount(value.completed) && isCount(value.total) && Array.isArray(value.lesson_ids)
    && value.lesson_ids.every(id => typeof id === "string");
}

function isTrack(value: unknown): value is StatisticsTrack {
  return isRecord(value) && typeof value.id === "string" && typeof value.title === "string"
    && isCount(value.lessons_completed) && isCount(value.lessons_countable)
    && Array.isArray(value.blocks) && value.blocks.every(isBlock);
}

function isLesson(value: unknown): value is StatisticsLesson {
  return isRecord(value) && typeof value.id === "string" && typeof value.title === "string"
    && typeof value.completed === "boolean" && typeof value.counts_toward_statistics === "boolean"
    && (value.historical_completion_credit === undefined || typeof value.historical_completion_credit === "boolean")
    && (value.completed_on === null || isDate(value.completed_on));
}

function isCatalog(value: Record<string, unknown>): value is Record<string, unknown> & StatisticsCatalog {
  const { tracks, courses } = value;
  return Array.isArray(tracks) && tracks.every(isTrack) && isRecord(courses)
    && tracks.every(track => {
      const lessons = courses[track.id];
      return Array.isArray(lessons) && lessons.every(isLesson);
    });
}

export class CatalogAccessError extends Error {
  constructor(readonly status: number, readonly studentSearchRequired = false) { super("Статистика ученика недоступна."); }
}

export async function fetchStudentStatisticsCatalog(courseId: string, target: Target, actorRef: string, signal: AbortSignal): Promise<StatisticsCatalog> {
  signal.throwIfAborted();
  const response = await fetch(`${apiPrefix}${coursePath(courseId)}/users/${target.id}/statistics`, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
    redirect: "error",
    headers: { Accept: "application/json", "X-Actor-Ref": actorRef, "X-Account-Ref": target.account_ref },
    signal,
  });
  signal.throwIfAborted();
  if (response.status === 403) {
    const denial: unknown = await response.json().catch(() => null);
    signal.throwIfAborted();
    throw new CatalogAccessError(response.status, isRecord(denial) && denial.detail === STUDENT_SEARCH_REQUIRED);
  }
  if (response.status === 401 || response.status === 404) throw new CatalogAccessError(response.status);
  if (!response.ok || !response.headers.get("content-type")?.toLowerCase().includes("application/json")) {
    throw new Error("Не удалось загрузить статистику ученика.");
  }
  const data: unknown = await response.json();
  signal.throwIfAborted();
  if (!isRecord(data) || data.course_id !== courseId || data.user_id !== target.id
    || typeof data.account_ref !== "string" || data.account_ref.toLowerCase() !== target.account_ref.toLowerCase()
    || !isCatalog(data)) {
    throw new Error("Получен некорректный ответ со статистикой ученика.");
  }
  return { tracks: data.tracks, courses: data.courses };
}
