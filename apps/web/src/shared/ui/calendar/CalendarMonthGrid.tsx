import { buildMonthCells, getLocalDateKey, getMonthTitle } from "../../lib/dates";
import { Check } from "lucide-react";
import { useRef, useState, type KeyboardEvent } from "react";
import type { CalendarMonth } from "../../api/contracts/account";

export type CalendarMarker = "is-activity" | "is-free" | "is-booking" | "is-completed" | "is-confirmed" | "is-cancelled";

type CalendarMonthGridProps = {
  month: CalendarMonth;
  activityDays: Set<string>;
  activityLabel?: string;
  freeDays?: Set<string>;
  freeSlotCounts?: ReadonlyMap<string, number>;
  bookingDays?: Set<string>;
  bookingLabel?: string;
  completedDays?: Set<string>;
  confirmedDays?: Set<string>;
  cancelledDays?: Set<string>;
  showCellEvents?: boolean;
  selectableDays?: Set<string>;
  selectAllDays?: boolean;
  todayDateKey?: string;
  selectedDay?: string | null;
  onSelectDay?: (dateKey: string) => void;
  legend: Array<{ marker: CalendarMarker; label: string }>;
};

const weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const dayOffsets = new Map([["ArrowLeft", -1], ["ArrowRight", 1], ["ArrowUp", -7], ["ArrowDown", 7]]);

export function CalendarMonthGrid({
  month,
  activityDays,
  activityLabel = "была активность",
  freeDays = new Set(),
  freeSlotCounts,
  bookingDays = new Set(),
  bookingLabel = "Ваша запись",
  completedDays = new Set(),
  confirmedDays = new Set(),
  cancelledDays = new Set(),
  showCellEvents = true,
  selectableDays = new Set(),
  selectAllDays = false,
  todayDateKey,
  selectedDay = null,
  onSelectDay,
  legend,
}: CalendarMonthGridProps) {
  const today = todayDateKey ?? getLocalDateKey();
  const cells = buildMonthCells(month);
  const grid = useRef<HTMLDivElement>(null);
  const [focused, setFocused] = useState<string | null>(null);
  const focusDay = cells.find((cell) => cell?.dateKey === focused)?.dateKey
    ?? cells.find((cell) => cell?.dateKey === today)?.dateKey
    ?? cells.find((cell) => cell !== null)?.dateKey;
  const lastDay = cells[cells.length - 1]?.day ?? 1;

  function moveFocus(event: KeyboardEvent<HTMLButtonElement>, day: number) {
    const direction = dayOffsets.get(event.key);
    const next = event.key === "Home" ? 1 : event.key === "End" ? lastDay : direction === undefined ? null : day + direction;
    if (next === null) return;
    event.preventDefault();
    const target = Math.max(1, Math.min(next, lastDay));
    grid.current?.querySelector<HTMLButtonElement>(`[data-calendar-day="${target}"]`)?.focus();
  }

  return (
    <>
      <div className="account-calendar__weekdays" aria-hidden="true">
        {weekdays.map((weekday) => <span key={weekday}>{weekday}</span>)}
      </div>
      <div key={`${month.year}:${month.month}`} ref={grid} className="account-calendar__grid"
        role={selectAllDays ? "group" : "grid"}
        aria-label={`События за ${getMonthTitle(month)}${selectAllDays ? ". Стрелки перемещают фокус, Enter выбирает день." : ""}`}>
        {cells.map((cell, index) => {
          if (!cell) return <span className="account-calendar__day is-empty" key={`empty-${index}`} aria-hidden="true" />;

          const hasActivity = activityDays.has(cell.dateKey);
          const hasFree = freeDays.has(cell.dateKey);
          const freeSlotCount = freeSlotCounts?.get(cell.dateKey) ?? 1;
          const hasBooking = bookingDays.has(cell.dateKey);
          const hasCompleted = completedDays.has(cell.dateKey);
          const hasConfirmed = confirmedDays.has(cell.dateKey);
          const hasCancelled = cancelledDays.has(cell.dateKey);
          const isSelected = selectedDay === cell.dateKey;
          const dayClass = [
            "account-calendar__day",
            cell.dateKey === today ? "is-today" : "",
            hasActivity ? "has-activity" : "",
            hasFree ? "has-free" : "",
            hasBooking ? "has-booking" : "",
            hasCompleted ? "has-completed" : "",
            hasConfirmed ? "has-confirmed" : "",
            hasCancelled ? "has-cancelled" : "",
            isSelected ? "is-selected" : "",
          ].filter(Boolean).join(" ");
          const dateLabel = new Date(month.year, month.month - 1, cell.day)
            .toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" });
          const statusLabel = [
            hasFree && `есть свободные окна: ${freeSlotCount}`,
            hasBooking && bookingLabel,
            hasCompleted && "время занятия прошло",
            hasConfirmed && "запись отмечена завершённой",
            hasCancelled && "запись отменена",
            hasActivity && activityLabel,
          ].filter(Boolean).join(", ");
          const dayEvents = showCellEvents ? (
            <span className="account-calendar__day-events" aria-hidden="true">
              {hasFree && <span className="account-calendar__day-event is-free">Свободно · {freeSlotCount}</span>}
              {hasBooking && <span className="account-calendar__day-event is-booking">{bookingLabel}</span>}
              {hasCompleted && <span className="account-calendar__day-event is-completed">Занятие прошло</span>}
              {hasConfirmed && <span className="account-calendar__day-event is-confirmed">Завершена</span>}
              {hasCancelled && <span className="account-calendar__day-event is-cancelled">Отмена</span>}
            </span>
          ) : (
            <span className="account-calendar__markers" aria-hidden="true">
              {hasFree && <i className="is-free" />}
              {hasBooking && <i className="is-booking" />}
              {hasCompleted && <i className="is-completed" />}
              {hasConfirmed && <i className="is-confirmed" />}
              {hasCancelled && <i className="is-cancelled" />}
            </span>
          );
          const dayContent = <>
            <span className="account-calendar__day-heading">
              <span className="account-calendar__day-number">{cell.day}</span>
              {hasActivity && <Check className="account-calendar__visit-check" size={15} aria-hidden="true" />}
            </span>
            {dayEvents}
          </>;

          if (onSelectDay && (selectAllDays || selectableDays.has(cell.dateKey))) {
            return (
              <button
                className={dayClass}
                key={cell.key}
                type="button"
                role={selectAllDays ? undefined : "gridcell"}
                aria-pressed={isSelected}
                aria-label={`${dateLabel}${statusLabel ? `, ${statusLabel}` : ""}`}
                aria-current={selectAllDays && cell.dateKey === today ? "date" : undefined}
                tabIndex={selectAllDays ? (focusDay === cell.dateKey ? 0 : -1) : undefined}
                data-calendar-day={selectAllDays ? cell.day : undefined}
                onFocus={selectAllDays ? () => setFocused(cell.dateKey) : undefined}
                onKeyDown={selectAllDays ? (event) => moveFocus(event, cell.day) : undefined}
                onClick={() => onSelectDay(cell.dateKey)}
              >
                {dayContent}
              </button>
            );
          }

          return (
            <span
              className={dayClass}
              key={cell.key}
              role="gridcell"
              title={statusLabel ? `${dateLabel}: ${statusLabel}` : undefined}
              aria-label={`${dateLabel}${statusLabel ? `, ${statusLabel}` : ""}`}
            >
              {dayContent}
            </span>
          );
        })}
      </div>
      <div className="account-calendar__legend" aria-label="Обозначения календаря">
        {legend.map(({ marker, label }) => <span key={marker}><i className={marker} /> {label}</span>)}
      </div>
    </>
  );
}
