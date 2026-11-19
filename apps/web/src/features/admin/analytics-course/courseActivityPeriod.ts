export type CourseActivityPeriod = 1 | 7 | 30;
const dayMs = 86_400_000;
// Moscow calendar days, half-open [from, to), including today.
export function courseActivityPeriod(days: CourseActivityPeriod, now = Date.now()) {
  const moscowDay = new Date(now + 3 * 3_600_000).toISOString().slice(0, 10);
  const end = Date.parse(`${moscowDay}T00:00:00+03:00`) + dayMs;
  return { from_time: new Date(end - days * dayMs).toISOString(), to_time: new Date(end).toISOString() };
}
