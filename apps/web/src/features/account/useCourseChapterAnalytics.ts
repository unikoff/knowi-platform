import { useEffect, useMemo, useRef, useState } from "react";
import { AnalyticsReportError, fetchLifetimeActivityOutline } from "../analytics-reporting/client";
import type { ActivityOutlineReport } from "../analytics-reporting/types";
import type { AccountStatisticsData } from "./AccountStatistics";
import type { LearnerAnalyticsCatalog } from "./LearnerAnalytics";

export type ChapterTimeState = { status: "pending" | "loading" | "ready" | "stale" | "error" | "denied" | "unavailable"; report: ActivityOutlineReport | null };
export function useCourseChapterAnalytics(enabled: boolean, actorRef: string | undefined, courseId: string, data: AccountStatisticsData, catalog: LearnerAnalyticsCatalog, attempt: number): ChapterTimeState {
  const body = useMemo(() => {
    if (!data.tracksLoaded) return null;
    const chapters = new Map<string, string[]>();
    const seen = new Set<string>();
    const add = (chapter: string, ids: string[]) => {
      if (!chapters.has(chapter)) chapters.set(chapter, []);
      for (const id of ids) if (!seen.has(id)) { seen.add(id); chapters.get(chapter)!.push(id); }
    };
    for (const chapter of data.courses) {
      add(chapter.id, chapter.blocks.flatMap(block => block.lesson_ids ?? block.lessons.map(lesson => lesson.id)));
    }
    for (const chapter of catalog) add(chapter.id, chapter.lessons.map(lesson => lesson.id));
    return {course_id: courseId, chapters: [...chapters].map(([chapter_id, lesson_ids]) => ({chapter_id, lesson_ids}))};
  }, [data.tracksLoaded, data.courses, catalog, courseId]);
  const key = JSON.stringify([enabled, actorRef, body, attempt]);
  const accountOpen = useRef(true);
  const [state, setState] = useState<ChapterTimeState & {key: string}>({key: "", status: "pending", report: null});
  useEffect(() => {
    let controller: AbortController | undefined;
    let timers: Array<ReturnType<typeof setTimeout>> = [];
    const clearFollowUps = () => {
      timers.forEach(clearTimeout);
      timers = [];
    };
    const cancel = () => {
      clearFollowUps();
      controller?.abort();
    };
    const refresh = () => {
      if (!enabled || !body) return;
      clearFollowUps();
      const read = () => {
        controller?.abort();
        const current = new AbortController();
        controller = current;
        fetchLifetimeActivityOutline(body, actorRef ?? "", current.signal).then(report => {
          if (current.signal.aborted) return;
          const status = report.status === "unavailable" ? "unavailable" : report.status === "stale" ? "stale" : "ready";
          setState({key, status, report: status === "unavailable" ? null : report});
        }, error => {
          if (current.signal.aborted || error?.name === "AbortError") return;
          const kind = error instanceof AnalyticsReportError ? error.kind : undefined;
          if (kind === "denied") clearFollowUps();
          setState({key, status: kind === "denied" || kind === "unavailable" ? kind : "error", report: null});
        });
      };
      read();
      // A newly closed lesson can reach the Analytics projection after the first read.
      timers = [3000, 15000].map(delay => setTimeout(read, delay));
    };
    const opened = () => {
      accountOpen.current = true;
      refresh();
    };
    const closed = () => {
      accountOpen.current = false;
      cancel();
    };
    window.addEventListener("pymentor:personal-analytics-opened", opened);
    window.addEventListener("pymentor:personal-analytics-closed", closed);
    if (accountOpen.current) refresh();
    return () => {
      cancel();
      window.removeEventListener("pymentor:personal-analytics-opened", opened);
      window.removeEventListener("pymentor:personal-analytics-closed", closed);
    };
  }, [key]);
  if (!enabled || !body) return {status: "pending", report: null};
  return state.key === key ? state : {status: "loading", report: null};
}
