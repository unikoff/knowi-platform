/** Latest-only, bounded retry for the account's read-only activity requests. */
export function createActivityRead<T>(options: {
  request: () => Promise<T>;
  isCurrent: () => boolean;
  isRetryable: (error: unknown) => boolean;
  onSuccess: (value: T) => void;
  onError: (error: unknown) => void;
}) {
  const delays = [250, 500, 1000, 2000, 4000, 8000];
  let sequence = 0;
  let disposed = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let wake: (() => void) | undefined;

  const cancelWait = () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
    wake?.();
    wake = undefined;
  };
  const load = () => {
    const requestId = ++sequence;
    cancelWait();
    const current = () => !disposed && requestId === sequence && options.isCurrent();
    void (async () => {
      for (let attempt = 0; attempt <= delays.length; attempt++) {
        if (!current()) return;
        let value: T;
        try {
          value = await options.request();
        } catch (error) {
          if (!current()) return;
          if (attempt === delays.length || !options.isRetryable(error)) {
            options.onError(error);
            return;
          }
          await new Promise<void>((resolve) => {
            wake = resolve;
            timer = setTimeout(() => { timer = undefined; wake = undefined; resolve(); }, delays[attempt]);
          });
          continue;
        }
        if (current()) options.onSuccess(value);
        return;
      }
    })();
  };
  return {
    load,
    dispose() { disposed = true; sequence++; cancelWait(); },
  };
}
