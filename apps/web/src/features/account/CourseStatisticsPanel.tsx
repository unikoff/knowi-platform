import { useMemo, useState } from "react";
import { ArrowUpRight, FlaskConical, UserRound } from "lucide-react";
import type { AccountStatisticsData } from "./AccountStatistics";
import type { LearnerAnalyticsCatalog } from "./LearnerAnalytics";
import { CourseStatisticsDashboard } from "./CourseStatisticsDashboard";
import { createCourseStatisticsDemo } from "./course-statistics-demo";
import { moscowToday, periodDates, shiftDate } from "./course-statistics-model";
import { useCourseChapterAnalytics } from "./useCourseChapterAnalytics";
import { useCourseStatisticsAnalytics } from "./useCourseStatisticsAnalytics";
import { usePeriodCompletion } from "./usePeriodCompletion";
import "./course-statistics-mode.css";

export function CourseStatisticsPanel({ courseId, data, catalog, hasActiveSubscription, analyticsEnabled, personalMeasurementsEnabled = false, actorRef, role, onViewSubscription }: {
  courseId: string;
  data: AccountStatisticsData;
  catalog: LearnerAnalyticsCatalog;
  hasActiveSubscription: boolean;
  analyticsEnabled: boolean;
  /** Keep real time empty during demo review, even if the service rollout flag is on. */
  personalMeasurementsEnabled?: boolean;
  actorRef?: string;
  role?: string;
  onViewSubscription?: () => void;
}) {
  const [mode, setMode] = useState<"demo" | "personal">(() => hasActiveSubscription ? "personal" : "demo");
  const [range, setRange] = useState(() => {
    const dates = periodDates(7, moscowToday());
    return { courseId, from: dates[0], to: dates[dates.length - 1] };
  });
  const isDemo = mode === "demo";
  const [attempt, setAttempt] = useState(0);
  const [chapterAttempt, setChapterAttempt] = useState(0);
  const usePersonalMeasurements = role === "user" && analyticsEnabled && personalMeasurementsEnabled;
  const usePersonalReports = usePersonalMeasurements && !isDemo;
  const analytics = useCourseStatisticsAnalytics(usePersonalReports && range.courseId === courseId, actorRef, range, attempt);
  const chapterAnalytics = useCourseChapterAnalytics(usePersonalReports, actorRef, courseId, data, catalog, chapterAttempt);
  const periodCompletion = usePeriodCompletion(!isDemo && role === "user" && range.courseId === courseId, actorRef, range, attempt);
  const dates = useMemo(() => {
    const length = (Date.parse(range.to) - Date.parse(range.from)) / 86400000 + 1;
    return Array.from({ length }, (_, index) => shiftDate(range.from, index));
  }, [range.from, range.to]);
  const demo = useMemo(() => isDemo ? createCourseStatisticsDemo(courseId, dates, data, catalog) : null, [isDemo, courseId, dates, data, catalog]);
  function selectMode(next: "demo" | "personal") {
    if (next === mode) return;
    setMode(next);
    setAttempt(value => value + 1);
    setChapterAttempt(value => value + 1);
  }

  return <div className="course-stats-panel">
    <section className={`course-stats-mode${isDemo ? " is-demo" : ""}`} aria-label="Режим статистики">
      <div className="course-stats__segments course-stats-mode__switch" role="group" aria-label="Источник данных статистики">
        <button type="button" aria-pressed={isDemo} onClick={() => selectMode("demo")}><FlaskConical size={14} aria-hidden="true" />Демо</button>
        <button type="button" aria-pressed={!isDemo} onClick={() => selectMode("personal")}><UserRound size={14} aria-hidden="true" />Мои данные</button>
      </div>
      {isDemo && <span className="course-stats-mode__example" role="status">Пример с тестовыми данными</span>}
      {!hasActiveSubscription && onViewSubscription && <button className="course-stats-mode__subscription" type="button" aria-label="Посмотреть подписку" onClick={onViewSubscription}>Подписка<ArrowUpRight size={16} aria-hidden="true" /></button>}
    </section>
    <CourseStatisticsDashboard courseId={courseId} data={demo?.data ?? data} catalog={demo?.catalog ?? catalog}
      timeData={demo?.snapshot ?? analytics.snapshot} timeState={isDemo ? "ready" : !usePersonalMeasurements ? (role === "user" ? "disabled" : "role_blocked") : analytics.status} demo={isDemo}
      demoProgressSnapshot={demo?.progressSnapshot}
      periodCompletedLessons={demo ? {from: demo.snapshot.from, to: demo.snapshot.to, count: demo.completedLessonsInPeriod} : periodCompletion}
      chapterTime={usePersonalReports ? chapterAnalytics : undefined} onChapterRetry={() => setChapterAttempt(value => value + 1)}
      onRetry={() => setAttempt(value => value + 1)}
      onRangeChange={setRange} />
  </div>;
}
