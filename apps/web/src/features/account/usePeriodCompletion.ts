import { useEffect, useRef, useState } from "react";
import type { CourseReportRange } from "./course-statistics-analytics";
import { fetchPeriodCompletion } from "./period-completion-api";

type PeriodCompletion = {from: string; to: string; count: number | null; loading?: boolean};
const openedEvent = "pymentor:personal-analytics-opened";
const closedEvent = "pymentor:personal-analytics-closed";

export function usePeriodCompletion(enabled: boolean, actorRef: string | undefined, range: CourseReportRange, attempt: number): PeriodCompletion | undefined {
  const {courseId, from, to} = range;
  const isOpen = useRef(true);
  // Completion counts belong to the mounted account and exact requested period.
  const cache = useRef<{scope: string; generation: number; counts: Map<string, number>}>({scope: "", generation: 0, counts: new Map()});
  const lastAttempt = useRef(attempt);
  const scope = JSON.stringify([enabled, actorRef]);
  if (cache.current.scope !== scope) cache.current = {scope, generation: cache.current.generation + 1, counts: new Map()};
  const store = cache.current;
  const period = JSON.stringify([courseId, from, to]);
  const forceRefresh = lastAttempt.current !== attempt;
  const key = JSON.stringify([cache.current.generation, enabled, actorRef, courseId, from, to, attempt]);
  const [result, setResult] = useState<{key: string; count: number | null} | null>(null);

  useEffect(() => {
    lastAttempt.current = attempt;
    let controller: AbortController | undefined;
    const read = () => {
      if (!enabled) return;
      controller?.abort();
      const currentRequest = new AbortController();
      controller = currentRequest;
      fetchPeriodCompletion({courseId, from, to}, currentRequest.signal).then(
        count => {
          if (currentRequest.signal.aborted || cache.current !== store) return;
          store.counts.delete(period);
          if (count !== null) {
            store.counts.set(period, count);
            if (store.counts.size > 32) store.counts.delete(store.counts.keys().next().value!);
          }
          setResult({key, count});
        },
        () => { if (!currentRequest.signal.aborted && cache.current === store) { store.counts.delete(period); setResult({key, count: null}); } },
      );
    };
    const open = () => { isOpen.current = true; read(); };
    const close = () => { isOpen.current = false; controller?.abort(); };
    window.addEventListener(openedEvent, open);
    window.addEventListener(closedEvent, close);
    if (isOpen.current && (forceRefresh || !cache.current.counts.has(period))) read();
    return () => {
      controller?.abort();
      window.removeEventListener(openedEvent, open);
      window.removeEventListener(closedEvent, close);
    };
  }, [enabled, courseId, from, to, key]);

  if (!enabled) return undefined;
  const cached = cache.current.counts.get(period);
  if (result?.key !== key && cached !== undefined && !forceRefresh) return {from, to, count: cached};
  return result?.key === key ? {from, to, count: result.count} : {from, to, count: null, loading: true};
}
