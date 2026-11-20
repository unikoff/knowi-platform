import { useCallback, useEffect, useRef, useState } from "react";
import { getActivitySummary, getMeasuredActivity, getBookingContext, getStudentSlots, getLegacyStudentSlots, bookLegacyStudentSlot, cancelLegacyStudentBooking } from "./api";
import { command } from "../admin/scopedApi";
import { ApiError } from "../../shared/api/client";
import type { ActivitySummaryResponse, CalendarMonth, QuotaStatus, StudentSlot } from "../../shared/api/contracts/account";
import { createActivityRead } from "./activityRead";

const emptyActivityDays = new Set<string>();
const isActivityUnavailable = (error: unknown) => error instanceof ApiError && error.status === 503;
export type AccountActivityStatus = "disabled" | "loading" | "unavailable" | "unknown" | "partial" | "complete";

export function useAccountSchedule(userId: number | null, courseId: string, calendarMonth: CalendarMonth, actorRef: string | undefined, authority: string, measured: boolean, bookingMode: "legacy" | "scoped" | null) {
  const [activitySummary, setActivitySummary] = useState<{ scope: string; value: ActivitySummaryResponse | null } | null>(null);
  const [measurement, setMeasurement] = useState<{ scope: string; value: Awaited<ReturnType<typeof getMeasuredActivity>> | null } | null>(null);
  const [bookingSlots, setBookingSlots] = useState<StudentSlot[]>([]);
  const [bookingQuota, setBookingQuota] = useState<QuotaStatus | null>(null);
  const [bookingError, setBookingError] = useState("");
  const [bookingLoading, setBookingLoading] = useState(true);
  const [loadedScope, setLoadedScope] = useState("");
  const legacy = bookingMode === "legacy" && courseId === "backend";
  const bookingAvailable = legacy || bookingMode === "scoped";
  const scope = JSON.stringify([userId, courseId, actorRef, authority, bookingMode, calendarMonth.year, calendarMonth.month]);
  const currentBookingScope = useRef({ scope, generation: 0 });
  if (currentBookingScope.current.scope !== scope) {
    currentBookingScope.current = { scope, generation: currentBookingScope.current.generation + 1 };
  }
  const bookingGeneration = currentBookingScope.current.generation;
  const displayScope = JSON.stringify([scope, bookingGeneration]);
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
    bookings.current = new Map(); setOwnerName(null);
    if (userId === null || !bookingAvailable) {
      if (bookingMode === "legacy") setBookingError("Для этого курса пока нет расписания.");
      setLoadedScope(displayScope); setBookingLoading(false); return;
    }
    const isCurrent = () => request === sequence.current && currentBookingScope.current.scope === scope && currentBookingScope.current.generation === bookingGeneration;
    try {
      if (legacy) {
        const data = await getLegacyStudentSlots(calendarMonth);
        if (!isCurrent()) return;
        setBookingSlots(data.slots ?? []); setBookingQuota(data.quota ?? null); setLoadedScope(displayScope);
        return;
      }
      const context = await getBookingContext(courseId);
      if (!isCurrent()) return;
      const data = context.active_owner_id === null ? null : await getStudentSlots(courseId, context.active_owner_id, calendarMonth);
      if (!isCurrent()) return;
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
      setBookingSlots(display); setBookingQuota(data?.quota ?? null); setLoadedScope(displayScope);
    } catch (error) {
      if (isCurrent()) { setLoadedScope(displayScope); setBookingError(error instanceof Error ? error.message : "Расписание недоступно"); }
    } finally { if (isCurrent()) setBookingLoading(false); }
  }, [userId, courseId, calendarMonth.year, calendarMonth.month, scope, bookingMode, legacy, bookingAvailable, bookingGeneration, displayScope]);

  useEffect(() => { void loadBookingSlots(); return () => { sequence.current += 1; }; }, [loadBookingSlots]);

  const runCommand = useCallback(async (kind: "book" | "cancel", slotId: number) => {
    if (currentBookingScope.current.scope !== scope || currentBookingScope.current.generation !== bookingGeneration ||
        userId === null || !bookingAvailable || loadedScope !== displayScope) {
      throw new Error("Расписание недоступно. Обновите страницу.");
    }
    const request = sequence.current;
    if (legacy) {
      await (kind === "book" ? bookLegacyStudentSlot(slotId) : cancelLegacyStudentBooking(slotId));
      if (currentBookingScope.current.scope === scope && currentBookingScope.current.generation === bookingGeneration && request === sequence.current) await loadBookingSlots();
      return;
    }
    const bookingId = bookings.current.get(slotId);
    if (kind === "cancel" && bookingId === undefined) throw new Error("Не удалось найти запись. Обновите расписание.");
    const root = `/courses/${encodeURIComponent(courseId)}/booking`;
    const path = kind === "book" ? `${root}/slots/${slotId}/book` : `${root}/bookings/${bookingId}/cancel`;
    await command(path, undefined, undefined, actorRef, authority);
    if (currentBookingScope.current.scope === scope && currentBookingScope.current.generation === bookingGeneration && request === sequence.current) await loadBookingSlots();
  }, [userId, courseId, actorRef, authority, legacy, bookingAvailable, loadedScope, scope, bookingGeneration, displayScope, loadBookingSlots]);
  const bookSlot = useCallback((id: number) => runCommand("book", id), [runCommand]);
  const cancelSlotBooking = useCallback((id: number) => runCommand("cancel", id), [runCommand]);
  return { activityDays: currentMeasurement ? new Set(currentMeasurement.days) : emptyActivityDays,
    measurementStatus, currentMeasurement,
    activitySummary: !measured && userId !== null && activitySummary?.scope === activityScope ? activitySummary.value : null,
    bookingSlots: loadedScope === displayScope ? bookingSlots : [], bookingQuota: loadedScope === displayScope ? bookingQuota : null, bookingError: loadedScope === displayScope ? bookingError : "", bookingLoading: loadedScope !== displayScope || bookingLoading, ownerName: loadedScope === displayScope ? ownerName : null, bookSlot, cancelSlotBooking };
}
