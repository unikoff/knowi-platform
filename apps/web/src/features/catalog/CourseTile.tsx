import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { ArrowUpRight } from "lucide-react";
import { catalogGroups, courseDetails, fallbackCourseDetails, type CatalogCourse } from "./catalog-data";
import { useCoursePreview } from "./CoursePreviewProvider";
import "./course-preview.css";

const GAP = 8;
const MARGIN = 12;

export function CourseTile({ course, context = "catalog", rank }: { course: CatalogCourse; context?: "catalog" | "recommended"; rank?: number }) {
  const details = courseDetails.get(course.id) ?? fallbackCourseDetails;
  const Icon = details.Icon;
  const groupLabel = catalogGroups.find(item => item.id === course.group)?.label;
  const id = `${context}-${course.id}`;
  const previewId = useId();
  const anchor = useRef<HTMLAnchorElement>(null);
  const preview = useRef<HTMLDivElement>(null);
  const { active, request, hold, leave, remove } = useCoursePreview();
  const hovering = useRef(false);
  const focused = useRef(false);
  const touchFocus = useRef(false);
  const isOwner = active?.key === id;
  const isOpen = isOwner && active.phase === "open";
  const [position, setPosition] = useState({ left: -9999, top: -9999, side: "right" });

  useEffect(() => () => remove(id), [id, remove]);

  useLayoutEffect(() => {
    if (!isOwner) return;
    let frame = 0;
    const update = () => {
      const target = anchor.current;
      const panel = preview.current;
      if (!target || !panel) return;
      const rect = target.getBoundingClientRect();
      if (rect.bottom <= 0 || rect.top >= window.innerHeight || rect.right <= 0 || rect.left >= window.innerWidth) {
        remove(id);
        return;
      }
      const { width, height } = panel.getBoundingClientRect();
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      let left: number;
      let top: number;
      let side = "right";
      if (rect.right + GAP + width <= viewportWidth - MARGIN) {
        left = rect.right + GAP;
        top = rect.top;
      } else if (rect.left - GAP - width >= MARGIN) {
        side = "left";
        left = rect.left - GAP - width;
        top = rect.top;
      } else {
        left = rect.left;
        const below = rect.bottom + GAP + height <= viewportHeight - MARGIN;
        side = below ? "below" : "above";
        top = below ? rect.bottom + GAP : rect.top - GAP - height;
      }
      left = Math.max(MARGIN, Math.min(left, viewportWidth - width - MARGIN));
      top = Math.max(MARGIN, Math.min(top, viewportHeight - height - MARGIN));
      setPosition(previous => previous.left === left && previous.top === top && previous.side === side ? previous : { left, top, side });
    };
    const scheduleUpdate = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(update);
    };
    const observer = new ResizeObserver(scheduleUpdate);
    if (anchor.current) observer.observe(anchor.current);
    if (preview.current) observer.observe(preview.current);
    window.addEventListener("resize", scheduleUpdate);
    document.addEventListener("scroll", scheduleUpdate, true);
    update();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", scheduleUpdate);
      document.removeEventListener("scroll", scheduleUpdate, true);
    };
  }, [id, isOwner, remove]);

  return <>
    <a ref={anchor} className={`course-tile${rank !== undefined ? " course-tile--ranked" : ""}`} style={{ "--course-color": details.color } as CSSProperties} href={course.href} aria-labelledby={`${id}-title`} aria-describedby={`${id}-group${rank !== undefined ? ` ${id}-rank` : ""}${isOpen ? ` ${previewId}` : ""}`}
      onPointerEnter={event => { if (event.pointerType === "mouse") { hovering.current = true; request(id, false); } }}
      onPointerLeave={event => { if (event.pointerType === "mouse") { hovering.current = false; if (!focused.current) leave(id); } }}
      onPointerDown={event => { touchFocus.current = event.pointerType === "touch"; }}
      onFocus={() => { focused.current = true; if (!touchFocus.current) request(id, true); }}
      onBlur={() => { focused.current = false; touchFocus.current = false; if (!hovering.current) leave(id); }}>
      <div className="course-tile__cover" aria-hidden="true">
        {rank !== undefined && <span className={`course-tile__rank${rank > 9 ? " course-tile__rank--double" : ""}`}>{rank}</span>}
        <span className="course-tile__cover-icon"><Icon size={22} /></span>
        <span className="course-tile__code">{details.cover}{course.id === "backend" && <span>_</span>}</span>
      </div>
      <div className="course-tile__body">
        <span className="course-tile__group" id={`${id}-group`}>{groupLabel}</span>
        <div className="course-tile__title"><h3 id={`${id}-title`}>{course.title}</h3><ArrowUpRight size={18} aria-hidden="true" /></div>
        {rank !== undefined && <span className="course-library__visually-hidden" id={`${id}-rank`}>Место {rank} в демоподборке</span>}
        {details.tags.length > 0 && <ul className="course-tile__tags" aria-label="О курсе">{details.tags.map(tag => <li key={tag}>{tag}</li>)}</ul>}
      </div>
    </a>
    {isOwner && createPortal(<div ref={preview} id={previewId} role="tooltip" aria-hidden={!isOpen} className={`course-preview course-preview--${position.side}${isOpen ? "" : " course-preview--closing"}`} style={{ left: position.left, top: position.top }}
      onPointerEnter={event => { if (event.pointerType === "mouse") { hovering.current = true; hold(id); } }}
      onPointerLeave={event => { if (event.pointerType === "mouse") { hovering.current = false; if (!focused.current) leave(id); } }}>
      <strong>{course.title}</strong>
      <p>{course.id === "backend" ? "От основ Python до создания backend API: HTTP и FastAPI, базы данных, пользователи и доступ, Docker и развёртывание." : "Описание курса скоро появится."}</p>
    </div>, document.body)}
  </>;
}
