const DAY_MS = 86_400_000;
const MOSCOW_OFFSET_MS = 3 * 3_600_000;

export function moscowDate(now = Date.now()): string {
  return new Date(now + MOSCOW_OFFSET_MS).toISOString().slice(0, 10);
}

/** Refresh once at the next Moscow midnight and when a hidden tab becomes visible. */
export function watchMoscowDay(refresh: () => void): () => void {
  let timer: ReturnType<typeof setTimeout>;
  const schedule = () => {
    clearTimeout(timer);
    const untilMidnight = DAY_MS - ((Date.now() + MOSCOW_OFFSET_MS) % DAY_MS);
    timer = setTimeout(() => {
      if (document.visibilityState === "visible") refresh();
      schedule();
    }, untilMidnight);
  };
  const onVisibility = () => {
    if (document.visibilityState === "visible") {
      refresh();
      schedule();
    }
  };
  document.addEventListener("visibilitychange", onVisibility);
  schedule();
  return () => { clearTimeout(timer); document.removeEventListener("visibilitychange", onVisibility); };
}
