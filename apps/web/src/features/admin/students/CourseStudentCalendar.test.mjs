import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const require = createRequire(import.meta.url);
function compile(path, localImports = {}) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020},
  });
  const exports = {};
  new Function("require", "exports", outputText)((id) => id.startsWith(".") ? (localImports[id] ?? {}) : require(id), exports);
  return exports;
}

const { bookingCalendarMarker, measuredCalendarDays } = compile("./CourseStudentPanels.tsx");
const dates = compile("../../../shared/lib/dates.ts");
const { CalendarMonthGrid } = compile("../../../shared/ui/calendar/CalendarMonthGrid.tsx", {"../../lib/dates": dates});

test("the end time does not turn a booking into a completed one", () => {
  const booking = {id: 1, starts_at: "2026-11-19T16:00:00+03:00", ends_at: "2026-11-19T17:00:00+03:00", status: "booked"};
  assert.equal(bookingCalendarMarker(booking, Date.parse("2026-11-19T13:59:59Z")), "booked");
  const ended = Date.parse("2026-11-19T14:00:00Z");
  assert.equal(bookingCalendarMarker(booking, ended), "elapsed");
  assert.equal(bookingCalendarMarker({...booking, status: "completed"}, ended), "completed");
  assert.equal(bookingCalendarMarker({...booking, status: "cancelled"}, ended), "cancelled");
});

test("a visit without measured active time does not mark the course calendar", () => {
  assert.deepEqual([...measuredCalendarDays([
    {date: "2026-11-18", unique_visitors: 1, active_ms: 0},
    {date: "2026-11-19", unique_visitors: 0, active_ms: 90000},
  ])], ["2026-11-19"]);
});

test("the shared student grid keeps learning, elapsed bookings and cancellations distinct", () => {
  const date = "2026-11-19";
  const html = renderToStaticMarkup(createElement(CalendarMonthGrid, {
    month: {year: 2026, month: 11},
    activityDays: new Set([date]),
    activityLabel: "учебная активность на курсе",
    completedDays: new Set([date]),
    cancelledDays: new Set([date]),
    todayDateKey: date,
    selectedDay: date,
    selectAllDays: true,
    showCellEvents: false,
    onSelectDay() {},
    legend: [{marker: "is-activity", label: "Учебная активность на курсе"}],
  }));
  assert.match(html, /account-calendar__day is-today has-activity has-completed has-cancelled is-selected/);
  assert.match(html, /время занятия прошло, запись отменена, учебная активность на курсе/);
  assert.match(html, /aria-current="date" tabindex="0" data-calendar-day="19"/);
  assert.equal((html.match(/tabindex="0"/g) ?? []).length, 1);
  assert.match(html, /account-calendar__visit-check/);
  assert.match(html, /<i class="is-completed"><\/i><i class="is-cancelled"><\/i>/);
});
