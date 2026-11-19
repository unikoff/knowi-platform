import { useEffect, useState } from "react";
import { getLearnerStatistics, type LearnerStatisticsReport } from "./statistics-api";

type State = { key: string; status: "loading" | "error" | "ready"; report: LearnerStatisticsReport | null };

export function useAccountStatistics(userId: number, courseId: string, days: 7 | 30) {
  const [attempt, setAttempt] = useState(0);
  const key = JSON.stringify([userId, courseId, days, attempt]);
  const [state, setState] = useState<State>({ key, status: "loading", report: null });
  useEffect(() => {
    let current = true;
    setState({ key, status: "loading", report: null });
    getLearnerStatistics(days, courseId).then(
      (report) => { if (current) setState({ key, status: "ready", report }); },
      () => { if (current) setState({ key, status: "error", report: null }); },
    );
    return () => { current = false; };
  }, [key, days, courseId]);
  return { ...(state.key === key ? state : { key, status: "loading" as const, report: null }), retry: () => setAttempt((value) => value + 1) };
}
