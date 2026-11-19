import type { CSSProperties } from "react";
import { learningTimeScale } from "./learningTimeScale";

export type WeeklyActivityDay = { date: string; minutes: number | null };

function formatDay(date: string, options: Intl.DateTimeFormatOptions) {
  return new Intl.DateTimeFormat("ru-RU", { ...options, timeZone: "UTC" }).format(new Date(`${date}T12:00:00Z`));
}

export function WeeklyActivityBars({ days, detailed = false }: { days: WeeklyActivityDay[]; detailed?: boolean }) {
  return <span className={`activity-week${detailed ? " activity-week--detailed" : ""}`} aria-hidden={detailed ? undefined : true}>
    {days.map((day, index) => {
      const scale = learningTimeScale(day.minutes);
      const minutes = scale === null ? null : Math.round(day.minutes!);
      const description = `${formatDay(day.date, { day: "numeric", month: "long" })}: ${minutes !== null
        ? `${Math.floor(minutes / 60)} ч ${minutes % 60} мин обучения на платформе`
        : "нет данных о времени обучения"}`;
      return <span key={day.date} className="activity-week__day" role={detailed ? "img" : undefined} aria-label={detailed ? description : undefined} title={description}>
        <span className={`activity-week__bar${scale === null ? " is-unknown" : day.minutes! > 0 ? " has-time" : ""}${index === days.length - 1 ? " is-today" : ""}`}
          style={scale === null ? undefined : { "--activity-fill": `${scale.fillPercent}%`, "--activity-white": `${scale.whitePercent}%` } as CSSProperties} />
        {detailed && <span className="activity-week__label">{formatDay(day.date, { weekday: "short" }).replace(".", "")}</span>}
      </span>;
    })}
  </span>;
}
