import { ChevronLeft, ChevronRight, Search, UserRoundSearch, UsersRound, X } from "lucide-react";
import { useEffect, useId, useRef, type FormEvent, type ReactNode } from "react";
import type { Person, RosterPerson } from "./scopedApi";

type DirectoryPage<PersonType> = { users: PersonType[]; next_cursor: string | null };

type SearchState<Page> = {
  data: Page | null;
  loading: boolean;
  error: string;
  retry: () => void;
  page: number;
  previous: () => void;
  next: (cursor: string) => void;
};

type Props = {
  roster: SearchState<DirectoryPage<RosterPerson>>;
  global: SearchState<DirectoryPage<Person>>;
  rosterSearch: string;
  setRosterSearch: (input: string) => void;
  rosterMonthControl: ReactNode;
  globalSearch: string;
  setGlobalSearch: (input: string) => void;
  submittedSearch: string;
  submitGlobalSearch: () => void;
  openRosterProfile: (person: RosterPerson) => void;
  openGlobalProfile: (person: Person) => void;
  restoreFocus: "roster" | "global" | null;
};

const moscowDate = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Europe/Moscow",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});
const moscowTime = new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit" });

function BookingDetail({ person }: { person: RosterPerson }) {
  const booking = person.display_booking;
  const hasDate = booking != null && booking.status !== "cancelled";
  return <span className="course-account-row__detail course-account-row__detail--booking">
    <small>{hasDate ? (booking.kind === "upcoming" ? "Ближайшая запись" : "Последняя запись") : "Записи"}</small>
    {hasDate ? <span className="course-account-row__booking-info">
      <time dateTime={booking.starts_at}>{moscowDate.format(new Date(booking.starts_at))}</time>{" "}
      <span className="course-account-row__booking-time">{moscowTime.format(new Date(booking.starts_at))}–{moscowTime.format(new Date(booking.ends_at))}</span>
    </span> : <span className="course-account-row__booking-empty">{booking === null ? "Нет записей" : "Данные недоступны"}</span>}
  </span>;
}

function AccountRow({ person, roster, open }: { person: Person; roster?: RosterPerson; open: () => void }) {
  const initial = Array.from(person.username.trim())[0] ?? "?";
  return <li>
    <button className={`course-account-row${roster ? " course-account-row--roster" : ""}`} type="button" onClick={open}>
      <span className="course-account-row__avatar" aria-hidden="true">{initial.toLocaleUpperCase("ru-RU")}</span>
      <span className="course-account-row__identity">
        <strong>{person.username}</strong>
        <small>{person.masked_email}</small>
      </span>
      {roster && <span className="course-account-row__details">
        <span className="course-account-row__detail">
          <small>Осталось занятий</small>
          <strong className="course-account-row__balance" data-known={roster.remaining_lessons != null}>
            {roster.remaining_lessons?.toLocaleString("ru-RU") ?? "—"}
          </strong>
        </span>
        <BookingDetail person={roster} />
        <span className="course-account-row__detail">
          <small>Подписка до</small>
          <span>{roster.subscription_until ? moscowDate.format(new Date(roster.subscription_until)) : "—"}</span>
        </span>
      </span>}
      <ChevronRight className="course-account-row__arrow" size={20} aria-hidden="true" />
    </button>
  </li>;
}

function SearchFeedback({ state, pending }: {
  state: { loading: boolean; error: string; retry: () => void };
  pending: string;
}) {
  if (state.loading) return <p className="course-accounts__feedback" role="status">{pending}</p>;
  if (!state.error) return null;
  return <div className="form-error course-accounts__feedback" role="alert">
    <span>{state.error}</span>
    <button className="admin-back" type="button" onClick={state.retry}>Повторить</button>
  </div>;
}

function Pages({ label, page, previous, next, nextCursor }: {
  label: string;
  page: number;
  previous: () => void;
  next: (cursor: string) => void;
  nextCursor: string | null;
}) {
  if (page === 1 && !nextCursor) return null;
  return <nav className="course-accounts__pages" aria-label={label}>
    <button className="course-accounts__icon-button" type="button" disabled={page === 1} onClick={previous} aria-label="Предыдущая страница">
      <ChevronLeft size={18} aria-hidden="true" />
    </button>
    <span>Страница {page}</span>
    <button className="course-accounts__icon-button" type="button" disabled={!nextCursor}
      onClick={() => { if (nextCursor) next(nextCursor); }} aria-label="Следующая страница">
      <ChevronRight size={18} aria-hidden="true" />
    </button>
  </nav>;
}

function SearchField({ value, onChange, label, hintId, placeholder, action }: {
  value: string;
  onChange: (value: string) => void;
  label: string;
  hintId?: string;
  placeholder: string;
  action?: ReactNode;
}) {
  const input = useRef<HTMLInputElement>(null);
  return <div className="course-accounts__search-controls">
    <div className="course-accounts__search-field">
      <Search size={20} aria-hidden="true" />
      <input ref={input} type="search" aria-label={label} aria-describedby={hintId} autoComplete="off"
        placeholder={placeholder} maxLength={100} value={value} onChange={(event) => onChange(event.target.value)} />
      {value && <button className="course-accounts__icon-button" type="button" aria-label={`Очистить: ${label}`} onClick={() => {
        onChange("");
        input.current?.focus();
      }}><X size={18} aria-hidden="true" /></button>}
    </div>
    {action}
  </div>;
}

export function CourseAccountDirectory({ roster, global, rosterSearch, setRosterSearch, rosterMonthControl, globalSearch, setGlobalSearch,
  submittedSearch, submitGlobalSearch, openRosterProfile, openGlobalProfile, restoreFocus }: Props) {
  const rosterTitle = useRef<HTMLHeadingElement>(null);
  const globalTitle = useRef<HTMLHeadingElement>(null);
  const rosterTitleId = useId();
  const globalTitleId = useId();
  const searchHintId = useId();

  useEffect(() => {
    if (restoreFocus === "roster") rosterTitle.current?.focus();
    if (restoreFocus === "global") globalTitle.current?.focus();
  }, [restoreFocus]);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submitGlobalSearch();
  };

  return <div className="course-accounts">
    <section className="course-accounts__panel" aria-labelledby={rosterTitleId}>
      <header className="course-accounts__titlebar">
        <div className="course-accounts__heading">
          <span className="course-accounts__heading-icon" aria-hidden="true"><UsersRound size={21} /></span>
          <h2 ref={rosterTitle} id={rosterTitleId} tabIndex={-1}>Аккаунты курса</h2>
        </div>
        {rosterMonthControl}
      </header>
      <div role="search" aria-label="Поиск по аккаунтам курса">
        <SearchField value={rosterSearch} onChange={setRosterSearch} label="Имя ученика курса" placeholder="Найти в списке курса" />
      </div>
      <SearchFeedback state={roster} pending={rosterSearch.trim() ? "Ищем в списке курса…" : "Загружаем аккаунты курса…"} />
      {roster.data && <>
        <p className="course-accounts__feedback" role="status">{roster.data.users.length
          ? `На странице: ${roster.data.users.length}`
          : rosterSearch.trim() ? "В списке курса ничего не найдено." : "У курса пока нет учеников."}</p>
        {roster.data.users.length > 0 && <ul className="course-accounts__list" aria-label="Ученики курса">
          {roster.data.users.map((person) => <AccountRow key={person.id} person={person} roster={person} open={() => openRosterProfile(person)} />)}
        </ul>}
        <Pages label="Страницы аккаунтов курса" page={roster.page}
          previous={() => { rosterTitle.current?.focus(); roster.previous(); }}
          next={(cursor) => { rosterTitle.current?.focus(); roster.next(cursor); }} nextCursor={roster.data.next_cursor} />
      </>}
    </section>

    <section className="course-accounts__panel course-accounts__panel--search" aria-labelledby={globalTitleId}>
      <header className="course-accounts__heading">
        <span className="course-accounts__heading-icon" aria-hidden="true"><UserRoundSearch size={21} /></span>
        <div>
          <h2 ref={globalTitle} id={globalTitleId} tabIndex={-1}>Поиск по всем ученикам</h2>
          <p id={searchHintId}>Имя от 3 символов или полный адрес почты.</p>
        </div>
      </header>
      <form role="search" aria-labelledby={globalTitleId} onSubmit={submit}>
        <SearchField value={globalSearch} onChange={setGlobalSearch} label="Имя или полная почта" hintId={searchHintId}
          placeholder="Имя или почта" action={<button className="admin-scoped-button admin-scoped-button--primary course-accounts__submit"
            type="submit" disabled={globalSearch.trim().length < 3}
            aria-disabled={global.loading && global.page === 1 && submittedSearch === globalSearch.trim() ? true : undefined}>Найти</button>} />
      </form>
      <SearchFeedback state={global} pending="Ищем по всем ученикам…" />
      {global.data && <>
        <p className="course-accounts__feedback" role="status">{global.data.users.length
          ? `«${submittedSearch}» · На странице: ${global.data.users.length}`
          : `По запросу «${submittedSearch}» ничего не найдено.`}</p>
        {global.data.users.length > 0 && <ul className="course-accounts__list" aria-label="Результаты поиска по всем ученикам">
          {global.data.users.map((person) => <AccountRow key={person.id} person={person} open={() => openGlobalProfile(person)} />)}
        </ul>}
        <Pages label="Страницы общего поиска" page={global.page}
          previous={() => { globalTitle.current?.focus(); global.previous(); }}
          next={(cursor) => { globalTitle.current?.focus(); global.next(cursor); }} nextCursor={global.data.next_cursor} />
      </>}
    </section>
  </div>;
}
