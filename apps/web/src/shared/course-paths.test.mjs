import test from "node:test";
import assert from "node:assert/strict";
import { generateCourses } from "../../tools/course-registry.mjs";
generateCourses();
const { courseUrls, normalizedBase, resolveCourseRoute } = await import("./course-paths.ts");

for (const base of ["/", "/platform/"]) {
  test(`course URLs and all direct routes under ${base}`, () => {
    for (const course of ["backend", "backend_copy"]) {
      const urls = courseUrls(base, course);
      for (const [page, kind] of [["learning", "learning"], ["personal", "personal"], ["about", "about"]]) {
        assert.equal(urls[page], `${base}${course}${page === "learning" ? "" : `/${page}`}`);
        assert.deepEqual(resolveCourseRoute(urls[page], base), { kind, courseId: course, legacy: false });
        assert.deepEqual(resolveCourseRoute(`${urls[page]}/`, base), { kind, courseId: course, legacy: false });
      }
    }
    assert.deepEqual(resolveCourseRoute(base, base), { kind: "catalog" });
  });

  test(`old courses-prefixed URLs redirect to canonical routes under ${base}`, () => {
    assert.deepEqual(resolveCourseRoute(`${base}courses`, base), { kind: "redirect", to: base });
    for (const course of ["backend", "backend_copy"]) {
      for (const suffix of ["", "/personal", "/about", "/personal/privacy-policy"]) {
        assert.deepEqual(resolveCourseRoute(`${base}courses/${course}${suffix}`, base), {
          kind: "redirect", to: `${base}${course}${suffix}`,
        });
      }
    }
  });

  test(`legacy learning and personal aliases preserve their meaning under ${base}`, () => {
    assert.deepEqual(resolveCourseRoute(`${base}backend`, base), { kind: "learning", courseId: "backend", legacy: false });
    assert.deepEqual(resolveCourseRoute(`${base}backend/landing`, base), { kind: "personal", courseId: "backend", legacy: true });
    for (const section of ["reviews", "platform", "pricing", "personal-data", "privacy-policy"]) {
      for (const prefix of ["backend", "backend/landing"]) {
        assert.deepEqual(resolveCourseRoute(`${base}${prefix}/${section}`, base), { kind: "personal", courseId: "backend", legacy: true, section });
      }
    }
  });

  test(`invalid product and path never select backend under ${base}`, () => {
    for (const path of ["unknown", "__proto__", "backend%2fabout", "backend/unknown", "backend_copy/about/extra", "backend_copy?course_id=backend", "courses/unknown", "courses/__proto__", "courses/../backend", "courses/backend%2fabout", "courses/backend/unknown", "courses/backend_copy/about/extra", "courses//backend", "courses/backend?course_id=backend_copy"]) {
      assert.deepEqual(resolveCourseRoute(`${base}${path}`, base), { kind: "not-found" });
    }
  });
}

test("base normalization and prefix confinement", () => {
  assert.equal(normalizedBase("/platform"), "/platform/");
  assert.deepEqual(resolveCourseRoute("/platform2/courses/backend", "/platform/"), { kind: "not-found" });
  assert.deepEqual(resolveCourseRoute("/courses/backend", "/platform/"), { kind: "not-found" });
  for (const invalid of ["https://example.invalid", "//example.invalid", "/../", "/platform?x=1", "/platform%2f"]) {
    assert.throws(() => normalizedBase(invalid));
  }
  assert.throws(() => courseUrls("/", "unknown"));
});
