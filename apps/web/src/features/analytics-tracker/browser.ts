import { AnalyticsTracker, type Clock, type Context } from './tracker';

export const browserClock: Clock = {
  now: () => ({ mono: performance.now(), wall: Date.now() }),
  uuid: () => crypto.randomUUID(),
  random: () => Math.random(),
};

/** Attach exactly once per mounted application. Never inspects event payloads or URLs. */
export class BrowserLifecycle {
  private running = false;
  private timer: ReturnType<typeof setInterval> | null = null;
  constructor(private readonly tracker: AnalyticsTracker, private readonly win: Window = window, private readonly doc: Document = document) {}
  private presence = () => {
    this.tracker.setPresence(this.doc.visibilityState === 'visible', this.doc.hasFocus());
    void this.tracker.flush();
  };
  private action = () => this.tracker.actionObserved();
  private blur = () => { this.tracker.setPresence(this.doc.visibilityState === 'visible', false); void this.tracker.flush(); };
  private hide = () => { this.tracker.setPresence(false, false); void this.tracker.flush(); };
  private online = () => { void this.tracker.flush(); };
  start() {
    if (this.running) return;
    this.running = true; this.tracker.start();
    this.doc.addEventListener('visibilitychange', this.presence);
    this.win.addEventListener('focus', this.presence);
    this.win.addEventListener('blur', this.blur);
    this.win.addEventListener('pagehide', this.hide);
    this.win.addEventListener('pageshow', this.presence);
    this.win.addEventListener('online', this.online);
    for (const type of ['pointerdown', 'keydown', 'wheel', 'touchstart', 'scroll']) this.win.addEventListener(type, this.action, { passive: true });
    this.presence();
    this.timer = setInterval(() => { this.tracker.tick(); void this.tracker.flush(); }, 15_000);
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
    this.timer = null; this.tracker.stop();
  }
  setContext(context: Context | null) { this.tracker.setContext(context); void this.tracker.flush(); }
}
