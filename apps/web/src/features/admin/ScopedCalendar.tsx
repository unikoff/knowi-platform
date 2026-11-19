import { useEffect, useMemo, useState } from "react";
import type { CalendarMonth } from "../../shared/api/contracts/account";
import { CalendarMonthGrid } from "../../shared/ui/calendar/CalendarMonthGrid";
import type { Slot } from "./scopedApi";
import "../account/calendar-base.css";
import "../account/calendar.css";
import "./scoped-calendar.css";

function localDay(value: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit"}).formatToParts(new Date(value));
  const part = (name: string) => parts.find((item) => item.type === name)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}
export function ScopedCalendar({ month, slots }: { month: CalendarMonth; slots: Slot[] }) {
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => { setSelected(null); }, [month.year, month.month]);
  const { free, booked, byDay, counts } = useMemo(() => {
    const free = new Set<string>(); const booked = new Set<string>();
    const byDay = new Map<string, Slot[]>(); const counts = new Map<string, number>();
    for (const slot of slots) {
      if (slot.status === "archived") continue;
      const day = localDay(slot.starts_at);
      byDay.set(day, [...(byDay.get(day) ?? []), slot]);
      if (slot.status === "free") { free.add(day); counts.set(day, (counts.get(day) ?? 0) + 1); }
      else booked.add(day);
    }
    return { free, booked, byDay, counts };
  }, [slots]);
  return <section className="account-calendar account-calendar--booking scoped-calendar" aria-label="Календарь курса">
    <div className="account-calendar__layout"><div className="account-calendar__main"><div className="account-calendar__month-frame">
      <CalendarMonthGrid month={month} activityDays={new Set()} freeDays={free} freeSlotCounts={counts} bookingDays={booked} bookingLabel="Занято" showCellEvents={false}
        selectableDays={new Set(byDay.keys())} selectedDay={selected} onSelectDay={(day) => setSelected(day === selected ? null : day)}
        legend={[{marker: "is-free", label: "Свободное время"}, {marker: "is-booking", label: "Занято"}]} />
    </div></div><aside className="account-calendar__details" aria-live="polite"><h3>{selected ? new Date(`${selected}T12:00:00+03:00`).toLocaleDateString("ru-RU", {timeZone:"Europe/Moscow", day:"numeric", month:"long"}) : "Выберите дату"}</h3>
      {selected && (byDay.get(selected) ?? []).map((slot) => <div className="account-calendar__slot" key={slot.id}><time dateTime={slot.starts_at}>{new Date(slot.starts_at).toLocaleTimeString("ru-RU", {timeZone:"Europe/Moscow", hour:"2-digit", minute:"2-digit"})}</time><span>{slot.status === "free" ? "Свободно" : "Занято"}</span></div>)}
    </aside></div>
  </section>;
}
