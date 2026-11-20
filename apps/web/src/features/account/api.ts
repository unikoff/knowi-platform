import { apiRequest } from "../../shared/api/client";
import type { ActivitySummaryResponse, ActivityCalendarResponse, ActivityVisitResponse, CalendarMonth, QuotaStatus, StudentSlot } from "../../shared/api/contracts/account";

export function recordActivityVisit(date?: string) {
  return apiRequest<ActivityVisitResponse>("/user/activity/visit", date === undefined ? {} : { activity_date: date });
}

export type MeasuredActivityResponse = {
  course_id?: string;
  days: string[];
  current_streak: number | null;
  streak_is_lower_bound: boolean;
  coverage: "unknown" | "partial" | "complete";
  as_of: string | null;
};

export function getMeasuredActivity(month: CalendarMonth, courseId: string) {
  const params = new URLSearchParams({year: String(month.year), month: String(month.month), course_id: courseId});
  return apiRequest<MeasuredActivityResponse>(`/user/activity/measured?${params}`);
}

export function getActivityCalendar(month: CalendarMonth) {
  return apiRequest<ActivityCalendarResponse>(`/user/activity?year=${month.year}&month=${month.month}`);
}

export function getActivitySummary() {
  return apiRequest<ActivitySummaryResponse>("/user/activity/summary");
}

export type ScopedBooking = { id: number; slot_id: number; starts_at: string; ends_at: string; owner_at_booking_id: number | null; status: string };
export type ScopedSlot = { id: number; starts_at: string; ends_at: string; status: "free" | "mine"; can_cancel: boolean };
export function getBookingContext(courseId: string) {
  return apiRequest<{ active_owner_id: number | null; active_owner_name: string | null; my_upcoming_bookings: ScopedBooking[] }>(`/courses/${encodeURIComponent(courseId)}/booking/context`);
}
export function getStudentSlots(courseId: string, ownerId: number, month: CalendarMonth) {
  return apiRequest<{ slots: ScopedSlot[]; quota: QuotaStatus }>(`/courses/${encodeURIComponent(courseId)}/booking/slots?owner_id=${ownerId}&year=${month.year}&month=${month.month}`);
}
export function bookStudentSlot(courseId: string, slotId: number, commandId: string) {
  return apiRequest(`/courses/${encodeURIComponent(courseId)}/booking/slots/${slotId}/book`, undefined, "POST", { "Idempotency-Key": commandId });
}
export function cancelStudentBooking(courseId: string, bookingId: number, commandId: string) {
  return apiRequest(`/courses/${encodeURIComponent(courseId)}/booking/bookings/${bookingId}/cancel`, undefined, "POST", { "Idempotency-Key": commandId });
}

// The legacy schedule is shared and is only valid for the backend course.
export function getLegacyStudentSlots(month: CalendarMonth) {
  return apiRequest<{ slots: StudentSlot[]; quota: QuotaStatus }>(`/user/slots?year=${month.year}&month=${month.month}`);
}
export function bookLegacyStudentSlot(slotId: number) {
  return apiRequest(`/user/slots/${slotId}/book`, {});
}
export function cancelLegacyStudentBooking(slotId: number) {
  return apiRequest(`/user/slots/${slotId}/cancel`, {});
}
