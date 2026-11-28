"""Bounded, Privacy-guarded historical migration of Analytics activity windows."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import text

from pymentor_analytics.adapters.postgres.student_windows import PostgresStudentWindows
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.metadata_v1 import MAX_LATENESS
from pymentor_analytics.contracts.v2 import MAX_INTERVAL_MS, AcceptedEvent
from pymentor_analytics.contracts.windows_v1 import (
    ReconcileDecision, WindowKey, WindowStat, apply_reconciliation, canonical_stats,
    late_deadline, window_bounds,
)
from pymentor_analytics.modules.analytics.activity.bucket import MAX_BUCKET_FACTS, MAX_BUCKET_INPUT_BYTES, BucketOverflow
from pymentor_analytics.modules.analytics.activity.student_windows_reconcile import _measure
from pymentor_analytics.modules.collector.ports import IntakeForbidden

RUN = "analytics.window_backfill_run"
WORK = "analytics.window_backfill_work"
CURSOR = "analytics.window_backfill_scan_cursor"
JOURNAL = "analytics.window_backfill_journal"
GATE = "analytics.window_backfill_gate"
FACTS = "analytics.activity_facts"
FENCES = "analytics.subject_fences"
KEY = "student_id,activity_date,slot_no"
KEY_ORDER = "student_id,activity_date,slot_no"
KEY_FILTER = "student_id=:student_id AND activity_date=:activity_date AND slot_no=:slot_no"
ADMISSION_MARGIN = timedelta(milliseconds=MAX_INTERVAL_MS)

SCHEMA_READY = text("""SELECT count(*)=3 FROM pg_trigger
    WHERE NOT tgisinternal AND tgenabled IN ('O','A') AND
    ((tgrelid=to_regclass('analytics.activity_facts') AND tgname='window_backfill_fact_changed') OR
     (tgrelid=to_regclass('analytics.subject_fences') AND tgname='window_backfill_subject_deleted') OR
     (tgrelid=to_regclass('analytics.privacy_receipts') AND tgname='window_backfill_before_privacy_complete'))""")
MEASURED = """source_kind IN ('browser_v2','browser_tool_v1')
    AND accepted_event#>>'{event,type}' IN ('activity_segment','tool_segment')"""
READ_FACTS = text(f"""SELECT accepted_event FROM {FACTS}
    WHERE subject_ref=:student_id AND started_at>=:earliest AND started_at<:end AND {MEASURED}
    ORDER BY started_at,producer_namespace,event_id LIMIT :limit""")


class BackfillError(ValueError):
    """A durable prerequisite for transferring or verifying the source is missing."""


@dataclass(frozen=True)
class LiveProof:
    """Independently collected exact total and the live confirmation it covers."""
    stats: tuple[WindowStat, ...]
    live_sha256: str
    source_generation: int
    change_generation: int
    post_t0_source_sha256: str


def json_digest(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def stat_values(stats):
    return [row.model_dump(mode="json") for row in stats]


def live_digest(current):
    return json_digest(current.model_dump(mode="json") if current is not None else None)


def possible_coordinates(start):
    # Old writers store position and typed start. A maximum-length fact can touch
    # no more than two slots. False candidates are removed by exact normalization.
    # The SQL trigger deliberately does not parse or reinterpret JSON timestamps.
    earliest = start.replace(microsecond=(start.microsecond // 1000) * 1000)
    starts = (earliest, earliest + ADMISSION_MARGIN - timedelta(milliseconds=1))
    local = (stamp.astimezone(ZoneInfo("Europe/Moscow")) for stamp in starts)
    return {(stamp.date(), stamp.hour * 4 + stamp.minute // 15 + 1) for stamp in local}


async def source_window(session, key):
    start, end = window_bounds(key)
    raw = (await session.execute(READ_FACTS, {
        "student_id": key.student_id, "earliest": start - ADMISSION_MARGIN,
        "end": end, "limit": MAX_BUCKET_FACTS + 1,
    })).scalars().all()
    encoded = [json.dumps(value, separators=(",", ":")) for value in raw]
    if len(encoded) > MAX_BUCKET_FACTS or sum(len(value.encode()) for value in encoded) > MAX_BUCKET_INPUT_BYTES:
        raise BucketOverflow("historical window exceeds the normalization bound")
    return tuple(AcceptedEvent.model_validate_json(value) for value in encoded)


def _normalization(facts, key):
    start, end = window_bounds(key)
    stats, versions, _, _ = _measure(facts, key, start=start, end=end)
    return canonical_stats(stats), versions


async def normalized_window(session, key):
    return _normalization(await source_window(session, key), key)


def post_t0_source_digest(facts, key, t0):
    start, end = window_bounds(key)
    return json_digest([item.model_dump(mode="json") for item in facts
                        if item.event.started_at < end and item.event.ended_at > max(start, t0)])


class StudentWindowsBackfill:
    def __init__(self, database, *, run_id: UUID, t0: datetime, proof=None):
        if t0.utcoffset() is None:
            raise ValueError("aware deployment T0 required")
        self.database = database
        self.run_id = run_id
        self.t0 = t0
        self.store = PostgresStudentWindows(database, cohort=())
        self.proof = dict(proof or {})

    async def _run(self, session, *, lock=True):
        suffix = " FOR UPDATE OF r" if lock else ""
        row = (await session.execute(text(f"""SELECT r.*,c.subject_ref AS scan_subject,c.scan_namespace,c.scan_event
            FROM {RUN} r LEFT JOIN {CURSOR} c USING (singleton) WHERE r.singleton""" + suffix))).mappings().one_or_none()
        if row is not None and (row["run_id"], row["t0"]) != (self.run_id, self.t0):
            raise BackfillError("another historical run or T0 owns this database")
        return row

    async def _require_run(self, session, *, lock=True):
        row = await self._run(session, lock=lock)
        if row is None:
            raise BackfillError("capture checkpoint absent; run plan first")
        return row

    async def _capture_barrier(self, session):
        if not (await session.execute(SCHEMA_READY)).scalar_one():
            raise BackfillError("historical change-capture triggers are unavailable")
        await session.execute(text("SELECT pg_advisory_xact_lock(280608, 1)"))

    async def _journal_high(self, session):
        return (await session.execute(text(f"SELECT COALESCE(max(position),0) FROM {JOURNAL}"))).scalar_one()

    async def plan(self):
        self.store._guard()
        async with self.database.transaction() as session:
            # The operator lock is separate from the source lock and does not
            # depend on a run row already existing.
            await session.execute(text("SELECT pg_advisory_xact_lock(280608, 2)"))
            if await self._run(session) is None:
                await self._capture_barrier(session)
                position = await self._journal_high(session)
                await session.execute(text(f"""INSERT INTO {RUN}
                    (run_id,t0,snapshot,snapshot_at,snapshot_position,accepted_position,applied_position)
                    VALUES (:run_id,:t0,pg_current_snapshot()::text,clock_timestamp(),:position,:position,:position)
                """), {"run_id": self.run_id, "t0": self.t0, "position": position})
            return dict(await self._require_run(session))

    async def barrier(self, *, close=False):
        """Observe a finite committed cut. Closing additionally fences future v2 and tool writes."""
        self.store._guard()
        async with self.database.transaction() as session:
            row = await self._require_run(session)
            await self._capture_barrier(session)
            if close and row["final_position"] is not None:
                return dict(row)
            if close and not row["scan_complete"]:
                raise BackfillError("initial source scan must finish before closing legacy writes")
            position = max(row["accepted_position"], await self._journal_high(session))
            if close:
                await session.execute(text(f"UPDATE {GATE} SET v2_closed=true WHERE singleton"))
            await session.execute(text(f"""UPDATE {RUN}
                SET accepted_position=:position,final_position=CASE WHEN :closed THEN :position ELSE final_position END
                WHERE singleton"""), {"position": position, "closed": close})
            return dict(await self._require_run(session))

    async def _enqueue(self, session, rows, *, cursor_subject=None):
        by_subject = {}
        for subject, start in rows:
            if subject is not None and start is not None:
                by_subject.setdefault(subject, set()).update(possible_coordinates(start))
        if cursor_subject is not None:
            by_subject.setdefault(cursor_subject, set())
        live = set()
        for subject, coordinates in sorted(by_subject.items()):
            args = {"student_id": subject, "lock": subject_lock_key(subject)}
            await session.execute(text("SELECT pg_advisory_xact_lock(:lock)"), args)
            if (await session.execute(text(f"SELECT deleted FROM {FENCES} WHERE subject_ref=:student_id"),
                                      args)).scalar_one_or_none() is True:
                continue
            live.add(subject)
            if not coordinates:
                continue
            await session.execute(text(f"""INSERT INTO {WORK}({KEY})
                VALUES (:student_id,:activity_date,:slot_no)
                ON CONFLICT ({KEY}) DO UPDATE SET result=NULL
                WHERE {WORK}.result IS NOT NULL"""), [
                    {"student_id": subject, "activity_date": day, "slot_no": slot}
                    for day, slot in sorted(coordinates)
                ])
        return live

    async def scan(self, size):
        """Seed in physical source-key order. Late inserts below it are independently journaled."""
        async with self.database.transaction() as session:
            state = await self._require_run(session)
            if state["scan_complete"]:
                return 0
            rows = (await session.execute(text(f"""SELECT producer_namespace,event_id,subject_ref,
                  CASE WHEN {MEASURED} THEN started_at END AS measured_start
                FROM {FACTS} f
                WHERE (CAST(:namespace AS text) IS NULL OR (producer_namespace,event_id) >
                      (CAST(:namespace AS text),CAST(:event AS uuid)))
                  AND NOT EXISTS (SELECT FROM {FENCES} s WHERE s.subject_ref=f.subject_ref AND s.deleted)
                ORDER BY producer_namespace,event_id LIMIT :limit"""),
                {"namespace": state["scan_namespace"], "event": state["scan_event"], "limit": size})).all()
            if rows:
                last = rows[-1]
                live = await self._enqueue(session, [(row.subject_ref, row.measured_start) for row in rows],
                                           cursor_subject=last.subject_ref)
                if last.subject_ref in live:
                    await session.execute(text(f"""INSERT INTO {CURSOR}(subject_ref,scan_namespace,scan_event)
                        VALUES (:subject,:namespace,:event) ON CONFLICT (singleton) DO UPDATE SET
                        subject_ref=excluded.subject_ref,scan_namespace=excluded.scan_namespace,scan_event=excluded.scan_event"""),
                        {"subject": last.subject_ref, "namespace": last.producer_namespace, "event": last.event_id})
                    await session.execute(text(f"UPDATE {RUN} SET scanned_facts=scanned_facts+:count WHERE singleton"),
                                          {"count": len(rows)})
            else:
                await session.execute(text(f"UPDATE {RUN} SET scan_complete=true WHERE singleton"))
                await session.execute(text(f"DELETE FROM {CURSOR} WHERE singleton"))
            return len(rows)

    async def catch_up(self, size):
        """Advance only after the affected keys are durably enqueued in the same transaction."""
        async with self.database.transaction() as session:
            state = await self._require_run(session)
            rows = (await session.execute(text(f"""SELECT position,subject_ref,measured_start FROM {JOURNAL}
                WHERE position<=:accepted ORDER BY position LIMIT :limit"""),
                {"accepted": state["accepted_position"], "limit": size})).all()
            if rows:
                await self._enqueue(session, [(row.subject_ref, row.measured_start) for row in rows
                                              if row.position > state["snapshot_position"]])
                await session.execute(text(f"DELETE FROM {JOURNAL} WHERE position<=:position"),
                                      {"position": rows[-1].position})
            tail = rows[-1].position if rows else state["accepted_position"]
            await session.execute(text(f"UPDATE {RUN} SET applied_position=GREATEST(applied_position,:tail) WHERE singleton"),
                                  {"tail": tail})
            return len(rows)

    async def candidates(self, size):
        async with self.database.transaction() as session:
            await self._require_run(session, lock=False)
            result = await session.execute(text(f"""SELECT {KEY} FROM {WORK} WHERE result IS NULL
                ORDER BY {KEY_ORDER} LIMIT :limit"""), {"limit": size})
            return [dict(row) for row in result.mappings()]

    async def requeue_unresolved(self, size):
        proofs = [key.model_dump(mode="json") for key in sorted(
            self.proof, key=lambda key: (key.student_id, key.activity_date, key.slot_no))]
        async with self.database.transaction() as session:
            await self._require_run(session)
            await session.execute(text(f"""UPDATE {WORK} SET result=NULL WHERE ctid IN (
                SELECT work.ctid FROM {WORK} work WHERE
                    (result='still_collecting' AND
                     ((activity_date+slot_no*interval '15 minutes') AT TIME ZONE 'Europe/Moscow')
                      + CAST(:late AS interval) <= clock_timestamp())
                    OR (result='live_overlap_unknown' AND EXISTS (
                        SELECT FROM jsonb_to_recordset(CAST(:proofs AS jsonb)) proof(
                            student_id uuid, activity_date date, slot_no smallint)
                        WHERE (proof.student_id,proof.activity_date,proof.slot_no)=
                              (work.student_id,work.activity_date,work.slot_no)))
                ORDER BY {KEY_ORDER} LIMIT :limit
            )"""), {"proofs": json.dumps(proofs), "limit": size,
                    "late": MAX_LATENESS + ADMISSION_MARGIN})

    def _coverage(self, key, stats, current, work, now, post_t0_digest):
        prior = current.confirmed
        if prior is not None and prior.stats == stats:
            return "matched"
        if prior is None and not stats:
            return "empty"
        if window_bounds(key)[1] > self.t0 and now < late_deadline(key):
            return "still_collecting"
        if window_bounds(key)[1] <= self.t0 and (
            prior is None or (work["managed"] and work["revision"] == prior.revision and prior.writer_id is None)
        ):
            return "historical"
        if (prior is not None and work["managed"] and work["revision"] == prior.revision
                and work["post_t0_source_sha256"] == post_t0_digest):
            return "covered_live"
        proof = self.proof.get(key)
        if (proof is not None and proof.stats == stats and proof.live_sha256 == live_digest(prior)
                and proof.source_generation == current.source_generation
                and proof.change_generation == current.change_generation
                and proof.post_t0_source_sha256 == post_t0_digest):
            return "covered_live"
        return "live_overlap_unknown"

    async def apply_key(self, coords):
        self.store._guard()
        async with self.database.transaction() as session:
            await self._require_run(session)
            try:
                key = WindowKey(**coords)
            except ValidationError:
                await self._outcome(session, coords, "unsupported_interval", None, False)
                return "unsupported_interval"
            try:
                await self.store._exclusive(session, key)
            except IntakeForbidden:
                return "purged"
            work = (await session.execute(text(f"SELECT * FROM {WORK} WHERE {KEY_FILTER}"), coords)).mappings().one_or_none()
            if work is None or work["result"] is not None:
                return "already_processed"
            current = await self.store._read(session, key)
            try:
                facts = await source_window(session, key)
                stats, versions = _normalization(facts, key)
                post_t0_digest = post_t0_source_digest(facts, key, self.t0)
            except (BucketOverflow, ValidationError, ValueError):
                await self._outcome(session, coords, "unsupported_interval", work["revision"], work["managed"])
                return "unsupported_interval"
            now = (await session.execute(text("SELECT clock_timestamp()"))).scalar_one()
            result = self._coverage(key, stats, current, work, now, post_t0_digest)
            version = current.confirmed.revision if current.confirmed is not None else None
            managed = work["managed"] and version == work["revision"]
            owned_live = managed and work["post_t0_source_sha256"] is not None
            if result in {"historical", "covered_live"} or (
                result == "matched" and owned_live and current.state == "pending_reconciliation"
            ):
                decision = ReconcileDecision(current.source_generation, current.change_generation, stats,
                                             tuple(sorted({**dict(versions), **dict(current.catalog_versions)}.items())))
                changed = apply_reconciliation(current, decision, rebase_at=now)
                if changed is None:
                    raise BackfillError("window changed under its lifecycle and window locks")
                await self.store._save(session, key, current, changed)
                await session.execute(text(f"""UPDATE analytics.student_activity_window_control
                    SET reconciled_generation=:generation,recheck_at=NULL,
                        history_backfill_owned=history_backfill_owned OR :own WHERE {KEY_FILTER}
                      AND source_generation=:source AND change_generation=:changed"""),
                    {**coords, "generation": changed.reconciled_generation, "source": changed.source_generation,
                     "changed": changed.change_generation, "own": result == "covered_live"})
                version = changed.confirmed.revision
                managed = True
            proof_anchor = post_t0_digest if result == "covered_live" else work["post_t0_source_sha256"]
            await self._outcome(session, coords, result, version, managed, proof_anchor)
            await self.store._guard()(session)
            return result

    async def _outcome(self, session, coords, result, revision, managed, anchor=None):
        await session.execute(text(f"""UPDATE {WORK} SET result=:result,revision=:revision,managed=:managed,post_t0_source_sha256=:anchor
            WHERE {KEY_FILTER}"""), {**coords, "result": result, "revision": revision, "managed": managed, "anchor": anchor})
