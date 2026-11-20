import { useId, useLayoutEffect, useRef, useState } from "react";
import { Activity, BarChart3, BookCheck, CalendarDays, Check, ChevronDown, Flame, TrendingUp } from "lucide-react";
import { useAccountStatistics } from "./useAccountStatistics";

type CourseLessonProgress = { id: string; title: string; completed: boolean; completedOn?: string | null; historicalCredit?: boolean };
type CourseBlockProgress = {
  id: string;
  title: string;
  completed: number;
  total: number;
  lesson_ids?: string[];
  lessons: CourseLessonProgress[];
};

export interface AccountStatisticsData {
  tracksLoaded: boolean;
  completedLessons: number;
  totalLessons: number;
  overallProgress: number;
  activeDays: number | null;
  currentStreak: number | null;
  weeklyLessonsCompleted: { activity_date: string; lessons_completed: number }[] | null;
  courses: { id: string; title: string; completed: number; total: number; blocks: CourseBlockProgress[] }[];
}

export type WeeklyChartPoint = { activity_date: string; value: number | null };

function formatDayCount(value: number): string {
  const lastTwo = value % 100;
  const last = value % 10;
  const unit = lastTwo >= 11 && lastTwo <= 14 ? "дней" : last === 1 ? "день" : last >= 2 && last <= 4 ? "дня" : "дней";
  return `${value} ${unit}`;
}

function formatWeekday(value: string): { short: string; day: string; full: string } {
  const date = new Date(`${value}T12:00:00Z`);
  if (Number.isNaN(date.getTime())) return { short: "—", day: "—", full: value };
  return {
    short: new Intl.DateTimeFormat("ru-RU", { weekday: "short", timeZone: "Europe/Moscow" }).format(date).replace(".", ""),
    day: new Intl.DateTimeFormat("ru-RU", { day: "numeric", timeZone: "Europe/Moscow" }).format(date),
    full: new Intl.DateTimeFormat("ru-RU", { weekday: "long", day: "numeric", month: "long", timeZone: "Europe/Moscow" }).format(date),
  };
}

function formatLessonCount(value: number): string {
  const lastTwo = value % 100;
  const last = value % 10;
  const unit = lastTwo >= 11 && lastTwo <= 14 ? "уроков" : last === 1 ? "урок" : last >= 2 && last <= 4 ? "урока" : "уроков";
  return `${value} ${unit}`;
}

function formatMinuteCount(value: number): string {
  const lastTwo = value % 100;
  const last = value % 10;
  const unit = lastTwo >= 11 && lastTwo <= 14 ? "минут" : last === 1 ? "минута" : last >= 2 && last <= 4 ? "минуты" : "минут";
  return `${value} ${unit}`;
}

function formatMeasuredTime(seconds: number | null, complete: boolean): string {
  if (seconds === null || (seconds === 0 && !complete)) return "Нет данных";
  if (seconds === 0) return "0 мин";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return [hours ? `${hours} ч` : "", minutes ? `${minutes} мин` : "", remainder ? `${remainder} с` : ""].filter(Boolean).join(" ");
}

function buildSmoothLinePath(points: Array<{ x: number; y: number }>): string {
  if (points.length === 0) return "";
  if (points.length === 1) return `M${points[0].x},${points[0].y}`;

  const slopes = points.slice(0, -1).map((point, index) =>
    (points[index + 1].y - point.y) / (points[index + 1].x - point.x));
  const tangents = Array<number>(points.length).fill(0);
  tangents[0] = slopes[0];
  tangents[tangents.length - 1] = slopes[slopes.length - 1];

  for (let index = 1; index < points.length - 1; index += 1) {
    const previous = slopes[index - 1];
    const next = slopes[index];
    if (previous * next > 0) tangents[index] = (2 * previous * next) / (previous + next);
  }

  const segments = points.slice(0, -1).map((point, index) => {
    const next = points[index + 1];
    const distance = next.x - point.x;
    const control1 = { x: point.x + distance / 3, y: point.y + tangents[index] * distance / 3 };
    const control2 = { x: next.x - distance / 3, y: next.y - tangents[index + 1] * distance / 3 };
    return `C${control1.x},${control1.y} ${control2.x},${control2.y} ${next.x},${next.y}`;
  });

  return `M${points[0].x},${points[0].y} ${segments.join(" ")}`;
}

export function WeeklyTrendChart({ points, title, unit, demo = false, valueDescription, totalDescription }: {
  points: WeeklyChartPoint[] | null;
  title: string;
  unit: "уроков" | "мин";
  demo?: boolean;
  valueDescription?: (value: number) => string;
  totalDescription?: (total: number) => string;
}) {
  const chartPoints = points ?? [];
  const canvasRef = useRef<HTMLDivElement>(null);
  const [chartSize, setChartSize] = useState({ width: 280, height: 120 });
  const [activePointIndex, setActivePointIndex] = useState<number | null>(null);
  const tooltipId = useId();

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || typeof ResizeObserver === "undefined") return;

    const updateSize = () => {
      const { width: nextWidth, height: nextHeight } = canvas.getBoundingClientRect();
      if (nextWidth < 1 || nextHeight < 1) return;
      setChartSize((current) => Math.abs(current.width - nextWidth) < 0.5 && Math.abs(current.height - nextHeight) < 0.5
        ? current
        : { width: nextWidth, height: nextHeight });
    };

    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [chartPoints.length]);

  const width = chartSize.width;
  const height = chartSize.height;
  const padX = Math.min(12, width * 0.05);
  const padTop = Math.min(16, height * 0.12);
  const padBottom = Math.min(12, height * 0.14);
  const plotHeight = height - padTop - padBottom;
  const numericPoints = chartPoints.filter((point): point is WeeklyChartPoint & { value: number } => point.value !== null);
  const maximum = Math.max(1, ...numericPoints.map((point) => point.value));
  const coordinates = chartPoints.flatMap((point, index) => point.value === null ? [] : [{
    ...point,
    value: point.value,
    x: chartPoints.length <= 1 ? width / 2 : padX + index / (chartPoints.length - 1) * (width - padX * 2),
    y: padTop + plotHeight - point.value / maximum * plotHeight,
  }]);
  const linePath = buildSmoothLinePath(coordinates);
  const areaPath = coordinates.length
    ? `${linePath} L${coordinates[coordinates.length - 1].x},${height - padBottom} L${coordinates[0].x},${height - padBottom} Z`
    : "";
  const total = chartPoints.reduce((sum, point) => sum + (point.value ?? 0), 0);
  const describeValue = valueDescription ?? ((value: number) => unit === "мин" ? formatMinuteCount(value) : formatLessonCount(value));
  const describeTotal = totalDescription ?? ((value: number) => demo
    ? `Примерные данные · ${value} мин за 7 дней`
    : `${formatLessonCount(value)} за 7 дней`);
  const activePoint = activePointIndex === null ? null : coordinates[activePointIndex];
  const chartLabel = `${title}: ${chartPoints.map((point) => {
    const weekday = formatWeekday(point.activity_date);
    if (point.value === null) return `${weekday.full} — нет данных`;
    return `${weekday.full} — ${describeValue(point.value)}`;
  }).join("; ")}`;

  return (
    <section className={`account-weekly-trend ${demo ? "is-demo" : ""}`}>
      <header>
        <div><h4>{title}</h4><p>{describeTotal(total)}</p></div>
        {demo && <span className="account-weekly-trend__badge">Демо</span>}
      </header>
      {chartPoints.length ? (
        <div className="account-weekly-trend__plot">
          <div className="account-weekly-trend__canvas" ref={canvasRef}>
            <svg viewBox={`0 0 ${width} ${height}`} role="group" aria-label={chartLabel}>
              <line className="account-weekly-trend__baseline" x1={padX} x2={width - padX} y1={height - padBottom} y2={height - padBottom} aria-hidden="true" />
              {areaPath && <path className="account-weekly-trend__area" d={areaPath} aria-hidden="true" />}
              {linePath && <path className="account-weekly-trend__line" d={linePath} aria-hidden="true" />}
              {coordinates.map((point, index) => {
                const pointLabel = `${formatWeekday(point.activity_date).full}: ${describeValue(point.value)}`;
                const isPeak = maximum > 0 && point.value === maximum;
                return (
                  <g className="account-weekly-trend__point" key={point.activity_date} role="img" tabIndex={0}
                    aria-label={pointLabel} aria-describedby={activePointIndex === index ? tooltipId : undefined}
                    onPointerEnter={() => setActivePointIndex(index)}
                    onPointerLeave={(event) => {
                      if (event.currentTarget !== document.activeElement) setActivePointIndex(null);
                    }}
                    onFocus={() => setActivePointIndex(index)}
                    onBlur={() => setActivePointIndex(null)}>
                    <circle className="account-weekly-trend__point-hit-area" cx={point.x} cy={point.y} r={10} />
                    <circle className={`account-weekly-trend__point-marker ${point.value > 0 ? "is-active" : "is-zero"} ${isPeak ? "is-peak" : ""}`}
                      cx={point.x} cy={point.y} r={isPeak ? 4.5 : point.value > 0 ? 4 : 3} />
                  </g>
                );
              })}
            </svg>
            {activePoint && (
              <div id={tooltipId} className={`account-weekly-trend__tooltip ${activePointIndex === 0 ? "is-start" : ""} ${activePointIndex === coordinates.length - 1 ? "is-end" : ""} ${activePoint.y < padTop + 24 ? "is-below" : ""}`}
                role="tooltip" style={{ left: `${activePoint.x / width * 100}%`, top: `${activePoint.y / height * 100}%` }}>
                <strong>{formatWeekday(activePoint.activity_date).full}</strong>
                <span>{describeValue(activePoint.value)}</span>
              </div>
            )}
          </div>
          <div className="account-weekly-trend__labels" aria-hidden="true">
            {chartPoints.map((point) => (
              <span key={point.activity_date}>
                <strong>{point.value ?? "—"}</strong>
                <small>{formatWeekday(point.activity_date).short}</small>
              </span>
            ))}
          </div>
        </div>
      ) : (
        <p className="account-weekly-trend__empty">Данные появятся после загрузки активности за последние 7 дней.</p>
      )}
    </section>
  );
}

export function AccountStatistics({ data, courseId, userId }: {
  data: AccountStatisticsData;
  courseId: string;
  userId: number;
  analyticsEnabled?: boolean;
}) {
  const [days, setDays] = useState<7 | 30>(7);
  const { status, report, retry } = useAccountStatistics(userId, courseId, days);
  const statsReady = data.tracksLoaded;
  const visibleCourses = data.courses.filter((course) => course.id === courseId);
  const summary = report?.summary;
  const lessonPoints = report?.days.map((day) => ({ activity_date: day.date, value: day.completed_lessons })) ?? null;
  return (
    <div className="account-section-stack account-statistics">
      <div className="account-statistics__period" role="group" aria-label="Период статистики">
        {[7, 30].map((period) => <button type="button" key={period} aria-pressed={days === period} onClick={() => setDays(period as 7 | 30)}>{period} дней</button>)}
      </div>
      {status === "loading" && <p role="status">Загружаем статистику…</p>}
      {status === "error" && <div role="alert" className="account-surface account-detail-card"><p>Не удалось загрузить статистику. Попробуйте ещё раз.</p><button type="button" onClick={retry}>Повторить</button></div>}
      {report && <>
        {report.coverage.visits === "partial" && <p className="account-statistics__note">История посещений доступна не полностью. Общая серия и число дней могут быть недоступны.</p>}
        <div className="account-metrics">
          <section className="account-surface account-metric account-metric--mint"><span className="account-metric__label"><Flame size={17} aria-hidden="true" />Серия посещений</span><strong>{summary?.current_streak == null ? "—" : formatDayCount(summary.current_streak)}</strong><small>дней подряд на платформе</small></section>
          <section className="account-surface account-metric account-metric--mint"><span className="account-metric__label"><Activity size={17} aria-hidden="true" />Дней на платформе</span><strong>{summary?.active_days_total == null ? "—" : summary.active_days_total.toLocaleString("ru-RU")}</strong><small>за всё время</small></section>
          <section className="account-surface account-metric account-metric--blue"><span className="account-metric__label"><BookCheck size={17} aria-hidden="true" />Уроков завершено</span><strong>{summary?.completed_lessons_period}</strong><small>в текущем курсе за {report.period.days} дней</small></section>
        </div>
        <section className="account-surface account-detail-card account-weekly-activity">
          <header><div><h3>Посещения платформы</h3><p>За последние {report.period.days} дней, время Москвы</p></div><span>{summary?.active_days_period == null ? "Итог за период недоступен" : `${summary.active_days_period} из ${report.period.days} дней`}</span></header>
          {summary?.active_days_period === 0 && <p className="account-statistics__note">За этот период посещений пока нет.</p>}
          <div className="account-statistics__days" role="list" aria-label="Посещения платформы по дням">
            {report.days.map((day) => { const label = formatWeekday(day.date); return <div className={`account-week-chart__day ${day.visited ? "is-active" : ""}`} role="listitem" aria-label={`${label.full}: ${day.visited === null ? "нет данных о посещении" : day.visited ? "было посещение" : "без посещения"}`} key={day.date}><span>{label.short}</span><strong>{label.day}</strong><i aria-hidden="true">{day.visited === null ? "?" : day.visited ? <Check size={12} /> : null}</i></div>; })}
          </div>
          <div className="account-statistics__chart"><WeeklyTrendChart points={lessonPoints} title="Завершено уроков в текущем курсе" unit="уроков" /></div>
          <div className="account-statistics__chart">
            <section className="account-weekly-trend" aria-label="Время занятий на платформе">
              <header><div><h4>Время занятий на платформе</h4><p>{report.coverage.time === "measured" ? "Измерено за период" : report.coverage.time === "partial" ? "Имеющиеся измерения за неполный период" : "Измерения недоступны"}: {formatMeasuredTime(summary?.active_seconds ?? null, report.coverage.time === "measured")}</p></div></header>
              <div className="account-weekly-trend__labels" role="list" aria-label="Время занятий по дням">
                {report.days.map((day) => { const label = formatWeekday(day.date); const time = formatMeasuredTime(day.active_seconds, report.coverage.time === "measured"); return <span role="listitem" key={day.date} aria-label={`${label.full}: ${time}`}><strong>{time === "Нет данных" ? "—" : time}</strong><small>{label.short} {label.day}</small></span>; })}
              </div>
            </section>
          </div>
        </section>
      </>}
      <div className="account-detail-grid">
        <section className="account-surface account-detail-card account-course-progress-card">
          <header><div><h3>Прогресс текущего курса</h3><p>Выполненные уроки и блоки</p></div></header>
          {visibleCourses.length ? (
            <div className="account-course-list">
              {visibleCourses.map((course) => {
                const percent = course.total ? Math.round(course.completed / course.total * 100) : 0;
                return (
                  <details className="account-course-accordion" key={course.id}>
                    <summary>
                      <span className="account-course-accordion__heading">
                        <strong>{course.title}</strong>
                        <span>{course.completed} / {course.total}</span>
                      </span>
                      <ChevronDown className="account-course-accordion__chevron" size={18} aria-hidden="true" />
                      <span className="account-progress-track" role="progressbar" aria-label={`Прогресс: ${course.title}`}
                        aria-valuemin={0} aria-valuemax={Math.max(1, course.total)}
                        aria-valuenow={Math.max(0, Math.min(course.completed, course.total))}>
                        <span style={{ width: `${Math.max(0, Math.min(100, percent))}%` }} />
                      </span>
                    </summary>
                    {course.blocks.length ? (
                      <div className="account-course-accordion__blocks">
                        {course.blocks.map((block) => {
                          const blockPercent = block.total ? Math.round(block.completed / block.total * 100) : 0;
                          return (
                            <details className="account-course-block" key={block.id}>
                              <summary>
                                <span className="account-course-block__heading">
                                  <strong>{block.title}</strong>
                                  <span>{block.completed} / {block.total}</span>
                                </span>
                                <ChevronDown className="account-course-block__chevron" size={16} aria-hidden="true" />
                                <span className="account-progress-track" role="progressbar"
                                  aria-label={`Прогресс блока ${block.title} в курсе ${course.title}`}
                                  aria-valuemin={0} aria-valuemax={Math.max(1, block.total)}
                                  aria-valuenow={Math.max(0, Math.min(block.completed, block.total))}>
                                  <span style={{ width: `${Math.max(0, Math.min(100, blockPercent))}%` }} />
                                </span>
                              </summary>
                              {block.lessons.length ? (
                                <ul className="account-course-block__lessons">
                                  {block.lessons.map((lesson) => (
                                    <li className="account-course-lesson" key={lesson.id}>
                                      <span className={`account-course-lesson__mark ${lesson.completed ? "is-complete" : ""}`} aria-hidden="true">
                                        {lesson.completed && <Check size={12} strokeWidth={2.7} />}
                                      </span>
                                      <span className="account-course-lesson__title">{lesson.title}</span>
                                      <span className={`account-course-lesson__status ${lesson.completed ? "is-complete" : ""}`}>
                                        {lesson.completed ? "Выполнено" : "Не выполнено"}
                                      </span>
                                    </li>
                                  ))}
                                </ul>
                              ) : (
                                <p className="account-course-block__empty">Список занятий сейчас недоступен.</p>
                              )}
                            </details>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="account-course-accordion__empty">Прогресс по блокам появится после обновления каталога.</p>
                    )}
                  </details>
                );
              })}
            </div>
          ) : (
            <div className="account-empty-state account-empty-state--compact">
              <span className="account-empty-state__icon"><BookCheck size={24} aria-hidden="true" /></span>
              <strong>{statsReady ? "Курсы ещё не добавлены" : "Загружаем курсы"}</strong>
              <p>{statsReady ? "Прогресс появится здесь после начала обучения." : "Подождите немного — скоро покажем ваши результаты."}</p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
