import { useEffect, useState, type ComponentType } from "react";
import { courseUrls } from "../../shared/course-paths";
import { loadCourseLanding, type CourseId } from "../../course-runtime/course-registry";

export function CourseLandingRoute({ courseId, page, legacy = false }: { courseId: CourseId; page: "about" | "personal"; legacy?: boolean }) {
  const [loaded, setLoaded] = useState<{ id: string; page: string; Page?: ComponentType<{ courseId: string; legacy?: boolean }>; title?: string }>();
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setLoaded(undefined);
    setFailed(false);
    loadCourseLanding(courseId, page).then((module) => {
      if (!cancelled) setLoaded({ id: courseId, page, Page: module.Page, title: module.landingCourse?.title });
    }).catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [courseId, page]);
  const current = loaded?.id === courseId && loaded.page === page ? loaded : undefined;
  if (current?.Page) return <current.Page courseId={courseId} legacy={legacy} />;
  if (!current) return <main className="status-page"><section className="not-found-card" role={failed ? "alert" : "status"}>
    {failed ? "Не удалось загрузить страницу. Обновите страницу и повторите попытку." : "Загружаем страницу…"}
  </section></main>;
  if (page === "personal") return <main className="status-page"><section className="not-found-card" role="alert">Страница курса недоступна.</section></main>;
  return <main className="status-page"><section className="not-found-card" aria-labelledby="course-landing-title">
    <h1 id="course-landing-title">{current.title ?? "Курс"}</h1><p>Страница курса готовится.</p>
    <a className="status-home-link" href={courseUrls(import.meta.env.BASE_URL, courseId).learning}>Перейти к обучению</a>
  </section></main>;
}
