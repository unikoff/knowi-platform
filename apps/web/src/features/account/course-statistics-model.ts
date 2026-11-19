export type StatisticsPeriod = 1 | 7 | 30 | 90;
export type TimeMetric = 'activeMs' | 'theoryMs' | 'practiceMs';
export type CourseTimeValues = {activeMs: number | null; theoryMs: number | null; practiceMs: number | null};
export type CourseTimeDay = CourseTimeValues & {date: string};
export type CourseLessonTime = CourseTimeValues & {lessonId: string};
export type CourseTimeSnapshot = {
  courseId: string; from: string; to: string; quality: 'complete' | 'partial' | 'unknown'; asOf: string | null;
  totals: CourseTimeValues; days: CourseTimeDay[]; lessons: CourseLessonTime[];
};

const emptyValues = (): CourseTimeValues => ({activeMs: null, theoryMs: null, practiceMs: null});
const validTime = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
const cleanValues = (values: Partial<CourseTimeValues> | null | undefined): CourseTimeValues => ({
  activeMs: validTime(values?.activeMs), theoryMs: validTime(values?.theoryMs), practiceMs: validTime(values?.practiceMs),
});

export function moscowToday(now = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', {timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit'}).formatToParts(now);
  const part = (type: string) => parts.find(item => item.type === type)?.value;
  return `${part('year')}-${part('month')}-${part('day')}`;
}

function dateAtNoon(date: string): Date {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new RangeError('Invalid date key');
  const result = new Date(`${date}T12:00:00Z`);
  if (!Number.isFinite(result.getTime()) || result.toISOString().slice(0, 10) !== date) throw new RangeError('Invalid date key');
  return result;
}

export function shiftDate(date: string, delta: number): string {
  const result = dateAtNoon(date);
  result.setUTCDate(result.getUTCDate() + delta);
  return result.toISOString().slice(0, 10);
}

export function validateStudyRange(from: string, to: string, today = moscowToday()): {valid: true; days: number} | {valid: false; message: string} {
  let start: number, end: number;
  try {
    start = dateAtNoon(from).getTime();
    end = dateAtNoon(to).getTime();
  } catch {
    return {valid: false, message: 'Укажите корректные даты'};
  }
  if (start > end) return {valid: false, message: 'Начало периода должно быть не позже окончания'};
  if (end > dateAtNoon(today).getTime()) return {valid: false, message: 'Дата окончания не может быть в будущем'};
  const days = (end - start) / 86400000 + 1;
  if (days > 31) return {valid: false, message: 'Можно выбрать не больше 31 дня'};
  return {valid: true, days};
}

export function periodDates(days: number, endDate: string): string[] {
  return Array.from({length: days}, (_, index) => shiftDate(endDate, index - days + 1));
}

export function formatStudyTime(ms: number | null): string {
  const time = validTime(ms);
  if (time === null) return '—';
  if (time === 0) return '0 мин';
  if (time < 60000) return '< 1 мин';
  const minutes = Math.floor(time / 60000);
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return hours ? `${hours} ч${remainder ? ` ${remainder} мин` : ''}` : `${minutes} мин`;
}

function formatChartTime(ms: number): string {
  if (ms < 1000) return `${ms.toLocaleString('ru-RU', {maximumSignificantDigits: 3})} мс`;
  const totalSeconds = Math.round(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor(totalSeconds / 60) % 60;
  const seconds = totalSeconds % 60;
  return [hours && `${hours} ч`, minutes && `${minutes} мин`, seconds && `${seconds} с`].filter(Boolean).join(' ');
}

/** Uses the largest observed day in the given series as the top of the axis. */
export function courseTimeChartScale(days: readonly CourseTimeValues[], metric: TimeMetric): {
  maxMs: number;
  ticks: readonly [top: string, middle: string, baseline: string];
} | null {
  const maxMs = days.reduce((max, day) => Math.max(max, validTime(day[metric]) ?? 0), 0);
  if (maxMs === 0) return null;
  return {maxMs, ticks: [formatChartTime(maxMs), formatChartTime(maxMs / 2), '0']};
}

export function formatStudyDate(date: string, options: Intl.DateTimeFormatOptions = {day: 'numeric', month: 'short'}): string {
  return new Intl.DateTimeFormat('ru-RU', {...options, timeZone: 'Europe/Moscow'}).format(dateAtNoon(date));
}

export function emptyCourseSnapshot(courseId: string, dates: string[]): CourseTimeSnapshot {
  return {courseId, from: dates[0] ?? '', to: dates[dates.length - 1] ?? '', quality: 'unknown', asOf: null,
    totals: emptyValues(), days: dates.map(date => ({date, ...emptyValues()})), lessons: []};
}

export function acceptCourseSnapshot(snapshot: CourseTimeSnapshot | null | undefined, courseId: string, dates: string[]): CourseTimeSnapshot {
  const empty = emptyCourseSnapshot(courseId, dates);
  if (!snapshot || snapshot.courseId !== courseId || snapshot.from !== empty.from || snapshot.to !== empty.to) return empty;
  const days = new Map((snapshot.days ?? []).map(day => [day.date, day]));
  return {courseId, from: empty.from, to: empty.to, quality: snapshot.quality, asOf: snapshot.asOf,
    totals: cleanValues(snapshot.totals), days: dates.map(date => ({date, ...cleanValues(days.get(date))})),
    lessons: (snapshot.lessons ?? []).map(lesson => ({lessonId: lesson.lessonId, ...cleanValues(lesson)}))};
}

export function summarizeCourseTime(snapshot: CourseTimeSnapshot): {activeDays: number | null; averageMs: number | null} {
  if (snapshot.totals.activeMs === null) return {activeDays: null, averageMs: null};
  const activeDays = snapshot.days.filter(day => (day.activeMs ?? 0) > 0).length;
  return {activeDays, averageMs: activeDays > 0 ? snapshot.totals.activeMs / activeDays : 0};
}

export type ProgressLesson = {id: string; trackId: string; blockId: string; chapterTitle: string; blockTitle: string; title: string; completed: boolean; completedOn?: string | null};
export function flattenCourseLessons(chapters: readonly {id: string; title: string; blocks: readonly {id: string; title: string; lessons: readonly {id: string; title: string; completed: boolean; completedOn?: string | null}[]}[]}[]): ProgressLesson[] {
  const seen = new Set<string>();
  const result: ProgressLesson[] = [];
  for (const chapter of chapters) for (const block of chapter.blocks) for (const lesson of block.lessons) {
    if (seen.has(lesson.id)) continue;
    seen.add(lesson.id);
    result.push({id: lesson.id, trackId: chapter.id, blockId: block.id, chapterTitle: chapter.title, blockTitle: block.title, title: lesson.title, completed: lesson.completed, completedOn: lesson.completedOn});
  }
  return result;
}
