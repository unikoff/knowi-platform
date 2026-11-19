import type { ActivityToday } from "./scopedApi";
import { moscowDate } from "./moscowDay";

function formatCoverage(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Moscow",
  }).format(date);
}

export function PlatformActivity({ activity, scope }: { activity?: ActivityToday | null; scope: string }) {
  const available = activity?.date === moscowDate() && typeof activity.unique_users === "number" && Number.isFinite(activity.unique_users);
  const note = !available ? "Данные о посещениях сегодня недоступны"
    : activity!.partial_day ? activity!.coverage_since ? `Данные с ${formatCoverage(activity!.coverage_since)} (Москва)` : "Данные за неполный день"
      : "Уникальные посетители за сегодня";
  return <section className="admin-overview__group"><div className="admin-overview__group-heading"><h2>Посещения сегодня</h2></div><div className="admin-overview__metric"><small>{scope}</small><strong>{available ? activity?.unique_users?.toLocaleString("ru-RU") ?? "—" : "—"}</strong><span>{note}</span></div></section>;
}
