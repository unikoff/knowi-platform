import type { Clock } from './tracker';
import { ServerClock } from './transport';
import { REQUEST_TIMEOUT, type Writer } from './windows';

type Sample = ReturnType<Clock['now']>;
const awareTime = (value: unknown) => typeof value === 'string' &&
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? Date.parse(value) : NaN;
const media = { Accept: 'application/json', 'Content-Type': 'application/json' };
// This module is recreated when the document reloads. No grant or account identity is retained here.
const unsupportedEndpoints = new Map<string, WindowHttpError>();

export class WindowHttpError extends Error {
  constructor(readonly status: number, readonly code: string | undefined, readonly retryAfter: number,
    readonly allow: string | null = null) {
    super('window HTTP ' + status);
  }

  get unsupported() {
    const postAllowed = this.allow?.split(',').some(method => method.trim().toUpperCase() === 'POST');
    return this.status === 404 || (this.status === 405 && !postAllowed) || this.code === 'unsupported_version';
  }
}

export class WindowAccountChanged extends Error {}

export class WindowTransport {
  private readonly clock: ServerClock;
  private grant?: { token: string; refreshAt: number };

  constructor(
    private readonly source: Clock,
    private readonly expectedSubject: string,
    private readonly fetcher: typeof fetch = fetch,
    private readonly baseUrl = '',
  ) {
    this.clock = new ServerClock(source);
  }

  get unsupportedCapability(): boolean { return unsupportedEndpoints.has(this.baseUrl); }

  project(sample: Sample) {
    return this.clock.projectSample(sample);
  }

  hasGrant() { return Boolean(this.grant && this.clock.isValid()); }

  needsGrant() {
    return !this.grant || this.source.now().mono >= this.grant.refreshAt || !this.clock.isValid();
  }

  clearGrant() { this.grant = undefined; }

  async authorize(writer: Writer, signal: AbortSignal) {
    const sent = this.source.now();
    const value = await this.request('/api/analytics/windows/grant', JSON.stringify({ protocol_version: 1, ...writer }), signal);
    const received = this.source.now();
    signal.throwIfAborted();
    if (!value || typeof value !== 'object') throw new Error('invalid window grant');
    const grant = value as Record<string, unknown>;
    if (typeof grant.subject_ref !== 'string' || grant.subject_ref.toLowerCase() !== this.expectedSubject) {
      throw new WindowAccountChanged();
    }
    const serverTime = awareTime(grant.server_time), expires = awareTime(grant.expires_at);
    const maxAge = expires - serverTime;
    if (grant.protocol_version !== 1 || grant.writer_id !== writer.writer_id || grant.writer_generation !== writer.writer_generation ||
      typeof grant.grant !== 'string' || !grant.grant || grant.grant.length > 16 * 1024 ||
      !Number.isFinite(maxAge) || maxAge > 300_000 || maxAge <= 1000 + received.mono - sent.mono ||
      !this.clock.sync(serverTime, sent, received, expires)) throw new Error('untrusted window grant');
    this.grant = {
      token: grant.grant,
      refreshAt: received.mono + Math.max(1000, maxAge - 15_000 - (received.mono - sent.mono)),
    };
  }

  send(body: string, signal: AbortSignal, keepalive: boolean) {
    const grant = this.grant;
    if (!grant || !this.clock.isValid()) throw new Error('window grant unavailable');
    return this.request('/api/analytics/windows/batch', body, signal, grant.token, keepalive);
  }

  private async request(path: string, body: string, lifecycle: AbortSignal, token?: string, keepalive?: boolean) {
    lifecycle.throwIfAborted();
    const unsupported = unsupportedEndpoints.get(this.baseUrl);
    if (unsupported) throw unsupported;
    const abort = new AbortController();
    const cancel = () => abort.abort();
    const fetcher = this.fetcher;
    const timer = setTimeout(cancel, REQUEST_TIMEOUT);
    lifecycle.addEventListener('abort', cancel, { once: true });
    const aborted = new Promise<never>((_resolve, reject) => {
      abort.signal.addEventListener('abort', () => reject(new Error('window request aborted')), { once: true });
    });
    try {
      const response = await Promise.race([fetcher(this.baseUrl + path, {
        method: 'POST', credentials: 'include', cache: 'no-store', signal: abort.signal, keepalive,
        headers: token ? { ...media, Authorization: 'Bearer ' + token } : media, body,
      }), aborted]);
      const parsed: unknown = await Promise.race([response.json().catch(() => null), aborted]);
      if (!response.ok) {
        const code = parsed && typeof parsed === 'object' && 'code' in parsed && typeof parsed.code === 'string' ? parsed.code : undefined;
        const error = new WindowHttpError(response.status, code, this.retryAfter(response), response.headers.get('Allow'));
        if (error.unsupported) unsupportedEndpoints.set(this.baseUrl, error);
        throw error;
      }
      return parsed;
    } finally {
      clearTimeout(timer);
      lifecycle.removeEventListener('abort', cancel);
    }
  }

  private retryAfter(response: Response) {
    const value = response.headers.get('Retry-After')?.trim();
    if (!value) return 0;
    if (/^\d+$/.test(value)) return Math.min(Number(value) * 1000, Number.MAX_SAFE_INTEGER);
    const date = Date.parse(value);
    if (!Number.isFinite(date)) return 0;
    const server = Date.parse(response.headers.get('Date') ?? '');
    const now = Number.isFinite(server) ? server : this.project(this.source.now());
    return now === null ? Number.MAX_SAFE_INTEGER : Math.max(0, date - now);
  }
}
