export type PublishedCatalog = { version: string; lessons: ReadonlySet<string> };
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

/** Reject ambiguous or mismatched metadata instead of guessing from a content version. */
export function parsePublishedCatalog(value: unknown, courseId: string): PublishedCatalog | null {
  if (!value || typeof value !== 'object') return null;
  const data = value as Record<string, unknown>;
  if (data.course_id !== courseId || typeof data.catalog_version !== 'string' || !IDENTIFIER.test(data.catalog_version) ||
      !Array.isArray(data.lesson_ids) || data.lesson_ids.length > 10000 ||
      !data.lesson_ids.every(id => typeof id === 'string' && IDENTIFIER.test(id)) || new Set(data.lesson_ids).size !== data.lesson_ids.length) return null;
  return { version: data.catalog_version, lessons: new Set(data.lesson_ids as string[]) };
}
