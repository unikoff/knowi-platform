import "./admin.css";
import "./admin-scoped.css";
import "./overview/overview.css";
import "./course-admin.css";
import { ArrowLeft, CalendarClock, CalendarDays, CalendarPlus, ChevronLeft, ChevronRight, Clock, LayoutDashboard, User } from "lucide-react";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { ApiError } from "../../shared/api/client";
import type { UserProfile } from "../../shared/api/contracts";
import type { CalendarMonth } from "../../shared/api/contracts/account";
import { getMonthTitle, shiftCalendarMonth } from "../../shared/lib/dates";
import { moscowCalendarMonth } from "../../shared/lib/moscow-month";
import { CourseAccountDirectory } from "./CourseAccountDirectory";
import { CourseCalendar } from "./CourseCalendar";
import { CourseOverviewDashboard } from "./CourseOverviewDashboard";
import { CourseStudentPanels } from "./students/CourseStudentPanels";
import { CourseStudentStatistics } from "./students/CourseStudentStatistics";
import { StudentProfileDisclosure } from "./students/StudentProfileDisclosure";
import { acknowledgeReconciliation, ReconciliationRequired, command, coursePath, read, type CourseOverview, type Person, type Quota, type RosterPerson, type Slot } from "./scopedApi";

type Tab = "overview" | "students" | "slots" | "calendar";
function slotCount(count: number) { const n = count % 100, last = count % 10; return `${count} ${n >= 11 && n <= 14 ? "слотов" : last === 1 ? "слот" : last >= 2 && last <= 4 ? "слота" : "слотов"}`; }
function message(error: unknown) { return error instanceof TypeError ? "Не удалось связаться с сервером. Проверьте соединение и попробуйте ещё раз." : error instanceof Error ? error.message : "Данные недоступны"; }
function useResource<T>(path: string | null, revision = 0, headers?: Record<string, string>) {
  const headerKey = JSON.stringify(headers ?? {});
  const key = JSON.stringify([path, revision, headerKey]);
  const [state, setState] = useState<{ key: string; data: T | null; loading: boolean; error: string }>({ key, data: null, loading: Boolean(path), error: "" });
  useEffect(() => {
    let active = true;
    setState({ key, data: null, loading: Boolean(path), error: "" });
    if (path) read<T>(path, JSON.parse(headerKey)).then((data) => { if (active) setState({ key, data, loading: false, error: "" }); })
      .catch((error) => { if (active) setState({ key, data: null, loading: false, error: message(error) }); });
    return () => { active = false; };
  }, [path, revision, headerKey]);
  return state.key === key ? { data: state.data, loading: state.loading, error: state.error } : { data: null, loading: Boolean(path), error: "" };
}
function State({ loading, error, retry }: { loading: boolean; error: string; retry: () => void }) {
  return <>{loading && <p role="status">Загружаем…</p>}{error && <div className="form-error" role="alert">{error} <button className="admin-back" type="button" onClick={retry}>Повторить</button></div>}</>;
}
type CourseCommand = (path: string, body: unknown, accountRef?: string, onSuccess?: () => void) => Promise<void>;
const subscriptionAndQuotaSuffix = "/subscription-and-quota";

const minCalendarYear = 2000;
const maxCalendarYear = 2100;

function Month({ month, change, disabled = false, label = "Выбор месяца" }: {
  month: CalendarMonth;
  change: (offset: -1 | 1) => void;
  disabled?: boolean;
  label?: string;
}) {
  return <div className="course-admin-month" role="group" aria-label={label}>
    <button type="button" onClick={() => change(-1)}
      disabled={disabled || (month.year === minCalendarYear && month.month === 1)} aria-label="Предыдущий месяц">
      <ChevronLeft size={16} aria-hidden="true" />
    </button>
    <strong aria-live="polite">{getMonthTitle(month).replace(/\s+г\.$/u, "")}</strong>
    <button type="button" onClick={() => change(1)}
      disabled={disabled || (month.year === maxCalendarYear && month.month === 12)} aria-label="Следующий месяц">
      <ChevronRight size={16} aria-hidden="true" />
    </button>
  </div>;
}

function StudentActions({ base, person, month, quota, busy, perform }: {
  base: string;
  person: Person;
  month: { year: number; month: number };
  quota: { ready: boolean; loading: boolean; error: string; retry: () => void };
  busy: boolean;
  perform: CourseCommand;
}) {
  const fieldId = useId();
  const reasonInput = useRef<HTMLInputElement>(null);
  const [data, setData] = useState({ lessons: "", days: "", reason: "" });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const add = Number(data.lessons);
    const days = Number(data.days);
    if (busy || !quota.ready || !Number.isInteger(add) || add < 1 || add > 100 || !Number.isInteger(days) || days < 1 || days > 3650) return;

    const field = reasonInput.current;
    if (!field) return;
    const reason = data.reason.trim();
    field.setCustomValidity(reason ? "" : "Укажите причину.");
    if (!field.reportValidity() || !reason || reason.length > 500) return;

    void perform(
      `${base}/users/${person.id}${subscriptionAndQuotaSuffix}`,
      { year: month.year, month: month.month, add, days, reason },
      person.account_ref,
      () => setData({ lessons: "", days: "", reason: "" }),
    );
  }

  return <form className="course-admin-student-actions" aria-labelledby={`${fieldId}-heading`} onSubmit={submit}>
    <div className="course-admin-student-actions__heading">
      <h3 id={`${fieldId}-heading`}>Управление занятиями и подпиской</h3>
      {busy && <span role="status" className="admin-note">Сохраняем изменения…</span>}
    </div>
    <div className="course-admin-student-actions__controls">
      <label className="admin-scoped-field">
        Количество занятий
        <input className="admin-scoped-input" type="number" min={1} max={100} step={1} required readOnly={busy}
          value={data.lessons} aria-describedby={`${fieldId}-month`}
          onChange={(event) => setData({ ...data, lessons: event.target.value })} />
        <span className="admin-note" id={`${fieldId}-month`}>{getMonthTitle(month)} · на этот курс</span>
      </label>
      <label className="admin-scoped-field">
        Дни продления
        <input className="admin-scoped-input" type="number" min={1} max={3650} step={1} required readOnly={busy}
          value={data.days} aria-describedby={`${fieldId}-scope`}
          onChange={(event) => setData({ ...data, days: event.target.value })} />
        <span className="admin-note" id={`${fieldId}-scope`}>Для всей платформы</span>
      </label>
      <label className="admin-scoped-field course-admin-student-actions__reason">
        Причина
        <input className="admin-scoped-input" ref={reasonInput} required maxLength={500} readOnly={busy}
          value={data.reason} onChange={(event) => {
            event.currentTarget.setCustomValidity("");
            setData({ ...data, reason: event.target.value });
          }} />
      </label>
    </div>
    <State {...quota} />
    <button className="admin-scoped-button admin-scoped-button--primary course-admin-student-actions__submit"
      type="submit" aria-disabled={busy || !quota.ready || undefined}>
      Добавить занятия и продлить подписку
    </button>
  </form>;
}

export function CourseAdmin({ courseId, user }: { courseId: string; user: UserProfile }) {
  return <CourseAdminContent key={`${courseId}:${user.account_ref}:${user.role}:${user.auth_revision}`} courseId={courseId} user={user} />;
}
function CourseAdminContent({ courseId, user }: { courseId: string; user: UserProfile }) {
  const analyticsReportsEnabled = import.meta.env.VITE_ANALYTICS_REPORTS_ENABLED === "true";
  const calendarActivityEnabled = analyticsReportsEnabled && import.meta.env.VITE_ANALYTICS_PERSONAL_MEASUREMENTS_ENABLED === "true";
  const [tab, setTab] = useState<Tab>("overview");
  const [month, setMonth] = useState(moscowCalendarMonth);
  const [revision, setRevision] = useState(0);
  const base = coursePath(courseId);
  const [selected, setSelected] = useState<Person | null>(null);
  const [selectedFromRoster, setSelectedFromRoster] = useState(false);
  const [restoreFocus, setRestoreFocus] = useState<"roster" | "global" | null>(null);
  const profileBack = useRef<HTMLButtonElement>(null);
  const [search, setSearch] = useState("");
  const [rosterLocation, setRosterLocation] = useState({ search: "", cursors: [""] });
  const [rosterRevision, setRosterRevision] = useState(0);
  const [grantSearch, setGrantSearch] = useState("");
  const [query, setQuery] = useState("");
  const [searchCursors, setSearchCursors] = useState([""]);
  const [searchRevision, setSearchRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const commandInFlight = useRef(false);
  const [error, setError] = useState("");
  const [reconcile, setReconcile] = useState<ReconciliationRequired | null>(null);
  const [notice, setNotice] = useState("");
  const [reason, setReason] = useState("");
  const [date, setDate] = useState("");
  const [time, setTime] = useState("10:00");
  const [duration, setDuration] = useState(60);
  const trimmedSearch = search.trim();
  const rosterSearchPending = trimmedSearch !== rosterLocation.search;
  const cursor = rosterLocation.cursors[rosterLocation.cursors.length - 1] ?? "";
  const searchCursor = searchCursors[searchCursors.length - 1] ?? "";
  const period = `year=${month.year}&month=${month.month}`;
  const scheduling = tab === "slots" || tab === "calendar";
  const ownerContext = useResource<{ course_id: string; owners: {id: number; username: string; active: boolean; pending_slots: number}[] }>(
    scheduling ? `${base}/calendar-context` : null, revision,
  );
  const overview = useResource<CourseOverview>(tab === "overview" ? `${base}/overview?${period}` : null, revision);
  const listing = tab === "students" && !selected;
  const roster = useResource<{ users: RosterPerson[]; next_cursor: string | null }>(
    listing && !rosterSearchPending ? `${base}/roster?${period}&${new URLSearchParams({ search: rosterLocation.search, ...(cursor ? { cursor } : {}) })}` : null,
    revision + rosterRevision,
  );
  const result = useResource<{ users: Person[]; next_cursor: string | null }>(
    listing && query ? `${base}/grant-search?${new URLSearchParams({ q: query, ...(searchCursor ? { cursor: searchCursor } : {}) })}` : null,
    revision + searchRevision,
  );
  const quota = useResource<{ quota: Quota }>(selected ? `${base}/users/${selected.id}/quota?${period}` : null, revision, selected ? { "X-Account-Ref": selected.account_ref } : undefined);
  const activeOwners = ownerContext.data?.owners.filter((person) => person.active) ?? [];
  const owner = activeOwners.length === 1 ? activeOwners[0] : null;
  const slots = useResource<{ slots: Slot[] }>(scheduling && owner ? `${base}/slots?owner_id=${owner.id}&${period}` : null, revision);
  const perform: CourseCommand = async (path, body, accountRef, onSuccess) => {
    if (commandInFlight.current) return;
    commandInFlight.current = true;
    setBusy(true); setError(""); setReconcile(null); setNotice("");
    const combined = path.endsWith(subscriptionAndQuotaSuffix);
    const actorRef = typeof user.auth_revision === "number" && user.role ? user.account_ref : undefined;
    try {
      const response = await command<{ subscription_until?: string | null }>(
        path, body, accountRef, actorRef, `${user.role}:${user.auth_revision}:${courseId}`,
      );
      onSuccess?.();
      const subscription = response.subscription_until
        ? `Общая подписка действует до ${new Date(response.subscription_until).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })}.`
        : "Подписка продлена.";
      setNotice(combined ? `Занятия добавлены. ${subscription}` : "Сохранено.");
      setRevision((v) => v + 1);
    }
    catch (e) {
      if (e instanceof ReconciliationRequired) setReconcile(e);
      setError(combined && e instanceof ApiError && e.status === 404 && e.message === "Not Found"
        ? "Сервер пока не поддерживает совместную выдачу занятий и продление подписки. Обновите сервер и повторите попытку."
        : message(e));
    }
    finally { commandInFlight.current = false; setBusy(false); }
  };
  useEffect(() => {
    if (!rosterSearchPending) return;
    const timeout = window.setTimeout(() => setRosterLocation({ search: trimmedSearch, cursors: [""] }), 180);
    return () => window.clearTimeout(timeout);
  }, [trimmedSearch, rosterSearchPending]);

  useEffect(() => { if (selected) profileBack.current?.focus(); }, [selected]);

  function select(person: Person, rosterMember: boolean) {
    setSelected(person);
    setSelectedFromRoster(rosterMember);
    setRestoreFocus(rosterMember ? "roster" : "global");
    setReconcile(null);
    setError("");
    setNotice("");
  }

  function submitGlobalSearch() {
    const next = grantSearch.trim();
    if (next.length < 3 || (result.loading && query === next && !searchCursor)) return;
    setQuery(next);
    setSearchCursors([""]);
    setSearchRevision((value) => value + 1);
  }

  function changeMonth(offset: -1 | 1) {
    setMonth((current) => {
      const next = shiftCalendarMonth(current, offset);
      return next.year >= minCalendarYear && next.year <= maxCalendarYear ? next : current;
    });
    setRosterLocation((location) => location.cursors.length > 1 ? { ...location, cursors: [""] } : location);
  }

  const selectedHasBooking = selectedFromRoster && selected !== null && "last_booking_status" in selected && selected.last_booking_status != null;
  const analyticsNeedsSearch = analyticsReportsEnabled && user.role === "leader" && selectedFromRoster && !selectedHasBooking;
  const sortedSlots = [...(slots.data?.slots ?? [])].sort((a, b) => a.starts_at.localeCompare(b.starts_at));
  const slotGroups = new Map<string, Slot[]>();
  for (const slot of sortedSlots) {
    const day = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(slot.starts_at));
    slotGroups.set(day, [...(slotGroups.get(day) ?? []), slot]);
  }
  const tabs = [["overview", "Обзор", LayoutDashboard], ["students", "Аккаунты", User], ["slots", "Слоты записи", CalendarClock], ["calendar", "Календарь", CalendarDays]] as const;
  return <section className={`admin-panel admin-scoped course-admin ${tab === "calendar" ? "admin-panel--calendar" : ""}`} aria-label={`Администрирование курса ${courseId}`}>
    <nav className="admin-tabs section-navigation" aria-label="Разделы курса">{tabs.map(([id, label, Icon]) => <button key={id} type="button" className={tab === id ? "is-active" : ""} aria-current={tab === id ? "page" : undefined} disabled={Boolean(selected && busy)} onClick={() => { setTab(id); setSelected(null); setRestoreFocus(null); setReconcile(null); setError(""); setNotice(""); }}><Icon size={15} aria-hidden="true" />{label}</button>)}</nav>
    {tab === "calendar" && <div className="course-admin-calendar-period"><Month month={month} change={changeMonth} /></div>}
    {error && <p className="form-error" role="alert">{error}</p>}{reconcile && <button className="admin-scoped-button" type="button" onClick={() => { if (window.confirm("Вы сверили результат предыдущего действия с данными системы или администратором? Новое действие не будет отправлено автоматически.")) { acknowledgeReconciliation(reconcile); setReconcile(null); setError(""); } }}>Подтвердить, что результат сверен</button>}{notice && <p role="status">{notice}</p>}
    {tab === "overview" && <><State {...overview} retry={() => setRevision((value) => value + 1)}/>{overview.data && (overview.data.dashboard ? <CourseOverviewDashboard trends={overview.data.course_trends} studyTimeContext={{courseId, actorRef: user.account_ref, actorRole: user.role, authRevision: user.auth_revision}} /> : <div className="admin-overview__groups"><section className="admin-overview__group"><div className="admin-overview__group-heading"><h2>Показатели курса</h2></div><div className="admin-overview__group-metrics"><div className="admin-overview__metric"><small>Аккаунты курса</small><strong>{overview.data.roster_count}</strong><span>За всю историю курса</span></div><div className="admin-overview__metric"><small>Записи</small><strong>{overview.data.booking_count}</strong><span>За выбранный месяц</span></div><div className="admin-overview__metric"><small>Занятия выданы / использованы</small><strong>{overview.data.lessons_granted} / {overview.data.lessons_used}</strong><span>За выбранный месяц</span></div><div className="admin-overview__metric"><small>Завершено уроков</small><strong>{overview.data.completed_lessons}</strong><span>За выбранный месяц</span></div></div><p className="admin-note">Подробные показатели курса пока недоступны.</p></section></div>)}</>}
    {tab === "students" && (selected ? <section className="admin-scoped-content course-admin-profile">
      <button ref={profileBack} className="admin-back course-admin-profile__back" type="button" disabled={busy} onClick={() => setSelected(null)}><ArrowLeft size={16} aria-hidden="true" /> К аккаунтам</button>
      <header className="course-admin-profile__header">
        <div className="course-admin-profile__identity"><h2>{selected.username}</h2><p>{selected.masked_email}</p></div>
        <div className="course-admin-profile__month"><span>Месяц занятий</span><Month month={month} change={changeMonth} disabled={busy} /></div>
      </header>
      {user.account_ref && (user.role === "leader" || user.role === "admin") && typeof user.auth_revision === "number" && <CourseStudentPanels
        key={`panels:${selected.id}:${selected.account_ref}`} courseId={courseId} target={selected} actorRef={user.account_ref}
        month={month} monthControl={<Month month={month} change={changeMonth} disabled={busy} label="Месяц календаря занятий" />}
        visitsMode={!calendarActivityEnabled ? "disabled" : analyticsNeedsSearch ? "search-required" : "enabled"} revision={revision}
        subscriptionActions={<StudentActions base={base} person={selected} month={month} busy={busy} perform={perform}
          quota={{ready: Boolean(quota.data), loading: quota.loading, error: quota.error, retry: () => setRevision((value) => value + 1)}} />}
      />}
      <StudentProfileDisclosure key={`statistics:${selected.id}:${selected.account_ref}`} title="Статистика ученика">
        {user.account_ref && user.role && typeof user.auth_revision === "number" ? <CourseStudentStatistics
          courseId={courseId} target={selected} actorRef={user.account_ref} actorRole={user.role} authRevision={user.auth_revision}
          analyticsEnabled={analyticsReportsEnabled && !analyticsNeedsSearch}
          timeDisabledMessage={analyticsNeedsSearch ? "У ученика нет записей на курс. Для просмотра времени откройте профиль через «Поиск по всем ученикам»." : undefined}
        /> : <p className="form-error" role="alert">Не удалось подтвердить учётную запись. Обновите страницу и войдите снова.</p>}
      </StudentProfileDisclosure>
    </section> : <CourseAccountDirectory
      roster={{
        ...roster,
        loading: rosterSearchPending || roster.loading,
        retry: () => setRosterRevision((value) => value + 1),
        page: rosterLocation.cursors.length,
        previous: () => setRosterLocation((location) => ({ ...location, cursors: location.cursors.slice(0, -1) })),
        next: (nextCursor) => setRosterLocation((location) => ({ ...location, cursors: [...location.cursors, nextCursor] })),
      }}
      global={{
        ...result,
        retry: () => setSearchRevision((value) => value + 1),
        page: searchCursors.length,
        previous: () => setSearchCursors((cursors) => cursors.slice(0, -1)),
        next: (nextCursor) => setSearchCursors((cursors) => [...cursors, nextCursor]),
      }}
      rosterSearch={search} setRosterSearch={setSearch}
      rosterMonthControl={<Month month={month} change={changeMonth} label="Месяц остатка занятий" />}
      globalSearch={grantSearch} setGlobalSearch={(value) => {
        setGrantSearch(value);
        if (!value.trim()) { setQuery(""); setSearchCursors([""]); }
      }}
      submittedSearch={query} submitGlobalSearch={submitGlobalSearch}
      openRosterProfile={(person) => select(person, true)} openGlobalProfile={(person) => select(person, false)}
      restoreFocus={restoreFocus}
    />)}
    {scheduling && <><State {...ownerContext} retry={() => setRevision((value) => value + 1)}/>{!ownerContext.loading && !ownerContext.error && !owner && <p className="form-error" role="alert">{activeOwners.length > 1 ? "Для курса найдено несколько активных ведущих. Расписание недоступно до исправления конфигурации." : "Ведущий курса не назначен. Расписание недоступно."}</p>}
      {tab === "slots" && <div className="admin-slots"><form className="slot-form" onSubmit={(e) => { e.preventDefault(); if (owner) void perform(`${base}/slots`, { owner_id: owner.id, slot_date: date, start_time: time, duration_minutes: duration }); }}><label>Дата<input type="date" required value={date} onChange={(e) => setDate(e.target.value)}/></label><label>Время (Москва)<input type="time" required value={time} onChange={(e) => setTime(e.target.value)}/></label><label>Длительность<select value={duration} onChange={(e) => setDuration(Number(e.target.value))}><option value={60}>60 мин</option><option value={90}>90 мин</option><option value={120}>120 мин</option></select></label><button className="admin-scoped-button admin-scoped-button--primary" type="submit" disabled={busy || !date || !time || !owner}><CalendarPlus size={16}/> Добавить</button></form>
      <section className="slots-schedule"><Month month={month} change={changeMonth} /><label className="admin-scoped-field">Причина архивирования <input className="admin-scoped-input" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} /></label><State {...slots} retry={() => setRevision((value) => value + 1)}/>{slots.data && <>{sortedSlots.length === 0 ? <p className="admin-note">На этот месяц слотов нет. Добавьте свободное окно выше.</p> : <div className="slot-days">{[...slotGroups].map(([day, daySlots]) => <section className="slot-day-group" key={day}><header className="slot-day-head"><strong>{new Date(`${day}T12:00:00+03:00`).toLocaleDateString("ru-RU", {timeZone:"Europe/Moscow", day:"numeric", month:"long", weekday:"long"})}</strong><span>{slotCount(daySlots.length)}</span></header><div className="slot-day-items">{daySlots.map((slot) => <article className="slot-row" key={slot.id}><div className="slot-row-when"><Clock size={15}/><div><strong>{new Date(slot.starts_at).toLocaleTimeString("ru-RU", {timeZone:"Europe/Moscow", hour:"2-digit", minute:"2-digit"})} – {new Date(slot.ends_at).toLocaleTimeString("ru-RU", {timeZone:"Europe/Moscow", hour:"2-digit", minute:"2-digit"})}</strong></div></div><div className="slot-row-status"><span className={slot.status === "free" ? "slot-free" : "slot-booked"}>{slot.status === "free" ? "Свободно" : slot.status === "booked" ? "Занято" : "В архиве"}</span></div>{slot.status === "free" && <button className="admin-scoped-button course-admin-archive" type="button" disabled={busy || !reason.trim()} onClick={() => void perform(`${base}/slots/${slot.id}/archive`, { reason: reason.trim() })}>Архивировать</button>}</article>)}</div></section>)}</div>}</>}</section></div>}
      {tab === "calendar" && <><State {...slots} retry={() => setRevision((value) => value + 1)}/>{slots.data && <CourseCalendar month={month} slots={slots.data.slots} />}</>}</>}
  </section>;
}
