import type { CourseId } from "../course-runtime/course-registry";
import { courseIds, registeredCourses } from "../../.cache/course-registry/course-ids.ts";
import { catalogPreviewCourses, type CatalogPreviewSlug } from "./catalog-preview-courses.ts";

export type CourseRoute =
  | { kind: "learning"; courseId: CourseId; legacy: boolean }
  | { kind: "personal"; courseId: CourseId; legacy: boolean; section?: string }
  | { kind: "about"; courseId: CourseId; legacy: false }
  | { kind: "catalog" }
  | { kind: "catalog-preview"; slug: CatalogPreviewSlug }
  | { kind: "redirect"; to: string }
  | { kind: "not-found" };

export function isCourseId(value: string): value is CourseId {
  return courseIds.includes(value);
}

export function normalizedBase(base: string): string {
  if (!base.startsWith("/") || base.startsWith("//") || /[\\?#%]/.test(base)) {
    throw new Error("The platform base must be an absolute internal path");
  }
  const parts = base.split("/").filter(Boolean);
  if (parts.some((part) => part === "." || part === "..")) {
    throw new Error("The platform base contains an unsafe segment");
  }
  return parts.length ? `/${parts.join("/")}/` : "/";
}

export function courseUrls(base: string, courseId: CourseId) {
  if (!isCourseId(courseId)) throw new Error("Unknown course");
  const slug = registeredCourses.find((course) => course.id === courseId)!.slug;
  const prefix = `${normalizedBase(base)}${slug}`;
  return { learning: prefix, personal: `${prefix}/personal`, about: `${prefix}/about` };
}

const landingSections = new Set(["reviews", "platform", "pricing", "personal-data", "privacy-policy"]);

export function catalogPreviewUrl(base: string, slug: CatalogPreviewSlug) {
  return `${normalizedBase(base)}catalog/${slug}`;
}

export function resolveCourseRoute(pathname: string, base: string): CourseRoute {
  const root = normalizedBase(base);
  if (/[\\?#]/.test(pathname) || !pathname.startsWith(root)) return { kind: "not-found" };
  const relative = pathname.slice(root.length).replace(/\/+$/, "");
  if (relative === "") return { kind: "redirect", to: `${root}backend` };
  if (relative === "catalog") return { kind: "catalog" };
  const preview = catalogPreviewCourses.find(course => relative === `catalog/${course.slug}`);
  if (preview) return { kind: "catalog-preview", slug: preview.slug };
  if (relative === "courses") return { kind: "redirect", to: `${root}catalog` };
  if (isCourseId("backend") && relative === "backend/landing") return { kind: "personal", courseId: "backend", legacy: true };
  for (const section of landingSections) {
    if (isCourseId("backend") && (relative === `backend/${section}` || relative === `backend/landing/${section}`)) {
      return { kind: "personal", courseId: "backend", legacy: true, section };
    }
  }
  const segments = relative.split("/");
  const prefixed = segments[0] === "courses";
  const parts = prefixed ? segments.slice(1) : segments;
  const courseId = registeredCourses.find((course) => course.slug === parts[0])?.id;
  if (!courseId) return { kind: "not-found" };
  const urls = courseUrls(root, courseId);
  if (parts.length === 1) return prefixed
    ? { kind: "redirect", to: urls.learning }
    : { kind: "learning", courseId, legacy: false };
  if (parts.length === 2 && parts[1] === "about") return prefixed
    ? { kind: "redirect", to: urls.about }
    : { kind: "about", courseId, legacy: false };
  if (parts[1] === "personal") {
    if (parts.length === 2) return prefixed
      ? { kind: "redirect", to: urls.personal }
      : { kind: "personal", courseId, legacy: false };
    if (parts.length === 3 && landingSections.has(parts[2])) return prefixed
      ? { kind: "redirect", to: `${urls.personal}/${parts[2]}` }
      : { kind: "personal", courseId, legacy: false, section: parts[2] };
  }
  return { kind: "not-found" };
}
