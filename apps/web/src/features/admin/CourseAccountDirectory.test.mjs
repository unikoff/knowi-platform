import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const source = readFileSync(new URL("./CourseAccountDirectory.tsx", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
});
const output = {};
new Function("require", "exports", outputText)(createRequire(import.meta.url), output);

const noop = () => {};
const idle = { data: null, loading: false, error: "", page: 1, retry: noop, previous: noop, next: noop };
const student = { id: 1, username: "Анна", masked_email: "а***@example.test", account_ref: "ref" };

function render(props) {
  return renderToStaticMarkup(createElement(output.CourseAccountDirectory, {
    roster: idle,
    global: idle,
    rosterSearch: "",
    rosterMonthControl: createElement("button", {type: "button"}, "Ноябрь 2026"),
    globalSearch: "",
    submittedSearch: "",
    setRosterSearch: noop,
    setGlobalSearch: noop,
    submitGlobalSearch: noop,
    openRosterProfile: noop,
    openGlobalProfile: noop,
    restoreFocus: null,
    ...props,
  }));
}

test("the course includes a profile row for an account with a quota and no booking", () => {
  const html = render({ roster: { ...idle, data: {
    users: [{ ...student, last_booking_status: null, display_booking: null, subscription_until: null }],
    next_cursor: null,
    history_coverage: "legacy_partial",
  } } });
  assert.match(html, /<button[^>]*course-account-row--roster[^>]*>[\s\S]*Анна[\s\S]*Нет записей/);
  assert.doesNotMatch(html, /Ученики с выданными занятиями или записями за всё время|Некоторые старые записи могли не сохраниться/);
  assert.match(html, /<\/section><section[^>]*course-accounts__panel--search[^>]*>[\s\S]*Поиск по всем ученикам/);
  assert.doesNotMatch(html, /Результаты поиска по всем ученикам/);
});

test("an error in the global search keeps the course list and its pagination usable", () => {
  const html = render({
    roster: { ...idle, page: 2, data: {
      users: [{ ...student, last_booking_status: "booked", subscription_until: "2026-11-19T22:00:00Z" }],
      next_cursor: "page3",
      history_coverage: "legacy_partial",
    } },
    global: { ...idle, error: "Поиск недоступен. Попробуйте ещё раз." },
    globalSearch: "Валерия",
    submittedSearch: "Валерия",
  });
  const [roster, global] = html.split("</section><section");
  assert.match(roster, /Данные недоступны/);
  assert.doesNotMatch(roster, /Запланирована|Последняя запись/);
  assert.match(roster, /20\.11\.2026/);
  assert.match(roster, /Страница 2/);
  assert.match(roster, /Следующая страница/);
  assert.doesNotMatch(roster, /role="alert"/);
  assert.match(global, /role="alert"[\s\S]*Поиск недоступен[\s\S]*Повторить/);
  assert.match(global, /value="Валерия"/);
  assert.doesNotMatch(global, /Страница 2/);
});

test("only the course list shows the selected month and distinguishes a zero balance from missing data", () => {
  const html = render({
    roster: { ...idle, data: {
      users: [
        { ...student, last_booking_status: "completed", subscription_until: null, remaining_lessons: 0 },
        { ...student, id: 2, username: "Борис", last_booking_status: null, subscription_until: null, remaining_lessons: 4 },
        { ...student, id: 3, username: "Вера", last_booking_status: null, subscription_until: null, remaining_lessons: null },
        { ...student, id: 4, username: "Глеб", last_booking_status: null, subscription_until: null },
      ],
      next_cursor: null,
    } },
    global: { ...idle, data: { users: [student], next_cursor: null } },
    globalSearch: student.username,
    submittedSearch: student.username,
  });
  const [roster, global] = html.split("</section><section");
  assert.match(roster, /Ноябрь 2026/);
  assert.deepEqual(
    [...roster.matchAll(/Осталось занятий<\/small><strong[^>]*>([^<]+)<\/strong>/g)].map(match => match[1]),
    ["0", "4", "—", "—"],
  );
  assert.doesNotMatch(global, /Осталось занятий|Ноябрь 2026/);
});

test("course rows show upcoming or latest booking times in Moscow without a status", () => {
  const renderBooking = (display_booking, last_booking_status) => render({roster: {...idle, data: {
    users: [{...student, display_booking, last_booking_status, subscription_until: null}], next_cursor: null,
  }}});
  const upcoming = renderBooking({kind: "upcoming", status: "booked", starts_at: "2026-11-19T21:15:00Z", ends_at: "2026-11-19T22:15:00Z"}, "cancelled");
  assert.match(upcoming, /Ближайшая запись[\s\S]*20\.11\.2026[\s\S]*00:15–01:15/);
  assert.doesNotMatch(upcoming, /Последняя запись|Запланирована|Отменена/);

  const latest = {kind: "latest", starts_at: "2026-11-05T11:00:00Z", ends_at: "2026-11-05T11:30:00Z"};
  const past = renderBooking({...latest, status: "completed"}, "booked");
  assert.match(past, /Последняя запись[\s\S]*05\.11\.2026[\s\S]*14:00–14:30/);
  assert.doesNotMatch(past, /Завершена|Забронирована|Ближайшая запись/);

  const legacyCancellation = renderBooking({...latest, status: "cancelled"}, "cancelled");
  assert.match(legacyCancellation, /Данные недоступны/);
  assert.doesNotMatch(legacyCancellation, /Последняя запись|Отменена|14:00–14:30/);
});
