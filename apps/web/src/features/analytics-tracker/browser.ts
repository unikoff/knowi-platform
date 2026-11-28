import type { AnalyticsTracker, Clock, Context } from './tracker';

export type TrackerSignals = Pick<AnalyticsTracker, 'start' | 'stop' | 'setPresence' | 'actionObserved' | 'setContext' | 'tick'> & {
  flush(keepalive?: boolean): void | Promise<void>;
};

export const browserClock: Clock = {
  now: () => ({ mono: performance.now(), wall: Date.now() }),
  uuid: () => crypto.randomUUID(),
  random: () => Math.random(),
};

/** Attach exactly once per mounted application. Never inspects event payloads or URLs. */
export class BrowserLifecycle {
  private running = false;
  private timer: ReturnType<typeof setInterval> | null = null;
  constructor(private readonly tracker: TrackerSignals, private readonly win: Window = window, private readonly doc: Document = document,
    private readonly parallel?: TrackerSignals, private readonly atTime: (run: () => void) => void = run => run()) {}
  private each(run: (tracker: TrackerSignals) => void) {
    this.atTime(() => {
      run(this.tracker);
      if (this.parallel) run(this.parallel);
    });
  }
  private presence = () => {
    const visible = this.doc.visibilityState === 'visible', focused = this.doc.hasFocus();
    this.each(tracker => { tracker.setPresence(visible, focused); void tracker.flush(); });
  };
  private action = () => this.each(tracker => tracker.actionObserved());
  private blur = () => this.each(tracker => { tracker.setPresence(this.doc.visibilityState === 'visible', false); void tracker.flush(); });
  private hide = () => this.each(tracker => { tracker.setPresence(false, false); void tracker.flush(true); });
  private online = () => this.each(tracker => { void tracker.flush(); });
  start() {
    if (this.running) return;
    this.running = true; this.each(tracker => tracker.start());
    this.doc.addEventListener('visibilitychange', this.presence);
    this.win.addEventListener('focus', this.presence);
    this.win.addEventListener('blur', this.blur);
    this.win.addEventListener('pagehide', this.hide);
    this.win.addEventListener('pageshow', this.presence);
    this.win.addEventListener('online', this.online);
    for (const type of ['pointerdown', 'keydown', 'wheel', 'touchstart', 'scroll']) this.win.addEventListener(type, this.action, { passive: true });
    this.presence();
    this.timer = setInterval(() => this.each(tracker => { tracker.tick(); void tracker.flush(); }), 15_000);
  }
  stop() {
    if (!this.running) return;
    this.running = false;
    this.doc.removeEventListener('visibilitychange', this.presence);
    this.win.removeEventListener('focus', this.presence);
    this.win.removeEventListener('blur', this.blur);
    this.win.removeEventListener('pagehide', this.hide);
    this.win.removeEventListener('pageshow', this.presence);
    this.win.removeEventListener('online', this.online);
    for (const type of ['pointerdown', 'keydown', 'wheel', 'touchstart', 'scroll']) this.win.removeEventListener(type, this.action);
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null; this.each(tracker => tracker.stop());
  }
  setContext(context: Context | null) { this.each(tracker => { tracker.setContext(context); void tracker.flush(); }); }
}
