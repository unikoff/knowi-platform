import type { CourseReportRange } from "./course-statistics-analytics";

export async function fetchPeriodCompletion({courseId, from, to}: CourseReportRange, signal: AbortSignal): Promise<number> {
  const query = new URLSearchParams({course_id: courseId, from, to});
  const prefix = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api`;
  const response = await fetch(`${prefix}/learning/progress/completed-count?${query}`, {
    credentials: "include",
    cache: "no-store",
    redirect: "error",
    headers: {Accept: "application/json"},
    signal,
  });
  if (!response.ok || !response.headers.get("content-type")?.toLowerCase().includes("application/json")) {
    throw new Error("Не удалось загрузить число завершённых занятий");
  }
  const data: {course_id?: unknown; from?: unknown; to?: unknown; completed_lessons?: unknown} | null = await response.json();
  const count = data?.completed_lessons;
  if (data?.course_id !== courseId || data.from !== from || data.to !== to || typeof count !== "number" || !Number.isSafeInteger(count) || count < 0) {
    throw new Error("Число завершённых занятий вернулось в неожиданном формате");
  }
  return count;
}
