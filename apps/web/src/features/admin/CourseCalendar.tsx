import { useEffect, useMemo, useState } from "react";
import { Clock } from "lucide-react";
import type { CalendarMonth } from "../../shared/api/contracts/account";
import { buildMonthCells, formatSlotDate } from "../../shared/lib/dates";
import type { Slot } from "./scopedApi";
import "./calendar/calendar.css";
import "./course-calendar.css";

const weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const dayKey = (value: string) => new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(value));
const time = (value: string) => new Date(value).toLocaleTimeString("ru-RU", { timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit" });
export function CourseCalendar({ month, slots }: { month: CalendarMonth; slots: Slot[] }) {
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => setSelected(null), [month.year, month.month]);
  const cells = useMemo(() => buildMonthCells(month), [month.year, month.month]);
  const byDay = useMemo(() => {
    const groups = new Map<string, Slot[]>();
    for (const slot of [...slots].filter((item) => item.status !== "archived").sort((a, b) => a.starts_at.localeCompare(b.starts_at))) {
      const day = dayKey(slot.starts_at);
      groups.set(day, [...(groups.get(day) ?? []), slot]);
    }
    return groups;
  }, [slots]);
  const visible = selected ? byDay.get(selected) ?? [] : [...byDay.values()].flat();
  return <section className="admin-calendar course-calendar" aria-label="Календарь курса">
    <header className="admin-calendar__header"><div className="admin-calendar__title"><div><h2>Календарь занятий</h2><p>Расписание курса · время московское, UTC+3</p></div></div></header>
    <div className="admin-calendar__scroll" role="region" aria-label="Месячный календарь занятий" tabIndex={0}><div className="admin-calendar__month">
      <div className="admin-calendar__weekdays" aria-hidden="true">{weekdays.map((day) => <span key={day}>{day}</span>)}</div>
      <div className="admin-calendar__grid" role="group" aria-label="Дни месяца">{cells.map((cell, index) => {
        if (!cell) return <div className="admin-calendar__blank" key={`empty-${index}`} aria-hidden="true"/>;
        const daySlots = byDay.get(cell.dateKey) ?? [];
        return <div key={cell.key} className={`admin-calendar__day ${selected === cell.dateKey ? "is-selected" : ""}`}><button type="button" className="admin-calendar__date" aria-pressed={selected === cell.dateKey} aria-label={`${formatSlotDate(cell.dateKey)}, слотов: ${daySlots.length}`} onClick={() => setSelected((current) => current === cell.dateKey ? null : cell.dateKey)}><span>{cell.day}</span>{daySlots.length > 0 && <small>{daySlots.length}</small>}</button><div className="admin-calendar__day-events">{daySlots.slice(0, 3).map((slot) => <button type="button" className="admin-calendar__event" key={slot.id} onClick={() => setSelected(cell.dateKey)}><strong>{time(slot.starts_at)}</strong><span>{slot.status === "free" ? "Свободно" : "Занято"}</span></button>)}{daySlots.length > 3 && <button type="button" className="admin-calendar__more" onClick={() => setSelected(cell.dateKey)}>Ещё {daySlots.length - 3}</button>}</div></div>;
      })}</div></div></div>
    <div className="admin-calendar__agenda" aria-live="polite"><div className="admin-calendar__agenda-title"><h3>{selected ? `Расписание · ${formatSlotDate(selected)}` : "Расписание за месяц"} <small>{visible.length}</small></h3>{selected && <button type="button" onClick={() => setSelected(null)}>Показать весь месяц</button>}</div>{visible.length === 0 ? <p className="admin-note">{selected ? "На этот день слотов нет." : "На этот месяц слотов нет."}</p> : visible.map((slot) => <div className="admin-calendar__booking course-calendar__booking" key={slot.id}><Clock size={17} aria-hidden="true"/><div><strong>{formatSlotDate(dayKey(slot.starts_at))} · {time(slot.starts_at)} – {time(slot.ends_at)}</strong><small>{slot.status === "free" ? "Свободно" : "Занято"}</small></div></div>)}</div>
  </section>;
}
