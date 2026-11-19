import { useEffect, useState } from "react";
import { AnalyticsReportError, fetchActivityReport } from "../analytics-reporting/client";
import type { ActivityReportV1 } from "../analytics-reporting/types";
import { courseReportFilters, type CourseReportRange } from "./course-statistics-analytics";
import { formatStudyDate, formatStudyTime, type CourseTimeValues } from "./course-statistics-model";

type LessonReportState =
  | { status: "loading" | "denied" | "error" }
  | { status: "received"; report: ActivityReportV1 };

export function CourseStatisticsLessonDetails({ actorRef, courseId, from, to, lessonId, fallbackTime }: CourseReportRange & {
  actorRef?: string;
  lessonId: string;
  fallbackTime?: CourseTimeValues;
}) {
  const [state, setState] = useState<LessonReportState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!actorRef || !from || !to) return;
    const controller = new AbortController();
    const { course_id, from_time, to_time } = courseReportFilters({ courseId, from, to }, 0);
    fetchActivityReport({ course_id, from_time, to_time, lesson_id: lessonId }, actorRef, controller.signal).then(report => {
      if (!controller.signal.aborted) setState({ status: "received", report });
    }, error => {
      if (controller.signal.aborted) return;
      const denied = error instanceof AnalyticsReportError && error.kind === "denied";
      setState({ status: denied ? "denied" : "error" });
    });
    return () => controller.abort();
  }, [actorRef, courseId, from, to, lessonId, attempt]);

  const report = state.status === "received" ? state.report : null;
  const available = report !== null && report.status !== "unavailable";
  const loading = Boolean(actorRef) && state.status === "loading";
  const loadingValue = <span className="course-stats__loading" aria-hidden="true" />;
  const time: CourseTimeValues | undefined = actorRef ? (available ? {
    activeMs: report.totals?.active_ms ?? null,
    theoryMs: report.totals?.theory_ms ?? null,
    practiceMs: report.totals?.practice_ms ?? null,
  } : undefined) : fallbackTime;
  const failed = actorRef && (state.status === "error" || report?.status === "unavailable");
  const notice = !actorRef ? null
    : failed ? "Не удалось загрузить время занятия."
    : state.status === "denied" ? "Нет доступа к отчёту по занятию."
    : report?.status === "no_data" ? "За этот период учебное время не зафиксировано."
    : report?.status === "stale" ? "Доступны прошлые замеры."
    : null;
  const coverage = actorRef && available && report.quality !== "complete"
    ? report.quality === "partial" ? "Данные за период неполные." : "Полнота данных за период неизвестна."
    : null;

  return <>
    <span>{from && to ? `Период с ${formatStudyDate(from)} по ${formatStudyDate(to)}` : "За выбранный период"}</span>
    <span className="course-stats__sr-only" role="status">{loading ? "Загружаем время занятия" : ""}</span>
    {(loading || !actorRef || available) && <dl aria-busy={loading}>
      <div><dt>Всего</dt><dd>{loading ? loadingValue : formatStudyTime(time?.activeMs ?? null)}</dd></div>
      <div><dt>Теория</dt><dd>{loading ? loadingValue : formatStudyTime(time?.theoryMs ?? null)}</dd></div>
      <div><dt>Практика</dt><dd>{loading ? loadingValue : formatStudyTime(time?.practiceMs ?? null)}</dd></div>
    </dl>}
    {(notice || coverage) && <div className="course-stats__notice" role="status">
      <span>{[notice, coverage].filter(Boolean).join(" ")}</span>
      {failed && <button type="button" onClick={() => {
        setState({ status: "loading" });
        setAttempt(value => value + 1);
      }}>Повторить</button>}
    </div>}
  </>;
}
