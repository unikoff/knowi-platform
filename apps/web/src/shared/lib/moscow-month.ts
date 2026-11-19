export function moscowCalendarMonth(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", {timeZone: "Europe/Moscow", year: "numeric", month: "numeric"}).formatToParts(now);
  return {year: Number(parts.find((item) => item.type === "year")?.value), month: Number(parts.find((item) => item.type === "month")?.value)};
}
