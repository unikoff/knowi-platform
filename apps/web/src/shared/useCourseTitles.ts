import { useEffect, useState } from "react";
import { loadCourseLanding } from "../course-runtime/course-registry";
import { courseIds } from "../../.cache/course-registry/course-ids";
export function useCourseTitles() {
  const [titles, setTitles] = useState<Record<string, string>>({});
  useEffect(() => {
    let active = true;
    Promise.all(courseIds.map(async (id) => {
      try { return [id, (await loadCourseLanding(id, "about")).landingCourse?.title ?? id] as const; }
      catch { return [id, id] as const; }
    })).then((entries) => { if (active) setTitles(Object.fromEntries(entries)); });
    return () => { active = false; };
  }, []);
  return titles;
}
