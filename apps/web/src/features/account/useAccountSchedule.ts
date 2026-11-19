import { useCallback, useEffect, useRef, useState } from "react";
import { getActivitySummary, getMeasuredActivity, getBookingContext, getStudentSlots } from "./api";
import { command } from "../admin/scopedApi";
import { ApiError } from "../../shared/api/client";
import type { ActivitySummaryResponse, CalendarMonth, QuotaStatus, StudentSlot } from "../../shared/api/contracts/account";
import { createActivityRead } from "./activityRead";

const emptyActivityDays = new Set<string>();
const isActivityUnavailable = (error: unknown) => error instanceof ApiError && error.status === 503;
export type AccountActivityStatus = "disabled" | "loading" | "unavailable" | "unknown" | "partial" | "complete";

export function useAccountSchedule(userId: number | null, courseId: string, calendarMonth: CalendarMonth, actorRef: string | undefined, authority: string, measured: boolean) {
  const [activitySummary, setActivitySummary] = useState<{ scope: string; value: ActivitySummaryResponse | null } | null>(null);
  const [measurement, setMeasurement] = useState<{ scope: string; value: Awaited<ReturnType<typeof getMeasuredActivity>> | null } | null>(null);
  const [bookingSlots, setBookingSlots] = useState<StudentSlot[]>([]);
  const [bookingQuota, setBookingQuota] = useState<QuotaStatus | null>(null);
  const [bookingError, setBookingError] = useState("");
  const [bookingLoading, setBookingLoading] = useState(true);
  const [loadedScope, setLoadedScope] = useState("");
  const scope = `${userId}:${courseId}:${calendarMonth.year}:${calendarMonth.month}`;
  const activityScope = JSON.stringify([userId, courseId, actorRef, authority, calendarMonth.year, calendarMonth.month, measured]);
  const currentActivityScope = useRef(activityScope);
  currentActivityScope.current = activityScope;
  const [ownerName, setOwnerName] = useState<string | null>(null);
  const bookings = useRef(new Map<number, number>());
  const sequence = useRef(0);

  useEffect(() => {
    if (userId === null || measured) return;
    const read = createActivityRead({
      request: getActivitySummary,
      isCurrent: () => currentActivityScope.current === activityScope,
      isRetryable: isActivityUnavailable,
      onSuccess: (value) => setActivitySummary({ scope: activityScope, value }),
      onError: () => setActivitySummary({ scope: activityScope, value: null }),
    });
    read.load();
    window.addEventListener("pymentor:activity-updated", read.load);
    return () => {
      read.dispose();
      window.removeEventListener("pymentor:activity-updated", read.load);
    };
  }, [userId, actorRef, authority, calendarMonth.year, calendarMonth.month, activityScope, measured]);

  useEffect(() => {
    if (userId === null || !measured || !courseId) return;
    const read = createActivityRead({
      request: () => getMeasuredActivity({ year: calendarMonth.year, month: calendarMonth.month }, courseId),
      isCurrent: () => currentActivityScope.current === activityScope,
      isRetryable: isActivityUnavailable,
      onSuccess: (value) => setMeasurement({ scope: activityScope, value: value?.course_id === courseId && Array.isArray(value.days) ? value : null }),
      onError: () => setMeasurement({ scope: activityScope, value: null }),
    });
    read.load();
    window.addEventListener("pymentor:activity-updated", read.load);
    return () => { read.dispose(); window.removeEventListener("pymentor:activity-updated", read.load); };
  }, [userId, courseId, calendarMonth.year, calendarMonth.month, activityScope, measured]);

  const currentMeasurement = measured && userId !== null && measurement?.scope === activityScope ? measurement.value : null;
  const measurementStatus: AccountActivityStatus = !measured ? "disabled" : !courseId || userId === null ? "unavailable"
    : measurement?.scope !== activityScope ? "loading" : currentMeasurement?.coverage ?? "unavailable";
  const loadBookingSlots = useCallback(async () => {
    const request = ++sequence.current;
    setBookingSlots([]); setBookingQuota(null); setBookingError(""); setBookingLoading(true);
    if (userId === null) { setBookingLoading(false); return; }
    try {
      const context = await getBookingContext(courseId);
      const data = context.active_owner_id === null ? null : await getStudentSlots(courseId, context.active_owner_id, calendarMonth);
      if (request !== sequence.current) return;
      setOwnerName(context.active_owner_name);
      bookings.current = new Map(context.my_upcoming_bookings.map((booking) => [booking.slot_id, booking.id]));
      const slots = [...(data?.slots ?? [])];
      const ids = new Set(slots.map((slot) => slot.id));
      for (const booking of context.my_upcoming_bookings) {
        if (!ids.has(booking.slot_id)) slots.push({ id: booking.slot_id, starts_at: booking.starts_at, ends_at: booking.ends_at, status: "mine", can_cancel: new Date(booking.starts_at).getTime() - Date.now() >= 36 * 60 * 60 * 1000 });
      }
      const display = slots.map((slot) => {
        const date = new Date(slot.starts_at);
        const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(date);
        const part = (name: string) => parts.find((item) => item.type === name)?.value ?? "";
        return { id: slot.id, date: `${part("year")}-${part("month")}-${part("day")}`, start_time: `${part("hour")}:${part("minute")}`, duration_minutes: (new Date(slot.ends_at).getTime() - date.getTime()) / 60000, status: slot.status, can_cancel: slot.can_cancel };
      }).filter((slot) => slot.date.startsWith(`${calendarMonth.year}-${String(calendarMonth.month).padStart(2, "0")}`));
      setBookingSlots(display); setBookingQuota(data?.quota ?? null); setLoadedScope(scope);
    } catch (error) {
      if (request === sequence.current) { setLoadedScope(scope); setBookingError(error instanceof Error ? error.message : "Расписание недоступно"); }
    } finally { if (request === sequence.current) setBookingLoading(false); }
  }, [userId, courseId, calendarMonth.year, calendarMonth.month, scope]);

  useEffect(() => { void loadBookingSlots(); return () => { sequence.current += 1; }; }, [loadBookingSlots]);

  const runCommand = useCallback(async (kind: "book" | "cancel", slotId: number) => {
    const bookingId = bookings.current.get(slotId);
    if (kind === "cancel" && bookingId === undefined) throw new Error("Не удалось найти запись. Обновите расписание.");
    const root = `/courses/${encodeURIComponent(courseId)}/booking`;
    const path = kind === "book" ? `${root}/slots/${slotId}/book` : `${root}/bookings/${bookingId}/cancel`;
    await command(path, undefined, undefined, actorRef, authority);
    await loadBookingSlots();
  }, [courseId, actorRef, authority, loadBookingSlots]);
  const bookSlot = useCallback((id: number) => runCommand("book", id), [runCommand]);
  const cancelSlotBooking = useCallback((id: number) => runCommand("cancel", id), [runCommand]);
  return { activityDays: currentMeasurement ? new Set(currentMeasurement.days) : emptyActivityDays,
    measurementStatus, currentMeasurement,
    activitySummary: !measured && userId !== null && activitySummary?.scope === activityScope ? activitySummary.value : null,
    bookingSlots: loadedScope === scope ? bookingSlots : [], bookingQuota: loadedScope === scope ? bookingQuota : null, bookingError: loadedScope === scope ? bookingError : "", bookingLoading: loadedScope !== scope || bookingLoading, ownerName: loadedScope === scope ? ownerName : null, bookSlot, cancelSlotBooking };
}
