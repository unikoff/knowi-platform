import { AnalyticsTracker, isToolId, type Clock, type ToolId, type Context } from './tracker';
import { BrowserLifecycle, type TrackerSignals } from './browser';
import { BrowserTransport, ServerClock } from './transport';

export type Panel = { id: string; kind?: 'theory' | 'practice'; target?: { trackId: string; lessonId: string } };
export type Course = { courseId: string; catalogVersion: string | null };
export type ControllerOptions = {
  identityKey: string; subjectRef: string; role: string; toolsEnabled?: boolean; win?: Window; doc?: Document; fetcher?: typeof fetch; baseUrl?: string;
  timeSource?: Clock;
  v2Enabled?: boolean;
  createWindows?(source: Clock): TrackerSignals;
  canTrackLesson(target: { trackId: string; lessonId: string }): boolean;
};

/** One instance per auth lifecycle. Only a queued delivery requests a grant. */
export class BrowserTrackerController {
  private readonly tracker: AnalyticsTracker;
  private readonly lifecycle: BrowserLifecycle;
  private readonly transport: BrowserTransport;
  private running = false;
  private course: Course | null = null;
  private panel: Panel | undefined;
  private tool: ToolId | undefined;
  constructor(private readonly options: ControllerOptions) {
    const win = options.win ?? window, doc = options.doc ?? document;
    const baseTime = options.timeSource ?? { now: () => ({ mono: win.performance.now(), wall: Date.now() }),
      uuid: () => win.crypto.randomUUID(), random: () => Math.random() };
    let signalTime: ReturnType<Clock['now']> | undefined;
    const source = options.createWindows ? { ...baseTime, now: () => signalTime ?? baseTime.now() } : baseTime;
    const clock = new ServerClock(source);
    const expectedSubject = options.subjectRef.toLowerCase();
    const transport = new BrowserTransport(clock, subject => {
      if (subject === expectedSubject) return true;
      this.stop();
      return false;
    }, options.fetcher, options.baseUrl);
    this.transport = transport;
    this.tracker = new AnalyticsTracker({ clock: source,
      grant: (identity, signal, course) => transport.grant(identity, signal, course),
      projectEventTime: (event, endMono) => clock.projectEventTime(event, endMono),
      send: (grant, body, signal) => transport.send(grant, body, signal) }, options.v2Enabled !== false);
    const windows = options.createWindows?.(source);
    this.lifecycle = new BrowserLifecycle(this.tracker, win, doc, windows, windows ? run => {
      signalTime = baseTime.now();
      try { run(); } finally { signalTime = undefined; }
    } : undefined);
  }
  start() {
    if (this.running || this.options.role !== 'user' || !this.options.identityKey || !this.options.subjectRef) return;
    this.running = true; this.tracker.setIdentity(this.options.identityKey, this.options.role); this.lifecycle.start();
    this.applyContext();
  }
  /** Call synchronously before logout/account replacement, including when the local ID may be reused. */
  stop() {
    if (!this.running) return;
    this.running = false;
    this.transport.clearGrant();
    this.tracker.setIdentity(null, null); this.lifecycle.stop(); this.course = null; this.panel = undefined; this.tool = undefined;
  }
  setCourse(course: Course | null) {
    if (course && (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$/.test(course.courseId) || (course.catalogVersion !== null && !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(course.catalogVersion)) || (course.courseId === 'platform-tools' && !this.options.toolsEnabled) || (!course.catalogVersion && !this.options.toolsEnabled))) course = null;
    if (this.course?.courseId === course?.courseId && this.course?.catalogVersion === course?.catalogVersion) return;
    if (this.course?.courseId !== course?.courseId) this.transport.clearGrant();
    this.course = course ? { ...course } : null;
    this.applyContext();
  }
  onActivePanel(panel?: Panel) { this.panel = panel; this.applyContext(); }
  onActiveTool(toolId?: ToolId) { this.tool = isToolId(toolId) ? toolId : undefined; this.applyContext(); }
  private applyContext() {
    const p = this.panel, c = this.course;
    let context: Context | null = null;
    if (c) {
      if (this.options.toolsEnabled && this.tool) context = { courseId: c.courseId, toolId: this.tool };
      else if (c.courseId !== 'platform-tools' && c.catalogVersion && p?.target && (p.kind === 'theory' || p.kind === 'practice') && p.id === p.kind && this.options.canTrackLesson(p.target)) {
        context = { courseId: c.courseId, catalogVersion: c.catalogVersion, lessonId: p.target.lessonId, section: p.kind };
      } else if (this.options.toolsEnabled) {
        const toolId: ToolId = p && (p.id === 'theory' || p.id === 'practice' || p.id === 'interpreter' || p.id === 'account') ? p.id : 'workspace';
        context = { courseId: c.courseId, toolId };
      }
    }
    this.lifecycle.setContext(context);
  }
}
