import { apiRequest, ApiError } from "../../shared/api/client";
import { allRegisteredCourseIds } from "../../../.cache/course-registry/course-ids";
import type { AdminOverview } from "../../shared/api/contracts/admin";
import type { CourseTrends } from "./CourseOverviewDashboard";

export type Person = { id: number; account_ref: string; username: string; masked_email: string };
export type DisplayBooking = {
  kind: "upcoming" | "latest";
  starts_at: string;
  ends_at: string;
  status: "booked" | "cancelled" | "completed";
};
export type RosterPerson = Person & {
  last_booking_status: string | null;
  display_booking?: DisplayBooking | null;
  subscription_until: string | null;
  remaining_lessons?: number | null;
};
export type Quota = { course_id: string; user_id: number; year: number; month: number; granted: number; used: number; remaining: number };
export type ScopedDashboard = Omit<AdminOverview, "active_students_7d" | "inactive_students_7d" | "daily_active_students"> & { active_students_7d: number | null; inactive_students_7d: number | null; daily_active_students: AdminOverview["daily_active_students"] | null };
export type CourseOverview = { course_id: string; period: { year: number; month: number }; roster_count: number; booking_count: number; lessons_granted: number; lessons_used: number; completed_lessons: number; course_activity_days: null; dashboard: ScopedDashboard; course_trends?: CourseTrends };
export type ActivityToday = { date: string; unique_users: number | null; coverage_since: string | null; partial_day: boolean };
export type PlatformOverview = { users: number; active_subscribers: number; courses: number; course_id: string | null; timezone: "Europe/Moscow"; activity_today: ActivityToday; dashboard: ScopedDashboard };
export type Slot = { id: number; course_id: string; owner_id: number; starts_at: string; ends_at: string; status: "free" | "booked" | "archived"; student_id: number | null };
export type CourseSummary = { course_id: string; leader_id: number | null };
export type PlatformUser = { id: number; account_ref: string; username: string; email: string; role: string; subscription: { subscription_until: string | null }; quotas: Quota[] };
export type Operation = { command_id: string; actor_id: number | null; action: string; course_id: string | null; reason: string | null; anonymized: boolean; created_at: string };
export const coursePath = (courseId: string) => `/admin/courses/${encodeURIComponent(courseId)}`;
export const read = <T,>(path: string, headers: Record<string, string> = {}) => apiRequest<T>(path, undefined, "GET", headers);
// Only hashes and random command IDs are kept across reload; payloads stay in memory.
const prefix = "pymentor:pending-command:v1:";
const subscriptionPrefix = "pymentor:pending-subscription:v1:";
const pendingTtl = 24 * 60 * 60 * 1000;
type PendingCommand = { fingerprint: string; key?: string; createdAt?: number; generation?: string };
function expiredMarker(record: PendingCommand) {
  return JSON.stringify({ fingerprint: record.fingerprint, generation: record.generation });
}
function markerRecord(raw: string | null): PendingCommand | null {
  try { return JSON.parse(raw ?? "null"); } catch { return null; }
}
function scheduleExpiry(operation: string, fingerprint: string, key: string, createdAt: number) {
  if (typeof window === "undefined") return;
  window.setTimeout(() => {
    try {
      const stored = markerRecord(sessionStorage.getItem(operation));
      if (stored?.key === key && stored.fingerprint === fingerprint && stored.createdAt === createdAt && Date.now() - createdAt >= pendingTtl) {
        sessionStorage.setItem(operation, expiredMarker(stored));
      }
    } catch { /* A later retry enforces expiry again. */ }
  }, Math.max(0, createdAt + pendingTtl - Date.now()));
}
function restoreExpiryTimers() {
  if (typeof window === "undefined") return;
  try {
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const operation = sessionStorage.key(i);
      if (!operation?.startsWith(prefix)) continue;
      const record = markerRecord(sessionStorage.getItem(operation));
      if (!record?.key || !record.createdAt || !record.fingerprint) continue;
      if (Date.now() - record.createdAt >= pendingTtl) sessionStorage.setItem(operation, expiredMarker(record));
      else scheduleExpiry(operation, record.fingerprint, record.key, record.createdAt);
    }
  } catch { /* A later retry enforces expiry again. */ }
}
restoreExpiryTimers();
export class ReconciliationRequired extends Error {
  constructor(message: string, readonly operation: string, readonly marker: string, readonly subscriptionIndex?: string) { super(message); }
}
// The caller must explicitly verify the previous business outcome before clearing this marker.
export function acknowledgeReconciliation(error: ReconciliationRequired) {
  removeCommandMarker(error.operation, error.marker, error.subscriptionIndex);
}
function removeCommandMarker(operation: string, expectedMarker: string, subscriptionIndex?: string) {
  const currentMarker = sessionStorage.getItem(operation);
  if (currentMarker === null) return;
  const generation = markerRecord(expectedMarker)?.generation;
  if (currentMarker !== expectedMarker && (!generation || markerRecord(currentMarker)?.generation !== generation)) return;
  sessionStorage.removeItem(operation);
  if (subscriptionIndex && sessionStorage.getItem(subscriptionIndex) === operation) {
    sessionStorage.removeItem(subscriptionIndex);
  }
}
async function hash(value: string) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(bytes), (item) => item.toString(16).padStart(2, "0")).join("");
}
function studentCommandScope(path: string) {
  const course = /^\/admin\/courses\/([^/]+)\/users\/(\d+)\/(subscription-and-quota|subscription-extensions|quota-grants)$/u.exec(path);
  const platform = /^\/admin-platform\/users\/(\d+)\/(subscription-extensions|subscription-reset)$/u.exec(path);
  const userId = course?.[2] ?? platform?.[1];
  if (!userId) return null;
  const selected = course ? `/admin/courses/${course[1]}/users/${userId}` : null;
  if (course?.[3] === "quota-grants") {
    return { combined: false, globalSubscription: false, conflictingPaths: [`${selected}/subscription-and-quota`] };
  }
  const platformUser = `/admin-platform/users/${userId}`;
  const courseUsers = new Set(allRegisteredCourseIds.map(courseId => `${coursePath(courseId)}/users/${userId}`));
  if (selected) courseUsers.add(selected);
  const combined = course?.[3] === "subscription-and-quota";
  const paths = [
    `${platformUser}/subscription-extensions`,
    `${platformUser}/subscription-reset`,
    ...Array.from(courseUsers, base => [`${base}/subscription-extensions`, `${base}/subscription-and-quota`]).flat(),
  ];
  if (combined) paths.push(`${selected}/quota-grants`);
  return { combined, globalSubscription: true, conflictingPaths: paths.filter(candidate => candidate !== path) };
}

export async function command<T>(path: string, body: unknown, accountRef?: string, actorRef: string | undefined = undefined, authority = ""): Promise<T> {
  if (!actorRef) throw new Error("Не удалось подтвердить учётную запись. Обновите страницу и войдите снова.");
  const scope = studentCommandScope(path);
  if (scope?.globalSubscription && !accountRef) throw new Error("Не удалось подтвердить учётную запись ученика.");
  const getOperation = async (candidate: string) => prefix + await hash(JSON.stringify([actorRef, candidate, accountRef]));
  const [operation, fingerprint, subscriptionIndex, conflictingOperations] = await Promise.all([
    getOperation(path),
    hash(JSON.stringify([actorRef, authority, path, body, accountRef])),
    scope?.globalSubscription ? hash(JSON.stringify([actorRef, accountRef])).then(digest => subscriptionPrefix + digest) : undefined,
    Promise.all((scope?.conflictingPaths ?? []).map(getOperation)),
  ]);
  // Read and reserve these markers without yielding, before starting the HTTP request.
  const indexedOperation = subscriptionIndex ? sessionStorage.getItem(subscriptionIndex) : null;
  if (indexedOperation !== null && (!indexedOperation.startsWith(prefix) || !/^[a-f0-9]{64}$/u.test(indexedOperation.slice(prefix.length)))) {
    throw new Error("Не удалось прочитать сведения о предыдущем изменении подписки. Сверьте результат с администратором.");
  }
  if (indexedOperation && indexedOperation !== operation) conflictingOperations.push(indexedOperation);
  for (const candidate of conflictingOperations) {
    const marker = sessionStorage.getItem(candidate);
    if (marker !== null) {
      throw new ReconciliationRequired(
        "Исход предыдущего изменения подписки или выдачи занятий неизвестен. Сверьте результат перед новой операцией.",
        candidate,
        marker,
        subscriptionIndex,
      );
    }
  }
  const savedOperation = sessionStorage.getItem(operation);
  const previous = markerRecord(savedOperation);
  if (savedOperation !== null && previous?.fingerprint !== fingerprint) {
    throw new ReconciliationRequired("Исход предыдущего действия неизвестен. Сверьте результат перед новой операцией.", operation, savedOperation, subscriptionIndex);
  }
  if (previous && (!previous.key || !previous.createdAt || Date.now() - previous.createdAt >= pendingTtl)) {
    const marker = expiredMarker(previous);
    sessionStorage.setItem(operation, marker);
    throw new ReconciliationRequired("Срок повтора неизвестной операции истёк. Сначала сверьте результат с администратором.", operation, marker, subscriptionIndex);
  }
  const key = previous?.key ?? crypto.randomUUID();
  const createdAt = previous?.createdAt ?? Date.now();
  const generation = previous?.generation ?? crypto.randomUUID();
  const marker = JSON.stringify({ fingerprint, key, createdAt, generation });
  try {
    if (subscriptionIndex) sessionStorage.setItem(subscriptionIndex, operation);
    sessionStorage.setItem(operation, marker);
    scheduleExpiry(operation, fingerprint, key, createdAt);
  }
  catch { throw new Error("Не удалось сохранить идентификатор операции. Действие не отправлено."); }
  try {
    const result = await apiRequest<T>(path, body, "POST", {
      "Idempotency-Key": key,
      ...(accountRef ? { "X-Account-Ref": accountRef } : {}),
      ...(scope?.combined ? { "X-Actor-Ref": actorRef } : {}),
    });
    removeCommandMarker(operation, marker, subscriptionIndex);
    return result;
  } catch (error) {
    if (!previous && error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408 && error.status !== 429) {
      removeCommandMarker(operation, marker, subscriptionIndex);
    }
    throw error;
  }
}
