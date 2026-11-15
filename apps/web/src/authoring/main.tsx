import React, { Component, Suspense, lazy, useState, type ComponentType, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "../app/styles.css";
import "../features/learning/theory/lesson-theory.css";
import "./preview.css";
import { ThemeProvider } from "../shared/theme";
import { previewTarget } from "./preview-target";
import { LessonMetadataProvider } from "@course-ui";

type Page = ComponentType<{ module?: string }>;
import { previewCourses } from "../../.cache/course-registry/generated-preview";
import { isRegisteredCourse, loadCoursePages, type MetadataResolver } from "../course-runtime/course-registry";
const courses: Record<string, { prefix: string; modules: Record<string, () => Promise<Record<string, Page>>> }> = previewCourses;

class PreviewError extends Component<{ children: ReactNode }, { error: string | null }> {
  state: { error: string | null } = { error: null };
  static getDerivedStateFromError(error: unknown) {
    return { error: error instanceof Error ? error.message : String(error) };
  }
  render() {
    return this.state.error ? <p role="alert">Предпросмотр не загружен: {this.state.error}</p> : this.props.children;
  }
}

function resolvePage(target: string, courseId: string) {
  return lazy<Page>(async () => {
    if (target === "examples") {
      const { ReferenceLesson } = await import("./ReferenceLesson");
      return { default: ReferenceLesson };
    }
    const selected = courses[courseId];
    const load = selected?.modules[`${selected.prefix}${target}.tsx`];
    if (!load) throw new Error(`TSX не найден: ${target}.tsx`);
    const name = target.slice(target.lastIndexOf("/") + 1);
    const result = await load();
    if (!result[name]) throw new Error(`В файле нет экспорта ${name}`);
    return { default: result[name] };
  });
}

function Preview({ target, courseId }: { target: string; courseId: string }) {
  const previewUrl = `${import.meta.env.BASE_URL}authoring.html`;
  const [width, setWidth] = useState(1120);
  const [revision, setRevision] = useState(0);
  const [Page] = useState(() => resolvePage(target, courseId));
  const [resolveMetadata, setResolveMetadata] = useState<MetadataResolver>();
  React.useEffect(() => {
    let cancelled = false;
    loadCoursePages(courseId).then((course) => { if (!cancelled) setResolveMetadata(() => course.getLessonMetadata); }).catch(() => {});
    return () => { cancelled = true; };
  }, [courseId]);
  return (
    <>
      <header className="authoring-toolbar">
        <h1>Предпросмотр автора</h1>
        <form action={previewUrl}>
          <label htmlFor="authoring-lesson">TSX без расширения</label>
          <input type="hidden" name="course" value={courseId} />
          <input id="authoring-lesson" name="lesson" defaultValue={target} placeholder="chapter_01/block_01/Lesson01" />
          <button type="submit">Открыть</button>
        </form>
        <label htmlFor="authoring-width">Ширина контейнера</label>
        <select id="authoring-width" value={width} onChange={(event) => setWidth(Number(event.target.value))}>
          <option value={1120}>Широкий: 1120 px</option>
          <option value={720}>Разделённый: 720 px</option>
          <option value={360}>Узкий: 360 px</option>
        </select>
        <button type="button" onClick={() => setRevision((value) => value + 1)}>Сбросить пример</button>
        <a href={`${previewUrl}?course=${encodeURIComponent(courseId)}&lesson=examples`}>Образцы</a>
        <p>Теория и визуал из текущего TSX. Практика, API и итоговая приёмка проверяются на странице платформы.</p>
      </header>
      <main className="authoring-canvas" style={{ maxWidth: width }}>
        <div className="dock-lesson authoring-lesson" data-authoring-target={target}>
          <PreviewError key={`${target}:${revision}`}>
            <Suspense fallback={<p role="status">Загружаем занятие…</p>}>
              <LessonMetadataProvider source={target} resolveMetadata={resolveMetadata}>
                <Page />
              </LessonMetadataProvider>
            </Suspense>
          </PreviewError>
        </div>
      </main>
    </>
  );
}

const element = document.getElementById("root");
if (!element) throw new Error("Root element is missing");
try {
  const params = new URLSearchParams(window.location.search);
  const courseId = params.get("course") ?? "backend";
  if (!isRegisteredCourse(courseId)) throw new Error("Неизвестный курс");
  const target = previewTarget(window.location.origin, import.meta.env.DEV, params.get("lesson"));
  document.body.classList.add("authoring-page");
  createRoot(element).render(<ThemeProvider><Preview target={target} courseId={courseId} /></ThemeProvider>);
} catch (error) {
  element.textContent = error instanceof Error ? error.message : String(error);
}
