import type {CourseLessonTime, TimeMetric} from './course-statistics-model';

export function completeBlockLessonIds(block: {lesson_ids?: readonly string[]; total: number; lessons: readonly {id: string}[]}): readonly string[] | undefined {
  const ids = block.lesson_ids;
  if (!ids || ids.length < block.total || ids.some(id => !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(id))) return undefined;
  // Progress totals count only countable lessons. The canonical ID list includes the other lessons as well.
  const uniqueIds = new Set(ids);
  if (uniqueIds.size !== ids.length || block.lessons.some(lesson => !uniqueIds.has(lesson.id))) return undefined;
  return ids;
}

export function sumBlockTime(
  lessonIds: readonly string[],
  times: ReadonlyMap<string, CourseLessonTime>,
  metric: TimeMetric,
  expectedLessonCount?: number,
  allRowsLoaded = false,
): number | null {
  const uniqueIds = new Set(lessonIds);
  if ((uniqueIds.size === 0 && !allRowsLoaded) || (expectedLessonCount !== undefined && expectedLessonCount > uniqueIds.size)) return null;

  let total = 0;
  for (const id of uniqueIds) {
    const row = times.get(id);
    if (row === undefined && allRowsLoaded) continue;
    const value = row?.[metric];
    if (value == null || !Number.isFinite(value) || value < 0) return null;
    total += value;
  }
  return total;
}
