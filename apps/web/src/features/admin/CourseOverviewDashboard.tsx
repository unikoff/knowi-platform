import "./overview/overview.css";
import "../account/course-statistics.css";
import "./course-overview-trends.css";
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { BookOpenCheck, Clock3, Users } from "lucide-react";
import { useCourseStudyTime, type CourseStudyTimeContext, type StudyTimeDay } from "./course-study-time";

type SubscriptionGroup = "with_subscription" | "without_subscription";
type TrendMode = "all" | SubscriptionGroup;
type GroupValues = Record<SubscriptionGroup, number | null>;
type DailyTrend = { date: string } & GroupValues;
type ChartDay = DailyTrend & { learners?: GroupValues };
type Trend = { status: "available" | "unavailable"; quality?: "unknown" | "complete" | null; daily: DailyTrend[] };

export type CourseTrends = { visits: Trend; completions: Trend; estimated_completion_time?: Trend | null };

const trendMetrics = {
  visits: { label: "Ученики", icon: Users, title: "Уникальные посетители курса по дням", emptyMessage: "Посещений не было" },
  completions: { label: "Обучение", icon: BookOpenCheck, title: "Завершённые уроки по дням", emptyMessage: "Завершений не было" },
  studyTime: { label: "Время учеников", icon: Clock3, title: "Время занятий учеников по дням", emptyMessage: "Нет зафиксированных данных" },
  estimated_completion_time: { label: "Условное время", icon: Clock3, title: "Условное время за завершённые занятия по дням", emptyMessage: "Нет завершённых занятий за этот период" },
};
const metricKeys = ["visits", "completions", "studyTime", "estimated_completion_time"] as const;

const subscriptionGroups = [
  { key: "with_subscription", label: "С подпиской" },
  { key: "without_subscription", label: "Без подписки" },
] as const;
const trendModes = [{ key: "all", label: "Все" }, ...subscriptionGroups] as const;

function dailyValue(day: GroupValues, mode: TrendMode) {
  if (mode !== "all") return day[mode];
  if (day.with_subscription === null && day.without_subscription === null) return null;
  return (day.with_subscription ?? 0) + (day.without_subscription ?? 0);
}

const numberFormat = new Intl.NumberFormat("ru-RU");
const subsecondFormat = new Intl.NumberFormat("ru-RU", { maximumSignificantDigits: 3 });
const chartDateFormat = new Intl.DateTimeFormat("ru-RU", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });
const shortWeekdayFormat = new Intl.DateTimeFormat("ru-RU", { weekday: "short", timeZone: "UTC" });
const averageLabel = "Среднее на ученика с измеренным временем";

function chartDate(value: string) {
  return new Date(`${value.slice(0, 10)}T00:00:00Z`);
}

function formatDuration(ms: number) {
  if (ms === 0) return "0";
  if (ms < 1000) return `${subsecondFormat.format(ms)} мс`;
  const totalSeconds = Math.round(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor(totalSeconds / 60) % 60;
  const seconds = totalSeconds % 60;
  return [hours && `${hours} ч`, minutes && `${minutes} мин`, seconds && `${seconds} с`].filter(Boolean).join(" ");
}

function toStudyTimeChartDay(day: StudyTimeDay): ChartDay {
  return {
    date: day.date,
    with_subscription: day.with_subscription?.active_ms ?? null,
    without_subscription: day.without_subscription?.active_ms ?? null,
    learners: {
      with_subscription: day.with_subscription?.learners ?? null,
      without_subscription: day.without_subscription?.learners ?? null,
    },
  };
}

function formatAverage(day: ChartDay, mode: TrendMode) {
  const observedTime = dailyValue(day, mode);
  const observedLearners = day.learners && dailyValue(day.learners, mode);
  return observedTime !== null && observedLearners ? formatDuration(observedTime / observedLearners) : "Нет данных";
}

function displayTrendValue(day: DailyTrend, mode: TrendMode, formatValue: (value: number) => string) {
  const value = dailyValue(day, mode);
  if (value === null) return "Нет данных";
  if (isPartialTotal(day, mode)) {
    return `Зафиксировано ${formatValue(value)}`;
  }
  return formatValue(value);
}

function formatDateRange(startValue: string, endValue: string) {
  const parseDate = (value: string) => {
    const match = value.slice(0, 10).match(/^([0-9]{4})-([0-9]{2})-([0-9]{2})$/);
    if (!match) return null;
    return new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  };
  const start = parseDate(startValue);
  const end = parseDate(endValue);
  if (!start || !end) return "";

  const shortDate = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", timeZone: "UTC" });
  const fullDate = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
  const startLabel = start.getUTCFullYear() === end.getUTCFullYear()
    ? shortDate.format(start)
    : fullDate.format(start);
  return `${startLabel} — ${fullDate.format(end)}`;
}

function getChartScale(peak: number | null) {
  if (peak === null) return { maximum: 1, ticks: [] };
  if (peak === 0) return { maximum: 1, ticks: [0] };

  const divisions = Math.min(peak, 4);
  const ticks = Array.from({ length: divisions + 1 }, (_, index) => Math.round(peak * index / divisions));
  return { maximum: peak, ticks };
}

function isPartialTotal(day: DailyTrend, mode: TrendMode) {
  return mode === "all" && (day.with_subscription === null || day.without_subscription === null);
}

type DayPreview = { date: string; segment?: SubscriptionGroup };

function TrendChart({ daily, title, mode, emptyMessage, quality, isStudyTime, isEstimated }: {
  daily: ChartDay[];
  title: string;
  mode: TrendMode;
  emptyMessage: string;
  quality?: Trend["quality"];
  isStudyTime: boolean;
  isEstimated?: boolean;
}) {
  const lastDate = daily[daily.length - 1]?.date;
  const [focusedDate, setFocusedDate] = useState(lastDate);
  const [selectedDate, setSelectedDate] = useState<string>();
  const [preview, setPreview] = useState<DayPreview>();
  const figure = useRef<HTMLElement>(null);
  const scrollArea = useRef<HTMLDivElement>(null);
  const tooltip = useRef<HTMLDivElement>(null);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const activeDate = preview?.date ?? selectedDate;
  const activeDay = daily.find(day => day.date === activeDate);
  const tabStop = daily.some(day => day.date === focusedDate) ? focusedDate : lastDate;
  const groups = subscriptionGroups.filter(group => mode === "all" || mode === group.key);
  const observed = daily.map(day => dailyValue(day, mode)).filter(value => value !== null);
  const peak = observed.length ? Math.max(...observed) : null;
  const scale = getChartScale(peak);
  const hasPositive = peak !== null && peak > 0;
  const hasUnknown = daily.some(day => dailyValue(day, mode) === null || isPartialTotal(day, mode));
  const formatValue = isStudyTime || isEstimated ? formatDuration : numberFormat.format;

  useEffect(() => {
    if (scrollArea.current) scrollArea.current.scrollLeft = scrollArea.current.scrollWidth;
  }, [daily]);

  useEffect(() => {
    const chart = figure.current;
    const card = tooltip.current;
    const anchor = activeDate ? buttons.current.get(activeDate) : null;
    const viewport = scrollArea.current;
    if (!chart || !card || !anchor || !viewport) return;

    const positionTooltip = () => {
      const chartBounds = chart.getBoundingClientRect();
      const anchorBounds = anchor.getBoundingClientRect();
      const viewportBounds = viewport.getBoundingClientRect();
      const valueBounds = anchor.querySelector(".course-stats__bar, .course-stats__day-marker")?.getBoundingClientRect();
      if (anchorBounds.right <= viewportBounds.left || anchorBounds.left >= viewportBounds.right
        || anchorBounds.bottom <= 0 || anchorBounds.top >= window.innerHeight) {
        card.style.visibility = "hidden";
        return;
      }

      const width = card.offsetWidth;
      const height = card.offsetHeight;
      const leftEdge = Math.max(chartBounds.left + 8, 8);
      const rightEdge = Math.min(chartBounds.right - 8, window.innerWidth - 8);
      const left = Math.max(leftEdge, Math.min(rightEdge - width, anchorBounds.left + (anchorBounds.width - width) / 2));
      const anchorTop = valueBounds?.top ?? anchorBounds.bottom;
      const above = anchorTop - height - 10;
      const top = Math.max(8, Math.min(window.innerHeight - height - 8, above >= 8 ? above : anchorTop + 8));
      card.style.left = `${left - chartBounds.left}px`;
      card.style.top = `${top - chartBounds.top}px`;
      card.style.visibility = "visible";
    };
    const closeOnOutside = (event: Event) => {
      if (!(event.target instanceof Node) || chart.contains(event.target)) return;
      setPreview(undefined);
      setSelectedDate(undefined);
    };
    const dismissOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setPreview(undefined);
      setSelectedDate(undefined);
    };

    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(positionTooltip) : null;
    observer?.observe(chart);
    observer?.observe(card);
    document.addEventListener("scroll", positionTooltip, true);
    window.addEventListener("resize", positionTooltip);
    document.addEventListener("pointerdown", closeOnOutside);
    document.addEventListener("focusin", closeOnOutside);
    document.addEventListener("keydown", dismissOnEscape);
    positionTooltip();
    return () => {
      observer?.disconnect();
      document.removeEventListener("scroll", positionTooltip, true);
      window.removeEventListener("resize", positionTooltip);
      document.removeEventListener("pointerdown", closeOnOutside);
      document.removeEventListener("focusin", closeOnOutside);
      document.removeEventListener("keydown", dismissOnEscape);
    };
  }, [activeDate]);

  function moveFocus(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number;
    switch (event.key) {
      case "ArrowLeft": next = Math.max(0, index - 1); break;
      case "ArrowRight": next = Math.min(daily.length - 1, index + 1); break;
      case "Home": next = 0; break;
      case "End": next = daily.length - 1; break;
      default: return;
    }
    event.preventDefault();
    const date = daily[next].date;
    setFocusedDate(date);
    setPreview({ date });
    buttons.current.get(date)?.focus();
  }

  function previewWithPointer(event: PointerEvent<HTMLButtonElement>, date: string) {
    if (event.pointerType !== "mouse" || !window.matchMedia("(any-hover: hover)").matches) return;
    const key = (event.target as HTMLElement).dataset?.barSegment;
    const segment = key === "with_subscription" || key === "without_subscription" ? key : undefined;
    setPreview(current => current?.date === date && current.segment === segment ? current : { date, segment });
  }

  return (
    <figure className="course-trend-chart course-stats" ref={figure} aria-label={title}>
      <figcaption className="course-trend-chart__legend">
        {groups.map(group => <span key={group.key} data-segment={group.key}><i aria-hidden="true" />{group.label}</span>)}
        {!hasPositive && <span>{hasUnknown || quality === "unknown" ? "Нет зафиксированных данных" : emptyMessage}</span>}
      </figcaption>

      <div className="course-trend-chart__graphic">
        <div className="course-trend-chart__axis" aria-hidden="true">
          {scale.ticks.map(tick => <span key={tick} style={{ bottom: `${100 * tick / scale.maximum}%` }}>{formatValue(tick)}</span>)}
        </div>
        <div className="course-trend-chart__plot">
          <div className="course-trend-chart__grid" aria-hidden="true">
            {scale.ticks.filter(tick => tick > 0).map(tick => (
              <div key={tick} style={{ bottom: `${100 * tick / scale.maximum}%` }} />
            ))}
          </div>
          <div className="course-stats__chart-scroll" ref={scrollArea}>
            <div className="course-stats__bars" role="group" aria-label={`${title}. Стрелки перемещают между днями. Enter выбирает день, Escape закрывает подсказку.`}
              style={{ gridTemplateColumns: `repeat(${daily.length}, minmax(0, 1fr))`, minWidth: `${daily.length * 24}px` }}>
              {daily.map((day, index) => {
                const date = chartDate(day.date);
                const value = dailyValue(day, mode);
                const summary = displayTrendValue(day, mode, formatValue);
                const isPreviewed = preview?.date === day.date;
                const isSplit = mode === "all" && subscriptionGroups.every(group => (day[group.key] ?? 0) > 0);
                const breakdown = mode === "all"
                  ? subscriptionGroups.map(group => {
                      const cohort = day[group.key];
                      return `${group.label}: ${cohort === null ? "нет данных" : formatValue(cohort)}`;
                    })
                  : [];
                if (isStudyTime) breakdown.push(`${averageLabel}: ${formatAverage(day, mode)}`);
                return <button key={day.date} type="button" className="course-stats__day"
                  ref={node => { if (node) buttons.current.set(day.date, node); else buttons.current.delete(day.date); }}
                  tabIndex={day.date === tabStop ? 0 : -1} aria-pressed={selectedDate === day.date} data-preview={activeDate === day.date}
                  aria-label={[chartDateFormat.format(date), summary, ...breakdown].join(", ")}
                  onPointerEnter={event => previewWithPointer(event, day.date)}
                  onPointerMove={event => previewWithPointer(event, day.date)}
                  onPointerLeave={() => setPreview(current => current?.date === day.date ? undefined : current)}
                  onFocus={event => {
                    setFocusedDate(day.date);
                    if (event.currentTarget.matches(":focus-visible")) setPreview({ date: day.date });
                  }}
                  onBlur={() => setPreview(current => current?.date === day.date ? undefined : current)}
                  onClick={() => {
                    setSelectedDate(current => current === day.date ? undefined : day.date);
                    setPreview(undefined);
                  }}
                  onKeyDown={event => moveFocus(event, index)}>
                  <span className="course-stats__day-track" aria-hidden="true">
                    {value !== null && value > 0 ? (
                      <span className="course-stats__bar" data-detached={isSplit && isPreviewed && preview.segment !== undefined}
                        style={{ height: `${100 * value / scale.maximum}%` }}>
                        {groups.map(group => {
                          const count = day[group.key];
                          return count !== null && count > 0 && <span key={group.key} className="course-stats__bar-segment"
                            data-bar-segment={group.key} data-active={preview?.date === day.date && preview.segment === group.key}
                            style={{ height: `${100 * count / value}%` }} />;
                        })}
                      </span>
                    ) : <span className="course-stats__day-marker">{value === null ? "—" : formatValue(value)}</span>}
                  </span>
                  <span className="course-stats__day-label" aria-hidden="true">{date.getUTCDate()}</span>
                  <span className="course-stats__day-weekday" aria-hidden="true">{shortWeekdayFormat.format(date)}</span>
                </button>;
              })}
            </div>
          </div>
        </div>
      </div>

      {activeDay && (
        <div className="course-stats__chart-preview course-trend-chart__preview" ref={tooltip} aria-hidden="true">
          <div className="course-stats__chart-preview-heading">
            <span>{chartDateFormat.format(chartDate(activeDay.date))}</span>
            <strong>{displayTrendValue(activeDay, mode, formatValue)}</strong>
          </div>
          {isStudyTime && <div className="course-trend-chart__average">
            <span>{averageLabel}</span>
            <strong>{formatAverage(activeDay, mode)}</strong>
          </div>}
          <dl>
            {groups.map(group => {
              const count = activeDay[group.key];
              const total = dailyValue(activeDay, "all");
              const percentage = mode === "all" && !isPartialTotal(activeDay, mode)
                && total !== null && total > 0 && count !== null ? Math.round(100 * count / total) : null;
              return <div key={group.key} data-segment={group.key} data-spotlight={preview?.segment === group.key}>
                <dt><i />{group.label}</dt>
                <dd>{percentage !== null && <small>{percentage}%</small>}<strong>{count === null ? "Нет данных" : formatValue(count)}</strong></dd>
              </div>;
            })}
          </dl>
        </div>
      )}
    </figure>
  );
}

const studyTimeMessages = {
  loading: "Загружаем время учеников…",
  unavailable: "Время учеников за этот период пока недоступно.",
  denied: "Нет доступа к данным о времени учеников.",
  error: "Не удалось загрузить время учеников.",
};

export function CourseOverviewDashboard({ trends, studyTimeContext }: { trends?: CourseTrends; studyTimeContext?: CourseStudyTimeContext }) {
  const [metric, setMetric] = useState<(typeof metricKeys)[number]>("visits");
  const [mode, setMode] = useState<TrendMode>("all");
  const chartId = useId();
  const { label, icon: Icon, title, emptyMessage } = trendMetrics[metric];
  const isStudyTime = metric === "studyTime";
  const studyTime = useCourseStudyTime(isStudyTime ? studyTimeContext : undefined);
  const studyTimeDaily = useMemo(() => studyTime.view.status === "available" ? studyTime.view.daily.map(toStudyTimeChartDay) : [], [studyTime.view]);
  const trend = isStudyTime ? undefined : trends?.[metric];
  const daily = isStudyTime ? studyTimeDaily : trend?.status === "available" ? trend.daily : [];
  const quality = isStudyTime ? "unknown" : trend?.quality;
  const firstDay = daily[0];
  const lastDay = daily[daily.length - 1];
  const timeStatus = isStudyTime && studyTime.view.status !== "available" ? studyTime.view.status : undefined;

  return (
    <section className="admin-overview" aria-label="Обзор показателей курса">
      <section className="admin-overview__group">
        <div className="admin-overview__group-heading course-trend__metric-heading">
          <h2><Icon size={17} aria-hidden="true" /> {label}</h2>
          <div className="course-stats__segments course-stats__segments--small" role="group" aria-label="Показатель графика">
            {metricKeys.filter(key => key !== "estimated_completion_time" || trends?.estimated_completion_time).map(key => (
              <button type="button" key={key} aria-pressed={metric === key} aria-controls={chartId}
                onClick={() => setMetric(key)}>{trendMetrics[key].label}</button>
            ))}
          </div>
        </div>
        <div className="course-trend" id={chartId}>
          <div className="course-trend__heading">
            <div>
              <h3>{title}</h3>
              <span>{firstDay && lastDay ? formatDateRange(firstDay.date, lastDay.date) : "Последние 30 дней"}</span>
            </div>
            {daily.length > 0 && <div className="course-stats__segments course-stats__segments--small" role="group" aria-label="Статус подписки">
              {trendModes.map(option => (
                <button type="button" key={option.key} aria-pressed={mode === option.key}
                  onClick={() => setMode(option.key)}>{option.label}</button>
              ))}
            </div>}
          </div>
          {daily.length > 0 ? (
            <TrendChart key={`${metric}:${mode}`} daily={daily} title={title} mode={mode} emptyMessage={emptyMessage} quality={quality} isStudyTime={isStudyTime} isEstimated={metric === "estimated_completion_time"} />
          ) : timeStatus === "error" ? (
            <div className="form-error" role="alert">{studyTimeMessages.error} <button className="admin-back" type="button" onClick={studyTime.retry}>Повторить</button></div>
          ) : (
            <p className="course-trend__unavailable" role="status">{timeStatus ? studyTimeMessages[timeStatus] : "Данные за этот период пока недоступны."}</p>
          )}
        </div>
      </section>
    </section>
  );
}
