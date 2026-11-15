import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ArrowLeft, ArrowRight, Moon, Sun } from "lucide-react";
import { RichLesson } from "../ui";
import { applyTheme, type ThemeId } from "@platform/shared/theme";
import type { CatalogExample } from "./catalog";
import { catalogGroups } from "./examples";
import "@platform/app/styles/tokens.css";
import "@platform/app/styles/base.css";
import "@platform/shared/ui/controls.css";
import "@platform/features/learning/theory/lesson-theory.css";
import "./demo.css";

const catalogMetadata = {
  components: catalogGroups.reduce((sum, group) => sum + group.examples.length, 0),
  variants: catalogGroups.reduce((sum, group) => sum + group.examples.reduce((total, item) => total + item.variants.length, 0), 0),
  tools: catalogGroups.flatMap(group => group.examples.map(item => ({ id: item.id, api: item.api, kind: item.kind, examples: item.variants.map(variant => variant.title) }))),
};

function ComponentPreview({ item }: { item: CatalogExample }) {
  type Slide = { index: number; key: number };
  const [slides, setSlides] = useState<{ current: Slide; previous: Slide | null; direction: 1 | -1 }>({ current: { index: 0, key: 0 }, previous: null, direction: 1 });
  const [height, setHeight] = useState<number>();
  const activeSlide = useRef<HTMLDivElement>(null);
  const id = useId();
  const variants = item.variants;
  const index = Math.min(slides.current.index, variants.length - 1);
  const variant = variants[index];

  useLayoutEffect(() => {
    const element = activeSlide.current;
    if (!element) return;
    const measure = () => setHeight(element.getBoundingClientRect().height);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [slides.current.key, index]);

  useEffect(() => {
    if (!slides.previous) return;
    const timer = window.setTimeout(() => setSlides(current => ({ ...current, previous: null })), 320);
    return () => window.clearTimeout(timer);
  }, [slides.current.key, slides.previous]);

  function navigate(target: number | ((current: number) => number), direction?: 1 | -1) {
    setSlides(previous => {
      const currentIndex = Math.min(previous.current.index, variants.length - 1);
      const requested = typeof target === "function" ? target(currentIndex) : target;
      const nextIndex = (requested % variants.length + variants.length) % variants.length;
      if (nextIndex === currentIndex) return previous;
      return {
        current: { index: nextIndex, key: previous.current.key + 1 },
        previous: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? null : previous.current,
        direction: direction ?? (nextIndex > currentIndex ? 1 : -1),
      };
    });
  }

  const visibleSlides = [...(slides.previous ? [slides.previous] : []), slides.current];
  return <article className="demo-component" id={`example-${item.id}`} aria-labelledby={`${id}-title`} data-component={item.api} data-kind={item.kind} data-variants={variants.length}>
    <header className="demo-component-heading">
      <h3 id={`${id}-title`} title={item.title}>{item.title}</h3>
      {variants.length > 1 && <div className="demo-carousel-navigation" role="group" aria-label={`Переключить пример: ${item.title}`} onKeyDown={event => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        const delta = event.key === "ArrowRight" ? 1 : -1;
        navigate(current => current + delta, delta);
      }}>
        <button className="demo-arrow" type="button" onClick={() => navigate(current => current - 1, -1)} aria-controls={`${id}-viewport`} aria-label={`Предыдущий пример: ${item.title}`}><ArrowLeft size={16} aria-hidden="true" /></button>
        <div className="demo-dots">
          {variants.map((example, position) => <button key={example.id} className="demo-dot" type="button" onClick={() => navigate(position)} aria-current={index === position ? "true" : undefined} aria-controls={`${id}-viewport`} aria-label={`Пример ${position + 1}: ${example.title}`} title={example.title} />)}
        </div>
        <button className="demo-arrow" type="button" onClick={() => navigate(current => current + 1, 1)} aria-controls={`${id}-viewport`} aria-label={`Следующий пример: ${item.title}`}><ArrowRight size={16} aria-hidden="true" /></button>
      </div>}
    </header>
    <div className="demo-viewport" id={`${id}-viewport`} role="group" aria-roledescription="карусель" aria-label={`Примеры: ${item.title}`} style={{ height }} data-direction={slides.direction}>
      {visibleSlides.map(slide => {
        const example = variants[Math.min(slide.index, variants.length - 1)];
        const isCurrent = slide.key === slides.current.key;
        return <div key={slide.key} ref={isCurrent ? activeSlide : null} className={`demo-slide${isCurrent ? slides.current.key > 0 ? " is-entering" : "" : " is-leaving"}`} aria-hidden={!isCurrent || undefined} {...(!isCurrent ? { inert: "" } : {})}>
          <div className="demo-preview" role="group" aria-roledescription="слайд" aria-label={`${slide.index + 1} из ${variants.length}: ${example.title}`} data-example={example.id}>
            <RichLesson className="demo-stack">{example.content}</RichLesson>
          </div>
        </div>;
      })}
    </div>
    <span className="demo-sr-only" role="status" aria-live="polite">{index + 1} из {variants.length}: {variant.title}</span>
  </article>;
}

function Demo() {
  const [category, setCategory] = useState("all");
  const [theme, setTheme] = useState<ThemeId>("dark");
  useEffect(() => { window.scrollTo(0, 0); }, [category]);
  useEffect(() => { applyTheme(theme); }, [theme]);
  const exampleCount = catalogGroups.reduce((sum, group) => sum + group.examples.length, 0);
  const selectedCategory = catalogGroups.some(group => group.id === category) ? category : "all";
  const visibleGroups = catalogGroups.filter(group => selectedCategory === "all" || group.id === selectedCategory);

  function toggleTheme() {
    setTheme(current => current === "dark" ? "light" : "dark");
  }
  return <div className="demo-page">
    <aside className="demo-sidebar">
      <nav aria-label="Разделы каталога">
        <button type="button" aria-pressed={selectedCategory === "all"} onClick={() => setCategory("all")}>Весь каталог<small>{exampleCount}</small></button>
        {catalogGroups.map(group => <button type="button" key={group.id} aria-pressed={selectedCategory === group.id} onClick={() => setCategory(group.id)}>{group.title}<small>{group.examples.length}</small></button>)}
      </nav>
      <button className="demo-theme" type="button" onClick={toggleTheme} aria-label={theme === "dark" ? "Включить светлую тему" : "Включить тёмную тему"}>
        {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
        {theme === "dark" ? "Светлая тема" : "Тёмная тема"}
      </button>
    </aside>
    <main className="demo-catalog" aria-label="Каталог компонентов">
      <h1 className="demo-sr-only">Каталог визуала и интерактива</h1>
      {visibleGroups.map(group => <section key={group.id} className="demo-examples" aria-label={group.title}>
        <h2 className="demo-sr-only">{group.title}</h2>
        {group.examples.map(item => <ComponentPreview item={item} key={item.id} />)}
      </section>)}
    </main>
    <script type="application/json" id="demo-catalog-data">{JSON.stringify(catalogMetadata)}</script>
  </div>;
}
declare global {
  interface Window { __lessonDemoRoot?: Root }
}
const root = window.__lessonDemoRoot ?? createRoot(document.getElementById("root")!);
window.__lessonDemoRoot = root;
root.render(<Demo />);
