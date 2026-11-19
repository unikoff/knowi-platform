import type { Lesson } from '../learning/types';

export function activitySources(trackerFlag: boolean, cutoverFlag: boolean, catalogVersion: string | null, role: string | null) {
  if (!trackerFlag) return { tracker: false, legacy: true };
  const learner = role === 'user';
  const tracker = learner && typeof catalogVersion === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(catalogVersion);
  return { tracker, legacy: !cutoverFlag };
}

type CourseEntry = { lessons: Lesson[]; loading: boolean; error?: string };
type MaterialEntry = { lesson?: Lesson; loading: boolean; error?: string };
/** Match WorkspacePanels' rendering and access gates; loading/error/locked views are not study time. */
export function isTrackableLesson(target: { trackId: string; lessonId: string }, course: {
  tracksLoaded: boolean; tracksError?: string; courses: Record<string, CourseEntry>; materials: Record<string, MaterialEntry>;
}, user: { has_active_subscription?: boolean } | null) {
  if (!user || !course.tracksLoaded || course.tracksError) return false;
  const entry = course.courses[target.trackId], material = course.materials[target.lessonId];
  const summary = entry?.lessons.find(lesson => lesson.id === target.lessonId);
  if (!entry || entry.error || !summary || !material?.lesson || material.error || material.lesson.id !== target.lessonId) return false;
  const lesson = { ...material.lesson, ...summary };
  return lesson.is_available !== false && (lesson.access !== 'subscription' || user.has_active_subscription === true);
}
