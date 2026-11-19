import { useEffect, useMemo, useState } from "react";
import { fetchCourseLessons, type CourseLessonOption } from "./courseLessonCatalog";
import { CourseActivityReport, type CourseActivityView } from "./CourseActivityReport";
import { CourseAnalyticsPanel } from "./CourseAnalyticsPanel";
import { CourseActivityDetails } from "./CourseActivityDetails";
import { courseActivityPeriod, type CourseActivityPeriod } from "./courseActivityPeriod";
import { startCourseActivityDetailsRequest, type CourseDetailsFetch, type CourseDetailsFilters } from "./courseActivityDetailsRequest";
import type { ActivityDetailsReport } from "../../analytics-reporting/types";

export type CourseActivityTarget = { userId: number; accountRef: string };
export function activityScopeDescription(actorRole: string, hasTarget: boolean) {
  return hasTarget ? "Выбранный ученик курса" : actorRole === "leader" ? "Люди с сохранёнными записями на курс" : "Все пользователи курса";
}
type CatalogState = { key: string; lessons: CourseLessonOption[]; loading: boolean; error: "access" | "unavailable" | null };
function useCourseLessons(courseId: string, actorRef: string, authRevision: number) {
  const [retry, setRetry] = useState(0);
  const key = JSON.stringify([courseId, actorRef, authRevision, retry]);
  const [state, setState] = useState<CatalogState>({key, lessons: [], loading: true, error: null});
  useEffect(() => {
    let active = true, finished = false;
    const controller = new AbortController();
    setState({key, lessons: [], loading: true, error: null});
    const timer = setTimeout(() => { if (active && !finished) { finished = true; controller.abort(); setState({key, lessons: [], loading: false, error: "unavailable"}); } }, 12_000);
    fetchCourseLessons(courseId, controller.signal).then((lessons) => {
      if (!active || finished || controller.signal.aborted) return;
      finished = true; clearTimeout(timer); setState({key, lessons, loading: false, error: null});
    }).catch((error: unknown) => { if (active && !finished) { finished = true; clearTimeout(timer); const status = typeof error === "object" && error !== null && "status" in error ? error.status : undefined; setState({key, lessons: [], loading: false, error: status === 401 || status === 403 ? "access" : "unavailable"}); } });
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [key]);
  return { ...(state.key === key ? state : {lessons: [], loading: true, error: null}), retry: () => setRetry((value) => value + 1) };
}
// Missing identity fails closed. The target may only come from roster, never grant-search.
export function CourseActivitySection({ courseId, actorRef, actorRole, authRevision, target, fetchDetails }: {
  courseId: string; actorRef?: string; actorRole?: string; authRevision?: number;
  target?: CourseActivityTarget; fetchDetails: CourseDetailsFetch;
}) {
  if (!actorRef || !actorRole || typeof authRevision !== "number" || (target && !target.accountRef)) {
    return <CourseAnalyticsPanel title="Учебная активность" state={{ kind: "access" }} />;
  }
  const scopeKey = JSON.stringify([courseId, actorRef, actorRole, authRevision, target?.userId, target?.accountRef]);
  return <CourseActivityRequest key={scopeKey} courseId={courseId} actorRef={actorRef} actorRole={actorRole} authRevision={authRevision} target={target} fetchDetails={fetchDetails} />;
}
type DetailsView = {kind: "loading"} | {kind: "error"; reason: "access" | "error" | "timeout"} | {kind: "ready"; details: ActivityDetailsReport};
function CourseActivityRequest({ courseId, actorRef, actorRole, authRevision, target, fetchDetails }: {
  courseId: string; actorRef: string; actorRole: string; authRevision: number;
  target?: CourseActivityTarget; fetchDetails: CourseDetailsFetch;
}) {
  const [days, setDays] = useState<CourseActivityPeriod>(30);
  const [anchor, setAnchor] = useState(() => Date.now());
  const [lessonId, setLessonId] = useState("");
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const period = useMemo(() => courseActivityPeriod(days, anchor), [days, anchor]);
  const catalog = useCourseLessons(courseId, actorRef, authRevision);
  const targetId = target?.userId, targetRef = target?.accountRef;
  const key = JSON.stringify([courseId, actorRef, actorRole, authRevision, targetId, targetRef, period.from_time, period.to_time, lessonId, offset, revision]);
  const [state, setState] = useState<{ key: string; view: DetailsView }>({key, view: {kind: "loading"}});
  useEffect(() => {
    const filters: CourseDetailsFilters = { ...period, course_id: courseId, timezone: "Europe/Moscow", offset, limit: 20,
      ...(lessonId ? {lesson_id: lessonId} : {}), ...(targetId === undefined ? {} : {user_id: targetId, expected_target_ref: targetRef}) };
    setState({key, view: {kind: "loading"}});
    return startCourseActivityDetailsRequest({filters, fetchDetails, expectedActorRef: actorRef,
      onDetails: (details) => setState({key, view: {kind: "ready", details}}),
      onFailure: (reason) => setState({key, view: {kind: "error", reason}}),
    });
  }, [key, fetchDetails]);
  const current: DetailsView = state.key === key ? state.view : {kind: "loading"};
  const retry = () => {setAnchor(Date.now()); setRevision((value) => value + 1);};
  const view: CourseActivityView = current.kind === "ready" ? {kind: "report", report: current.details.report, retry}
    : current.kind === "error" ? {kind: "error", reason: current.reason, ...(current.reason === "access" ? {} : {retry})} : {kind: "loading"};
  const chooseLesson = (id: string) => {setLessonId(id); setOffset(0);};
  const hasSelectedLesson = catalog.lessons.some((lesson) => lesson.id === lessonId);
  return <div className="admin-scoped-content" aria-label="Фильтры и отчёт учебной активности">
    <div className="course-analytics-filters">
      <fieldset><legend>Период активности (Москва)</legend>{([[1, "День"], [7, "7 дней"], [30, "30 дней"]] as const).map(([value,label]) => <button key={value} className="admin-back" type="button" aria-pressed={days === value} onClick={() => {setDays(value); setAnchor(Date.now()); setOffset(0);}}>{label}</button>)}</fieldset>
      <label className="admin-scoped-field">Урок<select className="admin-scoped-input" value={lessonId} onChange={(event) => chooseLesson(event.target.value)}><option value="">Все уроки</option>{lessonId && !hasSelectedLesson && <option value={lessonId}>Урок ({lessonId})</option>}{catalog.lessons.map((lesson) => <option key={lesson.id} value={lesson.id}>{lesson.title}</option>)}</select></label>
    </div>
    {catalog.loading && <p className="admin-note" role="status">Загружаем список уроков…</p>}
    {catalog.error && <div className="form-error" role="alert">{catalog.error === "access" ? "Нет доступа к каталогу уроков." : <>Не удалось загрузить список уроков. <button className="admin-back" type="button" onClick={catalog.retry}>Повторить</button></>}</div>}
    {!catalog.loading && !catalog.error && catalog.lessons.length === 0 && <p className="admin-note">В каталоге курса нет уроков.</p>}
    <CourseActivityReport courseId={courseId} view={view} title={targetId === undefined ? "Учебная активность курса" : "Учебная активность ученика"} scopeDescription={activityScopeDescription(actorRole, targetId !== undefined)} />
    {current.kind === "ready" && <CourseActivityDetails details={current.details} lessons={catalog.lessons} selectedLesson={lessonId} onSelectLesson={chooseLesson} offset={offset} onPrevious={() => setOffset((value) => Math.max(0, value - 20))} onNext={(next) => setOffset(next)} />}
  </div>;
}
