import { useId, useMemo, useState } from "react";
import { BookOpen, Check, ChevronDown, Search } from "lucide-react";
import type { AccountStatisticsData } from "./AccountStatistics";
import type { ChapterTimeState } from "./useCourseChapterAnalytics";
import { formatStudyDate, formatStudyTime, type CourseLessonTime, type CourseTimeSnapshot, type ProgressLesson, type TimeMetric } from "./course-statistics-model";
import { completeBlockLessonIds, sumBlockTime } from "./course-progress-block-time";
import "./course-statistics-progress.css";

type Lesson = ProgressLesson & { completionKnown: boolean };
type Chapter = { key: string; id: string; title: string; lessons: Lesson[] };
const lessonMetrics = [
  { key: "activeMs", label: "Всего" },
  { key: "theoryMs", label: "Теория" },
  { key: "practiceMs", label: "Практика" },
] as const;

function completionDate(value: string | null | undefined): string | null {
  if (!value) return null;
  try { return formatStudyDate(value, { day: "2-digit", month: "2-digit", year: "numeric" }); }
  catch { return null; }
}

export function CourseStatisticsProgress({ data, lessons, snapshot, metric, chapterTime, completionTime, onChapterRetry, timeLoading = false, lessonTimeLoading = timeLoading, lessonRowsComplete = false }: {
  chapterTime?: ChapterTimeState;
  onChapterRetry?: () => void;
  data: AccountStatisticsData;
  lessons: readonly Lesson[];
  snapshot: CourseTimeSnapshot;
  completionTime?: readonly CourseLessonTime[];
  metric: TimeMetric;
  timeLoading?: boolean;
  lessonTimeLoading?: boolean;
  /** True only when snapshot.lessons covers the full current lesson report. */
  lessonRowsComplete?: boolean;
}) {
  const prefix = useId();
  const [query, setQuery] = useState("");
  const [openChapters, setOpenChapters] = useState<Set<string>>(() => new Set());
  const [openBlocks, setOpenBlocks] = useState<Set<string>>(() => new Set());
  const [searchChapters, setSearchChapters] = useState<Record<string, boolean>>({});
  const [searchBlocks, setSearchBlocks] = useState<Record<string, boolean>>({});
  const chapters = useMemo(() => {
    const result: Chapter[] = [];
    const byKey = new Map<string, Chapter>();
    const add = (id: string, title: string) => {
      const key = id || "uncategorized";
      let chapter = byKey.get(key);
      if (!chapter) { chapter = { key, id, title, lessons: [] }; byKey.set(key, chapter); result.push(chapter); }
      return chapter;
    };
    // Preserve the source chapter order, including chapters with no lessons.
    for (const chapter of data.courses) add(chapter.id, chapter.title);
    for (const lesson of lessons) add(lesson.trackId, lesson.chapterTitle).lessons.push(lesson);
    return result;
  }, [data.courses, lessons]);
  const times = useMemo(() => new Map(snapshot.lessons.map(item => [item.lessonId, item])), [snapshot.lessons]);
  const migratedTimes = useMemo(() => new Map(completionTime?.map(item => [item.lessonId, item])), [completionTime]);
  const allLessonRowsLoaded = lessonRowsComplete && !lessonTimeLoading;
  const chapterLoading = chapterTime?.status === "loading" || (chapterTime?.status === "pending" && lessonTimeLoading);
  const loadingNotice = timeLoading ? ""
    : chapterLoading && lessonTimeLoading ? "Загружаем время глав и занятий"
    : chapterLoading ? "Загружаем время глав"
    : lessonTimeLoading ? "Загружаем время занятий" : "";
  const loadingValue = <span className="course-stats__loading" aria-hidden="true" />;
  const search = query.trim().toLocaleLowerCase("ru-RU");
  const toggle = (setter: typeof setOpenChapters, key: string) => setter(previous => {
    const next = new Set(previous);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });
  const toggleSearch = (setter: typeof setSearchChapters, key: string, expanded: boolean) =>
    setter(previous => ({ ...previous, [key]: !expanded }));
  const metricLabel = metric === "theoryMs" ? "Теория" : metric === "practiceMs" ? "Практика" : "Время";
  const visible = chapters.map(chapter => {
    const chapterMatches = chapter.title.toLocaleLowerCase("ru-RU").includes(search);
    const blockMatches = data.courses.find(item => item.id === chapter.id)?.blocks.some(block => block.title.toLocaleLowerCase("ru-RU").includes(search));
    const matching = search && !chapterMatches ? chapter.lessons.filter(lesson => `${lesson.blockTitle} ${lesson.title}`.toLocaleLowerCase("ru-RU").includes(search)) : chapter.lessons;
    return { ...chapter, matching, chapterMatches, visible: !search || chapterMatches || blockMatches || matching.length > 0 };
  }).filter(chapter => chapter.visible);

  return <section className="course-stats__card course-progress" aria-label="Прогресс курса">
    <header className="course-stats__card-heading course-progress__toolbar">
      <h3>Прогресс курса</h3>
      <label className="course-progress__search">
        <Search size={17} aria-hidden="true" />
        <input type="search" aria-label="Найти главу, блок или занятие" placeholder="Найти главу, блок или занятие" value={query} onChange={event => { setQuery(event.target.value); setSearchChapters({}); setSearchBlocks({}); }} />
      </label>
    </header>
    <span className="course-stats__sr-only" role="status">{loadingNotice}</span>
    {chapterTime && ["error", "unavailable"].includes(chapterTime.status) && <div className="course-stats__notice" role="status">
      <span>Время по главам и занятиям временно недоступно.</span>
      {onChapterRetry && <button type="button" onClick={onChapterRetry}>Повторить</button>}
    </div>}
    {chapterTime?.status === "denied" && <p className="course-stats__notice" role="status">Нет доступа к времени по главам и занятиям.</p>}
    {chapterTime?.status === "stale" && <p className="course-stats__notice" role="status">Время показано по прошлым замерам.</p>}
    {chapterTime?.report?.quality === "partial" && <p className="course-stats__notice" role="status">Замеры времени доступны частично.</p>}
    {visible.length ? <div className="course-progress__chapters">{visible.map(chapter => {
      const chapterValues = chapterTime?.report?.chapters.find(item => item.chapter_id === chapter.id);
      const chapterValue = metric === "theoryMs" ? chapterValues?.theory_ms : metric === "practiceMs" ? chapterValues?.practice_ms : chapterValues?.active_ms;
      const sourceChapter = data.courses.find(item => item.id === chapter.id);
      const source = data.tracksLoaded ? sourceChapter : undefined;
      const blockMembership = new Map(source?.blocks.map(block => [block.id, completeBlockLessonIds(block)]));
      const members = [...blockMembership.values()].flatMap(ids => ids ?? []);
      const chapterHasMembership = !!source && blockMembership.size > 0 && members.length >= source.total
        && new Set(members).size === members.length && [...blockMembership.values()].every(ids => ids !== undefined);
      // The current chapter contract sends null instead of zero for a successful sum with unknown coverage.
      const chapterReportReady = chapterTime?.status === "ready" && (chapterTime.report?.status === "ok" || chapterTime.report?.status === "no_data");
      const showChapterTime = chapterTime !== undefined || allLessonRowsLoaded;
      let chapterTimeValue = chapterValue ?? null;
      if (!chapterTime && allLessonRowsLoaded) {
        chapterTimeValue = sumBlockTime(chapter.lessons.map(lesson => lesson.id), times, metric, source?.total, true);
      } else if ((chapterValue === 0 || chapterValue === null) && chapterHasMembership && (chapterReportReady || (chapterTime?.status === "stale" && chapterValue === 0))) {
        chapterTimeValue = 0;
      } else if (chapterValue === 0) {
        chapterTimeValue = null;
      }
      const migratedTime = chapter.lessons.reduce((total, lesson) => total + (migratedTimes.get(lesson.id)?.[metric] ?? 0), 0);
      if (migratedTime > 0 && (chapterTime || !allLessonRowsLoaded)) chapterTimeValue = (chapterTimeValue ?? 0) + migratedTime;
      const countKnown = !!source && source.total > 0;
      const percent = countKnown ? Math.max(0, Math.min(100, source.completed / source.total * 100)) : null;
      const expanded = search ? searchChapters[chapter.key] ?? true : openChapters.has(chapter.key);
      const panelId = `${prefix}-chapter-${encodeURIComponent(chapter.key)}`;
      const blocks = new Map<string, { id: string; title: string; lessons: Lesson[] }>();
      for (const block of sourceChapter?.blocks ?? []) blocks.set(block.id, { id: block.id, title: block.title, lessons: [] });
      for (const lesson of chapter.lessons) {
        const key = lesson.blockId || "uncategorized";
        if (!blocks.has(key)) blocks.set(key, { id: lesson.blockId, title: lesson.blockTitle, lessons: [] });
        blocks.get(key)!.lessons.push(lesson);
      }
      const matchingIds = new Set(chapter.matching.map(lesson => lesson.id));
      const visibleBlocks = [...blocks.entries()].map(([key, block]) => ({ ...block, key, matching: block.lessons.filter(lesson => matchingIds.has(lesson.id)) }))
        .filter(block => !search || chapter.chapterMatches || block.title.toLocaleLowerCase("ru-RU").includes(search) || block.matching.length > 0);
      return <div className="course-progress__chapter" key={chapter.key}>
        <button type="button" className="course-progress__chapter-button" aria-expanded={expanded} aria-controls={panelId} onClick={() => {
          if (search) toggleSearch(setSearchChapters, chapter.key, expanded);
          else toggle(setOpenChapters, chapter.key);
        }}>
          <span className={`course-progress__chapter-top${showChapterTime ? " has-time" : ""}`}>
            <span className="course-progress__disclosure" aria-hidden="true"><ChevronDown size={17} /></span>
            <strong className="course-progress__heading-title">{chapter.title || "Другие занятия"}</strong>
            <span className="course-progress__completion">
              <span className="course-progress__chapter-count">{countKnown ? <><span className="course-stats__sr-only">Пройдено </span>{`${source.completed} / ${source.total}`}</> : "Нет данных"}</span>
              <span className={`course-progress__bar${percent === null ? " is-unknown" : ""}`} aria-hidden="true">{percent !== null && <i style={{ width: `${percent}%` }} />}</span>
            </span>
            {(showChapterTime || migratedTime > 0) && <span className="course-progress__time"><small>{metricLabel}</small><strong>{chapterLoading && !migratedTime ? loadingValue : formatStudyTime(chapterTimeValue)}</strong></span>}
          </span>
        </button>
        <div id={panelId} hidden={!expanded} className="course-progress__chapter-content">
          {visibleBlocks.length ? visibleBlocks.map(block => {
            const blockKey = JSON.stringify([chapter.key, block.key]);
            const blockOpen = search ? searchBlocks[blockKey] ?? true : openBlocks.has(blockKey);
            const blockPanelId = `${panelId}-block-${encodeURIComponent(block.key)}`;
            const blockSource = source?.blocks.find(item => item.id === block.id);
            const blockMemberIds = blockMembership.get(block.id);
            const overviewLessons = blockSource && blockSource.total === 0 && /^00(?:\s|$)/.test(blockSource.title.trim())
              && blockMemberIds && blockMemberIds.length > 0 && blockSource.lessons.length === blockMemberIds.length
              && new Set(blockSource.lessons.map(lesson => lesson.id)).size === blockMemberIds.length
              ? blockSource.lessons : null;
            const blockTotal = overviewLessons?.length ?? blockSource?.total ?? 0;
            const blockCompleted = overviewLessons ? overviewLessons.filter(lesson => lesson.completed).length : blockSource?.completed ?? 0;
            const blockCountKnown = !!blockSource && blockTotal > 0;
            const blockPercent = blockCountKnown ? Math.max(0, Math.min(100, blockCompleted / blockTotal * 100)) : null;
            const blockIds = blockMemberIds ?? block.lessons.map(lesson => lesson.id);
            const blockTime = sumBlockTime(blockIds, times, metric, blockSource?.total, allLessonRowsLoaded && blockMemberIds !== undefined)
              ?? (blockIds.some(id => migratedTimes.has(id)) ? sumBlockTime(blockIds.filter(id => times.get(id)?.[metric] != null), times, metric) : null);
            return <div className="course-progress__block" key={blockKey}>
              <button type="button" className="course-progress__block-button" aria-expanded={blockOpen} aria-controls={blockPanelId} onClick={() => {
                if (search) toggleSearch(setSearchBlocks, blockKey, blockOpen);
                else toggle(setOpenBlocks, blockKey);
              }}>
                <span className="course-progress__block-top">
                  <span className="course-progress__disclosure" aria-hidden="true"><ChevronDown size={15} /></span>
                  <strong className="course-progress__heading-title">{block.title || "Другие занятия"}</strong>
                  <span className="course-progress__completion">
                    <span className="course-progress__chapter-count">{blockCountKnown ? <><span className="course-stats__sr-only">Пройдено </span>{`${blockCompleted} / ${blockTotal}`}</> : "Нет данных"}</span>
                    <span className={`course-progress__bar${blockPercent === null ? " is-unknown" : ""}`} aria-hidden="true">{blockPercent !== null && <i style={{ width: `${blockPercent}%` }} />}</span>
                  </span>
                  <span className="course-progress__time"><small>{metricLabel}</small><strong>{lessonTimeLoading ? loadingValue : formatStudyTime(blockTime)}</strong></span>
                </span>
              </button>
              <div className="course-progress__block-content" id={blockPanelId} hidden={!blockOpen}>
                {block.matching.length ? <ul className="course-progress__lessons" role="list">
                  {block.matching.map(lesson => {
                    const time = times.get(lesson.id);
                    const completed = lesson.completionKnown && lesson.completed;
                    const status = !lesson.completionKnown ? "Статус недоступен" : completed ? "Пройдено" : "Не пройдено";
                    const date = completed ? completionDate(lesson.completedOn) : null;
                    return <li className="course-progress__lesson" data-completed={completed} key={lesson.id}>
                      <div className="course-progress__lesson-identity">
                        <span className={`course-progress__mark${completed ? " is-done" : ""}`} aria-hidden="true">{completed ? <Check size={15} /> : <BookOpen size={15} />}</span>
                        <div className="course-progress__lesson-name"><strong>{lesson.title}</strong><small>{status}{date && <> <span aria-hidden="true">·</span> <time dateTime={lesson.completedOn!}>{date}</time></>}</small></div>
                      </div>
                      <dl className="course-progress__lesson-time" aria-label="Время за всё время" aria-busy={lessonTimeLoading || undefined}>
                        {lessonMetrics.map(({ key, label }) => {
                          const value = time === undefined && allLessonRowsLoaded ? 0 : time?.[key] ?? null;
                          return <div key={key} data-current={key === metric}>
                            <dt>{label}</dt>
                            <dd>{lessonTimeLoading ? loadingValue : value === null ? <><span aria-hidden="true">—</span><span className="course-stats__sr-only">Время недоступно</span></> : formatStudyTime(value)}</dd>
                          </div>;
                        })}
                      </dl>
                    </li>;
                  })}
                </ul> : <p className="course-progress__empty">{search ? "По запросу занятий не найдено." : "В этом блоке пока нет занятий."}</p>}
              </div>
            </div>;
          }) : <p className="course-progress__empty">{search ? "По запросу блоков не найдено." : "В этой главе пока нет блоков."}</p>}
        </div>
      </div>;
    })}</div> : <p className="course-progress__empty">{search ? "Ничего не найдено. Попробуйте другой запрос." : "Программа курса пока недоступна."}</p>}
  </section>;
}
