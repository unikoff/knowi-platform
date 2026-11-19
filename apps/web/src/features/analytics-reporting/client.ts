import type { ActivityChaptersFilters, ActivityChaptersReport, ActivityDetailsFilters, ActivityDetailsReport, ActivityLifetimeOutlineFilters, ActivityMetrics, ActivityOutlineLesson, ActivityOutlineReport, ActivityReportFilters, ActivityReportV1, CalendarActivityDelta, CalendarValidators, StudentActivityFilters, StudentActivityTarget } from "./types";

export class AnalyticsReportError extends Error {
  constructor(readonly kind: "denied" | "unavailable" | "invalid_response", readonly studentSearchRequired = false) {
    super(kind === "denied" ? "Нет доступа к учебной аналитике." : kind === "invalid_response" ? "Получен некорректный ответ аналитики." : "Учебная аналитика временно недоступна.");
    this.name = "AnalyticsReportError";
  }
}
export const STUDENT_SEARCH_REQUIRED = "Select an account through course search";
const prefix = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api/analytics`;
const safeInt = (v: unknown): v is number => typeof v === "number" && Number.isSafeInteger(v) && v >= 0;
const object = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const uuid = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v);
const validStudentTarget = (target: StudentActivityTarget | undefined) => target && safeInt(target.user_id)
  && target.user_id > 0 && uuid(target.expected_target_ref);
const timestamp = (v: unknown): v is string => typeof v === "string" && /(?:Z|[+-]\d{2}:\d{2})$/i.test(v) && Number.isFinite(Date.parse(v));
const utc = (v: unknown): v is string => timestamp(v) && /(?:Z|\+00:00|-00:00)$/i.test(v);
const identifier = (v: unknown): v is string => typeof v === "string" && v.length <= 128 && /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(v);
function values(v: unknown): v is ActivityMetrics {
  return object(v) && safeInt(v.unique_visitors) && safeInt(v.active_ms) && safeInt(v.theory_ms) && safeInt(v.practice_ms) && v.active_ms === v.theory_ms + v.practice_ms;
}
function validFilters(f: ActivityReportFilters) {
  if (!utc(f.from_time) || !utc(f.to_time)) return false;
  const start = Date.parse(f.from_time), end = Date.parse(f.to_time);
  return end > start && end - start <= 31 * 86400000 && (!f.course_id || (identifier(f.course_id) && f.course_id.length <= 100)) && (f.lesson_id == null || (identifier(f.lesson_id) && !!f.course_id)) && (f.subject_ref == null || uuid(f.subject_ref)) && (f.user_id == null || (safeInt(f.user_id) && f.user_id > 0)) && !(f.subject_ref != null && f.user_id != null) && (f.expected_target_ref == null || (f.user_id != null && uuid(f.expected_target_ref)));
}
function validDetails(v: unknown, f: ActivityDetailsFilters): v is ActivityDetailsReport {
  const offset = f.offset ?? 0, limit = f.limit ?? 100;
  if (!object(v) || !validReport(v.report, f) || v.lesson_id !== (f.lesson_id ?? null) || !Array.isArray(v.rows) || v.rows.length > limit) return false;
  if (v.next_offset !== null && (!safeInt(v.next_offset) || v.next_offset > 20000 || v.next_offset !== offset + limit || v.rows.length !== limit)) return false;
  for (const row of v.rows) {
    if (!object(row) || !identifier(row.course_id) || (f.course_id && row.course_id !== f.course_id) || !["course", "lesson"].includes(String(row.level)) || (row.level === "course" ? row.lesson_id !== null : !identifier(row.lesson_id)) || (f.lesson_id && row.level === "lesson" && row.lesson_id !== f.lesson_id) || !values(row)) return false;
  }
  return true;
}
function validReport(v: unknown, f: ActivityReportFilters): v is ActivityReportV1 {
  if (!object(v) || v.schema_version !== 1 || v.metric_version !== "activity-v1" || v.timezone !== "Europe/Moscow" || !["ok", "no_data", "stale", "unavailable"].includes(String(v.status)) || !["complete", "partial", "unknown"].includes(String(v.quality)) || v.course_id !== (f.course_id || null) || !utc(v.from_time) || !utc(v.to_time) || Date.parse(v.from_time) !== Date.parse(f.from_time) || Date.parse(v.to_time) !== Date.parse(f.to_time) || !(v.as_of === null || timestamp(v.as_of)) || !(v.lag_seconds === null || safeInt(v.lag_seconds)) || !Array.isArray(v.daily) || v.daily.length > 32) return false;
  if (v.totals !== null && !values(v.totals)) return false;
  if (v.status === "unavailable" && (v.totals !== null || v.quality !== "unknown" || v.daily.length)) return false;
  if (v.status === "ok" && v.totals === null) return false;
  if (v.status === "no_data" && (v.quality !== "complete" || !values(v.totals) || v.as_of === null || Object.values(v.totals).some(n => n !== 0))) return false;
  let previous = "";
  let active = 0, theory = 0, practice = 0;
  for (const day of v.daily) {
    if (!object(day) || typeof day.date !== "string") return false;
    const date = day.date;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || date <= previous || !values(day)) return false;
    const dateMs = Date.parse(`${date}T00:00:00+03:00`);
    if (!Number.isFinite(dateMs) || new Date(dateMs + 10800000).toISOString().slice(0, 10) !== date || dateMs >= Date.parse(f.to_time) || dateMs + 86400000 <= Date.parse(f.from_time)) return false;
    previous = date; active += day.active_ms; theory += day.theory_ms; practice += day.practice_ms;
  }
  if (v.totals !== null && (!values(v.totals) || active !== v.totals.active_ms || theory !== v.totals.theory_ms || practice !== v.totals.practice_ms)) return false;
  if (v.trusted_progress !== undefined && v.trusted_progress !== null && (!object(v.trusted_progress) || !["known", "unavailable"].includes(String(v.trusted_progress.state)) || (v.trusted_progress.state === "known" ? !safeInt(v.trusted_progress.completed_lessons) : v.trusted_progress.completed_lessons !== null))) return false;
  return true;
}
async function json(response: Response): Promise<unknown> {
  if (response.status === 401 || response.status === 403) throw new AnalyticsReportError("denied");
  if (!response.ok) throw new AnalyticsReportError("unavailable");
  if (!response.headers.get("content-type")?.toLowerCase().includes("application/json")) throw new AnalyticsReportError("invalid_response");
  try { return await response.json(); } catch { throw new AnalyticsReportError("invalid_response"); }
}

/** No token is persisted; the timeout covers both grant and report requests. */
async function fetchActivity<T>(
  input: ActivityDetailsFilters,
  expectedActorRef: string,
  signal: AbortSignal | undefined,
  validate: (report: unknown) => report is T,
  mode?: "details" | {route: "chapters" | "outline" | "outline/lifetime" | "outline/lifetime/student"; selection: ActivityChaptersFilters | ActivityLifetimeOutlineFilters},
  studentTarget?: StudentActivityTarget,
  calendar?: {
    onGrant: (marker: string) => Promise<CalendarValidators | null>;
    validate: (result: unknown) => result is T;
  },
): Promise<T> {
  const filters = studentTarget
    ? {...input, user_id: studentTarget.user_id, expected_target_ref: studentTarget.expected_target_ref}
    : {...input};
  const details = mode === "details";
  const selection = typeof mode === "object" ? mode : undefined;
  if (!uuid(expectedActorRef)) throw new AnalyticsReportError("denied");
  if (!validFilters(filters)) throw new AnalyticsReportError("invalid_response");
  if (studentTarget && !filters.course_id) throw new AnalyticsReportError("invalid_response");
  if (details && (!(filters.offset == null || (safeInt(filters.offset) && filters.offset <= 20000)) || !(filters.limit == null || (safeInt(filters.limit) && filters.limit >= 1 && filters.limit <= 100)))) throw new AnalyticsReportError("invalid_response");
  const controller = new AbortController();
  const cancel = () => controller.abort();
  if (signal?.aborted) cancel(); else signal?.addEventListener("abort", cancel, {once: true});
  let timedOut = false;
  const timer = setTimeout(() => {timedOut = true; controller.abort();}, 12000);
  try {
    if (controller.signal.aborted) throw new DOMException("Request aborted", "AbortError");
    const scope = studentTarget
      ? {course_id: filters.course_id, user_id: filters.user_id, expected_target_ref: filters.expected_target_ref, expected_actor_ref: expectedActorRef, student_statistics: true}
      : {...(filters.course_id ? {course_id: filters.course_id} : {}), ...(filters.subject_ref ? {subject_ref: filters.subject_ref} : {}), ...(filters.user_id != null ? {user_id: filters.user_id} : {})};
    const grantResponse = await fetch(`${prefix}/grants/report`, {method: "POST", credentials: "include", cache: "no-store", redirect: "error", headers: {Accept: "application/json", "Content-Type": "application/json"}, body: JSON.stringify(scope), signal: controller.signal});
    if (studentTarget && grantResponse.status === 403) {
      const denial: unknown = await grantResponse.json().catch(() => null);
      throw new AnalyticsReportError("denied", object(denial) && denial.detail === STUDENT_SEARCH_REQUIRED);
    }
    const grant = await json(grantResponse);
    if (!object(grant) || typeof grant.grant !== "string" || !grant.grant || grant.grant.length > 16384 || !uuid(grant.subject_ref) || !timestamp(grant.server_time) || !timestamp(grant.expires_at) || Date.parse(grant.expires_at) <= Date.parse(grant.server_time)) throw new AnalyticsReportError("invalid_response");
    if (grant.subject_ref.toLowerCase() !== expectedActorRef.toLowerCase()) throw new AnalyticsReportError("denied");
    if (filters.user_id != null ? !uuid(grant.target_subject_ref) : grant.target_subject_ref != null) throw new AnalyticsReportError("invalid_response");
    if (filters.expected_target_ref && (grant.target_subject_ref as string).toLowerCase() !== filters.expected_target_ref.toLowerCase()) throw new AnalyticsReportError("denied");
    if (controller.signal.aborted) throw new DOMException("Request aborted", "AbortError");
    const target = filters.user_id != null ? grant.target_subject_ref as string : filters.subject_ref;
    const params = new URLSearchParams({from_time: filters.from_time, to_time: filters.to_time, timezone: "Europe/Moscow", ...(filters.course_id ? {course_id: filters.course_id} : {}), ...(filters.lesson_id ? {lesson_id: filters.lesson_id} : {}), ...(target ? {subject_ref: target} : {}), ...(details ? {offset: String(filters.offset ?? 0), limit: String(filters.limit ?? 100)} : {})});
    const marker = calendar && grantResponse.headers.get("X-Calendar-Session-Marker");
    if (calendar && marker && /^[a-f0-9]{64}$/.test(marker)) {
      const known = await calendar.onGrant(marker);
      if (controller.signal.aborted) throw new DOMException("Request aborted", "AbortError");
      if (known) {
        const response = await fetch(`${prefix}/reports/activity/calendar`, {
          method: "POST", credentials: "omit", cache: "no-store", redirect: "error",
          headers: {Accept: "application/json", "Content-Type": "application/json", Authorization: `Bearer ${grant.grant}`},
          body: JSON.stringify({course_id: filters.course_id, from_time: filters.from_time, to_time: filters.to_time, ...known}),
          signal: controller.signal,
        });
        // Older Query instances still support an ordinary self report.
        if (![404, 405, 501].includes(response.status)) {
          const delta = await json(response);
          if (controller.signal.aborted) throw new DOMException("Request aborted", "AbortError");
          if (!calendar.validate(delta)) throw new AnalyticsReportError("invalid_response");
          return delta;
        }
      }
    }
    const body = selection?.route === "outline/lifetime/student"
      ? {...selection.selection, subject_ref: target}
      : selection?.selection;
    const response = await fetch(selection ? `${prefix}/reports/activity/${selection.route}` : `${prefix}/reports/activity${details ? "/details" : ""}?${params}`,
      {method: selection ? "POST" : "GET", ...(body ? {body: JSON.stringify(body)} : {}), credentials: "omit", cache: "no-store", redirect: "error", headers: {Accept: "application/json", ...(selection ? {"Content-Type": "application/json"} : {}), Authorization: `Bearer ${grant.grant}`}, signal: controller.signal});
    const report = await json(response);
    if (controller.signal.aborted) throw new DOMException("Request aborted", "AbortError");
    if (!validate(report)) throw new AnalyticsReportError("invalid_response");
    return report;
  } catch (error) {
    if (signal?.aborted && !timedOut) throw new DOMException("Request aborted", "AbortError");
    if (error instanceof AnalyticsReportError) throw error;
    throw new AnalyticsReportError("unavailable");
  } finally {clearTimeout(timer); signal?.removeEventListener("abort", cancel);}
}

export function fetchActivityReport(filters: ActivityReportFilters, expectedActorRef: string, signal?: AbortSignal): Promise<ActivityReportV1> {
  const copy = {...filters};
  return fetchActivity(copy, expectedActorRef, signal, (report): report is ActivityReportV1 => validReport(report, copy));
}

function validCalendarDelta(result: unknown): result is CalendarActivityDelta {
  const revision = (token: unknown) => typeof token === "string" && /^[a-f0-9]{64}$/.test(token);
  const date = (value: unknown) => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
  if (!object(result) || !revision(result.history_generation) || !(result.as_of === null || timestamp(result.as_of))
      || !Array.isArray(result.reused_dates) || result.reused_dates.length > 28 || !result.reused_dates.every(date)
      || !Array.isArray(result.revisions) || result.revisions.length > 28 || !Array.isArray(result.daily) || result.daily.length > 31) return false;
  return result.revisions.every(item => object(item) && date(item.date) && (item.token === null || revision(item.token)))
    && result.daily.every(item => object(item) && date(item.date) && values(item) && item.unique_visitors <= 1);
}

export function fetchCalendarActivityReport(filters: ActivityReportFilters, expectedActorRef: string, signal: AbortSignal,
                                           onGrant: (marker: string) => Promise<CalendarValidators | null>): Promise<ActivityReportV1 | CalendarActivityDelta> {
  const copy = {...filters};
  return fetchActivity<ActivityReportV1 | CalendarActivityDelta>(copy, expectedActorRef, signal,
    (report): report is ActivityReportV1 => validReport(report, copy),
    undefined, undefined, {onGrant, validate: validCalendarDelta});
}

export function fetchStudentActivityReport(filters: StudentActivityFilters, studentTarget: StudentActivityTarget, expectedActorRef: string, signal?: AbortSignal): Promise<ActivityReportV1> {
  if (!validStudentTarget(studentTarget)) return Promise.reject(new AnalyticsReportError("invalid_response"));
  const copy = {course_id: filters.course_id, from_time: filters.from_time, to_time: filters.to_time};
  return fetchActivity(copy, expectedActorRef, signal, (report): report is ActivityReportV1 => validReport(report, copy), undefined, studentTarget);
}

export function fetchActivityDetails(filters: ActivityDetailsFilters, expectedActorRef: string, signal?: AbortSignal): Promise<ActivityDetailsReport> {
  const copy = {...filters};
  return fetchActivity(copy, expectedActorRef, signal, (report): report is ActivityDetailsReport => validDetails(report, copy), "details");
}

function validChapterInput(f: ActivityChaptersFilters): boolean {
  if (!validFilters(f) || !identifier(f.course_id) || f.chapters.length > 100) return false;
  const ids = new Set<string>(), lessons = new Set<string>();
  for (const chapter of f.chapters) {
    if (typeof chapter.chapter_id !== "string" || chapter.chapter_id.length === 0 || [...chapter.chapter_id].length > 128 || ids.has(chapter.chapter_id)) return false;
    ids.add(chapter.chapter_id);
    for (const lesson of chapter.lesson_ids) { if (!identifier(lesson) || lessons.has(lesson)) return false; lessons.add(lesson); }
  }
  return lessons.size <= 2000;
}
function validChapters(v: unknown, f: ActivityChaptersFilters): v is ActivityChaptersReport & Record<string, unknown> {
  if (!object(v) || v.course_id !== f.course_id || !utc(v.from_time) || !utc(v.to_time) || Date.parse(v.from_time) !== Date.parse(f.from_time) || Date.parse(v.to_time) !== Date.parse(f.to_time) || !["ok", "no_data", "stale", "unavailable"].includes(String(v.status)) || !["complete", "partial", "unknown"].includes(String(v.quality)) || !(v.as_of === null || timestamp(v.as_of)) || !Array.isArray(v.chapters) || v.chapters.length !== f.chapters.length) return false;
  const expected = new Set(f.chapters.map(c => c.chapter_id));
  for (const c of v.chapters) {
    if (!object(c) || typeof c.chapter_id !== "string" || !expected.delete(c.chapter_id) || ![c.active_ms, c.theory_ms, c.practice_ms].every(n => n === null || safeInt(n))) return false;
    if (safeInt(c.active_ms) && safeInt(c.theory_ms) && safeInt(c.practice_ms) && c.active_ms !== c.theory_ms + c.practice_ms) return false;
  }
  return expected.size === 0;
}
function copyChapterFilters(filters: ActivityChaptersFilters): ActivityChaptersFilters {
  const {course_id, from_time, to_time, chapters} = filters;
  return {course_id, from_time, to_time, chapters: chapters.map(({chapter_id, lesson_ids}) => ({chapter_id, lesson_ids: [...lesson_ids]}))};
}
export function fetchActivityChapters(filters: ActivityChaptersFilters, expectedActorRef: string, signal?: AbortSignal): Promise<ActivityChaptersReport> {
  const copy = copyChapterFilters(filters);
  if (!validChapterInput(copy)) return Promise.reject(new AnalyticsReportError("invalid_response"));
  return fetchActivity(copy, expectedActorRef, signal, (report): report is ActivityChaptersReport => validChapters(report, copy), {route: "chapters", selection: copy});
}

function validOutlineLesson(row: unknown): row is ActivityOutlineLesson {
  return object(row) && identifier(row.lesson_id) && safeInt(row.active_ms) && safeInt(row.theory_ms)
    && safeInt(row.practice_ms) && row.active_ms === row.theory_ms + row.practice_ms;
}

function validOutline(value: unknown, filters: ActivityChaptersFilters): value is ActivityOutlineReport {
  if (!object(value) || typeof value.status !== "string" || typeof value.quality !== "string") return false;
  const {lessons: requestedRows, unmapped_lessons: unmappedRows} = value;
  if (!validChapters(value, filters) || !Array.isArray(requestedRows) || !Array.isArray(unmappedRows) || unmappedRows.length > 20000) return false;

  const selectedIds = filters.chapters.flatMap(chapter => chapter.lesson_ids);
  const remainingIds = new Set(selectedIds);
  if (requestedRows.length !== remainingIds.size) return false;
  const lessons = new Map<string, ActivityOutlineLesson>();
  for (const row of requestedRows) {
    if (!validOutlineLesson(row) || !remainingIds.delete(row.lesson_id)) return false;
    lessons.set(row.lesson_id, row);
  }
  const seen = new Set(selectedIds);
  for (const row of unmappedRows) {
    if (!validOutlineLesson(row) || seen.has(row.lesson_id)) return false;
    seen.add(row.lesson_id);
  }
  if (value.status === "no_data" && (value.quality !== "complete" || value.as_of === null
    || unmappedRows.length !== 0 || [...lessons.values()].some(row => row.active_ms !== 0))) return false;

  const chapters = new Map(value.chapters.map(row => [row.chapter_id, row]));
  for (const {chapter_id, lesson_ids} of filters.chapters) {
    const totals = {active_ms: 0, theory_ms: 0, practice_ms: 0};
    for (const id of lesson_ids) {
      const lesson = lessons.get(id)!;
      totals.active_ms += lesson.active_ms;
      totals.theory_ms += lesson.theory_ms;
      totals.practice_ms += lesson.practice_ms;
    }
    const chapter = chapters.get(chapter_id)!;
    for (const metric of ["active_ms", "theory_ms", "practice_ms"] as const) {
      const observed = totals[metric];
      if (!safeInt(observed)) return false;
      if (chapter[metric] !== observed && !(value.quality !== "complete" && chapter[metric] === null && observed === 0)) return false;
    }
  }
  return true;
}

export function fetchActivityOutline(filters: ActivityChaptersFilters, expectedActorRef: string, signal?: AbortSignal): Promise<ActivityOutlineReport> {
  const copy = copyChapterFilters(filters);
  if (!validChapterInput(copy)) return Promise.reject(new AnalyticsReportError("invalid_response"));
  return fetchActivity(copy, expectedActorRef, signal, (report): report is ActivityOutlineReport => validOutline(report, copy), {route: "outline", selection: copy});
}

function fetchLifetimeOutline(filters: ActivityLifetimeOutlineFilters, expectedActorRef: string, signal?: AbortSignal, studentTarget?: StudentActivityTarget): Promise<ActivityOutlineReport> {
  const selection = {course_id: filters.course_id, chapters: filters.chapters.map(({chapter_id, lesson_ids}) => ({chapter_id, lesson_ids: [...lesson_ids]}))};
  // The grant helper validates a short window; the lifetime endpoint receives no dates.
  const scope = {...selection, from_time: "2000-01-01T00:00:00Z", to_time: "2000-01-02T00:00:00Z"};
  if (!validChapterInput(scope)) return Promise.reject(new AnalyticsReportError("invalid_response"));
  return fetchActivity(scope, expectedActorRef, signal, (value): value is ActivityOutlineReport => {
    if (!object(value) || !utc(value.from_time) || !utc(value.to_time) || Date.parse(value.to_time) <= Date.parse(value.from_time)) return false;
    return validOutline(value, {...selection, from_time: value.from_time, to_time: value.to_time});
  }, {route: studentTarget ? "outline/lifetime/student" : "outline/lifetime", selection}, studentTarget);
}

export function fetchLifetimeActivityOutline(filters: ActivityLifetimeOutlineFilters, expectedActorRef: string, signal?: AbortSignal): Promise<ActivityOutlineReport> {
  return fetchLifetimeOutline(filters, expectedActorRef, signal);
}

export function fetchStudentLifetimeActivityOutline(filters: ActivityLifetimeOutlineFilters, studentTarget: StudentActivityTarget, expectedActorRef: string, signal?: AbortSignal): Promise<ActivityOutlineReport> {
  if (!validStudentTarget(studentTarget)) return Promise.reject(new AnalyticsReportError("invalid_response"));
  return fetchLifetimeOutline(filters, expectedActorRef, signal, studentTarget);
}
