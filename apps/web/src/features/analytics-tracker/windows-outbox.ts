import {
  HEARTBEAT, LEASE_DURATION, MAX_ATTEMPTS, MAX_KEYS, MAX_OUTBOX_BYTES, MAX_SNAPSHOT_BYTES,
  OUTBOX_AGE, REQUEST_TIMEOUT, SEND_INTERVAL, WINDOW_MS, bytes, positionId, sortStats, splitWindowSpan, windowEnd, windowId,
  type Ack, type AckOutcome, type CoverageReason, type Snapshot, type Span, type Stat, type WindowKey, type Writer,
} from './windows';

const DB_NAME = 'knowi:analytics:windows:v1';
const STORE = 'partition';
const KEY = 'current';

type Pending = { snapshot: Snapshot; body: string; attempts: number; nextAt: number; reauth?: true; quarantine?: CoverageReason };
type Draft = {
  key: WindowKey; stats: Stat[]; versions: Record<string, string>; seq: number; nextDue: number;
  dirty: boolean; clientFinalSeq?: number; pending?: Pending; confirmed?: Ack; reason?: CoverageReason;
};
export type Lease = { token: string; startedAt: number; until: number };
export type Partition = {
  subject: string;
  writer: Writer;
  lease?: Lease;
  contender?: Lease;
  cursor?: number;
  windows: Record<string, Draft>;
  sendTimes: number[];
  reasons: CoverageReason[];
};
export type SendClaim = { key: string; pending: Pending };
export type LeaseResult = { owned: boolean; consumed: boolean };

export const emptyPartition = (subject: string, writer: Writer): Partition => ({
  subject, writer, windows: {}, sendTimes: [], reasons: [],
});

export function noteLoss(state: Partition, reason: CoverageReason) {
  if (!state.reasons.includes(reason)) state.reasons.push(reason);
}

function quarantine(state: Partition, draft: Draft, reason: CoverageReason) {
  noteLoss(state, reason);
  draft.reason = reason;
  if (draft.pending) draft.pending.quarantine = reason;
}

function addSpan(state: Partition, span: Span) {
  const start = Math.max(span.start, state.cursor ?? span.start);
  if (start >= span.end) return;
  state.cursor = span.end;
  for (const piece of splitWindowSpan({ ...span, start })) {
    const key = { activity_date: piece.activity_date, slot_no: piece.slot_no };
    const id = windowId(key);
    const draft = state.windows[id] ?? (state.windows[id] = {
      key, stats: [], versions: {}, seq: 0, nextDue: piece.start + SEND_INTERVAL, dirty: false,
    });
    if (draft.reason || draft.clientFinalSeq) continue;
    const { position, catalogVersion } = piece.context;
    if (position.course_id && catalogVersion) {
      const known = Object.prototype.hasOwnProperty.call(draft.versions, position.course_id) ? draft.versions[position.course_id] : undefined;
      if (known && known !== catalogVersion) {
        quarantine(state, draft, 'catalog_conflict');
        continue;
      }
      draft.versions = { ...draft.versions, [position.course_id]: catalogVersion };
    }
    const duration = piece.end - piece.start;
    const existing = draft.stats.find(row => positionId(row) === positionId(position));
    const total = draft.stats.reduce((sum, row) => sum + row.active_ms, 0);
    if (total + duration > WINDOW_MS || (!existing && draft.stats.length >= 32)) {
      quarantine(state, draft, 'queue_overflow');
      continue;
    }
    if (existing) existing.active_ms += duration;
    else draft.stats.push({ ...position, active_ms: duration });
    draft.dirty = true;
  }
}

export function updateLease(
  state: Partition, token: string, now: number, startedAt: number | null, spans: Span[], releasing: boolean,
): LeaseResult {
  const previous = state.lease;
  const available = !previous || previous.until <= now;
  const candidate = startedAt === null || releasing ? undefined : { token, startedAt, until: now + LEASE_DURATION };
  const outranks = (a: Lease, b: Lease) => a.startedAt > b.startedAt || (a.startedAt === b.startedAt && a.token > b.token);
  if (state.contender && state.contender.until <= now) delete state.contender;
  if (!available && previous.token !== token) {
    if (releasing && state.contender?.token === token) delete state.contender;
    if (candidate && outranks(candidate, previous) && (!state.contender || outranks(candidate, state.contender) ||
      candidate.token === state.contender.token)) state.contender = candidate;
    return { owned: false, consumed: spans.length === 0 };
  }
  if (previous && available) {
    noteLoss(state, 'lease_lost');
    state.cursor = Math.max(state.cursor ?? 0, now);
    delete state.lease;
  }
  const sameLease = previous?.token === token && !available;
  const contender = state.contender;
  const yieldTo = contender && contender.token !== token && (!candidate || outranks(contender, candidate))
    ? contender : undefined;
  if (yieldTo && !sameLease && !releasing) return { owned: false, consumed: spans.length === 0 };
  if ((sameLease || startedAt !== null) && spans.length) {
    for (const span of spans) addSpan(state, yieldTo ? { ...span, end: Math.min(span.end, yieldTo.startedAt) } : span);
    trim(state, now);
  }
  if (releasing || !candidate || yieldTo) delete state.lease;
  else {
    state.lease = candidate;
    delete state.contender;
  }
  return { owned: Boolean(state.lease), consumed: true };
}

function trim(state: Partition, now: number) {
  for (const [key, draft] of Object.entries(state.windows)) {
    if (now <= windowEnd(draft.key) + OUTBOX_AGE) continue;
    if (draft.dirty || draft.pending || !draft.clientFinalSeq) noteLoss(state, 'expired');
    delete state.windows[key];
  }
  const ordered = oldestFirst(state);
  while (ordered.length > MAX_KEYS || (ordered.length && bytes(JSON.stringify(state.windows)) > MAX_OUTBOX_BYTES)) {
    delete state.windows[ordered.shift()![0]];
    noteLoss(state, 'queue_overflow');
  }
}

function oldestFirst(state: Partition) {
  return Object.entries(state.windows).sort(([, a], [, b]) => windowEnd(a.key) - windowEnd(b.key));
}

function stageSnapshot(state: Partition, draft: Draft, now: number) {
  if (draft.pending || draft.reason || !draft.stats.length) return;
  const end = windowEnd(draft.key);
  const final = now >= end;
  if (final && state.lease && state.lease.until > now && (state.cursor ?? 0) < end && now < end + LEASE_DURATION) return;
  if ((final && draft.clientFinalSeq) || (!final && (!draft.dirty || now < draft.nextDue))) return;
  // A no-op context refresh can request a flush between heartbeat checkpoints.
  if (!final && state.lease && state.lease.until > now && (state.cursor ?? 0) < draft.nextDue) return;
  const snapshot: Snapshot = {
    ...draft.key, ...state.writer, server_epoch: 1, seq: draft.seq + 1,
    stats: sortStats(draft.stats).map(row => ({ ...row })),
    catalog_versions: { ...draft.versions }, client_final: final,
  };
  if (bytes(JSON.stringify(snapshot)) > MAX_SNAPSHOT_BYTES) {
    quarantine(state, draft, 'queue_overflow');
    return;
  }
  draft.seq = snapshot.seq;
  draft.dirty = false;
  draft.nextDue = now + SEND_INTERVAL;
  if (final) draft.clientFinalSeq = snapshot.seq;
  draft.pending = { snapshot, body: JSON.stringify({ protocol_version: 1, windows: [snapshot] }), attempts: 0, nextAt: now };
}

export function claimSend(state: Partition, now: number, offline: boolean, token?: string): SendClaim | null {
  trim(state, now);
  state.sendTimes = state.sendTimes.filter(start => now - start < SEND_INTERVAL);
  if (offline || state.sendTimes.length >= 12) return null;
  if (!state.lease || state.lease.until <= now || state.lease.token === token) {
    for (const [, draft] of oldestFirst(state)) stageSnapshot(state, draft, now);
  }
  trim(state, now);
  for (const [key, draft] of oldestFirst(state)) {
    const pending = draft.pending;
    if (!pending || pending.quarantine || pending.nextAt > now) continue;
    if (pending.attempts >= MAX_ATTEMPTS) {
      quarantine(state, draft, 'retry_exhausted');
      continue;
    }
    pending.attempts += 1;
    pending.nextAt = now + REQUEST_TIMEOUT + HEARTBEAT;
    state.sendTimes.push(now);
    return { key, pending: structuredClone(pending) };
  }
  return null;
}

export function settleSend(state: Partition, claim: SendClaim, outcome: AckOutcome, nextAt: number) {
  const draft = state.windows[claim.key];
  if (!draft?.pending || draft.pending.body !== claim.pending.body || draft.pending.attempts !== claim.pending.attempts) return;
  if (outcome.kind === 'accepted') {
    draft.confirmed = outcome.ack;
    delete draft.pending;
    return;
  }
  if (outcome.kind === 'quarantine') {
    quarantine(state, draft, outcome.reason);
    return;
  }
  if (outcome.reason === 'writer_conflict') noteLoss(state, outcome.reason);
  draft.pending.nextAt = nextAt;
  if (outcome.authRefresh) draft.pending.reauth = true;
  if (draft.pending.attempts >= MAX_ATTEMPTS) quarantine(state, draft, 'retry_exhausted');
}

export class WindowStorageError extends Error {}

export class WindowOutbox {
  private database?: Promise<IDBDatabase>;

  constructor(private readonly factory: () => IDBFactory) {}

  private open() {
    return this.database ??= new Promise<IDBDatabase>((resolve, reject) => {
      const request = this.factory().open(DB_NAME, 1);
      request.onupgradeneeded = () => request.result.createObjectStore(STORE);
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error('blocked'));
      request.onsuccess = () => {
        const database = request.result;
        database.onversionchange = () => database.close();
        resolve(database);
      };
    }).catch(() => { throw new WindowStorageError(); });
  }

  close() {
    void this.database?.then(database => database.close(), () => undefined);
    this.database = undefined;
  }

  async transact<T>(mutate: (state: Partition | undefined) => { state?: Partition; result: T }): Promise<T> {
    const database = await this.open();
    return new Promise<T>((resolve, reject) => {
      const transaction = database.transaction(STORE, 'readwrite', { durability: 'strict' });
      const store = transaction.objectStore(STORE);
      const request = store.get(KEY);
      let result: T;
      let failure: unknown;
      request.onsuccess = () => {
        try {
          const update = mutate(request.result as Partition | undefined);
          result = update.result;
          if (update.state) store.put(update.state, KEY);
        } catch (error) {
          failure = error;
          transaction.abort();
        }
      };
      transaction.oncomplete = () => resolve(result);
      transaction.onabort = () => reject(failure ?? new WindowStorageError());
      transaction.onerror = () => reject(new WindowStorageError());
    });
  }

  update<T>(subject: string, writer: Writer, mutate: (state: Partition) => T): Promise<T | undefined> {
    return this.transact(state => {
      if (!state || state.subject !== subject || state.writer.writer_id !== writer.writer_id ||
        state.writer.writer_generation !== writer.writer_generation) return { result: undefined };
      return { state, result: mutate(state) };
    });
  }
}
