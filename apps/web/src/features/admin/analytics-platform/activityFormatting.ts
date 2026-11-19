export function formatActivityCount(value: number, incomplete: boolean) {
  return !Number.isFinite(value) || value < 0 || (incomplete && value === 0) ? "—" : value.toLocaleString("ru-RU");
}
export function formatActivityMinutes(value: number, incomplete: boolean) {
  if (!Number.isFinite(value) || value < 0 || (incomplete && value === 0)) return "—";
  const result = value / 60_000;
  if (result > 0 && result < 0.01) return "< 0,01 мин";
  return `${result.toLocaleString("ru-RU", {maximumFractionDigits: 2})} мин`;
}
