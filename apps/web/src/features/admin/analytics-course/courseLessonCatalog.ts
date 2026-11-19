import { ApiError } from "../../../shared/api/client";

export type CourseLessonOption = {id: string; title: string};
const prefix = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api`;
const object = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);

// The existing course catalog is requested for this explicit course. The signal
// is also checked after JSON decoding so a late response cannot repopulate UI.
export async function fetchCourseLessons(courseId: string, signal: AbortSignal): Promise<CourseLessonOption[]> {
  if (signal.aborted) throw new DOMException("Request aborted", "AbortError");
  const query = new URLSearchParams({course_id: courseId});
  const response = await fetch(`${prefix}/learning/catalog?${query}`, {method: "GET", credentials: "include", cache: "no-store", headers: {Accept: "application/json"}, signal});
  if (signal.aborted) throw new DOMException("Request aborted", "AbortError");
  if (response.status === 401 || response.status === 403) throw new ApiError("Нет доступа к каталогу уроков", response.status);
  if (!response.ok || !response.headers.get("content-type")?.toLowerCase().includes("application/json")) throw new Error("Каталог уроков недоступен");
  const data: unknown = await response.json();
  if (signal.aborted) throw new DOMException("Request aborted", "AbortError");
  if (!object(data) || !Array.isArray(data.tracks) || !object(data.courses)) throw new Error("Некорректный каталог уроков");
  const lessons: CourseLessonOption[] = [], seen = new Set<string>();
  for (const track of data.tracks) {
    if (!object(track) || typeof track.id !== "string") throw new Error("Некорректный каталог уроков");
    const entries = data.courses[track.id];
    if (!Array.isArray(entries)) throw new Error("Некорректный каталог уроков");
    for (const lesson of entries) {
      if (!object(lesson) || typeof lesson.id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(lesson.id) || typeof lesson.title !== "string" || seen.has(lesson.id)) continue;
      seen.add(lesson.id); lessons.push({id: lesson.id, title: lesson.title.trim() || `Урок (${lesson.id})`});
    }
  }
  return lessons;
}
