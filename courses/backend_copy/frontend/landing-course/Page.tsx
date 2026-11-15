import { courseUrls } from "@platform/shared/course-paths";

export function Page({ courseId }: { courseId: string; legacy?: boolean }) {
  const learningUrl = courseUrls(import.meta.env.BASE_URL, courseId).learning;
  return <main className="status-page">
    <section className="not-found-card" aria-labelledby="course-about-title">
      <h1 id="course-about-title">Курс по Python · копия</h1>
      <p>Страница курса готовится.</p>
      <a className="status-home-link" href={learningUrl}>Перейти к обучению</a>
    </section>
  </main>;
}
