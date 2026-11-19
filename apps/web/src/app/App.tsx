import { lazy, useEffect, useState, type ReactNode } from "react";
import { AuthScreen } from "../features/auth";
import { useAuthSession } from "../features/auth/useAuthSession";
import { useAnalyticsTrackerIdentity } from "../features/analytics-tracker/useAnalyticsTracker";
import type { ToolId } from "../features/analytics-tracker/tracker";
import { AsyncBoundary } from "../shared/ui/async-boundary/AsyncBoundary";
import { platformUrl } from "../shared/paths";
import { normalizedBase, resolveCourseRoute } from "../shared/course-paths";
import type { CourseId } from "../course-runtime/course-registry";
import { NotFoundPage } from "./NotFoundPage";
import { StartupScreen } from "./StartupScreen";
import { EmailActionScreen } from "../features/auth/EmailActionScreen";
import { clearEmailLink, readEmailLink } from "../features/auth/email-link";
import "./status-pages.css";

const PlatformAdminEntry = lazy(() => import("../features/admin/PlatformAdmin").then(({ PlatformAdminEntry }) => ({ default: PlatformAdminEntry })));
const Workspace = lazy(() => import("./Workspace").then(({ Workspace }) => ({ default: Workspace })));
const CourseCatalog = lazy(() => import("../features/catalog").then(({ CourseCatalog }) => ({ default: CourseCatalog })));
const CatalogCoursePlaceholder = lazy(() => import("../features/catalog").then(({ CatalogCoursePlaceholder }) => ({ default: CatalogCoursePlaceholder })));
const CourseLandingRoute = lazy(() => import("../features/landing/CourseLandingRoute").then(({ CourseLandingRoute }) => ({ default: CourseLandingRoute })));

function normalizePath(pathname: string) {
  const normalized = pathname.replace(/\/+$/, "");
  return normalized || "/";
}

const appPath = normalizePath(new URL(platformUrl, window.location.origin).pathname);

export function App() {
  const pathname = window.location.pathname;
  const route = resolveCourseRoute(pathname, import.meta.env.BASE_URL);

  if (import.meta.env.DEV && normalizePath(pathname) === `${appPath}/preview-loading`) {
    return <StartupScreen />;
  }

  if (normalizePath(pathname) === `${normalizedBase(import.meta.env.BASE_URL)}admin-platform`) return <AsyncBoundary fallback={<StartupScreen />}><PlatformAdminEntry /></AsyncBoundary>;

  if (route.kind === "redirect") return <CourseRedirect to={route.to} />;

  if (route.kind === "catalog") return <PlatformToolRoute tool="catalog"><CourseCatalog /></PlatformToolRoute>;
  if (route.kind === "catalog-preview") return <PlatformToolRoute tool="catalog-preview"><CatalogCoursePlaceholder slug={route.slug} /></PlatformToolRoute>;
  if (route.kind === "about" || route.kind === "personal") return <PlatformToolRoute tool={route.kind === "about" ? "course-about" : "course-personal"} courseId={route.courseId}>
    <CourseLandingRoute courseId={route.courseId} page={route.kind} legacy={route.legacy} />
  </PlatformToolRoute>;
  if (route.kind !== "learning") return <NotFoundPage />;

  return <PlatformEntry courseId={route.courseId} />;
}

function PlatformToolRoute({ children, tool, courseId = "platform-tools" }: {
  children: ReactNode;
  tool: ToolId;
  courseId?: string;
}) {
  return <AsyncBoundary fallback={<StartupScreen />}>
    {import.meta.env.VITE_ANALYTICS_TOOLS_ENABLED === "true" && <PlatformToolTracking tool={tool} courseId={courseId} />}
    {children}
  </AsyncBoundary>;
}

const noLessonOnPlatformPage = () => false;

function PlatformToolTracking({ tool, courseId }: { tool: ToolId; courseId: string }) {
  const { user, initialized, checking, revision } = useAuthSession();
  const actor = initialized && !checking && user?.role === "user" ? user : null;
  const { onActiveTool } = useAnalyticsTrackerIdentity({
    enabled: Boolean(actor), toolsEnabled: true,
    identityKey: actor ? `${actor.id}:${revision}` : null,
    subjectRef: actor?.account_ref ?? null,
    role: actor?.role ?? null,
    courseId, catalogVersion: null, canTrackLesson: noLessonOnPlatformPage,
    baseUrl: import.meta.env.BASE_URL.replace(/\/$/, ""),
  });
  useEffect(() => { onActiveTool(tool); }, [onActiveTool, tool]);
  return null;
}

function CourseRedirect({ to }: { to: string }) {
  useEffect(() => {
    window.location.replace(`${to}${window.location.search}${window.location.hash}`);
  }, [to]);
  return <StartupScreen />;
}

function PlatformEntry({ courseId }: { courseId: CourseId }) {
  const { user, checking, initialized, revision, recheck, onUserChange } = useAuthSession();
  const [isAuthOpen, setIsAuthOpen] = useState(false);
  const [emailAction, setEmailAction] = useState(() => {
    const url = new URL(window.location.href);
    if (!url.searchParams.has("email_action")) return undefined;
    let result = null;
    try { result = readEmailLink(url, window.sessionStorage); }
    catch { result = readEmailLink(url, { getItem: () => null, setItem: () => {} }); }
    if (url.hash) {
      url.hash = "";
      window.history.replaceState(window.history.state, "", url);
    }
    return result;
  });

  if (!initialized) {
    return <StartupScreen />;
  }

  if (emailAction !== undefined) {
    return <EmailActionScreen link={emailAction} user={user} onUserChange={onUserChange} onClose={() => {
      clearEmailLink();
      setEmailAction(undefined);
      if (!user) setIsAuthOpen(true);
    }} />;
  }

  if (!user && isAuthOpen) {
    return (
      <AsyncBoundary fallback={<StartupScreen />}><AuthScreen
        onAuth={() => {
          setIsAuthOpen(false);
          recheck();
        }}
        onBack={() => setIsAuthOpen(false)}
      /></AsyncBoundary>
    );
  }

  // Invalidation removes the old owner's entire workspace before /user/me settles.
  if (checking) return <StartupScreen />;

  return (
    <AsyncBoundary fallback={<StartupScreen />}>
      <Workspace
        key={`${user ? String(user.id) : "guest"}:${courseId}:${revision}`}
        courseId={courseId}
        user={user}
        onUserChange={onUserChange}
        onLogout={recheck}
        onRequireAuth={() => setIsAuthOpen(true)}
      />
    </AsyncBoundary>
  );
}
