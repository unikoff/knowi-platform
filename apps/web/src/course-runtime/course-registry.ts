import type { ComponentType } from "react";
import { courseLoaders } from "../../.cache/course-registry/generated-loaders";
import { courseIds } from "../../.cache/course-registry/course-ids";

export type CourseId = string;
export type CourseLessonPages = Record<string, ComponentType<{ module?: string }>>;
export type MetadataResolver = (source: string, module?: string, title?: string) => { block: string; blockNumber: number; lessonNumber: string } | null;
export interface LoadedCoursePages { courseId: CourseId; richLessonPages: CourseLessonPages; getLessonMetadata?: MetadataResolver }
type LandingModule = { Page?: ComponentType<{ courseId: string; legacy?: boolean }>; landingCourse?: { title: string }; landingPersonal?: { courseId: string; title: string; kind: string } };
type Loaders = Record<string, { course: () => Promise<{ richLessonPages: CourseLessonPages; getLessonMetadata?: MetadataResolver }>; personal_landing: () => Promise<LandingModule>; course_landing: () => Promise<LandingModule> }>;
const loaders: Loaders = courseLoaders;
export function isRegisteredCourse(id: string): boolean { return courseIds.includes(id); }
function selected(id: string) {
  if (!isRegisteredCourse(id) || !Object.prototype.hasOwnProperty.call(loaders, id)) throw new Error(`Unknown course: ${id}`);
  return loaders[id];
}
export async function loadCoursePages(courseId: string): Promise<LoadedCoursePages> {
  const { richLessonPages, getLessonMetadata } = await selected(courseId).course();
  if (!richLessonPages || typeof richLessonPages !== "object" || Array.isArray(richLessonPages) || getLessonMetadata !== undefined && typeof getLessonMetadata !== "function") {
    throw new Error(`Invalid course frontend exports: ${courseId}`);
  }
  return { courseId, richLessonPages, getLessonMetadata };
}
export async function loadCourseLanding(courseId: string, page: "personal" | "about"): Promise<LandingModule> {
  const course = selected(courseId);
  const module = await (page === "personal" ? course.personal_landing() : course.course_landing());
  if (module.landingPersonal && module.landingPersonal.courseId !== courseId) throw new Error(`Landing course mismatch: ${courseId}`);
  return module;
}
