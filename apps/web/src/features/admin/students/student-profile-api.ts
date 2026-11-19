import type { CalendarMonth } from "../../../shared/api/contracts/account";
import { getMoscowDateKey } from "../../../shared/lib/dates";
import { STUDENT_SEARCH_REQUIRED } from "../../analytics-reporting/client";
import { coursePath } from "../scopedApi";
import type { Target } from "./student-statistics-api";

export type StudentBooking = { id: number; starts_at: string; ends_at: string; status: "booked" | "completed" | "cancelled" };
export type StudentProfileDetails = {
  bookings: StudentBooking[];
  bookingsComplete: boolean;
  subscriptionUntil: string | null;
  subscriptionPlan: string | null;
};

const prefix = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api`;
const maxPages = 10;
const pageSize = 100;
const object = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const timestamp = (value: unknown): value is string => typeof value === "string"
  && /T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));

export function monthDateRange({ year, month }: CalendarMonth) {
  const prefix = `${year}-${String(month).padStart(2, "0")}`;
  return { from: `${prefix}-01`, to: `${prefix}-${new Date(Date.UTC(year, month, 0)).getUTCDate()}` };
}

function bookingInRange(value: unknown, range: ReturnType<typeof monthDateRange>): value is StudentBooking {
  if (!object(value) || typeof value.id !== "number" || !Number.isSafeInteger(value.id) || value.id < 1
    || !timestamp(value.starts_at) || !timestamp(value.ends_at) || Date.parse(value.ends_at) <= Date.parse(value.starts_at)
    || (value.status !== "booked" && value.status !== "completed" && value.status !== "cancelled")) return false;
  const day = getMoscowDateKey(new Date(value.starts_at));
  return day >= range.from && day <= range.to;
}

export class StudentProfileReadError extends Error {
  constructor(readonly kind: "search-required" | "denied" | "invalid" | "unavailable") {
    super("Не удалось получить данные профиля ученика.");
  }
}

export async function fetchStudentProfileDetails(courseId: string, target: Target, actorRef: string, month: CalendarMonth,
  signal: AbortSignal): Promise<StudentProfileDetails> {
  const range = monthDateRange(month);
  const details: StudentProfileDetails = {bookings: [], bookingsComplete: false, subscriptionUntil: null, subscriptionPlan: null};
  const seen = new Set<number>();
  const cursors = new Set<string>();
  let cursor: string | undefined;

  for (let page = 0; page < maxPages; page += 1) {
    signal.throwIfAborted();
    const params = new URLSearchParams({ year: String(month.year), month: String(month.month), limit: String(pageSize), ...(cursor ? { cursor } : {}) });
    const response = await fetch(`${prefix}${coursePath(courseId)}/users/${target.id}/profile-details?${params}`, {
      headers: { Accept: "application/json", "X-Actor-Ref": actorRef, "X-Account-Ref": target.account_ref },
      credentials: "include", cache: "no-store", redirect: "error", signal,
    });
    signal.throwIfAborted();
    if (response.status === 403) {
      const denial: unknown = await response.json().catch(() => null);
      signal.throwIfAborted();
      throw new StudentProfileReadError(object(denial) && denial.detail === STUDENT_SEARCH_REQUIRED ? "search-required" : "denied");
    }
    if (response.status === 401 || response.status === 404) throw new StudentProfileReadError("denied");
    if (!response.ok) throw new StudentProfileReadError("unavailable");
    if (!response.headers.get("content-type")?.toLowerCase().includes("application/json")) throw new StudentProfileReadError("invalid");
    const data: unknown = await response.json().catch(() => null);
    signal.throwIfAborted();
    if (!object(data) || data.course_id !== courseId || data.user_id !== target.id
      || typeof data.account_ref !== "string" || data.account_ref.toLowerCase() !== target.account_ref.toLowerCase()
      || !object(data.period) || data.period.year !== month.year || data.period.month !== month.month
      || !(data.subscription_until === null || timestamp(data.subscription_until))
      || !(data.subscription_plan === null || typeof data.subscription_plan === "string")
      || !Array.isArray(data.bookings) || data.bookings.length > pageSize || !data.bookings.every(item => bookingInRange(item, range))
      || !(data.next_cursor === null || (typeof data.next_cursor === "string" && data.next_cursor.length > 0 && data.next_cursor.length <= 2048))) {
      throw new StudentProfileReadError("invalid");
    }
    details.subscriptionUntil = data.subscription_until;
    details.subscriptionPlan = data.subscription_plan;
    for (const booking of data.bookings) {
      if (seen.has(booking.id)) throw new StudentProfileReadError("invalid");
      seen.add(booking.id);
      details.bookings.push(booking);
    }
    if (data.next_cursor === null) return {...details, bookingsComplete: true};
    if (data.bookings.length < pageSize || cursors.has(data.next_cursor)) throw new StudentProfileReadError("invalid");
    cursors.add(data.next_cursor);
    cursor = data.next_cursor;
  }
  return details;
}
