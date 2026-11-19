import type { BrowserEvent, Clock, DeliveryResult } from './tracker';

type Sample = { mono: number; wall: number };
type TimeSource = { now(): Sample; uuid(): string; random(): number };
const UTC = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_RTT = 5000;
const parseUtc = (value: unknown): number => {
  if (typeof value !== 'string' || !UTC.test(value)) return NaN;
  const result = Date.parse(value);
  return Number.isFinite(result) && new Date(result).toISOString().slice(0, 19) === value.slice(0, 19) ? result : NaN;
};

/** UTC anchor from a recent Core grant. Date.now is only a jump detector, not the source of event UTC. */
export class ServerClock implements Clock {
  private anchor: { mono: number; wall: number; localWall: number; validFor: number } | null = null;
  constructor(private readonly source: TimeSource) {}
  uuid() { return this.source.uuid(); }
  random() { return this.source.random(); }
  sample() { return this.source.now(); }
  sync(serverWall: number, sent: Sample, received: Sample, expires: number): boolean {
    const rtt = received.mono - sent.mono;
    if (!Number.isFinite(serverWall) || rtt < 0 || rtt > MAX_RTT || Math.abs((received.wall - sent.wall) - rtt) > 1000) {
      this.anchor = null; return false;
    }
    const previous = this.anchor, elapsed = previous ? received.mono - previous.mono : 0;
    const current = previous && elapsed >= 0 && Math.abs(received.wall - previous.localWall - elapsed) <= 1000
      ? previous.wall + elapsed : -Infinity;
    // Keep a continuous timeline across grants. Core's second precision and RTT jitter
    // would otherwise move adjacent segments apart or make them overlap.
    const estimate = serverWall + rtt / 2;
    if (Number.isFinite(current) && Math.abs(estimate - current) > 5000) { this.anchor = null; return false; }
    const wall = Number.isFinite(current) ? current : estimate;
    const validFor = expires - Math.max(estimate, wall) - 1000;
    if (validFor <= 0) { this.anchor = null; return false; }
    this.anchor = { mono: received.mono, wall, localWall: received.wall, validFor };
    return true;
  }
  isValid() {
    if (!this.anchor) return false;
    const now = this.source.now(), elapsed = now.mono - this.anchor.mono;
    return elapsed >= 0 && elapsed < this.anchor.validFor && Math.abs((now.wall - this.anchor.localWall) - elapsed) <= 1000;
  }
  projectEventTime(event: BrowserEvent, endMono: number): BrowserEvent | null {
    if (!this.anchor || !this.isValid()) return null;
    const localEnd = Date.parse(event.ended_at), sample = this.source.now();
    const elapsed = sample.mono - endMono;
    // A clock jump or a suspend with a stopped monotonic clock makes buffered UTC ambiguous.
    if (elapsed < 0 || Math.abs(sample.wall - localEnd - elapsed) > 1000) return null;
    const end = Math.trunc(this.anchor.wall + (endMono - this.anchor.mono));
    const shift = end - localEnd;
    const restamp = (value: string) => new Date(Date.parse(value) + shift).toISOString();
    return { ...event, started_at: restamp(event.started_at), ended_at: restamp(event.ended_at),
      last_action_at: restamp(event.last_action_at) };
  }
  now() {
    const sample = this.source.now();
    return { mono: sample.mono, wall: this.anchor ? this.anchor.wall + Math.max(0, sample.mono - this.anchor.mono) : sample.wall };
  }
}

export type GrantReply = string | { status: 401 | 403 };
export class GrantUnavailable extends Error {
  constructor(readonly retryAfterMs: number) { super('grant unavailable'); }
}
/** A single volatile grant, scoped to the caller's identity and course. */
export class BrowserTransport {
  private sequence = 0;
  private applied = 0;
  private grantEpoch = 0;
  private cachedGrant: { identity: string; courseId: string; token: string } | null = null;
  constructor(private readonly clock: ServerClock, private readonly bindSubject: (subject: string) => boolean,
    private readonly fetcher: typeof fetch = fetch, private readonly baseUrl = '') {}
  clearGrant() {
    this.cachedGrant = null;
    this.grantEpoch++;
  }
  async grant(identity: string, signal: AbortSignal, courseId: string): Promise<GrantReply> {
    signal.throwIfAborted();
    const cached = this.cachedGrant;
    if (cached?.identity === identity && cached.courseId === courseId && this.clock.isValid()) return cached.token;
    this.cachedGrant = null;
    const epoch = this.grantEpoch;
    const order = ++this.sequence, sent = this.clock.sample();
    const request = this.fetcher;
    const response = await request(`${this.baseUrl}/api/analytics/grants/collect`, {
      method: 'POST', credentials: 'include', cache: 'no-store', signal,
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' }, body: JSON.stringify({ course_id: courseId }),
    });
    if (response.status === 401 || response.status === 403) return { status: response.status };
    if (!response.ok) throw new GrantUnavailable(response.status === 429 ? this.retryAfter(response) : 0);
    const value: unknown = await response.json();
    const received = this.clock.sample();
    if (signal.aborted || order < this.applied || !value || typeof value !== 'object') throw new Error('stale or invalid grant');
    const data = value as Record<string, unknown>;
    if (typeof data.grant !== 'string' || !data.grant || typeof data.subject_ref !== 'string' || !UUID.test(data.subject_ref) ||
        typeof data.server_time !== 'string' || typeof data.expires_at !== 'string') throw new Error('invalid grant');
    const server = parseUtc(data.server_time), expires = parseUtc(data.expires_at), rtt = received.mono - sent.mono;
    if (!Number.isFinite(server) || !Number.isFinite(expires) || expires - server > 300_000 || expires - server <= Math.max(0, rtt) + 1000) throw new Error('expired grant');
    if (!this.clock.sync(server, sent, received, expires)) throw new Error('untrusted clock');
    this.applied = order;
    if (!this.bindSubject(data.subject_ref.toLowerCase())) throw new Error('lifecycle changed');
    if (epoch === this.grantEpoch) this.cachedGrant = { identity, courseId, token: data.grant };
    return data.grant;
  }
  async send(grant: string, body: { events: BrowserEvent[] }, signal: AbortSignal): Promise<DeliveryResult> {
    const request = this.fetcher;
    const response = await request(`${this.baseUrl}/api/analytics/events/batch`, {
      method: 'POST', credentials: 'omit', cache: 'no-store', signal,
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', Authorization: `Bearer ${grant}` }, body: JSON.stringify(body),
    });
    if (this.cachedGrant?.token === grant && (response.status === 401 || response.status === 403 ||
        (response.status === 503 && response.headers.has('WWW-Authenticate')))) this.clearGrant();
    let data: unknown;
    if (response.ok) { try { data = await response.json(); } catch { /* unknown ACK is retried */ } }
    return { status: response.status, body: data, retryAfterMs: this.retryAfter(response) };
  }
  private retryAfter(response: Response) {
    const raw = response.headers.get('Retry-After')?.trim();
    if (!raw) return 0;
    if (/^\d+$/.test(raw)) return Math.min(Number(raw) * 1000, Number.MAX_SAFE_INTEGER);
    const date = Date.parse(raw);
    if (!Number.isFinite(date)) return 0;
    const serverDate = Date.parse(response.headers.get('Date') ?? '');
    const origin = Number.isFinite(serverDate) ? serverDate : this.clock.isValid() ? this.clock.now().wall : NaN;
    return Number.isFinite(origin) ? Math.max(0, date - origin) : Number.MAX_SAFE_INTEGER;
  }
}
