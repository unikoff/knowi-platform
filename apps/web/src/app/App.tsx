import { lazy, useEffect, useState } from "react";
import { AuthScreen } from "../features/auth";
import { useAuthSession } from "../features/auth/useAuthSession";
import { AsyncBoundary } from "../shared/ui/async-boundary/AsyncBoundary";
import { platformUrl } from "../shared/paths";
import { courseUrls, resolveCourseRoute } from "../shared/course-paths";
import type { CourseId } from "../course-runtime/course-registry";
import { NotFoundPage } from "./NotFoundPage";
import { StartupScreen } from "./StartupScreen";
import { EmailActionScreen } from "../features/auth/EmailActionScreen";
import { clearEmailLink, readEmailLink } from "../features/auth/email-link";
import "./status-pages.css";

const Workspace = lazy(() => import("./Workspace").then(({ Workspace }) => ({ default: Workspace })));
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

  if (route.kind === "redirect") return <CourseRedirect to={route.to} />;

  if (route.kind === "catalog") return <CourseCatalog />;
  if (route.kind === "about" || route.kind === "personal") return <AsyncBoundary fallback={<StartupScreen />}>
    <CourseLandingRoute courseId={route.courseId} page={route.kind} legacy={route.legacy} />
  </AsyncBoundary>;
  if (route.kind !== "learning") return <NotFoundPage />;

  return <PlatformEntry courseId={route.courseId} />;
}

function CourseRedirect({ to }: { to: string }) {
  useEffect(() => {
    window.location.replace(`${to}${window.location.search}${window.location.hash}`);
  }, [to]);
  return <StartupScreen />;
}

function CourseCatalog() {
  return <main className="status-page"><section className="not-found-card" aria-labelledby="courses-title">
    <div className="status-brand">
      <span className="status-brand__mark" aria-hidden="true"><i /><i /><i /><i /></span>
      <span>knowi<span className="status-brand__dot">.</span></span>
    </div>
    <h1 id="courses-title">Платформа в разработке</h1>
    <p className="startup-copy">Мы работаем над платформой. Уже сейчас доступен курс Python Backend.</p>
    <a className="status-home-link" href={courseUrls(import.meta.env.BASE_URL, "backend").learning}>
      Перейти к курсу Python Backend <span aria-hidden="true">→</span>
    </a>
  </section></main>;
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
