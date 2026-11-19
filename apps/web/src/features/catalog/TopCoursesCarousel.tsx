import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { CatalogCourse } from "./catalog-data";
import "./top-courses-carousel.css";

type TopCoursesCarouselProps = {
  courses: readonly CatalogCourse[];
  selectionKey: string;
  renderCourse: (course: CatalogCourse, rank: number) => ReactNode;
};

export function TopCoursesCarousel({ courses, selectionKey, renderCourse }: TopCoursesCarouselProps) {
  const headingId = useId();
  const trackId = useId();
  const trackRef = useRef<HTMLOListElement>(null);
  const sectionRef = useRef<HTMLElement>(null);
  const targetRef = useRef(0);
  const directionRef = useRef<1 | -1>(1);
  const animationRef = useRef<number | null>(null);
  const [position, setPosition] = useState({ index: 0, max: 0 });
  const [paused, setPaused] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [visible, setVisible] = useState(!document.hidden);
  const [onScreen, setOnScreen] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const visibleCourses = courses.slice(0, 10);

  const geometry = useCallback(() => {
    const track = trackRef.current;
    const first = track?.children[0] as HTMLElement | undefined;
    const second = track?.children[1] as HTMLElement | undefined;
    if (!track || !first) return { stride: 0, max: 0 };
    const stride = second ? second.getBoundingClientRect().left - first.getBoundingClientRect().left : first.getBoundingClientRect().width;
    const max = stride > 0 ? Math.max(0, Math.round((track.scrollWidth - track.clientWidth) / stride)) : 0;
    return { stride, max };
  }, []);

  const cancelAnimation = useCallback((keepSnapSuppressed = false) => {
    if (animationRef.current !== null) cancelAnimationFrame(animationRef.current);
    animationRef.current = null;
    if (!keepSnapSuppressed) trackRef.current?.classList.remove("top-courses__track--moving");
  }, []);

  const syncPosition = useCallback(() => {
    const track = trackRef.current;
    if (!track || animationRef.current !== null) return;
    const { stride, max } = geometry();
    const index = stride ? Math.min(max, Math.max(0, Math.round(track.scrollLeft / stride))) : 0;
    targetRef.current = index;
    setPosition({ index, max });
  }, [geometry]);

  const goTo = useCallback((requested: number, animate: boolean) => {
    const track = trackRef.current;
    if (!track) return;
    const { stride, max } = geometry();
    const index = Math.min(max, Math.max(0, requested));
    const destination = Math.min(track.scrollWidth - track.clientWidth, index * stride);
    cancelAnimation(true);
    targetRef.current = index;
    setPosition({ index, max });
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!animate || reducedMotion || Math.abs(track.scrollLeft - destination) < 1) {
      track.scrollLeft = destination;
      track.classList.remove("top-courses__track--moving");
      return;
    }
    const start = track.scrollLeft;
    const started = performance.now();
    const durationToken = getComputedStyle(track).getPropertyValue("--motion-slow").trim();
    const parsedDuration = Number.parseFloat(durationToken);
    const duration = Number.isFinite(parsedDuration) && parsedDuration > 0
      ? parsedDuration * (durationToken.endsWith("ms") ? 1 : 1000)
      : 340;
    track.classList.add("top-courses__track--moving");
    const frame = (now: number) => {
      const progress = Math.min(1, (now - started) / duration);
      // Ease out, keeping each repeated action responsive to its latest target.
      track.scrollLeft = start + (destination - start) * (1 - Math.pow(1 - progress, 3));
      if (progress < 1) animationRef.current = requestAnimationFrame(frame);
      else {
        animationRef.current = null;
        track.classList.remove("top-courses__track--moving");
        syncPosition();
      }
    };
    animationRef.current = requestAnimationFrame(frame);
  }, [cancelAnimation, geometry, syncPosition]);

  useLayoutEffect(() => {
    targetRef.current = 0;
    directionRef.current = 1;
    goTo(0, false);
    return () => cancelAnimation();
  }, [selectionKey, courses.length, goTo, cancelAnimation]);

  useLayoutEffect(() => {
    const track = trackRef.current;
    if (!track) return;
    const observer = new ResizeObserver(() => goTo(targetRef.current, false));
    observer.observe(track);
    Array.from(track.children).forEach((child) => observer.observe(child));
    return () => observer.disconnect();
  }, [courses, goTo]);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const updateMotion = () => setReducedMotion(media.matches);
    const updateVisibility = () => setVisible(!document.hidden);
    media.addEventListener("change", updateMotion);
    document.addEventListener("visibilitychange", updateVisibility);
    return () => {
      media.removeEventListener("change", updateMotion);
      document.removeEventListener("visibilitychange", updateVisibility);
    };
  }, []);

  useEffect(() => {
    const section = sectionRef.current;
    if (!section) return;
    if (typeof IntersectionObserver === "undefined") {
      setOnScreen(true);
      return;
    }
    const observer = new IntersectionObserver(([entry]) => setOnScreen(entry.isIntersecting));
    observer.observe(section);
    return () => observer.disconnect();
  }, [visibleCourses.length > 0]);

  useEffect(() => {
    if (paused || hovered || focused || !visible || !onScreen || reducedMotion || position.max === 0) {
      cancelAnimation();
      syncPosition();
      return;
    }
    const timer = window.setInterval(() => {
      const max = geometry().max;
      if (!max) return;
      if (targetRef.current >= max) directionRef.current = -1;
      else if (targetRef.current <= 0) directionRef.current = 1;
      goTo(targetRef.current + directionRef.current, true);
    }, 6000);
    return () => window.clearInterval(timer);
  }, [selectionKey, paused, hovered, focused, visible, onScreen, reducedMotion, position.max, cancelAnimation, geometry, goTo, syncPosition]);

  const takeOver = () => {
    if (animationRef.current === null) return;
    cancelAnimation();
    syncPosition();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLOListElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      goTo(targetRef.current + (event.key === "ArrowLeft" ? -1 : 1), false);
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      goTo(event.key === "Home" ? 0 : geometry().max, false);
    }
  };

  if (!visibleCourses.length) return null;

  return (
    <section ref={sectionRef} className="top-courses" aria-labelledby={headingId} onMouseEnter={() => { takeOver(); setHovered(true); }} onMouseLeave={() => setHovered(false)} onFocusCapture={() => { takeOver(); setFocused(true); }} onBlurCapture={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false); }}>
      <div className="top-courses__header">
        <div>
          <span className="top-courses__eyebrow">Рекомендуем</span>
          <div className="top-courses__title-row">
            <h2 id={headingId}>Топ-10 курсов</h2>
            <span className="top-courses__badge">Демо-подборка</span>
          </div>
        </div>
        <div className="top-courses__controls">
          <button type="button" aria-label={reducedMotion ? "Автопрокрутка отключена настройкой уменьшения движения" : paused ? "Включить автопрокрутку" : "Остановить автопрокрутку"} aria-disabled={reducedMotion} onClick={() => { if (!reducedMotion) setPaused((value) => !value); }}><span aria-hidden="true">{paused || reducedMotion ? "▶" : "Ⅱ"}</span></button>
          <button type="button" aria-label="Предыдущие курсы" aria-controls={trackId} aria-disabled={position.index === 0} onClick={() => goTo(targetRef.current - 1, true)}><span aria-hidden="true">←</span></button>
          <button type="button" aria-label="Следующие курсы" aria-controls={trackId} aria-disabled={position.index >= position.max} onClick={() => goTo(targetRef.current + 1, true)}><span aria-hidden="true">→</span></button>
        </div>
      </div>
      <ol id={trackId} ref={trackRef} className="top-courses__track" tabIndex={0} aria-label="Рекомендуемые курсы по порядку" onScroll={syncPosition} onKeyDown={handleKeyDown} onWheel={takeOver} onTouchStart={takeOver} onPointerDown={takeOver} onFocus={(event) => { if (event.target.closest("a")) takeOver(); }}>
        {visibleCourses.map((course, index) => <li key={course.id}>{renderCourse(course, index + 1)}</li>)}
      </ol>
      {position.max > 0 && <div className="top-courses__progress" aria-hidden="true">{Array.from({ length: position.max + 1 }, (_, index) => <span key={index} className={index === position.index ? "is-active" : undefined} />)}</div>}
    </section>
  );
}
