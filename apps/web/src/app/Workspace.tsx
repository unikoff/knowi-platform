import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { useRecordActivityVisit } from "../features/account";
import { useAnalyticsTrackerIdentity } from "../features/analytics-tracker/useAnalyticsTracker";
import { canShowPersonalMeasurements } from "../features/account/course-statistics-analytics";
import { usePublishedCatalog } from "../features/analytics-tracker/usePublishedCatalog";
import { activitySources, isTrackableLesson } from "../features/analytics-tracker/integration";
import type { ToolId } from "../features/analytics-tracker/tracker";
import { logoutUser } from "../features/auth";
import { PracticeSessionProvider, useLearningCourse, type Lesson } from "../features/learning";
import type { UserProfile, CalendarMonth } from "../shared/api/contracts";
import { moscowCalendarMonth } from "../shared/lib/moscow-month";
import { WorkspaceActivityBar } from "./workspace/WorkspaceActivityBar";
import { WorkspaceWorkbench, type ActiveWorkspacePanel, type WorkspaceCommands } from "./workspace/WorkspaceWorkbench";
import { LessonsDrawerContent, WorkspacePanelContext } from "./workspace/WorkspacePanels";
import { useContentSwitch } from "../shared/ui/motion/useContentSwitch";
import type { LessonPanelKind, LessonTarget } from "./workspace/layout";
import type { CourseId } from "../course-runtime/course-registry";

const EXPLORER_MIN_WIDTH = 220;
const EXPLORER_MAX_WIDTH = 480;
const EXPLORER_DEFAULT_WIDTH = 320;

function getInitialExplorerWidth(storageKey: string, legacyKey?: string) {
  try {
    const savedWidth = localStorage.getItem(storageKey) ?? (legacyKey ? localStorage.getItem(legacyKey) : null);
    if (savedWidth !== null && savedWidth.trim() !== "") {
      const width = Number(savedWidth);
      if (Number.isFinite(width)) {
        try { localStorage.setItem(storageKey, savedWidth); } catch { /* Keep the readable legacy value. */ }
        return Math.min(EXPLORER_MAX_WIDTH, Math.max(EXPLORER_MIN_WIDTH, width));
      }
    }
  } catch { /* Storage is optional. */ }
  return EXPLORER_DEFAULT_WIDTH;
}

export function Workspace({ user, courseId, onUserChange, onLogout, onRequireAuth }: {
  user: UserProfile | null;
  courseId: CourseId;
  onUserChange: (user: UserProfile) => void;
  onLogout: () => void;
  onRequireAuth: () => void;
}) {
  const userKey = user ? String(user.id) : "guest";
  const ownerKey = `${userKey}:${courseId}`;
  const canManageCourse = Boolean((user?.role === undefined && user?.is_admin) || (user?.capabilities?.includes("course.read") && (user.role === "admin" || (user.role === "leader" && user.course_id === courseId))));
  const course = useLearningCourse(user, courseId);
  const [calendarMonth, setCalendarMonth] = useState<CalendarMonth>(moscowCalendarMonth);
  const trackerFlag = import.meta.env.VITE_ANALYTICS_TRACKER_ENABLED === "true";
  const windowsEnabled = import.meta.env.VITE_ANALYTICS_WINDOWS_ENABLED === "true";
  const toolsEnabled = import.meta.env.VITE_ANALYTICS_TOOLS_ENABLED === "true";
  const cutoverFlag = import.meta.env.VITE_ANALYTICS_LEGACY_VISIT_CUTOVER === "true";
  const analyticsBase = import.meta.env.BASE_URL.replace(/\/$/, "");
  const publishedCatalog = usePublishedCatalog((trackerFlag || windowsEnabled) && user?.role === "user", user ? userKey : null, courseId, analyticsBase, cutoverFlag ? "cutover" : "parallel");
  const sources = activitySources(trackerFlag, cutoverFlag, publishedCatalog?.version ?? null, user?.role ?? null);
  useRecordActivityVisit(sources.legacy ? user?.id ?? null : null);
  const canTrackLesson = useCallback((target: LessonTarget) => Boolean(publishedCatalog?.lessons.has(target.lessonId) && isTrackableLesson(target, course, user)),
    [publishedCatalog, course.tracksLoaded, course.tracksError, course.courses, course.materials, user?.id, user?.has_active_subscription]);
  const analytics = useAnalyticsTrackerIdentity({ enabled: sources.tracker || toolsEnabled, windowsEnabled, toolsEnabled, identityKey: user ? userKey : null, role: user?.role ?? null,
    subjectRef: user?.account_ref ?? null,
    courseId, catalogVersion: sources.tracker || windowsEnabled ? publishedCatalog?.version ?? null : null, canTrackLesson, baseUrl: analyticsBase });
  const [compact, setCompact] = useState(() => window.matchMedia("(max-width: 900px)").matches);
  const [activePanel, setActivePanel] = useState<ActiveWorkspacePanel>();
  const [accountTool, setAccountTool] = useState<ToolId>("account-statistics");
  const [explorerActive, setExplorerActive] = useState(false);
  const previousActivePanel = useRef<string | undefined>();
  const [lessonTarget, setLessonTarget] = useState<LessonTarget>();
  const [isExplorerOpen, setIsExplorerOpen] = useState(!compact);
  const [lessonMenuPeek, setLessonMenuPeek] = useState<"closed" | "open" | "closing">("closed");
  const explorerWidthKey = `pymentor:explorer-width:${ownerKey}`;
  const [explorerWidth, setExplorerWidth] = useState(() => getInitialExplorerWidth(explorerWidthKey,
    courseId === "backend" ? "pymentor-explorer-width" : undefined));
  const [isResizingExplorer, setIsResizingExplorer] = useState(false);
  const lessonMenuTimers = useRef<number[]>([]);
  const commands = useRef<WorkspaceCommands | null>(null);
  const workspaceRef = useRef<HTMLElement>(null);
  const switchContent = useContentSwitch();
  const clearLessonMenuTimers = useCallback(() => {
    lessonMenuTimers.current.forEach((timer) => window.clearTimeout(timer));
    lessonMenuTimers.current = [];
  }, []);
  const firstAvailable = course.displayLessons.find((lesson) => lesson.is_available !== false);
  const initialLesson = useMemo(() => firstAvailable ? { trackId: course.activeTrackId, lessonId: firstAvailable.id } : undefined,
    [course.activeTrackId, firstAvailable?.id]);
  const entry = course.courses[course.activeTrackId];
  const ready = Boolean(course.tracksError || (course.tracksLoaded && (!course.activeTrackId || (entry && !entry.loading))));
  const workspaceStyle = { "--explorer": `${explorerWidth}px` } as CSSProperties;
  const isDrawerVisible = isExplorerOpen || lessonMenuPeek === "open";
  const { onActiveTool } = analytics;
  const activeTool = explorerActive && isDrawerVisible ? "lessons" : activePanel?.id === "account" ? accountTool : undefined;
  useEffect(() => { onActiveTool(activeTool); }, [onActiveTool, activeTool]);

  useEffect(() => {
    try { localStorage.setItem(explorerWidthKey, String(explorerWidth)); }
    catch { /* Storage is optional. */ }
  }, [explorerWidth, explorerWidthKey]);

  useEffect(() => {
    if (!isResizingExplorer) return;
    const move = (event: globalThis.PointerEvent) => {
      const activityWidth = document.querySelector(".activity-bar")?.getBoundingClientRect().width ?? 54;
      setExplorerWidth(Math.min(EXPLORER_MAX_WIDTH, Math.max(EXPLORER_MIN_WIDTH, event.clientX - activityWidth)));
    };
    const finish = () => setIsResizingExplorer(false);
    document.body.classList.add("is-resizing-panel");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish, { once: true });
    return () => {
      document.body.classList.remove("is-resizing-panel");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
    };
  }, [isResizingExplorer]);

  useEffect(() => clearLessonMenuTimers, [clearLessonMenuTimers]);
  useEffect(() => {
    if (!compact) return;
    clearLessonMenuTimers();
    setLessonMenuPeek("closed");
  }, [clearLessonMenuTimers, compact]);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 900px)");
    const update = () => setCompact(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  const onActivePanel = useCallback((panel?: ActiveWorkspacePanel) => {
    const previous = previousActivePanel.current;
    if (previous !== panel?.id) {
      setExplorerActive(false);
      if (previous === "account") window.dispatchEvent(new Event("pymentor:personal-analytics-closed"));
      if (panel?.id === "account") window.dispatchEvent(new Event("pymentor:personal-analytics-opened"));
      previousActivePanel.current = panel?.id;
    }
    setActivePanel(panel);
  }, []);
  const onLessonChange = useCallback((target?: LessonTarget) => {
    setLessonTarget(target);
    if (target && compact) setIsExplorerOpen(false);
  }, [compact]);
  const previewLessonsOnHover = useCallback((pointerType: string) => {
    if (pointerType !== "mouse" || compact || isExplorerOpen) return;
    clearLessonMenuTimers();
    setLessonMenuPeek("open");
  }, [clearLessonMenuTimers, compact, isExplorerOpen]);
  const closeLessonPreview = useCallback((pointerType: string) => {
    if (pointerType !== "mouse" || compact || isExplorerOpen) return;
    clearLessonMenuTimers();
    lessonMenuTimers.current.push(window.setTimeout(() => {
      setLessonMenuPeek("closing");
      setExplorerActive(false);
      lessonMenuTimers.current.push(window.setTimeout(() => {
        setLessonMenuPeek("closed");
        lessonMenuTimers.current = [];
      }, 370));
    }, 110));
  }, [clearLessonMenuTimers, compact, isExplorerOpen]);
  const toggleLessons = useCallback(() => {
    clearLessonMenuTimers();
    setLessonMenuPeek("closed");
    setIsExplorerOpen(!isExplorerOpen);
    setExplorerActive(!isExplorerOpen);
  }, [clearLessonMenuTimers, isExplorerOpen]);
  const closeLessons = useCallback(() => {
    const drawer = workspaceRef.current?.querySelector("#workspace-lessons-drawer");
    if (drawer?.contains(document.activeElement)) {
      workspaceRef.current?.querySelector<HTMLButtonElement>(".activity-button--lessons")?.focus();
    }
    clearLessonMenuTimers();
    setLessonMenuPeek("closed");
    setIsExplorerOpen(false);
    setExplorerActive(false);
  }, [clearLessonMenuTimers]);
  const openLesson = useCallback((kind: LessonPanelKind, target: LessonTarget) => {
    const changingLesson = lessonTarget?.trackId !== target.trackId || lessonTarget?.lessonId !== target.lessonId;
    const current = changingLesson
      ? Array.from(workspaceRef.current?.querySelectorAll<HTMLElement>(".dock-lesson:not(.content-switch-snapshot)") ?? [])
        .filter((panel) => panel.getClientRects().length > 0 && getComputedStyle(panel).visibility === "visible")
      : null;
    switchContent("lesson", current ?? null, () => commands.current?.openLesson(kind, target));
  }, [lessonTarget, switchContent]);
  function onSelectLesson(lesson: Lesson) {
    openLesson("theory", { trackId: course.activeTrackId, lessonId: lesson.id });
  }
  function openStudy(kind: LessonPanelKind) {
    const target = lessonTarget ?? initialLesson;
    if (target) openLesson(kind, target);
    else setIsExplorerOpen(true);
  }
  function openAccount() {
    if (!user) { onRequireAuth(); return; }
    commands.current?.openAccount();
  }
  async function handleLogout() {
    analytics.stopBeforeLogout();
    try { await logoutUser(); onLogout(); }
    catch (error) { analytics.restartAfterFailedLogout(); window.alert(error instanceof Error ? error.message : "Не удалось выйти из аккаунта"); }
  }
  return <PracticeSessionProvider>
    <WorkspacePanelContext.Provider value={{ user, courseId, ownerKey, onUserChange, course, calendarMonth, setCalendarMonth,
      selectedLessonId: lessonTarget?.trackId === course.activeTrackId ? lessonTarget.lessonId : "", onSelectLesson, openLesson, onRequireAuth, onLogout: handleLogout,
      onCloseLessons: closeLessons, onAccountToolChange: setAccountTool }}>
      <main ref={workspaceRef} className={`workspace workspace--dock ${compact ? "workspace--compact" : ""} ${isExplorerOpen ? "workspace--explorer-open" : "workspace--explorer-closed"}`} style={workspaceStyle}>
        <WorkspaceActivityBar key={userKey} ownerKey={userKey} compact={compact} isExplorerOpen={isDrawerVisible} isAdmin={canManageCourse} isPlatformAdmin={Boolean(user?.role === "admin" && user.capabilities?.includes("platform.read"))}
          isAdminOpen={activePanel?.id === "admin"} isAccountOpen={activePanel?.id === "account"} isInterpreterOpen={activePanel?.id === "interpreter"} activeView={activePanel?.kind}
          onOpenLessons={toggleLessons} onLessonsPointerEnter={previewLessonsOnHover} onLessonsPointerLeave={closeLessonPreview}
          onOpenTheory={() => openStudy("theory")} onOpenPractice={() => openStudy("practice")}
          onOpenAdmin={() => commands.current?.openAdmin()} onOpenAccount={openAccount} onOpenInterpreter={() => commands.current?.openInterpreter()} />
        <aside
          id="workspace-lessons-drawer"
          className={`workspace-drawer${lessonMenuPeek !== "closed" && !isExplorerOpen ? ` workspace-drawer--peek workspace-drawer--peek-${lessonMenuPeek}` : ""}`}
          ref={(element) => { if (element) element.inert = !isDrawerVisible; }}
          aria-hidden={!isDrawerVisible}
          aria-label="Список занятий"
          onFocusCapture={() => setExplorerActive(true)}
          onPointerDownCapture={() => setExplorerActive(true)}
          onKeyDown={(event) => {
            if (event.key === "Escape" && !event.defaultPrevented) {
              event.preventDefault();
              closeLessons();
            }
          }}
          onPointerEnter={(event) => previewLessonsOnHover(event.pointerType)}
          onPointerLeave={(event) => closeLessonPreview(event.pointerType)}>
          <LessonsDrawerContent visible={isDrawerVisible} />
        </aside>
        {isExplorerOpen && !compact && <button className="explorer-resizer" type="button" role="separator"
          aria-label="Ширина списка занятий" aria-controls="workspace-lessons-drawer" aria-orientation="vertical" aria-valuemin={EXPLORER_MIN_WIDTH}
          aria-valuemax={EXPLORER_MAX_WIDTH} aria-valuenow={explorerWidth} title="Изменить ширину: стрелки влево и вправо"
          onPointerDown={(event: PointerEvent<HTMLButtonElement>) => { event.preventDefault(); setIsResizingExplorer(true); }}
          onKeyDown={(event) => {
            if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
              event.preventDefault();
              const delta = event.key === "ArrowLeft" ? -16 : 16;
              setExplorerWidth((width) => Math.min(EXPLORER_MAX_WIDTH, Math.max(EXPLORER_MIN_WIDTH, width + delta)));
            } else if (event.key === "Home" || event.key === "End") {
              event.preventDefault();
              setExplorerWidth(event.key === "Home" ? EXPLORER_MIN_WIDTH : EXPLORER_MAX_WIDTH);
            }
          }} />}
        <div className="workspace-body" onFocusCapture={() => setExplorerActive(false)} onPointerDownCapture={() => setExplorerActive(false)}>
          <WorkspaceWorkbench key={compact ? "mobile" : "desktop"} ownerKey={userKey} courseId={courseId} compact={compact}
            isAuthenticated={Boolean(user)} metricsIdentity={user?.account_ref && typeof user.auth_revision === "number" ? { id: user.id, accountRef: user.account_ref, authRevision: user.auth_revision } : null}
            metricsEnabled={canShowPersonalMeasurements(import.meta.env.VITE_ANALYTICS_REPORTS_ENABLED, import.meta.env.VITE_ANALYTICS_PERSONAL_MEASUREMENTS_ENABLED, user?.role)} isAdmin={canManageCourse} ready={ready} initialLesson={initialLesson}
            selectedLesson={lessonTarget} commands={commands} onActivePanel={onActivePanel} onTrackerPanel={analytics.onActivePanel}
            onLessonChange={onLessonChange} onOpenLessons={() => setIsExplorerOpen(true)} />
        </div>
      </main>
    </WorkspacePanelContext.Provider>
  </PracticeSessionProvider>;
}
