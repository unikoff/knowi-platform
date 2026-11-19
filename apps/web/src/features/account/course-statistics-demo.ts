import type { AccountStatisticsData } from './AccountStatistics';
import type { LearnerAnalyticsCatalog } from './LearnerAnalytics';
import { moscowToday, shiftDate, type CourseTimeSnapshot } from './course-statistics-model';

const exampleTitles = [
  ['Знакомство с Python', 'Переменные и значения', 'Числа и строки', 'Ввод и вывод', 'Условия', 'Циклы'],
  ['Списки', 'Словари', 'Функции', 'Аргументы функций', 'Работа с файлами', 'Обработка ошибок'],
];

function hash(value: string): number {
  let result = 2166136261;
  for (let i = 0; i < value.length; i++) result = Math.imul(result ^ value.charCodeAt(i), 16777619);
  return result >>> 0;
}

/** Creates fictional progress and time without reading actual learner measurements. */
export function createCourseStatisticsDemo(courseId: string, dates: string[], sourceData?: AccountStatisticsData, sourceCatalog?: LearnerAnalyticsCatalog, today = moscowToday()): {
  data: AccountStatisticsData;
  catalog: LearnerAnalyticsCatalog;
  snapshot: CourseTimeSnapshot;
  progressSnapshot: CourseTimeSnapshot;
  completedLessonsInPeriod: number;
} {
  const dataCatalog: LearnerAnalyticsCatalog = (sourceData?.courses ?? []).map(chapter => ({id: chapter.id, title: chapter.title, lessons: chapter.blocks.flatMap(block => block.lessons.map(lesson => ({id: lesson.id, title: lesson.title})))}));
  const inputCatalog = sourceCatalog?.some(chapter => chapter.lessons.length > 0) ? sourceCatalog
    : dataCatalog.some(chapter => chapter.lessons.length > 0) ? dataCatalog
    : exampleTitles.map((titles, chapter) => ({id: `demo-chapter-${chapter + 1}`, title: chapter ? 'Коллекции и функции' : 'Основы Python', lessons: titles.map((title, index) => ({id: `demo-lesson-${chapter + 1}-${index + 1}`, title}))}));
  const seen = new Set<string>();
  const catalog: LearnerAnalyticsCatalog = inputCatalog.map(chapter => ({id: chapter.id, title: chapter.title, lessons: chapter.lessons.flatMap(lesson => {
    if (seen.has(lesson.id)) return [];
    seen.add(lesson.id);
    return [{id: lesson.id, title: lesson.title}];
  })}));

  const uniqueIds = [...new Set(catalog.flatMap(chapter => chapter.lessons.map(lesson => lesson.id)))];
  const completedIds = new Set(uniqueIds.slice(0, Math.ceil(uniqueIds.length * 0.6)));
  const completionDates = new Map([...completedIds].map((lessonId, index) => {
    const daysAgo = index === 0 ? 0 : hash(`${courseId}:${lessonId}:completion`) % 120;
    return [lessonId, shiftDate(today, -daysAgo)];
  }));
  const selectedDates = new Set(dates);
  const completedLessonsInPeriod = [...completionDates.values()].reduce((count, date) => count + Number(selectedDates.has(date)), 0);
  const courses = catalog.map(chapter => {
    const sourceChapter = sourceData?.courses.find(item => item.id === chapter.id);
    const available = new Map(chapter.lessons.map(lesson => [lesson.id, lesson]));
    const assigned = new Set<string>();
    const blocks = (sourceChapter?.blocks ?? []).map(block => {
      const lessons = block.lessons.flatMap(lesson => {
        const publicLesson = available.get(lesson.id);
        if (!publicLesson || assigned.has(lesson.id)) return [];
        assigned.add(lesson.id);
        return [{...publicLesson, completed: completedIds.has(lesson.id), completedOn: completionDates.get(lesson.id) ?? null}];
      });
      return {id: block.id, title: block.title, completed: lessons.filter(lesson => lesson.completed).length, total: lessons.length, lessons};
    }).filter(block => block.total > 0);
    const remaining = chapter.lessons.filter(lesson => !assigned.has(lesson.id)).map(lesson => ({...lesson, completed: completedIds.has(lesson.id), completedOn: completionDates.get(lesson.id) ?? null}));
    if (remaining.length) blocks.push({id: `demo-block-${chapter.id}`, title: 'Занятия', completed: remaining.filter(lesson => lesson.completed).length, total: remaining.length, lessons: remaining});
    return {id: chapter.id, title: chapter.title, completed: blocks.reduce((sum, block) => sum + block.completed, 0), total: blocks.reduce((sum, block) => sum + block.total, 0), blocks};
  });
  const totalLessons = courses.reduce((sum, chapter) => sum + chapter.total, 0);
  const completedLessons = courses.reduce((sum, chapter) => sum + chapter.completed, 0);
  const historyStart = shiftDate(today, -119);
  const dayValues = (date: string) => {
    const seed = hash(`${courseId}:${date}`);
    const rest = date < historyStart || date > today || seed % 5 === 0;
    const theoryMs = rest ? 0 : (10 + seed % 31) * 60000;
    const practiceMs = rest ? 0 : (8 + (seed >>> 8) % 42) * 60000;
    return {seed, date, activeMs: theoryMs + practiceMs, theoryMs, practiceMs};
  };
  const lessonTimes = new Map(uniqueIds.map(lessonId => [lessonId, {lessonId, activeMs: 0, theoryMs: 0, practiceMs: 0}]));
  const days = dates.map(date => {
    const {seed, activeMs, theoryMs, practiceMs} = dayValues(date);
    const lesson = lessonTimes.get(uniqueIds[seed % uniqueIds.length]);
    if (lesson) { lesson.theoryMs += theoryMs; lesson.practiceMs += practiceMs; lesson.activeMs += activeMs; }
    return {date, activeMs, theoryMs, practiceMs};
  });
  const totals = days.reduce((sum, day) => ({activeMs: sum.activeMs + day.activeMs, theoryMs: sum.theoryMs + day.theoryMs, practiceMs: sum.practiceMs + day.practiceMs}), {activeMs: 0, theoryMs: 0, practiceMs: 0});
  const to = dates[dates.length - 1] ?? '';
  const snapshot: CourseTimeSnapshot = {courseId, from: dates[0] ?? '', to, quality: 'complete', asOf: to ? `${to}T23:59:59+03:00` : null, totals, days, lessons: [...lessonTimes.values()]};
  const lifetimeTimes = new Map(uniqueIds.map(lessonId => [lessonId, {lessonId, activeMs: 0, theoryMs: 0, practiceMs: 0}]));
  for (let offset = 0; offset < 120; offset++) {
    const {seed, activeMs, theoryMs, practiceMs} = dayValues(shiftDate(historyStart, offset));
    const lesson = lifetimeTimes.get(uniqueIds[seed % uniqueIds.length]);
    if (lesson) { lesson.activeMs += activeMs; lesson.theoryMs += theoryMs; lesson.practiceMs += practiceMs; }
  }
  const progressSnapshot: CourseTimeSnapshot = {...snapshot, lessons: [...lifetimeTimes.values()]};
  const data: AccountStatisticsData = {tracksLoaded: true, completedLessons, totalLessons, overallProgress: totalLessons ? Math.round(completedLessons / totalLessons * 100) : 0, activeDays: null, currentStreak: null, weeklyLessonsCompleted: null, courses};
  return {data, catalog, snapshot, progressSnapshot, completedLessonsInPeriod};
}
