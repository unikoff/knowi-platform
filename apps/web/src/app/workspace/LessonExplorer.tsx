import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, ChevronRight, Lock, PanelLeftClose } from "lucide-react";
import { getLessonAccessLabel, isLessonUnavailable, type Lesson, type Track } from "../../features/learning";

function trackLabel(track: Track) {
  const match = track.title.match(/^(?:Глава\s*)?(\d+)\s*[:.·-]?\s*(.+)$/i);
  const chapterNumber = match?.[1] ?? track.id.match(/chapter[_-]?(\d+)/i)?.[1];
  const chapterTitle = match?.[2]?.trim() ?? track.title.trim();
  return { number: chapterNumber ? String(Number(chapterNumber)).padStart(2, "0") : "", title: chapterTitle };
}

export function LessonExplorer({
  tracks,
  activeTrackId,
  isTrackMenuOpen,
  progress,
  lessonModules,
  displayLessons,
  activeLessonId,
  openModules,
  isGuest,
  isAuthenticated,
  hasActiveSubscription,
  onToggleTrackMenu,
  onSelectTrack,
  onToggleModule,
  onSelectLesson,
  onRequireAuth,
  onCollapse,
}: {
  tracks: Track[];
  activeTrackId: string;
  isTrackMenuOpen: boolean;
  progress: number;
  lessonModules: string[];
  displayLessons: Lesson[];
  activeLessonId: string;
  openModules: Set<string>;
  isGuest: boolean;
  isAuthenticated: boolean;
  hasActiveSubscription: boolean;
  onToggleTrackMenu: () => void;
  onSelectTrack: (trackId: string) => void;
  onToggleModule: (moduleName: string) => void;
  onSelectLesson: (lesson: Lesson) => void;
  onRequireAuth: () => void;
  onCollapse: () => void;
}) {
  const trackStatusRef = useRef<HTMLDivElement>(null);
  const trackMenuRef = useRef<HTMLDivElement | null>(null);
  const [trackMenuStyle, setTrackMenuStyle] = useState<CSSProperties>();
  const [keepTrackMenu, setKeepTrackMenu] = useState(isTrackMenuOpen);
  const hasTrackMenuPosition = Boolean(trackMenuStyle);
  useEffect(() => {
    if (isTrackMenuOpen) { setKeepTrackMenu(true); return; }
    const timer = window.setTimeout(() => setKeepTrackMenu(false), 140);
    return () => window.clearTimeout(timer);
  }, [isTrackMenuOpen]);

  useEffect(() => {
    if (!isTrackMenuOpen || !hasTrackMenuPosition) return;
    const menu = trackMenuRef.current;
    (menu?.querySelector<HTMLButtonElement>("[aria-current='true']") ?? menu?.querySelector<HTMLButtonElement>("button"))?.focus();
  }, [isTrackMenuOpen, hasTrackMenuPosition]);

  useEffect(() => {
    if (!isTrackMenuOpen) return;
    let dismissed = false;
    const outside = (event: Event) => {
      const target = event.target as Node | null;
      if (target instanceof Element && target.closest(".explorer-resizer")) return;
      if (!dismissed && target && !trackMenuRef.current?.contains(target) && !trackStatusRef.current?.contains(target)) {
        dismissed = true;
        if (event.type === "pointerdown" && trackMenuRef.current?.contains(document.activeElement)) {
          trackStatusRef.current?.querySelector<HTMLButtonElement>(".track-switch")?.focus();
        }
        onToggleTrackMenu();
      }
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented || dismissed) return;
      event.preventDefault();
      event.stopPropagation();
      dismissed = true;
      onToggleTrackMenu();
      trackStatusRef.current?.querySelector<HTMLButtonElement>(".track-switch")?.focus();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("focusin", outside);
    document.addEventListener("keydown", escape, true);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("focusin", outside);
      document.removeEventListener("keydown", escape, true);
    };
  }, [isTrackMenuOpen, onToggleTrackMenu]);

  useLayoutEffect(() => {
    if (!isTrackMenuOpen) return;

    const updatePosition = () => {
      const anchor = trackStatusRef.current?.getBoundingClientRect();
      if (!anchor) return;

      const margin = 12;
      const width = Math.min(anchor.width - 24, window.innerWidth - margin * 2);
      const left = Math.min(Math.max(margin, anchor.left + 12), window.innerWidth - width - margin);
      const preferredTop = anchor.bottom + 8;
      const maxHeight = Math.min(672, Math.max(120, window.innerHeight - preferredTop - margin));
      const top = Math.min(preferredTop, window.innerHeight - maxHeight - margin);
      setTrackMenuStyle({ top, left, width, maxHeight });
    };

    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    const resizeObserver = new ResizeObserver(updatePosition);
    if (trackStatusRef.current) resizeObserver.observe(trackStatusRef.current);

    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
      resizeObserver.disconnect();
    };
  }, [isTrackMenuOpen]);

  return (
    <aside className="explorer-panel" aria-label="Занятия">
      <div className="track-status" ref={trackStatusRef}>
        <div className="track-status__header">
          <button
            className="track-switch"
            type="button"
            onClick={onToggleTrackMenu}
            aria-expanded={isTrackMenuOpen}
            aria-label="Выбрать главу"
            aria-haspopup="dialog"
            aria-controls="workspace-chapter-picker"
            title={tracks.find((track) => track.id === activeTrackId)?.title ?? "Курс"}
          >
            <span>{tracks.find((track) => track.id === activeTrackId)?.title ?? "Курс"}</span>
            <ChevronDown size={14} className={isTrackMenuOpen ? "is-flipped" : ""} />
          </button>
          <button className="icon-button" type="button" onClick={onCollapse} title="Свернуть список занятий" aria-label="Свернуть список занятий">
            <PanelLeftClose size={18} />
          </button>
        </div>
        <div className="track-status__progress">
          <div className="progress-line" role="progressbar" aria-label="Прогресс главы" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
            <i style={{ width: `${progress}%` }} />
          </div>
          <strong aria-hidden="true">{progress}%</strong>
        </div>
        {(isTrackMenuOpen || keepTrackMenu) && trackMenuStyle && createPortal(
          <div id="workspace-chapter-picker" ref={(element) => { trackMenuRef.current = element; if (element) element.inert = !isTrackMenuOpen; }}
            className={`track-menu track-menu--overlay${isTrackMenuOpen ? "" : " is-closing"}`} aria-hidden={!isTrackMenuOpen} style={trackMenuStyle} role="dialog" aria-label="Главы курса">
            <div className="track-menu__list">
              {tracks.map((track) => {
                const label = trackLabel(track);
                const trackProgress = track.lessons_countable
                  ? Math.round((track.lessons_completed / track.lessons_countable) * 100)
                  : 0;
                return (
                  <button
                    key={track.id}
                    type="button"
                    aria-current={track.id === activeTrackId ? "true" : undefined}
                    title={track.title}
                    className={track.id === activeTrackId ? "is-active" : ""}
                    onClick={() => {
                      onSelectTrack(track.id);
                      trackStatusRef.current?.querySelector<HTMLButtonElement>(".track-switch")?.focus();
                    }}
                  >
                    <span className="track-menu__number">{label.number}</span>
                    <strong>{label.title}</strong>
                    <span className="track-menu__progress">{trackProgress}%</span>
                  </button>
                );
              })}
            </div>
          </div>,
          document.body,
        )}
      </div>
      <nav className="lesson-tree" aria-label="Модули">
        {lessonModules.map((moduleName) => {
          const isOpen = openModules.has(moduleName);
          const moduleLessons = displayLessons.filter((lesson) => lesson.module === moduleName);

          return (
            <div className="module-group" key={moduleName}>
              <button className="module-toggle" type="button" aria-expanded={isOpen} title={moduleName} onClick={() => onToggleModule(moduleName)}>
                <ChevronRight size={16} />
                <span>{moduleName}</span>
              </button>
              <div className={`module-lessons ${isOpen ? "is-open" : ""}`} aria-hidden={!isOpen}><div className="module-lessons__inner">
                {moduleLessons.map((lesson) => {
                  const isUnavailable = isLessonUnavailable(lesson);
                  const accessLabel = getLessonAccessLabel(lesson, isAuthenticated, hasActiveSubscription);

                  return (
                    <button
                      className={`lesson-row ${lesson.id === activeLessonId ? "is-active" : ""} ${lesson.completed && !isUnavailable ? "is-completed" : ""} ${isUnavailable ? "is-locked" : ""}`}
                      type="button"
                      key={lesson.id}
                      aria-current={lesson.id === activeLessonId ? "true" : undefined}
                      aria-label={lesson.completed && !isUnavailable ? `${lesson.title}, завершено` : undefined}
                      title={lesson.locked_reason || lesson.title}
                      onClick={() => {
                        if (isUnavailable) {
                          if (isGuest) {
                            onRequireAuth();
                          }
                          return;
                        }
                        onSelectLesson(lesson);
                      }}
                    >
                      {isUnavailable ? <Lock size={15} aria-hidden="true" /> : lesson.completed ? <Check size={16} strokeWidth={2} className="lesson-done-icon" aria-hidden="true" /> : null}
                      <span>{lesson.title}</span>
                      {accessLabel && <small>{accessLabel}</small>}
                    </button>
                  );
                })}
              </div></div>
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
