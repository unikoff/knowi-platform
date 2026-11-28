import { listenForAuthRefresh } from '../auth/session-coordination';
import type { Clock, Context } from './tracker';
import { WindowOutbox, WindowStorageError, claimSend, emptyPartition, noteLoss, settleSend, updateLease, type Partition, type SendClaim } from './windows-outbox';
import { WindowAccountChanged, WindowHttpError, WindowTransport } from './windows-transport';
import {
  HEARTBEAT, IDLE_LIMIT, LEASE_DURATION, MAX_ATTEMPTS, MAX_CLOCK_GAP, WINDOWS_AUTH_BOUNDARY, backoff, checkWindowAck,
  mapWindowContext, snapshotHash, type AckOutcome, type CoverageReason, type Span, type WindowContext, type Writer,
} from './windows';

type Sample = ReturnType<Clock['now']>;
type Active = { next: Sample; probe: Sample; lastAction: Sample; context: WindowContext; start: number };
type Browser = Window & typeof globalThis;
export type WindowTrackerOptions = {
  source: Clock; subjectRef: string; win?: Browser; fetcher?: typeof fetch; baseUrl?: string;
  observe?: (event: { kind: 'ack'; claim: SendClaim; outcome: AckOutcome } | { kind: 'loss'; reason: CoverageReason }) => void;
};

const PARTITION_MARKER = 'knowi:analytics:windows:partition:v1';
const TRANSFER = 'knowi:analytics:windows:lease:v1';
const SENDER = 'knowi:analytics:windows:send:v1';
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export class WindowTracker {
  private readonly browser: Browser;
  private readonly outbox: WindowOutbox;
  private readonly transport: WindowTransport;
  private readonly subject: string;
  private readonly tab: string;
  private readonly signal = new AbortController();
  private readonly losses = new Set<CoverageReason>();
  private running = false;
  private disabled = false;
  private timer?: number;
  private channel?: BroadcastChannel;
  private unsubscribe?: () => void;
  private focused = false;
  private context: WindowContext | null = null;
  private active?: Active;
  private spans: Span[] = [];
  private owned = false;
  private trustedAt?: number;
  private writer?: Writer;
  private initializing = false;
  private driving = false;
  private queuedFlush?: boolean;
  private writes = Promise.resolve();
  private grantFailures = 0;
  private grantRetryAt = 0;

  constructor(private readonly options: WindowTrackerOptions) {
    this.browser = options.win ?? window;
    this.subject = options.subjectRef.toLowerCase();
    this.tab = options.source.uuid();
    this.outbox = new WindowOutbox(() => this.browser.indexedDB);
    this.transport = new WindowTransport(options.source, this.subject, options.fetcher, options.baseUrl);
  }

  start() {
    if (this.running || !uuid.test(this.subject)) return;
    if (this.transport.unsupportedCapability) { this.loss('unsupported'); return; }
    this.running = true;
    this.browser.addEventListener(WINDOWS_AUTH_BOUNDARY, this.invalidate);
    this.unsubscribe = listenForAuthRefresh(onFocus => { if (!onFocus) this.invalidate(); });
    try {
      this.channel = new this.browser.BroadcastChannel(TRANSFER);
      this.channel.onmessage = () => { if (this.active || this.spans.length) this.persist(); };
    } catch { /* The heartbeat handles coordination when the channel is unavailable. */ }
    this.timer = this.browser.setInterval(this.pulse, HEARTBEAT);
    this.flush();
  }

  stop() {
    if (!this.running) return;
    this.cut(this.options.source.now());
    this.active = undefined;
    this.persist();
    this.running = false;
    this.signal.abort();
    this.browser.removeEventListener(WINDOWS_AUTH_BOUNDARY, this.invalidate);
    this.unsubscribe?.();
    this.channel?.close();
    this.channel = undefined;
    if (this.timer !== undefined) this.browser.clearInterval(this.timer);
    void this.writes.then(() => this.outbox.close());
  }

  private invalidate = () => {
    this.loss('account_change');
    this.stop();
  };

  setContext(context: Context | null) {
    const next = mapWindowContext(context);
    if (JSON.stringify(this.context) === JSON.stringify(next)) return;
    const now = this.options.source.now();
    this.cut(now);
    this.context = next;
    this.arm(now);
    this.persist();
  }

  setPresence(visible: boolean, focused: boolean) {
    const next = visible && focused;
    if (next === this.focused) return;
    const now = this.options.source.now();
    this.cut(now);
    this.focused = next;
    this.arm(now);
    this.persist();
  }

  actionObserved() {
    if (!this.focused || !this.context || !this.writer) return;
    const now = this.options.source.now(), active = this.active;
    if (active && now.mono - active.lastAction.mono < IDLE_LIMIT && now.mono - active.probe.mono <= MAX_CLOCK_GAP) {
      active.lastAction = now;
      return;
    }
    this.cut(now);
    this.arm(now);
    this.persist();
  }

  tick() {
    if (!this.running || this.disabled) return;
    this.cut(this.options.source.now());
    this.persist();
  }

  private pulse = () => {
    this.tick();
    this.flush();
  };

  private arm(now: Sample) {
    const projected = this.transport.project(now);
    this.active = this.running && !this.disabled && this.writer && this.focused && this.context && projected !== null
      ? { next: now, probe: now, lastAction: now, context: this.context, start: projected } : undefined;
    if (projected !== null) this.trustedAt = projected;
  }

  private cut(now: Sample) {
    const active = this.active;
    if (!active) return;
    const monoGap = now.mono - active.probe.mono;
    const nowUtc = this.transport.project(now);
    const startUtc = this.transport.project(active.next);
    if (nowUtc === null || startUtc === null || monoGap < 0 || monoGap > MAX_CLOCK_GAP ||
      Math.abs(now.wall - active.probe.wall - monoGap) > 1000) {
      this.active = undefined;
      this.loss('clock_untrusted');
      return;
    }
    this.trustedAt = nowUtc;
    active.probe = now;
    const idleMono = active.lastAction.mono + IDLE_LIMIT;
    const end = Math.min(now.mono, idleMono);
    const duration = Math.floor(end - active.next.mono);
    if (duration > 0) {
      this.spans.push({ start: startUtc, end: startUtc + duration, context: active.context });
      active.next = { mono: active.next.mono + duration, wall: active.next.wall + duration };
    }
    if (now.mono >= idleMono) this.active = undefined;
  }

  private persist() {
    const writer = this.writer;
    if (!writer || (!this.active && !this.spans.length && !this.owned)) return;
    const spans = this.spans.splice(0);
    const startedAt = this.active?.start ?? spans[0]?.start ?? null;
    const releasing = !this.active;
    const now = this.transport.project(this.options.source.now()) ?? this.trustedAt;
    if (now === undefined) return;
    this.writes = this.writes.then(async () => {
      const result = await this.outbox.update(this.subject, writer, state => {
        for (const reason of this.losses) noteLoss(state, reason);
        return updateLease(state, this.tab, now, startedAt, spans, releasing);
      });
      if (!result) { this.invalidate(); return; }
      const wasOwned = this.owned;
      this.owned = result.owned;
      if (!result.consumed && spans.length && this.running) {
        const elapsed = (this.transport.project(this.options.source.now()) ?? now) - spans[0].start;
        if (elapsed <= LEASE_DURATION) this.spans.unshift(...spans);
        else this.loss('lease_lost');
      }
      if (wasOwned && !this.owned) this.channel?.postMessage('released');
    }).catch(() => this.storageUnavailable());
  }

  private loss(reason: CoverageReason) {
    if (this.losses.has(reason)) return;
    this.losses.add(reason);
    this.options.observe?.({ kind: 'loss', reason });
  }

  private storageUnavailable() {
    this.loss('storage_denied');
    this.disabled = true;
    this.active = undefined;
    this.spans.length = 0;
    this.signal.abort();
  }

  private async initialize() {
    if (this.writer || this.initializing) return;
    this.initializing = true;
    try {
      const offered = { writer_id: this.options.source.uuid(), writer_generation: 1 };
      const loaded = await this.outbox.transact(prior => {
        if (prior) return { result: prior };
        const next = emptyPartition(this.subject, offered);
        try {
          if (this.browser.localStorage.getItem(PARTITION_MARKER)) noteLoss(next, 'lease_lost');
        } catch { /* IndexedDB remains authoritative. */ }
        return { state: next, result: next };
      });
      this.signal.signal.throwIfAborted();
      let partition = loaded;
      if (loaded.subject !== this.subject) {
        if (!this.browser.navigator.onLine) return;
        await this.transport.authorize(offered, this.signal.signal);
        this.signal.signal.throwIfAborted();
        partition = await this.outbox.transact(current => {
          if (current?.subject === this.subject) return { result: current };
          if (current?.writer.writer_id !== loaded.writer.writer_id) throw new WindowAccountChanged();
          this.signal.signal.throwIfAborted();
          const next = emptyPartition(this.subject, offered);
          return { state: next, result: next };
        });
        this.loss('account_change');
        if (partition.writer.writer_id !== offered.writer_id) this.transport.clearGrant();
      }
      this.signal.signal.throwIfAborted();
      this.writer = partition.writer;
      if (this.transport.hasGrant()) {
        this.arm(this.options.source.now());
        this.persist();
      }
      for (const reason of partition.reasons) this.loss(reason);
      try { this.browser.localStorage.setItem(PARTITION_MARKER, '1'); } catch { /* IndexedDB remains authoritative. */ }
    } finally {
      this.initializing = false;
    }
  }

  flush(keepalive = false) {
    if (!this.running || this.disabled || !this.browser.navigator.onLine) return;
    if (this.driving) {
      this.queuedFlush = Boolean(this.queuedFlush || keepalive);
      return;
    }
    this.driving = true;
    void this.run(keepalive).catch(error => this.onDriveFailure(error)).finally(() => {
      this.driving = false;
      const queued = this.queuedFlush;
      this.queuedFlush = undefined;
      if (queued !== undefined) this.flush(queued);
    });
  }

  private async run(keepalive: boolean) {
    const mono = this.options.source.now().mono;
    if (!this.writer && mono < this.grantRetryAt) return;
    await this.initialize();
    const writer = this.writer;
    if (!writer || this.signal.signal.aborted) return;
    if (this.transport.needsGrant() && mono >= this.grantRetryAt && !keepalive) {
      const previouslyTrusted = this.transport.hasGrant();
      try {
        await this.transport.authorize(writer, this.signal.signal);
        this.grantFailures = 0;
        this.grantRetryAt = 0;
      } catch (error) {
        this.onDriveFailure(error);
        if (!this.transport.hasGrant() || this.disabled) return;
      }
      if (!previouslyTrusted && this.transport.hasGrant()) {
        this.arm(this.options.source.now());
        this.persist();
      }
    }
    if (!this.transport.hasGrant()) return;
    const locks = this.browser.navigator.locks;
    if (locks) {
      const options: LockOptions = keepalive ? { ifAvailable: true } : { signal: this.signal.signal };
      await locks.request(SENDER, options, async lock => {
        if (lock) await this.deliver(writer, keepalive);
      });
    } else await this.deliver(writer, keepalive);
  }

  private onDriveFailure(error: unknown) {
    if (this.signal.signal.aborted) return;
    if (error instanceof WindowAccountChanged) { this.invalidate(); return; }
    if (error instanceof WindowStorageError) { this.storageUnavailable(); return; }
    if (error instanceof WindowHttpError && error.unsupported) { this.disableDelivery('unsupported'); return; }
    if (error instanceof WindowHttpError && error.status === 403) { this.disableDelivery('v3_disabled'); return; }
    this.grantFailures += 1;
    if (this.grantFailures >= MAX_ATTEMPTS) { this.disableDelivery('retry_exhausted'); return; }
    const retryAfter = error instanceof WindowHttpError ? error.retryAfter : 0;
    this.grantRetryAt = this.options.source.now().mono + backoff(this.grantFailures, this.options.source.random(), retryAfter);
  }

  private disableDelivery(reason: CoverageReason) {
    this.loss(reason);
    this.disabled = true;
    this.active = undefined;
    this.persist();
    const writer = this.writer;
    if (writer) {
      this.writes = this.writes.then(async () => {
        await this.outbox.update(this.subject, writer, state => noteLoss(state, reason));
      }).catch(() => this.storageUnavailable());
    }
  }

  private async deliver(writer: Writer, keepalive: boolean) {
    await this.writes;
    if (this.signal.signal.aborted || !this.browser.navigator.onLine) return;
    const now = this.transport.project(this.options.source.now());
    if (now === null) return;
    const claim = await this.outbox.update(this.subject, writer, state => {
      for (const reason of this.losses) noteLoss(state, reason);
      return claimSend(state, now, false, this.tab);
    }).catch(() => { this.storageUnavailable(); return null; });
    if (!claim || this.signal.signal.aborted) return;

    let outcome: AckOutcome;
    let retryAfter = 0;
    try {
      const digest = await snapshotHash(claim.pending.snapshot);
      const body = await this.transport.send(claim.pending.body, this.signal.signal, keepalive);
      outcome = checkWindowAck(body, claim.pending.snapshot, digest);
    } catch (error) {
      if (this.signal.signal.aborted) return;
      outcome = { kind: 'retry', reason: 'v3_unconfirmed' };
      if (error instanceof WindowHttpError) {
        retryAfter = error.retryAfter;
        if (error.unsupported) this.onDriveFailure(error);
        else if (error.status === 401) {
          if (claim.pending.reauth) outcome = { kind: 'quarantine', reason: 'v3_unconfirmed' };
          else {
            this.transport.clearGrant();
            outcome = { ...outcome, authRefresh: true };
          }
        } else if (error.status === 400 || error.status === 403 || error.status === 413) {
          outcome = { kind: 'quarantine', reason: 'v3_unconfirmed' };
        }
      }
    }
    if (this.signal.signal.aborted) return;
    const after = this.transport.project(this.options.source.now()) ?? now;
    const nextAt = after + backoff(claim.pending.attempts, this.options.source.random(), retryAfter);
    await this.outbox.update(this.subject, writer, state => {
      settleSend(state, claim, outcome, nextAt);
      for (const reason of this.losses) noteLoss(state, reason);
    }).catch(() => this.storageUnavailable());
    this.options.observe?.({ kind: 'ack', claim, outcome });
  }

  async inspect(): Promise<{ partition?: Partition; losses: CoverageReason[]; ready: boolean }> {
    const partition = await this.outbox.transact(state => ({ result: state?.subject === this.subject ? state : undefined }));
    return { partition, losses: [...this.losses], ready: this.transport.hasGrant() };
  }
}
