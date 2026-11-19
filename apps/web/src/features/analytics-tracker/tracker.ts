/** Isolated v2 tracker. No collection occurs until explicitly enabled and integrated. */
export const TOOL_IDS = ['workspace', 'lessons', 'theory', 'practice', 'interpreter', 'account', 'account-profile', 'account-statistics', 'account-booking', 'account-settings', 'account-payments', 'account-achievements', 'catalog', 'catalog-preview', 'course-about', 'course-personal'] as const;
export type ToolId = typeof TOOL_IDS[number];
export const isToolId = (value: unknown): value is ToolId => typeof value === 'string' && (TOOL_IDS as readonly string[]).includes(value);
export type Context = { courseId: string; catalogVersion: string; lessonId: string; section: 'theory' | 'practice'; toolId?: never } | { courseId: string; toolId: ToolId; catalogVersion?: never; lessonId?: never; section?: never };
export type BrowserEvent = {
  schema_version: 2; event_id: string; type: 'activity_segment' | 'tool_segment';
  course_id: string; catalog_version: string; lesson_id: string | null; section: 'theory' | 'practice' | null;
  session_id: string; started_at: string; ended_at: string; last_action_at: string;
  visible: true; focused: true; active_ms: number; payload: Record<string, never> | { tool_id: ToolId };
};
export type Clock = { now(): { mono: number; wall: number }; uuid(): string; random(): number; isValid?(): boolean };
export type QueueEntry = { event: BrowserEvent; createdAt: number; attempts: number; unresolvedEndMono?: number };
/** Must be volatile memory only; never persist grants, identity or events. */
export interface MemoryQueue { read(): QueueEntry[]; write(items: QueueEntry[]): void; clear(): void }
export class VolatileQueue implements MemoryQueue {
  private entries: QueueEntry[] = [];
  read() { return this.entries.slice(); }
  write(items: QueueEntry[]) { this.entries = items.slice(); }
  clear() { this.entries = []; }
}
export type DeliveryResult = { status: number; body?: unknown; retryAfterMs?: number };
export type Ports = {
  clock: Clock; queue?: MemoryQueue;
  /** Identity key is local only; provider must bind the returned grant to that identity. */
  grant(identityKey: string, signal: AbortSignal, courseId: string): Promise<string | { status: 401 | 403 }>;
  projectEventTime?(event: BrowserEvent, endMono: number): BrowserEvent | null;
  send(grant: string, body: { events: BrowserEvent[] }, signal: AbortSignal): Promise<DeliveryResult>;
};
const MAX_AGE = 24 * 60 * 60 * 1000, MAX_ITEMS = 200, MAX_BYTES = 512 * 1024;
const MAX_BATCH = 256 * 1024, MAX_EVENT = 4096, IDLE = 300_000, MAX_GAP = 20_000;
const bytes = (value: unknown) => new TextEncoder().encode(JSON.stringify(value)).length;
const identifier = (value: string) => /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value);
const stamp = (value: number) => new Date(value).toISOString();
const same = (a: Context | null, b: Context | null) => JSON.stringify(a) === JSON.stringify(b);

export class AnalyticsTracker {
  private readonly queue: MemoryQueue;
  private identity: string | null = null;
  private subject: string | null = null;
  private deniedCourses = new Set<string>();
  private generation = 0;
  private session = '';
  private context: Context | null = null;
  private visible = false;
  private focused = false;
  private started = false;
  private last: { mono: number; wall: number } | null = null;
  private action: { mono: number; wall: number } | null = null;
  private probe: { mono: number; wall: number } | null = null;
  private abort: AbortController | null = null;
  private inFlight = false;
  private nextAttempt = 0;
  private failures = 0;
  private diagnostics = { dropped: 0, rejected: 0, conflict: 0, malformedAck: 0 };

  constructor(private readonly ports: Ports, private readonly enabled = false) {
    this.queue = ports.queue ?? new VolatileQueue();
    // A new instance has no trustworthy association to an older identity.
    this.queue.clear();
  }
  /** Counters contain no identifiers or event data. Intended for synthetic tests. */
  testCounters() { return { ...this.diagnostics }; }
  start() { if (this.enabled && !this.started) { this.started = true; this.resume(); } }
  stop() { if (!this.started) return; this.boundary(); this.started = false; this.abort?.abort(); }
  setIdentity(key: string | null, role: string | null) {
    const next = role === 'user' && key ? key : null;
    if (next === this.identity) return;
    this.boundary(); this.abort?.abort(); this.generation++; this.queue.clear();
    this.identity = next; this.context = null; this.last = null; this.action = null; this.probe = null;
    this.subject = null; this.deniedCourses.clear();
    this.session = next ? this.ports.clock.uuid() : ''; this.nextAttempt = 0; this.failures = 0;
  }
  /** Bind a fresh, server-issued lifecycle before any grant can be used. */
  bindSubject(subject: string): boolean {
    if (!this.identity) return false;
    if (this.subject === null) { this.subject = subject; return true; }
    if (this.subject === subject) return true;
    // Old identity's unacknowledged events must never be sent under the new lifecycle.
    this.abort?.abort(); this.generation++; this.queue.clear(); this.subject = subject;
    this.last = null; this.action = null; this.probe = null; this.session = this.ports.clock.uuid();
    this.nextAttempt = 0; this.failures = 0; this.resume();
    return false;
  }
  revokeCourse(courseId: string) {
    this.deniedCourses.add(courseId);
    const before = this.queue.read();
    const remaining = before.filter(x => x.event.course_id !== courseId);
    this.diagnostics.dropped += before.length - remaining.length;
    this.queue.write(remaining);
    if (this.context?.courseId === courseId) { this.context = null; this.last = null; this.action = null; this.probe = null; }
  }
  setPresence(visible: boolean, focused: boolean) {
    if (this.visible === visible && this.focused === focused) return;
    this.boundary(); this.visible = visible; this.focused = focused;
    this.resume();
  }
  /** Route, lesson or active panel changes must call this with null for non-learning panels. */
  setContext(context: Context | null) {
    const valid = context && !this.deniedCourses.has(context.courseId) && identifier(context.courseId) &&
      (context.toolId ? isToolId(context.toolId) : context.courseId !== 'platform-tools' && !!context.catalogVersion && identifier(context.catalogVersion) && !!context.lessonId && identifier(context.lessonId) && (context.section === 'theory' || context.section === 'practice')) ? { ...context } : null;
    if (same(this.context, valid)) return;
    this.boundary(); this.context = valid; this.resume();
  }
  actionObserved() {
    if (!this.allowed() || !this.context) return;
    const now = this.ports.clock.now();
    // Frequent wheel/key events update the idle deadline without generating an event for each input.
    if (!this.last || !this.action || now.mono - this.action.mono >= IDLE || this.gapInvalid(now)) {
      this.boundary(); this.last = now; this.probe = now;
    }
    this.action = now;
  }
  tick() {
    const now = this.ports.clock.now();
    if (!this.last || !this.action) return;
    if (this.gapInvalid(now)) { this.last = null; this.action = null; this.probe = null; return; }
    this.probe = now;
    if (now.mono - this.action.mono >= IDLE) {
      this.boundary(); this.resume(false);
    } else if (now.mono - this.last.mono >= 60_000) {
      this.boundary(true);
    }
  }
  private gapInvalid(now: { mono: number; wall: number }) {
    const probe = this.probe;
    if (!probe) return true;
    const mono = now.mono - probe.mono, wall = now.wall - probe.wall;
    return mono < 0 || mono > MAX_GAP || Math.abs(wall - mono) > 1000;
  }
  private allowed() { return this.enabled && this.started && !!this.identity && this.visible && this.focused && (this.ports.clock.isValid?.() ?? true); }
  private resume(newAction = true) {
    if (!this.allowed() || !this.context) return;
    const now = this.ports.clock.now();
    if (newAction || !this.action) this.action = now;
    if (now.mono - this.action.mono < IDLE) { this.last = now; this.probe = now; }
  }
  private boundary(completeOnly = false) {
    const now = this.ports.clock.now(), last = this.last, action = this.action, context = this.context;
    this.last = null;
    if (!last || !action || !context || !this.allowed()) return;
    const monoDelta = now.mono - last.mono, wallDelta = now.wall - last.wall;
    // A delayed timer, suspend, non-monotonic clock or wall-clock adjustment has unknown duration.
    if (monoDelta < 0 || this.gapInvalid(now) || Math.abs(wallDelta - monoDelta) > 1000) { this.action = null; this.probe = null; return; }
    let duration = Math.min(Math.floor(monoDelta), Math.max(0, Math.floor(action.mono + IDLE - last.mono)),
      Math.max(0, Math.floor(action.wall + IDLE - last.wall)));
    if (completeOnly) duration = Math.floor(duration / 60_000) * 60_000;
    if (completeOnly) this.last = { mono: last.mono + duration, wall: last.wall + duration };
    else this.probe = null;
    if (duration <= 0) return;
    for (let offset = 0; offset < duration; offset += 60_000) {
      const part = Math.min(60_000, duration - offset), start = Math.trunc(last.wall) + offset, end = start + part;
      this.enqueue({ schema_version: 2, event_id: this.ports.clock.uuid(), type: context.toolId ? 'tool_segment' : 'activity_segment',
        course_id: context.courseId, catalog_version: context.toolId ? 'platform-tools-v1' : context.catalogVersion, lesson_id: context.toolId ? null : context.lessonId,
        section: context.toolId ? null : context.section, session_id: this.session, started_at: stamp(start), ended_at: stamp(end),
        last_action_at: stamp(Math.min(Math.trunc(action.wall), end)), visible: true, focused: true,
        active_ms: part, payload: context.toolId ? { tool_id: context.toolId } : {} }, now.mono,
        this.ports.projectEventTime ? last.mono + offset + part : undefined);
    }
  }
  private enqueue(event: BrowserEvent, now: number, unresolvedEndMono?: number) {
    if (bytes(event) > MAX_EVENT) { this.diagnostics.dropped++; return false; }
    const entries = this.queue.read(); entries.push({ event, createdAt: now, attempts: 0, unresolvedEndMono });
    while (entries.length && (entries.length > MAX_ITEMS || bytes(entries.map(x => x.event)) > MAX_BYTES || now - entries[0].createdAt > MAX_AGE)) {
      entries.shift(); this.diagnostics.dropped++;
    }
    this.queue.write(entries);
    return entries.some(entry => entry.event.event_id === event.event_id);
  }
  private resolveBatch(batch: QueueEntry[]) {
    const project = this.ports.projectEventTime;
    if (!project) return batch;
    const selected = new Set(batch.map(item => item.event.event_id));
    const ready: QueueEntry[] = [];
    this.queue.write(this.queue.read().flatMap(item => {
      if (!selected.has(item.event.event_id)) return [item];
      if (item.unresolvedEndMono === undefined) {
        ready.push(item);
        return [item];
      }
      const event = project(item.event, item.unresolvedEndMono);
      if (!event) { this.diagnostics.dropped++; return []; }
      const resolved = { event, createdAt: item.createdAt, attempts: item.attempts };
      ready.push(resolved);
      return [resolved];
    }));
    return ready;
  }
  /** One bounded delivery attempt. Unknown outcomes remain queued with identical IDs and payloads. */
  async flush() {
    if (!this.enabled || !this.started || !this.identity || this.inFlight) return;
    const now = this.ports.clock.now();
    const entries = this.queue.read().filter(x => {
      const keep = now.mono >= x.createdAt && now.mono - x.createdAt <= MAX_AGE && x.attempts < 8;
      if (!keep) this.diagnostics.dropped++; return keep;
    });
    this.queue.write(entries);
    if (now.mono < this.nextAttempt) return;
    let batch: QueueEntry[] = [];
    const courseId = entries[0]?.event.course_id;
    for (const item of entries) {
      if (item.event.course_id !== courseId) continue;
      if (batch.length >= 100 || bytes({ events: [...batch, item].map(x => x.event) }) > MAX_BATCH) break;
      batch.push(item);
    }
    if (!batch.length) return;
    const identity = this.identity, generation = this.generation, abort = new AbortController();
    this.abort = abort; this.inFlight = true;
    const timer = setTimeout(() => abort.abort(), 10_000);
    // Timeout also resolves even when an injected adapter ignores AbortSignal.
    const timeout = new Promise<never>((_, reject) => abort.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true }));
    try {
      const grant = await Promise.race([this.ports.grant(identity, abort.signal, courseId), timeout]);
      if (generation !== this.generation || abort.signal.aborted || !this.started) return;
      if (typeof grant !== 'string') { if (grant.status === 401) this.setIdentity(null, null); else this.revokeCourse(courseId); return; }
      // Freeze server timestamps before the first delivery so an unknown ACK can be retried with the same payload.
      batch = this.resolveBatch(batch);
      if (!batch.length) return;
      const response = await Promise.race([this.ports.send(grant, { events: batch.map(x => x.event) }, abort.signal), timeout]);
      if (generation !== this.generation || abort.signal.aborted) return;
      if (response.status === 401) { this.setIdentity(null, null); return; }
      if (response.status === 403) { this.revokeCourse(courseId); return; }
      const terminal = new Set<string>();
      if (response.status >= 200 && response.status < 300) {
        const body = response.body as { items?: unknown } | undefined;
        const pairs: Record<string, string[]> = { accepted: ['committed'], duplicate: ['same_payload'], conflict: ['payload_conflict'], rejected: ['invalid', 'too_large', 'unsupported_version', 'future_clock', 'too_late'], pending: ['unknown_catalog'] };
        const counts = new Map<number, number>();
        if (Array.isArray(body?.items)) for (const raw of body.items) {
          if (raw && typeof raw === 'object' && Number.isInteger((raw as { index?: unknown }).index)) {
            const index = (raw as { index: number }).index;
            counts.set(index, (counts.get(index) ?? 0) + 1);
          }
        }
        if (Array.isArray(body?.items)) for (const raw of body.items) {
          if (!raw || typeof raw !== 'object') { this.diagnostics.malformedAck++; continue; }
          const item = raw as { index: number; event_id: string | null; status: string; code: string };
          if (!Number.isInteger(item.index) || item.index < 0 || item.index >= batch.length || counts.get(item.index) !== 1 ||
              (item.event_id !== null && item.event_id !== batch[item.index].event.event_id) || !pairs[item.status]?.includes(item.code)) {
            this.diagnostics.malformedAck++; continue;
          }
          if (item.status !== 'pending') terminal.add(batch[item.index].event.event_id);
          if (item.status === 'conflict') this.diagnostics.conflict++;
          if (item.status === 'rejected') this.diagnostics.rejected++;
        }
      }
      const attempted = new Set(batch.map(x => x.event.event_id));
      this.queue.write(this.queue.read().filter(x => !terminal.has(x.event.event_id)).map(x => attempted.has(x.event.event_id) ? { ...x, attempts: x.attempts + 1 } : x));
      if (terminal.size === batch.length) { this.failures = 0; this.nextAttempt = 0; }
      else this.backoff(response.retryAfterMs);
    } catch (error) {
      if (generation === this.generation && this.started) {
        const attempted = new Set(batch.map(x => x.event.event_id));
        this.queue.write(this.queue.read().map(x => attempted.has(x.event.event_id) ? { ...x, attempts: x.attempts + 1 } : x));
        const retryAfter = error && typeof error === 'object' && 'retryAfterMs' in error && typeof error.retryAfterMs === 'number' ? error.retryAfterMs : 0;
        this.backoff(retryAfter);
      }
    } finally { clearTimeout(timer); if (this.abort === abort) this.abort = null; this.inFlight = false; }
  }
  private backoff(retryAfter = 0) {
    this.failures++;
    const serverDelay = Number.isFinite(retryAfter) ? Math.max(0, retryAfter) : Number.MAX_SAFE_INTEGER;
    const delay = Math.max(serverDelay, Math.min(60_000, 1000 * 2 ** Math.min(this.failures - 1, 6)) * (0.5 + this.ports.clock.random() * 0.5));
    this.nextAttempt = this.ports.clock.now().mono + delay;
  }
}
