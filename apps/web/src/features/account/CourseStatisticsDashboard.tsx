import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import { ArrowLeft, ArrowRight, BookCheck, CalendarDays, Clock3, Hourglass, X } from "lucide-react";
import type { AccountStatisticsData } from "./AccountStatistics";
import type { LearnerAnalyticsCatalog } from "./LearnerAnalytics";
import type { ChapterTimeState } from "./useCourseChapterAnalytics";
import { CourseStatisticsProgress } from "./CourseStatisticsProgress";
import { acceptCourseSnapshot, completionCreditSnapshot, courseTimeChartScale, flattenCourseLessons, formatStudyDate, formatStudyTime, mergeLessonStudyTime, mergeStudyTime, moscowToday, periodDates, shiftDate, summarizeCourseTime, type CourseTimeSnapshot, type CourseTimeValues, type ProgressLesson, type TimeMetric } from "./course-statistics-model";
import "./course-statistics.css";

const metrics = [
  { key: "activeMs", label: "Всего" },
  { key: "theoryMs", label: "Теория" },
  { key: "practiceMs", label: "Практика" },
] as const;
const periods = [{ days: 7, label: "Неделя" }, { days: 30, label: "Месяц" }] as const;
type Period = (typeof periods)[number]["days"];
type LessonRow = ProgressLesson & { completionKnown: boolean };
type TimeState = "pending" | "loading" | "ready" | "stale" | "error" | "unavailable" | "denied" | "disabled" | "role_blocked";
type ChartSegment = "theory" | "practice";
type ChartPreview = {
  target: string;
  anchor: HTMLButtonElement;
  source: "hover" | "focus" | "press";
  segment?: ChartSegment;
  pointerRatio?: number;
};
export type PeriodCompletedLessons = { from: string; to: string; count: number | null; loading?: boolean };

const ringCenter = 50;
const ringRadius = 45.5;
const ringStroke = 9;

function ringPoint(turn: number, radius: number) {
  const angle = turn * 2 * Math.PI;
  return [ringCenter + radius * Math.sin(angle), ringCenter - radius * Math.cos(angle)];
}

function ringArc(start: number, end: number) {
  const radius = ringRadius + ringStroke / 2;
  const innerRadius = ringRadius - ringStroke / 2;
  const large = end - start > .5 ? 1 : 0;
  return [
    `M ${ringPoint(start, radius).join(" ")}`,
    `A ${radius} ${radius} 0 ${large} 1 ${ringPoint(end, radius).join(" ")}`,
    `L ${ringPoint(end, innerRadius).join(" ")}`,
    `A ${innerRadius} ${innerRadius} 0 ${large} 0 ${ringPoint(start, innerRadius).join(" ")} Z`,
  ].join(" ");
}

function DistributionRing({ theoryShare, active }: { theoryShare: number; active?: ChartSegment }) {
  const mixed = theoryShare > 0 && theoryShare < 1;
  const gap = mixed ? Math.min(.0035, theoryShare / 3, (1 - theoryShare) / 3) : 0;
  const arcs = [
    { segment: "theory", start: gap, end: theoryShare - gap },
    { segment: "practice", start: theoryShare + gap, end: 1 - gap },
  ] as const;
  return <svg className="course-stats__distribution-arcs" viewBox="0 0 100 100" aria-hidden="true" focusable="false">
    {arcs.filter(arc => arc.end > arc.start).map(({ segment, start, end }) => {
      const fields = {
        className: "course-stats__distribution-arc",
        "data-ring-arc": segment,
        "data-active": active === segment,
      };
      if (!mixed) {
        return <circle key={segment} {...fields} cx={ringCenter} cy={ringCenter} r={ringRadius}
          fill="none" stroke="currentColor" strokeWidth={ringStroke} />;
      }
      const angle = (start + end) * Math.PI;
      const offset = {
        "--cs-arc-x": `${(Math.sin(angle) * 4.6).toFixed(3)}px`,
        "--cs-arc-y": `${(-Math.cos(angle) * 4.6).toFixed(3)}px`,
      } as CSSProperties;
      return <path key={segment} {...fields} d={ringArc(start, end)} fill="currentColor" style={offset} />;
    })}
  </svg>;
}

function chartBreakdown(values: CourseTimeValues) {
  const { theoryMs, practiceMs } = values;
  const sum = theoryMs === null || practiceMs === null ? null : theoryMs + practiceMs;
  const theoryPercent = sum && theoryMs !== null ? Math.round(theoryMs / sum * 100) : null;
  return [
    { id: "theory" as const, label: "Теория", ms: theoryMs, share: theoryPercent },
    { id: "practice" as const, label: "Практика", ms: practiceMs, share: theoryPercent === null ? null : 100 - theoryPercent },
  ];
}

function chartBarSegments(day: CourseTimeValues, metric: TimeMetric): { id: ChartSegment; fraction: number }[] {
  const value = day[metric];
  if (value === null || value <= 0) return [];
  if (metric === "theoryMs") return [{ id: "theory", fraction: 1 }];
  if (metric === "practiceMs") return [{ id: "practice", fraction: 1 }];
  const { theoryMs, practiceMs } = day;
  if (theoryMs === null || practiceMs === null || theoryMs + practiceMs !== value) return [];
  const segments: { id: ChartSegment; fraction: number }[] = [];
  if (theoryMs > 0) segments.push({ id: "theory", fraction: theoryMs / value });
  if (practiceMs > 0) segments.push({ id: "practice", fraction: practiceMs / value });
  return segments;
}

function describeTime(ms: number | null) {
  return ms === null ? "нет данных" : formatStudyTime(ms);
}

function acceptsChartHover(event: PointerEvent<HTMLElement>) {
  return event.pointerType === "mouse" && event.currentTarget.ownerDocument.defaultView
    ?.matchMedia("(any-hover: hover) and (any-pointer: fine)").matches;
}

export interface CourseStatisticsDashboardProps {
  courseId: string;
  data: AccountStatisticsData;
  catalog: LearnerAnalyticsCatalog;
  /** The authenticated adapter supplies only this learner's current course. */
  timeData?: CourseTimeSnapshot | null;
  timeState?: TimeState;
  timeDisabledMessage?: string;
  chapterTime?: ChapterTimeState;
  periodCompletedLessons?: PeriodCompletedLessons;
  demoProgressSnapshot?: CourseTimeSnapshot;
  onChapterRetry?: () => void;
  demo?: boolean;
  onRetry?: () => void;
  onRangeChange?: (range: { courseId: string; from: string; to: string }) => void;
}

export function CourseStatisticsDashboard({ courseId, data, catalog, timeData = null, timeState = "pending", timeDisabledMessage, chapterTime, periodCompletedLessons, demoProgressSnapshot, onChapterRetry, demo = false, onRetry, onRangeChange }: CourseStatisticsDashboardProps) {
  const today = moscowToday();
  const [period, setPeriod] = useState<Period>(7);
  const [endDate, setEndDate] = useState(today);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [focusedDate, setFocusedDate] = useState(today);
  const [metric, setMetric] = useState<TimeMetric>("activeMs");
  const [preview, setPreview] = useState<ChartPreview | null>(null);
  const detailId = useId();
  const dayButtons = useRef<Array<HTMLButtonElement | null>>([]);
  const chartScroll = useRef<HTMLDivElement>(null);
  const timeline = useRef<HTMLElement>(null);
  const previewCard = useRef<HTMLDivElement>(null);
  const previewExit = useRef<ReturnType<typeof setTimeout> | null>(null);
  const notifiedRange = useRef<string | null>(null);
  const dates = useMemo(() => periodDates(period, endDate), [period, endDate]);
  const progressLessons = useMemo(() => flattenCourseLessons(data.courses), [data.courses]);
  const measurement = useMemo(() => acceptCourseSnapshot(timeState === "ready" || timeState === "stale" ? timeData : null, courseId, dates), [timeData, timeState, courseId, dates]);
  const credit = useMemo(() => !demo && data.tracksLoaded ? completionCreditSnapshot(courseId, dates, progressLessons) : null,
    [courseId, data.tracksLoaded, dates, demo, progressLessons]);
  const timeLoading = !credit?.totals.activeMs && (timeState === "pending" || timeState === "loading");
  const loadingValue = <span className="course-stats__loading" aria-hidden="true" />;
  const snapshot = useMemo(() => mergeStudyTime(measurement, credit), [measurement, credit]);
  const outlineState = useMemo<ChapterTimeState | undefined>(() => {
    if (demo) return undefined;
    if (!chapterTime?.report) return chapterTime;
    const {report} = chapterTime;
    if (report.course_id === courseId) return chapterTime;
    return {status: "loading", report: null};
  }, [chapterTime, courseId, demo]);
  const outline = outlineState?.status === "ready" ? outlineState.report : null;
  const completeOutline = outline?.status === "ok" || outline?.status === "no_data" ? outline : null;
  const lessonRowsComplete = demo ? demoProgressSnapshot !== undefined : completeOutline !== null;
  const lessonTimeLoading = !credit && (outlineState?.status === "pending" || outlineState?.status === "loading");
  const progressSnapshot = useMemo<CourseTimeSnapshot>(() => {
    if (demo) return demoProgressSnapshot ?? {...snapshot, lessons: []};
    const lessons = completeOutline ? [...completeOutline.lessons, ...completeOutline.unmapped_lessons].map(row => ({
      lessonId: row.lesson_id, activeMs: row.active_ms, theoryMs: row.theory_ms, practiceMs: row.practice_ms,
    })) : [];
    return {...snapshot, lessons: mergeLessonStudyTime(lessons, credit?.lessons ?? [])};
  }, [completeOutline, credit, demo, demoProgressSnapshot, snapshot]);
  const activeDate = selectedDate !== null && dates.includes(selectedDate) ? selectedDate : null;
  const tabStopDate = dates.includes(focusedDate) ? focusedDate : endDate;
  const selectedDay = snapshot.days.find(day => day.date === activeDate);
  const detailValues = selectedDay ?? snapshot.totals;
  const selectedMetric = metrics.find(item => item.key === metric)!;
  const chartScale = courseTimeChartScale(snapshot.days, metric);
  const hasTime = chartScale !== null;
  const { activeDays, averageMs } = summarizeCourseTime(snapshot);
  const activeDaysShare = activeDays === null ? null : Math.round(activeDays / dates.length * 100);
  const progress = data.tracksLoaded && data.totalLessons > 0 ? Math.min(100, Math.round(data.completedLessons / data.totalLessons * 100)) : null;
  const completionsInRange = periodCompletedLessons?.from === dates[0] && periodCompletedLessons.to === endDate
    ? periodCompletedLessons
    : null;
  const periodCompletionsLoading = completionsInRange?.loading === true;
  const periodCompletionCount = completionsInRange?.count ?? null;
  const lessons = useMemo<LessonRow[]>(() => {
    // Tracks are chapters of the already scoped course, not top-level course IDs.
    const rows = progressLessons.map(lesson => ({ ...lesson, completionKnown: data.tracksLoaded }));
    const seen = new Set(rows.map(lesson => lesson.id));
    for (const track of catalog) for (const lesson of track.lessons) {
      if (seen.has(lesson.id)) continue;
      seen.add(lesson.id);
      rows.push({ ...lesson, trackId: track.id, blockId: "", chapterTitle: track.title, blockTitle: "", completed: false, completionKnown: false });
    }
    for (const lesson of progressSnapshot.lessons) {
      if (seen.has(lesson.lessonId)) continue;
      seen.add(lesson.lessonId);
      rows.push({id: lesson.lessonId, title: `Урок (${lesson.lessonId})`, trackId: "", blockId: "", chapterTitle: "", blockTitle: "", completed: false, completionKnown: false});
    }
    return rows;
  }, [progressLessons, data.tracksLoaded, catalog, progressSnapshot.lessons]);
  const splitTheory = detailValues.theoryMs;
  const splitPractice = detailValues.practiceMs;
  const splitTotal = splitTheory === null || splitPractice === null ? null : splitTheory + splitPractice;
  const theoryShare = splitTotal && splitTheory !== null ? splitTheory / splitTotal : null;
  const distributionState = timeLoading ? "loading" : splitTotal === null ? "unknown" : splitTotal === 0 ? "empty" : "ready";
  const ringAvailable = !timeLoading && theoryShare !== null;
  const detailDateLabel = selectedDay ? formatStudyDate(selectedDay.date, { day: "numeric", month: "long", weekday: "long", year: "numeric" }) : "";
  const measured = snapshot.totals.activeMs !== null;
  const noRecordedTime = measured && !hasTime && snapshot.days.every(day => day[metric] === 0);
  const retryAvailable = !demo && (timeState === "error" || timeState === "unavailable") && onRetry;

  useLayoutEffect(() => {
    if (!onRangeChange) return;
    const key = `${courseId}:${dates[0]}:${dates[dates.length - 1]}`;
    if (notifiedRange.current === key) return;
    notifiedRange.current = key;
    onRangeChange({ courseId, from: dates[0], to: dates[dates.length - 1] });
  }, [courseId, dates, onRangeChange]);
  useEffect(() => {
    // Show recent days without scrolling the page or moving keyboard focus.
    if (chartScroll.current) chartScroll.current.scrollLeft = chartScroll.current.scrollWidth;
  }, [dates]);

  function cancelPreviewExit() {
    if (previewExit.current !== null) clearTimeout(previewExit.current);
    previewExit.current = null;
  }

  function dismissPreview() {
    cancelPreviewExit();
    setPreview(null);
  }

  function showPreview(target: string, anchor: HTMLButtonElement, source: ChartPreview["source"], pointerRatio?: number) {
    if (timeLoading) return;
    cancelPreviewExit();
    setPreview(current => current?.target === target && (current.source === "press" || source === "hover")
      ? current
      : { target, anchor, source, pointerRatio, segment: current?.target === target ? current.segment : undefined });
  }

  function leavePreview(target: string) {
    cancelPreviewExit();
    previewExit.current = setTimeout(() => {
      setPreview(current => current?.target === target && current.source === "hover" ? null : current);
      previewExit.current = null;
    }, 180);
  }

  function previewEvents(target: string) {
    return {
      onPointerEnter: (event: PointerEvent<HTMLButtonElement>) => {
        if (!acceptsChartHover(event)) return;
        const box = event.currentTarget.getBoundingClientRect();
        showPreview(target, event.currentTarget, "hover", (event.clientY - box.top) / box.height);
        previewBarSegment(event, target);
      },
      onPointerLeave: (event: PointerEvent<HTMLButtonElement>) => {
        if (!acceptsChartHover(event)) return;
        leavePreview(target);
        setPreview(current => current?.target === target && current.segment ? { ...current, segment: undefined } : current);
      },
      onFocus: (event: React.FocusEvent<HTMLButtonElement>) => {
        if (event.currentTarget.matches(":focus-visible")) showPreview(target, event.currentTarget, "focus");
      },
      onBlur: () => setPreview(current => current?.target === target && current.source !== "hover" ? null : current),
    };
  }

  function previewBarSegment(event: PointerEvent<HTMLButtonElement>, date: string) {
    if (!acceptsChartHover(event)) return;
    const hit = (event.target as HTMLElement).dataset?.barSegment;
    const segment = hit === "theory" || hit === "practice" ? hit : undefined;
    setPreview(current => current?.target === date && current.segment !== segment ? { ...current, segment } : current);
  }

  function clearBarHoverOnScroll() {
    setPreview(current => current?.target !== "ring" && current?.segment ? { ...current, segment: undefined } : current);
  }

  function previewRingSegment(event: PointerEvent<HTMLButtonElement>, entering = false) {
    if (!acceptsChartHover(event)) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const radius = Math.min(bounds.width, bounds.height) / 2;
    const x = event.clientX - (bounds.left + bounds.width / 2);
    const y = event.clientY - (bounds.top + bounds.height / 2);
    const inside = radius > 0 && Math.hypot(x, y) <= radius;
    if (inside && entering) showPreview("ring", event.currentTarget, "hover");
    else if (preview?.target === "ring") {
      if (inside) cancelPreviewExit();
      else leavePreview("ring");
    }
    setPreview(current => {
      if (current?.target !== "ring") return current;
      let segment: ChartSegment | undefined;
      if (inside && ringAvailable) {
        // The unanimated target and the SVG share a center and start both slices at 12 o'clock.
        const turn = x === 0 && y === 0 ? 0 : (Math.atan2(x, -y) / (2 * Math.PI) + 1) % 1;
        segment = turn < theoryShare ? "theory" : "practice";
        // All directions meet at the center, so keep the current selection there.
        if (x === 0 && y === 0 && theoryShare > 0 && theoryShare < 1) segment = current.segment ?? segment;
      }
      return current.segment === segment ? current : { ...current, segment };
    });
  }

  useEffect(() => () => {
    if (previewExit.current !== null) clearTimeout(previewExit.current);
  }, []);
  useEffect(() => {
    setPreview(null);
  }, [courseId, timeLoading]);

  const previewAnchor = timeLoading ? null : preview?.anchor;
  const previewTarget = timeLoading ? null : preview?.target;
  const previewPointerRatio = preview?.pointerRatio;
  useLayoutEffect(() => {
    if (!previewAnchor) return;
    const section = timeline.current;
    const card = previewCard.current;
    if (!section || !card) return;
    const update = () => {
      const bounds = section.getBoundingClientRect();
      const trigger = previewAnchor.getBoundingClientRect();
      const scroller = previewTarget === "ring" ? null : chartScroll.current?.getBoundingClientRect();
      const minX = Math.max(bounds.left + 8, 8);
      const maxX = Math.min(bounds.right - 8, window.innerWidth - 8);
      const minY = 8;
      const maxY = window.innerHeight - 8;
      const visibleLeft = Math.max(scroller?.left ?? minX, minX);
      const visibleRight = Math.min(scroller?.right ?? maxX, maxX);
      if (!previewAnchor.isConnected || trigger.bottom <= minY || trigger.top >= maxY
        || trigger.right <= visibleLeft || trigger.left >= visibleRight || maxX <= minX || maxY <= minY) {
        setPreview(null);
        return;
      }
      const { offsetWidth: width, offsetHeight: height } = card;
      const bar = previewAnchor.querySelector(".course-stats__day-track > i")?.getBoundingClientRect();
      const hintedY = bar?.top ?? trigger.top;
      const centeredX = trigger.left + trigger.width / 2 - width / 2;
      const left = Math.min(Math.max(centeredX, minX), Math.max(minX, maxX - width));
      const pointerY = previewPointerRatio === undefined ? hintedY : trigger.top + trigger.height * previewPointerRatio;
      const aboveDay = Math.min(pointerY, hintedY) - height - 13;
      const top = previewTarget === "ring"
        ? (trigger.bottom + height + 6 <= maxY ? trigger.bottom + 6 : trigger.top - height - 6)
        : (aboveDay >= minY ? aboveDay : pointerY + 6);
      card.style.left = `${left - bounds.left - section.clientLeft}px`;
      card.style.top = `${Math.min(Math.max(top, minY), Math.max(minY, maxY - height)) - bounds.top - section.clientTop}px`;
    };
    const onOutsideInteraction = (event: Event) => {
      if (!(event.target instanceof Node) || previewAnchor.contains(event.target) || card.contains(event.target)) return;
      setPreview(current => current?.anchor === previewAnchor ? null : current);
    };
    const onEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape" && !event.defaultPrevented) setPreview(null);
    };
    const observer = new ResizeObserver(update);
    observer.observe(section);
    observer.observe(card);
    document.addEventListener("scroll", update, true);
    document.addEventListener("pointerdown", onOutsideInteraction);
    document.addEventListener("focusin", onOutsideInteraction);
    document.addEventListener("keydown", onEscape);
    window.addEventListener("resize", update);
    update();
    return () => {
      observer.disconnect();
      document.removeEventListener("scroll", update, true);
      document.removeEventListener("pointerdown", onOutsideInteraction);
      document.removeEventListener("focusin", onOutsideInteraction);
      document.removeEventListener("keydown", onEscape);
      window.removeEventListener("resize", update);
    };
  }, [previewAnchor, previewTarget, previewPointerRatio]);

  function selectDate(date: string, anchor: HTMLButtonElement) {
    setFocusedDate(date);
    setSelectedDate(current => current === date ? null : date);
    if (preview?.target !== date) showPreview(date, anchor, "press");
    else if (preview.source === "press") dismissPreview();
  }
  function clearDate() {
    setSelectedDate(null);
    dayButtons.current[dates.indexOf(tabStopDate)]?.focus();
    dismissPreview();
  }
  function handleDayKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.key === "Escape") {
      event.preventDefault();
      if (preview) dismissPreview();
      else clearDate();
      return;
    }
    const next = event.key === "ArrowRight" ? Math.min(dates.length - 1, index + 1)
      : event.key === "ArrowLeft" ? Math.max(0, index - 1)
      : event.key === "Home" ? 0 : event.key === "End" ? dates.length - 1 : null;
    if (next === null) return;
    event.preventDefault();
    setSelectedDate(dates[next]);
    setFocusedDate(dates[next]);
    dayButtons.current[next]?.focus();
    if (dayButtons.current[next]) showPreview(dates[next], dayButtons.current[next]!, "focus");
  }
  function movePeriod(direction: -1 | 1) {
    dismissPreview();
    const next = shiftDate(endDate, direction * period);
    const clamped = next > today ? today : next;
    setEndDate(clamped);
    setSelectedDate(null);
    setFocusedDate(clamped);
  }
  function selectPeriod(next: Period) {
    dismissPreview();
    setPeriod(next);
    setSelectedDate(null);
  }
  const statusText = timeLoading ? null
    : timeState === "disabled" ? timeDisabledMessage ?? "Отображение личного времени занятий пока не включено"
    : timeState === "role_blocked" ? "Личная аналитика времени недоступна для этой роли"
    : timeState === "error" ? "Не удалось загрузить учебную аналитику"
    : timeState === "denied" ? "Нет доступа к учебной аналитике"
    : timeState === "unavailable" ? "Учебная аналитика временно недоступна"
    : timeState === "stale" && (measured || snapshot.days.some(day => day.activeMs !== null)) ? "Доступны прошлые замеры"
    : !measured ? "Данные о времени недоступны" : null;
  const coverageNotice = measured && snapshot.quality === "partial" ? "Замеры доступны частично." : null;
  const chartNotice = [noRecordedTime ? "За этот период 0 мин" : null, statusText, coverageNotice].filter(Boolean).join(" · ");
  const totalStudyTime = formatStudyTime(snapshot.totals.activeMs);
  const averageStudyTime = formatStudyTime(averageMs);
  const peakDailyTime = Math.max(0, ...snapshot.days.map(day => day.activeMs ?? 0));
  const previewDay = snapshot.days.find(day => day.date === previewTarget);
  const previewValues = previewTarget === "ring" ? detailValues : previewDay;
  const previewLabel = previewDay
    ? formatStudyDate(previewDay.date, { day: "numeric", month: "long", weekday: "long" })
    : selectedDay ? detailDateLabel : "За весь период";
  const previewSegment = previewTarget === "ring" || (previewDay && chartBarSegments(previewDay, metric).some(segment => segment.id === preview?.segment))
    ? preview?.segment : undefined;
  const ringPreviewEvents = previewEvents("ring");
  const detailBreakdown = chartBreakdown(detailValues)
    .map(row => `${row.label}: ${describeTime(row.ms)}${row.share === null ? "" : `, ${row.share}%`}`).join(". ");

  return <div className="course-stats">
    <span className="course-stats__sr-only" role="status">{timeLoading ? "Загружаем время занятий" : ""}</span>
    <section className="course-stats__metrics" aria-label="Сводка по курсу" aria-busy={timeLoading}>
      <div className="course-stats__metric course-stats__metric--lead">
        <span className="course-stats__metric-label"><i><Clock3 size={17} aria-hidden="true" /></i>Время в курсе</span>
        <strong className="course-stats__metric-value" data-ready={measured} key={totalStudyTime}>
          {timeLoading ? loadingValue : totalStudyTime}
        </strong>
        <div className="course-stats__metric-trace" aria-hidden="true">
          {snapshot.days.map(day => <span key={day.date} data-observed={day.activeMs !== null}>
            {day.activeMs !== null && day.activeMs > 0 && <i style={{ "--cs-activity-ratio": day.activeMs / peakDailyTime } as CSSProperties} />}
          </span>)}
        </div>
      </div>
      <div className="course-stats__metric course-stats__metric--days">
        <span className="course-stats__metric-label"><i><CalendarDays size={17} aria-hidden="true" /></i>Дней с занятиями</span>
        <div className="course-stats__metric-reading">
          <strong className="course-stats__metric-value" data-ready={activeDays !== null} key={`${activeDays}/${dates.length}`}>
            {timeLoading ? loadingValue : activeDays ?? "—"}<small> / {dates.length}</small>
          </strong>
          {activeDaysShare !== null && <span className="course-stats__metric-share" key={activeDaysShare}><span className="course-stats__sr-only">Доля дней с занятиями: </span>{activeDaysShare}%</span>}
        </div>
        <div className="course-stats__day-strip" aria-hidden="true">
          {snapshot.days.map(day => <span key={day.date} data-state={day.activeMs === null ? "unknown" : day.activeMs > 0 ? "active" : "empty"} />)}
        </div>
      </div>
      <div className="course-stats__metric course-stats__metric--average">
        <span className="course-stats__metric-label"><i><Hourglass size={17} aria-hidden="true" /></i>В среднем за учебный день</span>
        <strong className="course-stats__metric-value" data-ready={averageMs !== null} key={averageStudyTime}>
          {timeLoading ? loadingValue : averageStudyTime}
        </strong>
      </div>
      <div className="course-stats__metric course-stats__metric--completion">
        <span className="course-stats__metric-label"><i><BookCheck size={17} aria-hidden="true" /></i>Пройдено занятий</span>
        <div className="course-stats__metric-reading">
          <strong className="course-stats__metric-value" data-ready={data.tracksLoaded} key={`${data.completedLessons}/${data.totalLessons}`}>
            {data.tracksLoaded ? data.completedLessons : "—"}<small> / {data.tracksLoaded ? data.totalLessons : "—"}</small>
          </strong>
          <small>За весь курс</small>
        </div>
        {progress !== null && <div className="course-stats__progress" role="progressbar" aria-label="Прогресс курса" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><span style={{ "--cs-course-ratio": progress / 100 } as CSSProperties} /></div>}
        <div className="course-stats__metric-completions" aria-busy={periodCompletionsLoading}>
          <span>За выбранный период</span>
          <strong data-ready={!periodCompletionsLoading && periodCompletionCount !== null} key={periodCompletionCount ?? "unknown"}>
            {periodCompletionsLoading ? <>{loadingValue}<span className="course-stats__sr-only">Загружаем число пройденных занятий</span></> : periodCompletionCount ?? "—"}
          </strong>
        </div>
      </div>
    </section>

    <section className="course-stats__card course-stats__timeline" aria-label="Время занятий по дням" ref={timeline}>
      <header className="course-stats__card-heading course-stats__history-head">
        <h3>История занятий</h3>
        <div className="course-stats__segments course-stats__segments--small" role="group" aria-label="Формат занятий на графике">
          {metrics.map(({ key, label }) => <button type="button" key={key} aria-pressed={metric === key} onClick={() => { setMetric(key); dismissPreview(); }}>{label}</button>)}
        </div>
      </header>

      <div className="course-stats__history-range">
        <div className="course-stats__segments course-stats__segments--small course-stats__periods" role="group" aria-label="Период истории занятий">
          {periods.map(({ days, label }) =>
            <button key={days} type="button" aria-pressed={period === days} onClick={() => selectPeriod(days)}>{label}</button>
          )}
        </div>
        <div className="course-stats__date-nav">
          <button type="button" aria-label="Предыдущий период" onClick={() => movePeriod(-1)}><ArrowLeft size={16} aria-hidden="true" /></button>
          <span aria-live="polite">{dates[0] === endDate ? formatStudyDate(endDate, { day: "numeric", month: "short", year: "numeric" }) : `${formatStudyDate(dates[0])} – ${formatStudyDate(endDate, { day: "numeric", month: "short", year: "numeric" })}`}</span>
          <button type="button" aria-label="Следующий период" disabled={endDate >= today} onClick={() => movePeriod(1)}><ArrowRight size={16} aria-hidden="true" /></button>
        </div>
      </div>

      <div className="course-stats__history-main">
        <div className="course-stats__plot" data-days={period} data-density={period > 14 ? "dense" : "wide"} data-empty={!hasTime && !timeLoading} aria-busy={timeLoading}>
          <div className="course-stats__plot-axis" aria-hidden="true">
            {timeLoading ? <>{loadingValue}{loadingValue}{loadingValue}</>
              : chartScale && <>{chartScale.ticks.map((tick, index) => <span key={index}>{tick}</span>)}</>}
          </div>
          <div className="course-stats__chart-scroll" ref={chartScroll} onScroll={clearBarHoverOnScroll}>
            <div className="course-stats__bars" style={{ gridTemplateColumns: `repeat(${dates.length}, minmax(0, 1fr))`, minWidth: `max(100%, ${period * (period > 14 ? 27 : 32)}px)` }} role="group" aria-label={`${selectedMetric.label} по дням. Стрелки выбирают день. Escape закрывает подсказку, повторное нажатие возвращает весь период.`}>
              {snapshot.days.map((day, index) => {
                const value = day[metric];
                const segments = chartBarSegments(day, metric);
                const activeSegment = previewTarget === day.date ? previewSegment : undefined;
                const dateLabel = formatStudyDate(day.date, { day: "numeric", month: "long", weekday: "long" });
                const breakdown = [
                  `всего: ${describeTime(day.activeMs)}`,
                  `теория: ${describeTime(day.theoryMs)}`,
                  `практика: ${describeTime(day.practiceMs)}`,
                ];
                if (metric === "theoryMs") [breakdown[0], breakdown[1]] = [breakdown[1], breakdown[0]];
                if (metric === "practiceMs") [breakdown[0], breakdown[2]] = [breakdown[2], breakdown[0]];
                const label = timeLoading ? dateLabel : `${dateLabel}: ${breakdown.join(", ")}`;
                return <button type="button" key={day.date} ref={node => { dayButtons.current[index] = node; }}
                  className={`course-stats__day${day.date === activeDate ? " is-selected" : ""}`}
                  tabIndex={day.date === tabStopDate ? 0 : -1} aria-pressed={day.date === activeDate}
                  data-preview={previewTarget === day.date} aria-controls={detailId} aria-label={label}
                  {...previewEvents(day.date)}
                  onPointerMove={event => previewBarSegment(event, day.date)}
                  onClick={event => selectDate(day.date, event.currentTarget)} onKeyDown={event => handleDayKey(event, index)}>
                  <span className="course-stats__day-track">
                    {timeLoading ? loadingValue : (value === null || value === 0) && <span className="course-stats__day-marker" aria-hidden="true">{value ?? "—"}</span>}
                    {value !== null && value > 0 && chartScale && <i className={segments.length ? "course-stats__bar" : "course-stats__bar is-total-unknown"}
                      data-detached={segments.length === 2 && activeSegment !== undefined} style={{ height: `${value / chartScale.maxMs * 100}%` }}>
                      {segments.map(segment => <span key={segment.id} className="course-stats__bar-segment"
                        data-bar-segment={segment.id} data-active={activeSegment === segment.id} style={{ height: `${segment.fraction * 100}%` }} />)}
                    </i>}
                  </span>
                  <span className="course-stats__day-label">{formatStudyDate(day.date, { day: "numeric" })}</span>
                  <span className="course-stats__day-weekday">{formatStudyDate(day.date, { weekday: "short" })}</span>
                </button>;
              })}
            </div>
          </div>
        </div>
        {chartNotice && <div className={`course-stats__notice${hasTime ? "" : " is-empty"}`} role="status">
          <span>{chartNotice}</span>
          {retryAvailable && <button type="button" onClick={onRetry}>Повторить</button>}
        </div>}
      </div>

      <aside className="course-stats__balance" id={detailId} aria-label="Детали времени занятий" aria-busy={timeLoading}>
        <header className="course-stats__detail-heading">
          <span aria-live="polite">{selectedDay ? detailDateLabel : "За весь период"}</span>
          {selectedDay && <button type="button" onClick={clearDate}>Весь период <X size={14} aria-hidden="true" /></button>}
        </header>
        <div className="course-stats__distribution" data-state={distributionState} data-preview={previewTarget === "ring"}>
          {!ringAvailable && <span className="course-stats__distribution-ring" aria-hidden="true" />}
          <div className="course-stats__distribution-total" aria-live="polite" aria-atomic="true">
            <strong>{timeLoading ? loadingValue : formatStudyTime(detailValues.activeMs)}</strong>
            {metric !== "activeMs" && detailValues.activeMs !== null && <small>всего</small>}
          </div>
          {!timeLoading && <button className="course-stats__distribution-trigger" type="button"
            aria-label={`Разбивка времени. ${selectedDay ? detailDateLabel : "За весь период"}. ${detailBreakdown}`}
            aria-pressed={previewTarget === "ring" && preview?.source === "press"}
            {...ringPreviewEvents}
            onPointerEnter={event => previewRingSegment(event, true)}
            onPointerMove={event => previewRingSegment(event)}
            onClick={event => {
              if (previewTarget === "ring" && preview?.source === "press") dismissPreview();
              else showPreview("ring", event.currentTarget, "press");
            }}
            onKeyDown={event => {
              if (event.key === "Escape" && previewTarget === "ring") {
                event.preventDefault();
                dismissPreview();
              }
            }}>
            {ringAvailable && <DistributionRing theoryShare={theoryShare} active={previewTarget === "ring" ? previewSegment : undefined} />}
          </button>}
        </div>
      </aside>
      {previewValues && <div className="course-stats__chart-preview" key={previewTarget} ref={previewCard} aria-hidden="true"
        onPointerEnter={event => { if (acceptsChartHover(event)) cancelPreviewExit(); }}
        onPointerLeave={event => { if (acceptsChartHover(event) && previewTarget) leavePreview(previewTarget); }}>
        <div className="course-stats__chart-preview-heading">
          <span>{previewLabel}</span>
          <strong>{formatStudyTime(previewValues.activeMs)}</strong>
        </div>
        <dl>
          {chartBreakdown(previewValues).map(row => <div key={row.id} data-segment={row.id} data-spotlight={row.id === previewSegment}>
            <dt><i />{row.label}</dt>
            <dd>{row.share !== null && <small>{row.share}%</small>}<strong>{formatStudyTime(row.ms)}</strong></dd>
          </div>)}
        </dl>
      </div>}
    </section>

    <div className="course-progress-layout">
      <section className="course-stats__card course-progress-layout__placeholder" aria-label="Дашборд">
        <p>Дашборд пока в разработке</p>
      </section>
      <div className="course-progress-layout__panel">
        <CourseStatisticsProgress chapterTime={outlineState} completionTime={credit?.lessons} onChapterRetry={onChapterRetry} data={data} lessons={lessons} snapshot={progressSnapshot}
          metric="activeMs" timeLoading={lessonTimeLoading} lessonRowsComplete={lessonRowsComplete} lessonTimeLoading={lessonTimeLoading} />
      </div>
    </div>
  </div>;
}
