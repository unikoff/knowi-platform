import { useCallback, useEffect, useMemo, useState } from "react";
import type { AccountStatisticsData } from "../../account/AccountStatistics";
import { CourseStatisticsDashboard } from "../../account/CourseStatisticsDashboard";
import type { LearnerAnalyticsCatalog } from "../../account/LearnerAnalytics";
import { courseReportFilters, courseSnapshot, type CourseReportRange } from "../../account/course-statistics-analytics";
import { periodDates, validateStudyRange } from "../../account/course-statistics-model";
import { AnalyticsReportError, fetchStudentActivityReport, fetchStudentLifetimeActivityOutline } from "../../analytics-reporting/client";
import { CatalogAccessError, fetchStudentStatisticsCatalog, type StatisticsCatalog, type Target } from "./student-statistics-api";

type Props = {
  courseId: string;
  target: Target;
  actorRef: string;
  actorRole: string;
  authRevision: number;
  analyticsEnabled: boolean;
  timeDisabledMessage?: string;
};

function dashboardCatalog({ tracks, courses: entries }: StatisticsCatalog) {
  const catalog: LearnerAnalyticsCatalog = tracks.map(track => ({
    id: track.id,
    title: track.title,
    lessons: entries[track.id].map(lesson => ({ id: lesson.id, title: lesson.title })),
  }));
  const completions: { id: string; day: string }[] = [];
  const courses = tracks.map(track => {
    const lessons = entries[track.id];
    const countedLessons = lessons.filter(lesson => lesson.counts_toward_statistics);
    for (const lesson of countedLessons) {
      if (lesson.completed && lesson.completed_on !== null) completions.push({ id: lesson.id, day: lesson.completed_on });
    }
    const blocks = track.blocks.map(block => {
      const ids = new Set(block.lesson_ids);
      const blockLessons = lessons.filter(lesson => ids.has(lesson.id));
      const counted = blockLessons.filter(lesson => lesson.counts_toward_statistics);
      return {
        ...block,
        ...(lessons.length > 0 && { completed: counted.filter(lesson => lesson.completed).length, total: counted.length }),
        lessons: blockLessons.map(lesson => ({ id: lesson.id, title: lesson.title, completed: lesson.completed, completedOn: lesson.completed_on,
          historicalCredit: lesson.historical_completion_credit === true })),
      };
    });
    return {
      id: track.id,
      title: track.title,
      completed: lessons.length ? countedLessons.filter(lesson => lesson.completed).length : track.lessons_completed,
      total: lessons.length ? countedLessons.length : track.lessons_countable,
      blocks,
    };
  });
  const completedLessons = courses.reduce((total, course) => total + course.completed, 0);
  const totalLessons = courses.reduce((total, course) => total + course.total, 0);
  const data: AccountStatisticsData = {
    tracksLoaded: true, completedLessons, totalLessons,
    overallProgress: totalLessons ? Math.round(completedLessons / totalLessons * 100) : 0,
    activeDays: null, currentStreak: null, weeklyLessonsCompleted: null,
    courses,
  };
  return { data, catalog, completions };
}

type ReadResult<T> = { status: "loading" } | { status: "ready"; data: T } | { status: "error"; error: unknown };
type Loader<T> = (signal: AbortSignal) => Promise<T>;

function useStudentRead<T>(load: Loader<T> | null) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ load: Loader<T>; attempt: number; result: ReadResult<T> } | null>(null);
  useEffect(() => {
    if (!load) return;
    const controller = new AbortController();
    load(controller.signal).then(
      data => { if (!controller.signal.aborted) setState({ load, attempt, result: { status: "ready", data } }); },
      error => { if (!controller.signal.aborted) setState({ load, attempt, result: { status: "error", error } }); },
    );
    return () => controller.abort();
  }, [load, attempt]);
  const result: ReadResult<T> = state && state.load === load && state.attempt === attempt
    ? state.result : { status: "loading" };
  return { result, retry: () => setAttempt(value => value + 1) };
}

function reportStatus(result: ReadResult<{ status: "ok" | "no_data" | "stale" | "unavailable" }>) {
  if (result.status === "loading") return "loading";
  if (result.status === "ready") {
    const { status } = result.data;
    return status === "stale" || status === "unavailable" ? status : "ready";
  }
  const { error } = result;
  if (error instanceof AnalyticsReportError && (error.kind === "denied" || error.kind === "unavailable")) return error.kind;
  return "error";
}

export function CourseStudentStatistics(props: Props) {
  const { courseId, target, actorRef, actorRole, authRevision, analyticsEnabled } = props;
  if (!actorRef || !target.account_ref || !Number.isSafeInteger(authRevision) || !["leader", "admin"].includes(actorRole)) {
    return <p className="admin-note" role="status">Нет доступа к статистике ученика.</p>;
  }
  const scopeKey = JSON.stringify([courseId, actorRef, actorRole, authRevision, target.id, target.account_ref, analyticsEnabled]);
  return <CourseStudentStatisticsRequest key={scopeKey} {...props} />;
}

function CourseStudentStatisticsRequest({ courseId, target: { id, account_ref }, actorRef, analyticsEnabled, timeDisabledMessage }: Props) {
  const loadCatalog = useCallback((signal: AbortSignal) => fetchStudentStatisticsCatalog(courseId, { id, account_ref }, actorRef, signal), [courseId, id, account_ref, actorRef]);
  const { result, retry } = useStudentRead(loadCatalog);
  if (result.status === "loading") return <p className="admin-note" role="status">Загружаем статистику ученика…</p>;
  if (result.status === "error") {
    if (result.error instanceof CatalogAccessError) {
      if (result.error.studentSearchRequired) return <p className="admin-note" role="status">Найдите ученика снова через общий поиск, чтобы открыть статистику.</p>;
      return <p className="admin-note" role="status">{result.error.status === 404 ? "Статистика этого ученика недоступна." : "Нет доступа к статистике ученика."}</p>;
    }
    return <div className="form-error" role="alert">Не удалось загрузить статистику ученика. <button type="button" className="admin-back" onClick={retry}>Повторить</button></div>;
  }
  return <section aria-label="Статистика ученика" style={{ container: "account-dashboard / inline-size" }}>
    <StudentDashboard courseId={courseId} analyticsEnabled={analyticsEnabled} actorRef={actorRef}
      target={{ id, account_ref }} progress={result.data} timeDisabledMessage={timeDisabledMessage} />
  </section>;
}

function StudentDashboard({ courseId, analyticsEnabled, actorRef, target: { id, account_ref }, progress, timeDisabledMessage }:
  Pick<Props, "courseId" | "analyticsEnabled" | "actorRef" | "target" | "timeDisabledMessage"> & { progress: StatisticsCatalog }) {
  const { data, catalog, completions } = useMemo(() => dashboardCatalog(progress), [progress]);
  const [range, setRange] = useState<CourseReportRange | null>(null);
  const loadTime = useCallback(async (signal: AbortSignal) => {
    if (!range) throw new Error("Период статистики не выбран.");
    const period = validateStudyRange(range.from, range.to);
    if (!period.valid) throw new Error(period.message);
    const report = await fetchStudentActivityReport(courseReportFilters(range), { user_id: id, expected_target_ref: account_ref }, actorRef, signal);
    return { status: report.status, snapshot: courseSnapshot(report, range, periodDates(period.days, range.to)) };
  }, [range, id, account_ref, actorRef]);
  const loadChapters = useCallback((signal: AbortSignal) => {
    const seen = new Set<string>();
    const chapters = catalog.map(chapter => ({ chapter_id: chapter.id, lesson_ids: chapter.lessons.flatMap(lesson => {
      if (seen.has(lesson.id)) return [];
      seen.add(lesson.id);
      return [lesson.id];
    }) }));
    return fetchStudentLifetimeActivityOutline({ course_id: courseId, chapters }, { user_id: id, expected_target_ref: account_ref }, actorRef, signal);
  }, [catalog, courseId, id, account_ref, actorRef]);
  const time = useStudentRead(analyticsEnabled && range ? loadTime : null);
  const outline = useStudentRead(analyticsEnabled ? loadChapters : null);
  const timeState = analyticsEnabled ? reportStatus(time.result) : "disabled";
  const timeData = time.result.status === "ready" ? time.result.data.snapshot : null;
  const chapterReport = outline.result.status === "ready" ? outline.result.data : null;
  const searchAgain = [time.result, outline.result].some(result => result.status === "error"
    && result.error instanceof AnalyticsReportError && result.error.studentSearchRequired);
  const periodCompletedLessons = range ? {
    from: range.from, to: range.to,
    count: new Set(completions.filter(({ day }) => day >= range.from && day <= range.to).map(({ id }) => id)).size,
  } : undefined;
  return <>
    {searchAgain && <p className="admin-note" role="status">Найдите ученика снова через общий поиск, чтобы обновить время занятий.</p>}
    <CourseStatisticsDashboard courseId={courseId} data={data} catalog={catalog}
      timeData={timeData} timeState={timeState} timeDisabledMessage={timeDisabledMessage} onRangeChange={setRange} onRetry={time.retry}
      periodCompletedLessons={periodCompletedLessons}
      chapterTime={analyticsEnabled ? { status: reportStatus(outline.result), report: chapterReport } : undefined} onChapterRetry={outline.retry} />
  </>;
}
