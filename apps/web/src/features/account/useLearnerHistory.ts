import { useEffect, useState } from "react";
import { fetchLearnerHistory, type LegacyHistoryDay } from "./learner-history";

type State = {key: string; status: "ready" | "error"; days: LegacyHistoryDay[]};
export function useLearnerHistory(enabled: boolean, actorRef: string | undefined, from: string, to: string, attempt: number) {
  const [revision, setRevision] = useState(0);
  const key = JSON.stringify([enabled, actorRef, from, to, attempt, revision]);
  const [state, setState] = useState<State>({key: "", status: "ready", days: []});
  useEffect(() => {
    if (!enabled || !actorRef) return;
    const controller = new AbortController();
    let active = true;
    const close = () => {active = false; controller.abort();};
    const open = () => setRevision(value => value + 1);
    window.addEventListener("pymentor:personal-analytics-closed", close);
    window.addEventListener("pymentor:personal-analytics-opened", open);
    fetchLearnerHistory(from, to, controller.signal).then(days => {
      if (active) setState({key, status: "ready", days});
    }, () => {if (active) setState({key, status: "error", days: []});});
    return () => {close(); window.removeEventListener("pymentor:personal-analytics-closed", close); window.removeEventListener("pymentor:personal-analytics-opened", open);};
  }, [key]);
  if (!enabled) return {status: "disabled" as const, days: []};
  if (!actorRef) return {status: "error" as const, days: []};
  return state.key === key ? state : {status: "loading" as const, days: []};
}
