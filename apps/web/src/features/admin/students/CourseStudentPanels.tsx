import "../../account/calendar-base.css";
import "../../account/calendar.css";
import "../../account/personal-calendar-layout.css";
import "./course-student-calendar.css";
import { CalendarCheck2, CalendarDays, Clock, History, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import type { CalendarMonth } from "../../../shared/api/contracts/account";
import { getMoscowDateKey } from "../../../shared/lib/dates";
import { CalendarMonthGrid, type CalendarMarker } from "../../../shared/ui/calendar/CalendarMonthGrid";
import { courseReportFilters } from "../../account/course-statistics-analytics";
import { AnalyticsReportError, fetchStudentActivityReport } from "../../analytics-reporting/client";
import { StudentProfileDisclosure } from "./StudentProfileDisclosure";
import { fetchStudentProfileDetails, monthDateRange, StudentProfileReadError, type StudentBooking, type StudentProfileDetails } from "./student-profile-api";
import type { Target } from "./student-statistics-api";

type StudentPanelResult<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: unknown };
export type StudentVisitsMode = "enabled" | "disabled" | "search-required";
type PanelRead<T> = { result: StudentPanelResult<T>; retry: () => void };

function useStudentPanelRead<T>(load: ((signal: AbortSignal) => Promise<T>) | null, revision = 0): PanelRead<T> {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ load: typeof load; revision: number; attempt: number; result: StudentPanelResult<T> } | null>(null);
  useEffect(() => {
    if (!load) return;
    const controller = new AbortController();
    load(controller.signal).then(
      data => { if (!controller.signal.aborted) setState({load, revision, attempt, result: {status: "ready", data}}); },
      error => { if (!controller.signal.aborted) setState({load, revision, attempt, result: {status: "error", error}}); },
    );
    return () => controller.abort();
  }, [load, revision, attempt]);
  const result: StudentPanelResult<T> = state && state.load === load && state.revision === revision && state.attempt === attempt
    ? state.result : {status: "loading"};
  return { result, retry: () => setAttempt(value => value + 1) };
}

const searchAgain = "Снова найдите ученика через «Поиск по всем ученикам», чтобы открыть данные.";

function StudentPanelFeedback({ error, subject, retry }: { error: unknown; subject: string; retry: () => void }) {
  if (error instanceof StudentProfileReadError) {
    if (error.kind === "search-required") return <p className="course-student-panel__note" role="status">{searchAgain}</p>;
    if (error.kind === "denied") return <p className="course-student-panel__note" role="status">Нет доступа к данным ученика.</p>;
  }
  return <div className="form-error course-student-panel__feedback" role="alert">
    <span>Не удалось загрузить {subject}.</span>
    <button className="admin-back" type="button" onClick={retry}>Повторить</button>
  </div>;
}

const subscriptionDate = new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Moscow", dateStyle: "long", timeStyle: "short" });

export function StudentSubscription({ until, plan, now = Date.now() }: { until: string | null; plan: string | null; now?: number }) {
  const active = until !== null && Date.parse(until) > now;
  return <div className="course-student-subscription">
    <p className="course-student-panel__note">Подписка общая для всей платформы.</p>
    <dl>
      <div><dt>Статус</dt><dd data-active={active}>{active ? "Действует" : until ? "Истекла" : "Нет подписки"}</dd></div>
      <div><dt>{until && !active ? "Истекла" : "Действует до"}</dt><dd>{until ? <time dateTime={until}>{subscriptionDate.format(new Date(until))} МСК</time> : "—"}</dd></div>
      {plan && <div><dt>Тариф</dt><dd>{plan}</dd></div>}
    </dl>
  </div>;
}

const bookingMarkers = {
  booked: { label: "Запланированная запись", icon: Clock },
  elapsed: { label: "Время занятия прошло", icon: History },
  completed: { label: "Запись отмечена завершённой", icon: CalendarCheck2 },
  cancelled: { label: "Запись отменена", icon: X },
};
type BookingMarker = keyof typeof bookingMarkers;
const fullDate = new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Moscow", weekday: "long", day: "numeric", month: "long", year: "numeric" });
const timeOfDay = new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit" });
const readableDay = (day: string) => fullDate.format(new Date(`${day}T12:00:00+03:00`));

export function bookingCalendarMarker(booking: StudentBooking, now: number): BookingMarker {
  if (booking.status !== "booked") return booking.status;
  return Date.parse(booking.ends_at) <= now ? "elapsed" : "booked";
}

export function measuredCalendarDays(days: ReadonlyArray<{ date: string; active_ms: number }>): Set<string> {
  return new Set(days.filter(day => day.active_ms > 0).map(day => day.date));
}

function CourseStudentCalendar({ courseId, target, actorRef, month, visitsMode, monthControl, profile }: {
  courseId: string;
  target: Target;
  actorRef: string;
  month: CalendarMonth;
  visitsMode: StudentVisitsMode;
  monthControl: ReactNode;
  profile: PanelRead<StudentProfileDetails>;
}) {
  const { from, to } = monthDateRange(month);
  const now = Date.now();
  const today = getMoscowDateKey(new Date(now));
  const lastObservedDay = to > today ? today : to;
  const {id, account_ref} = target;
  const readVisits = useCallback((signal: AbortSignal) => fetchStudentActivityReport(
    courseReportFilters({courseId, from, to: lastObservedDay}), {user_id: id, expected_target_ref: account_ref}, actorRef, signal,
  ), [courseId, from, lastObservedDay, id, account_ref, actorRef]);
  const mayReadVisits = visitsMode === "enabled" && from <= today;
  const visits = useStudentPanelRead(mayReadVisits ? readVisits : null);
  const report = visits.result.status === "ready" ? visits.result.data : null;
  const activeDays = measuredCalendarDays(report?.daily ?? []);
  const activityComplete = report?.quality === "complete" && (report.status === "ok" || report.status === "no_data");

  const bookingDays = useMemo(() => {
    const byDay = new Map<string, StudentBooking[]>();
    if (profile.result.status !== "ready") return byDay;
    const sorted = [...profile.result.data.bookings].sort((a, b) => Date.parse(a.starts_at) - Date.parse(b.starts_at));
    for (const booking of sorted) {
      const day = getMoscowDateKey(new Date(booking.starts_at));
      const items = byDay.get(day);
      if (items) items.push(booking);
      else byDay.set(day, [booking]);
    }
    return byDay;
  }, [profile.result]);
  const daysByMarker: Record<BookingMarker, Set<string>> = {
    booked: new Set(), elapsed: new Set(), completed: new Set(), cancelled: new Set(),
  };
  for (const [day, bookings] of bookingDays) {
    for (const booking of bookings) daysByMarker[bookingCalendarMarker(booking, now)].add(day);
  }
  const legend: Array<{ marker: CalendarMarker; label: string }> = [
    { marker: "is-booking", label: bookingMarkers.booked.label },
    { marker: "is-completed", label: bookingMarkers.elapsed.label },
    { marker: "is-activity", label: "Учебная активность на курсе" },
  ];
  if (daysByMarker.completed.size) legend.push({ marker: "is-confirmed", label: bookingMarkers.completed.label });
  if (daysByMarker.cancelled.size) legend.push({ marker: "is-cancelled", label: bookingMarkers.cancelled.label });

  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const selected = selectedDay && selectedDay >= from && selectedDay <= to ? selectedDay : null;
  const selectedBookings = selected ? bookingDays.get(selected) ?? [] : [];

  function renderVisitsFeedback() {
    if (visitsMode === "disabled") return <p className="course-student-panel__note">Данные об учебной активности на курсе недоступны.</p>;
    if (visitsMode === "search-required") return <p className="course-student-panel__note">Для просмотра учебной активности откройте профиль через «Поиск по всем ученикам».</p>;
    if (!mayReadVisits) return null;
    if (visits.result.status === "loading") return <p className="course-student-panel__note" role="status">Загружаем учебную активность…</p>;
    if (visits.result.status === "error") {
      const error = visits.result.error;
      if (error instanceof AnalyticsReportError && error.studentSearchRequired) return <p className="course-student-panel__note" role="status">{searchAgain}</p>;
      if (error instanceof AnalyticsReportError && error.kind === "denied") return <p className="course-student-panel__note" role="status">Нет доступа к сведениям об учебной активности.</p>;
      return <StudentPanelFeedback error={error} subject="сведения об учебной активности" retry={visits.retry} />;
    }
    if (report?.status === "unavailable") return <StudentPanelFeedback error={null} subject="сведения об учебной активности" retry={visits.retry} />;
    if (report?.status === "stale") return <p className="course-student-panel__note" role="status">
      Показаны ранее полученные сведения об учебной активности.
      {report.quality === "partial" && " Данные доступны частично."}
      {report.quality === "unknown" && " Полнота данных неизвестна."}
    </p>;
    if (report?.quality === "partial") return <p className="course-student-panel__note">Сведения об учебной активности доступны частично.</p>;
    if (report?.quality === "unknown") return <p className="course-student-panel__note">Полнота сведений об учебной активности неизвестна.</p>;
    return null;
  }

  return <section className="account-calendar account-calendar--booking account-calendar--personal course-student-calendar" aria-label="Календарь занятий ученика">
    <header className="account-calendar__header">
      <div className="account-calendar__title">
        <span className="account-calendar__icon"><CalendarDays size={19} aria-hidden="true" /></span>
        <div>
          <span className="account-calendar__eyebrow">Расписание</span>
          <p className="course-student-panel__note">Московское время</p>
        </div>
      </div>
      {monthControl}
    </header>
    <div className="account-calendar__layout">
      <div className="account-calendar__main">
        <div className="account-calendar__month-frame">
          <CalendarMonthGrid month={month} activityDays={activeDays} activityLabel="учебная активность на курсе"
            bookingDays={daysByMarker.booked} bookingLabel={bookingMarkers.booked.label}
            completedDays={daysByMarker.elapsed} confirmedDays={daysByMarker.completed} cancelledDays={daysByMarker.cancelled}
            showCellEvents={false} selectAllDays todayDateKey={today} selectedDay={selected}
            onSelectDay={(day) => setSelectedDay(day === selected ? null : day)} legend={legend} />
        </div>
      </div>
      <aside className="account-calendar__details" aria-live="polite">
        <header className="account-calendar__details-heading">
          <span className="account-calendar__details-icon"><CalendarCheck2 size={17} aria-hidden="true" /></span>
          <div>
            {selected && <span className="account-calendar__eyebrow">День в расписании</span>}
            <h3>{selected ? readableDay(selected) : "Выберите дату"}</h3>
          </div>
        </header>
        {selected ? <>
          {selected <= today && (activeDays.has(selected) ? <div className="account-calendar__activity-time">
            <span className="account-calendar__activity-icon"><Clock size={16} aria-hidden="true" /></span>
            <div><strong>Учебная активность зафиксирована</strong></div>
          </div> : activityComplete && <p className="course-student-panel__note">Учебная активность за день не зафиксирована.</p>)}
          {selectedBookings.length > 0 ? <ul className="account-calendar__slots course-student-calendar__bookings" aria-label="Личные записи">
            {selectedBookings.map(booking => {
              const kind = bookingCalendarMarker(booking, now);
              const { icon: Icon, label } = bookingMarkers[kind];
              return <li key={booking.id} className={`account-calendar__slot ${kind === "booked" ? "is-booked" : kind === "elapsed" ? "is-completed" : ""}`}>
                <Icon size={16} aria-hidden="true" />
                <div className="account-calendar__slot-completed-copy">
                  <strong><time dateTime={booking.starts_at}>{timeOfDay.format(new Date(booking.starts_at))}</time>–<time dateTime={booking.ends_at}>{timeOfDay.format(new Date(booking.ends_at))}</time></strong>
                  <span>{label}</span>
                </div>
              </li>;
            })}
          </ul> : <p className="course-student-panel__note">{profile.result.status === "ready" && profile.result.data.bookingsComplete
            ? "На этот день записей нет." : "Сведения о записях на этот день недоступны."}</p>}
        </> : <p className="course-student-panel__note">Выберите день, чтобы посмотреть записи и учебную активность.</p>}
        {profile.result.status === "loading" && <p className="course-student-panel__note" role="status">Загружаем записи…</p>}
        {profile.result.status === "error" && <StudentPanelFeedback error={profile.result.error} subject="записи" retry={profile.retry} />}
        {profile.result.status === "ready" && !profile.result.data.bookingsComplete && <p className="course-student-panel__note" role="status">Показана только часть записей за месяц.</p>}
        {renderVisitsFeedback()}
      </aside>
    </div>
  </section>;
}

export function CourseStudentPanels({ courseId, target, actorRef, month, visitsMode, revision, monthControl, subscriptionActions }: {
  courseId: string;
  target: Target;
  actorRef: string;
  month: CalendarMonth;
  visitsMode: StudentVisitsMode;
  revision: number;
  monthControl: ReactNode;
  subscriptionActions: ReactNode;
}) {
  const [hasOpened, setHasOpened] = useState(false);
  const {year, month: monthIndex} = month;
  const {id, account_ref} = target;
  const load = useCallback((signal: AbortSignal) => fetchStudentProfileDetails(courseId, {id, account_ref}, actorRef, {year, month: monthIndex}, signal),
    [courseId, id, account_ref, actorRef, year, monthIndex]);
  const profile = useStudentPanelRead(hasOpened ? load : null, revision);
  const start = () => setHasOpened(true);
  const profileError = profile.result.status === "error" ? profile.result.error : null;
  const commandsBlocked = profileError instanceof StudentProfileReadError
    && (profileError.kind === "denied" || profileError.kind === "search-required");

  return <>
    <StudentProfileDisclosure title="Занятия" onFirstOpen={start}>
      <CourseStudentCalendar courseId={courseId} target={target} actorRef={actorRef} month={month}
        monthControl={monthControl} visitsMode={visitsMode} profile={profile} />
    </StudentProfileDisclosure>
    <StudentProfileDisclosure title="Подписка" onFirstOpen={start}>
      <div className="admin-scoped-content">
        {profile.result.status === "loading" ? <p className="course-student-panel__note" role="status">Загружаем подписку…</p>
          : profile.result.status === "error" ? <StudentPanelFeedback error={profile.result.error} subject="подписку" retry={profile.retry} />
          : <StudentSubscription until={profile.result.data.subscriptionUntil} plan={profile.result.data.subscriptionPlan} />}
        {!commandsBlocked && subscriptionActions}
      </div>
    </StudentProfileDisclosure>
  </>;
}
