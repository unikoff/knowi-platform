import type { Context, ToolId } from './tracker';

export const WINDOW_MS = 900_000;
export const HOUR_MS = 3_600_000;
export const OUTBOX_AGE = 24 * HOUR_MS;
export const SEND_INTERVAL = 60_000;
export const HEARTBEAT = 5_000;
export const LEASE_DURATION = 15_000;
export const MAX_CLOCK_GAP = 20_000;
export const IDLE_LIMIT = 300_000;
export const REQUEST_TIMEOUT = 10_000;
export const MAX_ATTEMPTS = 8;
export const MAX_KEYS = 96;
export const MAX_OUTBOX_BYTES = 512 * 1024;
export const MAX_SNAPSHOT_BYTES = 16 * 1024;
const MOSCOW_OFFSET = 3 * HOUR_MS;

export const WINDOWS_AUTH_BOUNDARY = 'pymentor:windows:session-changing';
export type WindowKey = { activity_date: string; slot_no: number };
export type Writer = { writer_id: string; writer_generation: number };
export type Position = { tool_id: ToolId; course_id: string | null; lesson_id: string | null };
export type Stat = Position & { active_ms: number };
export type WindowContext = { position: Position; catalogVersion?: string };
export type Snapshot = WindowKey & Writer & {
  server_epoch: number;
  seq: number;
  stats: Stat[];
  catalog_versions: Record<string, string>;
  client_final: boolean;
};
export type Span = { start: number; end: number; context: WindowContext };
export type WindowPiece = WindowKey & Span;
export type CoverageReason =
  | 'clock_untrusted' | 'lease_lost' | 'writer_conflict' | 'catalog_conflict'
  | 'storage_denied' | 'queue_overflow' | 'retry_exhausted' | 'expired'
  | 'account_change' | 'unsupported' | 'epoch_rebase_required' | 'v3_unconfirmed' | 'v3_disabled';
export type Confirmed = Writer & {
  server_epoch: number;
  seq: number;
  payload_hash: string;
  revision: number;
  stats: Stat[];
  client_final: boolean;
  rebase_at: string | null;
};
export type Ack = WindowKey & Writer & {
  index: number;
  server_epoch: number;
  seq: number;
  payload_hash: string | null;
  status: string;
  code: string;
  confirmed: Confirmed | null;
};

export function mapWindowContext(context: Context | null): WindowContext | null {
  if (!context) return null;
  if (context.toolId) return {
    position: { tool_id: context.toolId, course_id: context.courseId === 'platform-tools' ? null : context.courseId, lesson_id: null },
  };
  return {
    position: { tool_id: context.section, course_id: context.courseId, lesson_id: context.lessonId },
    catalogVersion: context.catalogVersion,
  };
}

export const windowId = (key: WindowKey) => key.activity_date + '/' + key.slot_no;

export function windowAt(utc: number): WindowKey {
  const local = utc + MOSCOW_OFFSET;
  const startOfDay = Math.floor(local / (24 * HOUR_MS)) * (24 * HOUR_MS);
  return {
    activity_date: new Date(startOfDay).toISOString().slice(0, 10),
    slot_no: 1 + Math.floor((local - startOfDay) / WINDOW_MS),
  };
}

export function windowEnd(key: WindowKey): number {
  return Date.parse(key.activity_date + 'T00:00:00.000+03:00') + key.slot_no * WINDOW_MS;
}

export function splitWindowSpan(span: Span): WindowPiece[] {
  const pieces: WindowPiece[] = [];
  let start = Math.floor(span.start);
  const end = Math.floor(span.end);
  while (start < end) {
    const key = windowAt(start);
    const cut = Math.min(end, windowEnd(key));
    pieces.push({ ...key, ...span, start, end: cut });
    start = cut;
  }
  return pieces;
}

export const positionId = (position: Position) => JSON.stringify([position.tool_id, position.course_id, position.lesson_id]);
export const bytes = (body: string) => new TextEncoder().encode(body).length;
const compare = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;
export const sortStats = (stats: Stat[]) => [...stats].sort((a, b) =>
  compare(a.tool_id, b.tool_id) || compare(a.course_id ?? '', b.course_id ?? '') ||
    compare(a.lesson_id ?? '', b.lesson_id ?? ''));

function canonicalJson(value: unknown): string {
  if (!value || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(canonicalJson).join(',') + ']';
  const entries = Object.entries(value).sort(([a], [b]) => compare(a, b));
  return '{' + entries.map(([key, entry]) => JSON.stringify(key) + ':' + canonicalJson(entry)).join(',') + '}';
}

export async function snapshotHash(snapshot: Snapshot): Promise<string> {
  const normalized = { ...snapshot, writer_id: snapshot.writer_id.toLowerCase(), stats: sortStats(snapshot.stats) };
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonicalJson(normalized)));
  return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
}

const matchingTuple = (left: unknown, snapshot: Snapshot) => {
  if (!left || typeof left !== 'object') return false;
  const item = left as Partial<Snapshot>;
  return item.writer_id === snapshot.writer_id && item.writer_generation === snapshot.writer_generation &&
    item.server_epoch === snapshot.server_epoch && item.seq === snapshot.seq;
};

const rejectedCodes = new Set([
  'invalid_snapshot', 'subject_forbidden', 'writer_forbidden', 'course_forbidden', 'tool_forbidden',
  'invalid_active_ms', 'window_budget_exceeded', 'catalog_conflict', 'too_large', 'future_clock', 'too_late', 'privacy_forbidden',
]);

export type AckOutcome =
  | { kind: 'accepted'; ack: Ack }
  | { kind: 'quarantine'; reason: CoverageReason; ack?: Ack }
  | { kind: 'retry'; reason: CoverageReason; ack?: Ack; authRefresh?: true };

export function checkWindowAck(body: unknown, snapshot: Snapshot, digest: string): AckOutcome {
  const unknown: AckOutcome = { kind: 'retry', reason: 'v3_unconfirmed' };
  if (!body || typeof body !== 'object') return unknown;
  const envelope = body as { protocol_version?: unknown; items?: Ack[] };
  if (envelope.protocol_version !== 1 || !Array.isArray(envelope.items) || envelope.items.length !== 1) return unknown;
  const ack = envelope.items[0];
  if (!ack || ack.index !== 0 || ack.activity_date !== snapshot.activity_date || ack.slot_no !== snapshot.slot_no ||
    !matchingTuple(ack, snapshot)) return unknown;
  if (ack.status === 'rejected' && (ack.payload_hash === null || ack.payload_hash === digest) && rejectedCodes.has(ack.code)) {
    return { kind: 'quarantine', reason: ack.code === 'catalog_conflict' ? 'catalog_conflict' : 'v3_unconfirmed', ack };
  }
  if (ack.payload_hash !== digest) return unknown;
  if (ack.status === 'pending' && ack.code === 'ownership_conflict') return { kind: 'retry', reason: 'writer_conflict', ack };
  if (ack.status === 'stale' && ['stale_epoch', 'stale_seq', 'finalized'].includes(ack.code)) {
    return { kind: 'quarantine', reason: 'epoch_rebase_required', ack };
  }
  if (ack.status === 'conflict' && ['payload_conflict', 'non_monotonic', 'catalog_conflict'].includes(ack.code)) {
    return { kind: 'quarantine', reason: ack.code === 'catalog_conflict' ? 'catalog_conflict' : 'v3_unconfirmed', ack };
  }
  const success = (ack.status === 'committed' && ack.code === 'committed') ||
    (ack.status === 'duplicate' && ack.code === 'same_payload');
  const confirmed = ack.confirmed;
  if (!success || !confirmed || !matchingTuple(confirmed, snapshot) || confirmed.payload_hash !== digest ||
    !Number.isSafeInteger(confirmed.revision) || confirmed.revision <= 0 || confirmed.client_final !== snapshot.client_final ||
    !Array.isArray(confirmed.stats) || canonicalJson(confirmed.stats) !== canonicalJson(sortStats(snapshot.stats))) return unknown;
  return { kind: 'accepted', ack };
}

export function backoff(attempts: number, random: number, retryAfter: number): number {
  const delay = Math.min(60_000, 1000 * 2 ** Math.min(attempts - 1, 6));
  return Math.max(retryAfter, Math.floor(delay * (0.5 + random * 0.5)));
}
