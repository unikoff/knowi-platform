"""Transactional v3 window storage and the shared v2 changed-key port."""
from collections.abc import Iterable, Mapping, Set
from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.v2 import AcceptedEvent
from pymentor_analytics.contracts.windows_v1 import (
    ConfirmedWindow, ReconcileDecision, ReconciliationInput, Transition, WindowKey, WindowSnapshot,
    WindowStat, WindowState, apply_reconciliation, receive_snapshot, validate_window_age, window_bounds,
)
from pymentor_analytics.modules.analytics.activity.bucket import MAX_BUCKET_FACTS
from pymentor_analytics.modules.collector.ports import IntakeForbidden, IntakeUnavailable

_LOCK = text("SELECT pg_advisory_xact_lock(:lock_key)")
_FENCE = text("SELECT deleted FROM analytics.subject_fences WHERE subject_ref=:student_id")
_CONTROL = "analytics.student_activity_window_control"
_WINDOWS = "analytics.student_activity_windows"
_KEY = "student_id,activity_date,slot_no"
_WHERE = "student_id=:student_id AND activity_date=:activity_date AND slot_no=:slot_no"
_STATE_COLUMNS = (
    "writer_id", "writer_generation", "server_epoch", "seq", "payload_hash", "revision",
    "client_final", "rebase_at",
)
_UPDATE_COLUMNS = (*_STATE_COLUMNS, "catalog_versions", "phase", "source_generation",
                   "change_generation", "coverage_reasons")
_PARAMS = ",".join(
    "CAST(:catalog_versions AS jsonb)" if name == "catalog_versions" else f":{name}"
    for name in _UPDATE_COLUMNS
)
_STATE_UPSERT = text(f"""INSERT INTO {_CONTROL}({_KEY},{','.join(_UPDATE_COLUMNS)})
    VALUES (:student_id,:activity_date,:slot_no,{_PARAMS})
    ON CONFLICT ({_KEY}) DO UPDATE SET
        {','.join(f'{name}=excluded.{name}' for name in _UPDATE_COLUMNS)}""")
_MARK_V2 = text(f"""INSERT INTO {_CONTROL} AS current({_KEY},source_generation,change_generation)
    VALUES (:student_id,:activity_date,:slot_no,1,1)
    ON CONFLICT ({_KEY}) DO UPDATE SET source_generation=current.source_generation+1,
        change_generation=current.change_generation+1,phase='pending_reconciliation'""")
_READ = f"""SELECT c.*,w.stats,w.revision AS business_revision
    FROM {_CONTROL} c LEFT JOIN {_WINDOWS} w USING ({_KEY})
    WHERE c.student_id=:student_id AND c.activity_date=:activity_date AND c.slot_no=:slot_no"""


async def has_student_windows_schema(session: AsyncSession) -> bool:
    checks = (await session.execute(text(f"""SELECT
        to_regclass('{_CONTROL}') IS NOT NULL,
        to_regclass('{_WINDOWS}') IS NOT NULL,
        to_regprocedure('analytics.require_student_windows_purged()') IS NOT NULL,
        EXISTS (SELECT FROM pg_trigger
                WHERE tgrelid=to_regclass('analytics.privacy_receipts')
                  AND tgname='student_windows_before_privacy_complete' AND tgenabled IN ('O','A')),
        EXISTS (SELECT FROM pg_attribute
                WHERE attrelid=to_regclass('{_CONTROL}') AND attname='recheck_at' AND NOT attisdropped)
    """))).one()
    if any(checks) and not all(checks):
        raise IntakeUnavailable("incomplete student window schema")
    return all(checks)


def _key_args(key: WindowKey) -> dict[str, Any]:
    return key.model_dump()


def _window_lock(key: WindowKey) -> int:
    value = f"analytics:student-window:v1:{key.student_id}:{key.activity_date}:{key.slot_no}"
    return int.from_bytes(sha256(value.encode("ascii")).digest()[:8], "big", signed=True)


def _window_state(row) -> WindowState:
    if row is None:
        return WindowState()
    confirmed = None
    if row.revision is not None:
        if row.business_revision is not None and row.revision != row.business_revision:
            raise IntakeUnavailable("student window versions disagree")
        confirmed = ConfirmedWindow(
            **{name: getattr(row, name) for name in _STATE_COLUMNS},
            stats=tuple(WindowStat.model_validate(item) for item in (row.stats or [])),
        )
    elif row.business_revision is not None:
        raise IntakeUnavailable("student window confirmation is missing")
    return WindowState(confirmed=confirmed, catalog_versions=tuple(sorted(row.catalog_versions.items())),
                       state=row.phase, source_generation=row.source_generation,
                       change_generation=row.change_generation, reconciled_generation=row.reconciled_generation,
                       coverage_reasons=tuple(row.coverage_reasons))


class PostgresStudentWindows:
    def __init__(self, database: Database, *, cohort: Iterable[UUID],
                 observation_start: datetime | None = None, all_eligible: bool = False):
        self.database = database
        self._history_marker_schema = None
        self.cohort = frozenset(cohort)
        if type(all_eligible) is not bool or (all_eligible and (self.cohort or observation_start is None)):
            raise ValueError("all eligible windows require a start and an empty cohort")
        self.all_eligible = all_eligible
        if any(not isinstance(subject, UUID) for subject in self.cohort):
            raise ValueError("window cohort requires lifecycle UUIDs")
        if observation_start is not None and observation_start.utcoffset() is None:
            raise ValueError("window observation start must be aware")
        self.observation_start = observation_start

    def _observes(self, key: WindowKey) -> bool:
        return (self.all_eligible or key.student_id in self.cohort) and (
            self.observation_start is None or window_bounds(key)[1] > self.observation_start
        )

    def _guard(self):
        guard = self.database.transaction_guard
        if guard is None:
            raise PrivacyUnavailable()
        return guard

    async def _live(self, session, key):
        await self._guard()(session)
        deleted = (await session.execute(_FENCE, _key_args(key))).scalar_one_or_none()
        if deleted is True:
            raise IntakeForbidden("subject lifecycle is deleted")

    async def _exclusive(self, session, key):
        await session.execute(_LOCK, {"lock_key": subject_lock_key(key.student_id)})
        await session.execute(_LOCK, {"lock_key": _window_lock(key)})
        await self._live(session, key)

    async def _read_row(self, session, key):
        return (await session.execute(text(_READ), _key_args(key))).one_or_none()

    async def _read(self, session, key):
        return _window_state(await self._read_row(session, key))

    async def _has_history_marker(self, session):
        if self._history_marker_schema is None:
            self._history_marker_schema = (await session.execute(text("""SELECT EXISTS (
                SELECT FROM pg_attribute
                WHERE attrelid='analytics.student_activity_window_control'::regclass
                  AND attname='history_backfill_owned' AND NOT attisdropped
            )"""))).scalar_one()
        return self._history_marker_schema

    async def _save(self, session, key, previous, state):
        args = _key_args(key)
        confirmation = state.confirmed
        confirmed_values = {name: getattr(confirmation, name, None) for name in _STATE_COLUMNS}
        if confirmation is None:
            confirmed_values.update(server_epoch=1, client_final=False)
        await session.execute(_STATE_UPSERT, {
            **args, **confirmed_values, "catalog_versions": json.dumps(dict(state.catalog_versions)),
            "phase": state.state, "source_generation": state.source_generation,
            "change_generation": state.change_generation, "coverage_reasons": list(state.coverage_reasons),
        })
        if confirmation is None:
            return
        prior = previous.confirmed
        if prior is not None and (prior.stats, prior.revision) == (confirmation.stats, confirmation.revision):
            return
        if not confirmation.stats:
            await session.execute(text(f"DELETE FROM {_WINDOWS} WHERE {_WHERE}"), args)
            return
        await session.execute(text(f"""INSERT INTO {_WINDOWS}({_KEY},stats,revision)
            VALUES (:student_id,:activity_date,:slot_no,CAST(:stats AS jsonb),:revision)
            ON CONFLICT ({_KEY}) DO UPDATE SET stats=excluded.stats,revision=excluded.revision
        """), {**args, "stats": json.dumps([row.model_dump(mode="json") for row in confirmation.stats]),
               "revision": confirmation.revision})

    async def write_window(self, key: WindowKey, snapshot: WindowSnapshot, *, received_at: datetime) -> Transition:
        if (key.activity_date, key.slot_no) != (snapshot.activity_date, snapshot.slot_no):
            raise ValueError("snapshot coordinates differ from window key")
        if (not self._observes(key)
                or (self.all_eligible and self.observation_start is not None and received_at < self.observation_start)):
            return Transition(WindowState(), "rejected", "subject_forbidden")
        age_error = validate_window_age(key, received_at)
        if age_error is not None:
            return Transition(WindowState(), "rejected", age_error)
        self._guard()
        async with self.database.transaction() as session:
            try:
                await self._exclusive(session, key)
            except IntakeForbidden:
                return Transition(WindowState(), "rejected", "privacy_forbidden")
            previous = await self._read(session, key)
            transition = receive_snapshot(previous, snapshot)
            if transition.state != previous:
                await self._save(session, key, previous, transition.state)
            await self._guard()(session)
        return transition

    async def mark_v2_changed(
        self, transaction: AsyncSession, keys: Set[WindowKey], *, received_at: datetime,
    ) -> None:
        # PostgresIntake already holds this fact's lifecycle lock in this transaction.
        if self.all_eligible and self.observation_start is not None and received_at < self.observation_start:
            return
        eligible = sorted((key for key in keys if self._observes(key)),
                          key=lambda key: (key.student_id, key.activity_date, key.slot_no))
        if not eligible or not await has_student_windows_schema(transaction):
            return
        for key in eligible:
            await self._exclusive(transaction, key)
            await transaction.execute(_MARK_V2, _key_args(key))
        await self._guard()(transaction)

    async def changed_windows(
        self, *, limit: int, now: datetime | None = None, after: WindowKey | None = None,
    ) -> tuple[WindowKey, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("changed-window page must be between 1 and 1000")
        if now is not None and now.utcoffset() is None:
            raise ValueError("changed-window cutoff must be aware")
        if not (self.all_eligible or self.cohort):
            return ()
        self._guard()
        async with self.database.transaction() as session:
            history_filter = "AND NOT history_backfill_owned" if await self._has_history_marker(session) else ""
            rows = (await session.execute(text(f"""SELECT {_KEY} FROM {_CONTROL}
                WHERE (:all_eligible OR student_id=ANY(CAST(:cohort AS uuid[])))
                  {history_filter}
                  AND (phase IN ('accumulating','pending_reconciliation')
                       OR (phase='incomplete_coverage'
                           AND recheck_at <= COALESCE(CAST(:now AS timestamptz),CURRENT_TIMESTAMP)))
                  AND (activity_date+slot_no*interval '15 minutes') AT TIME ZONE 'Europe/Moscow'
                        <= COALESCE(CAST(:now AS timestamptz),CURRENT_TIMESTAMP)
                  AND (CAST(:observation_start AS timestamptz) IS NULL OR
                       (activity_date+slot_no*interval '15 minutes') AT TIME ZONE 'Europe/Moscow'
                           > CAST(:observation_start AS timestamptz))
                  AND (CAST(:after_date AS date) IS NULL OR (activity_date,slot_no,student_id) >
                       (CAST(:after_date AS date),CAST(:after_slot AS smallint),CAST(:after_subject AS uuid)))
                ORDER BY activity_date,slot_no,student_id LIMIT :limit
            """), {"cohort": sorted(self.cohort), "all_eligible": self.all_eligible, "limit": limit,
                   "observation_start": self.observation_start, "now": now,
                   "after_date": after.activity_date if after else None,
                   "after_slot": after.slot_no if after else None,
                   "after_subject": after.student_id if after else None})).mappings().all()
            await self._guard()(session)
        return tuple(WindowKey(**row) for row in rows)

    async def read_reconciliation(self, key: WindowKey) -> ReconciliationInput:
        if not self._observes(key):
            raise IntakeForbidden("subject outside window cohort")
        self._guard()
        async with self.database.transaction() as session:
            shared = (await session.execute(text("SELECT pg_try_advisory_xact_lock_shared(:lock_key)"),
                                            {"lock_key": subject_lock_key(key.student_id)})).scalar_one()
            await self._live(session, key)
            if not shared:
                return ReconciliationInput(key, WindowState(), (), True)
            state = await self._read(session, key)
            start, end = window_bounds(key)
            facts = (await session.execute(text("""SELECT accepted_event FROM analytics.activity_facts
                WHERE subject_ref=:student_id AND source_kind IN ('browser_v2','browser_tool_v1')
                  AND started_at >= :earliest AND started_at < :end
                  AND (accepted_event->'event'->>'ended_at')::timestamptz > :start
                ORDER BY started_at,producer_namespace,event_id LIMIT :limit
            """), {"student_id": key.student_id, "start": start,
                   "earliest": start - timedelta(minutes=1), "end": end,
                   "limit": MAX_BUCKET_FACTS + 1})).scalars().all()
            await self._guard()(session)
        return ReconciliationInput(
            key, state, tuple(AcceptedEvent.model_validate_json(json.dumps(row)) for row in facts), False,
        )

    async def reconcile_window(
        self, key: WindowKey, decision: ReconcileDecision, *, rebase_at: datetime,
        evidence: Mapping[str, Any] | None = None, recheck_at: datetime | None = None,
    ) -> WindowState | None:
        if not self._observes(key):
            raise IntakeForbidden("subject outside window cohort")
        if recheck_at is not None and (recheck_at.utcoffset() is None or decision.expected_stats is not None):
            raise ValueError("a dated recheck requires incomplete coverage")
        self._guard()
        async with self.database.transaction() as session:
            await self._exclusive(session, key)
            row = await self._read_row(session, key)
            # Mature history can carry measured time before the live T0.
            if row is not None and getattr(row, "history_backfill_owned", False):
                return None
            previous = _window_state(row)
            state = apply_reconciliation(previous, decision, rebase_at=rebase_at)
            if state is None:
                return None
            await self._save(session, key, previous, state)
            await session.execute(text(f"""UPDATE {_CONTROL} SET reconciled_generation=:generation,
                recheck_at=:recheck_at WHERE {_WHERE}"""),
                {**_key_args(key), "generation": state.reconciled_generation, "recheck_at": recheck_at})
            if evidence is not None:
                await session.execute(text(f"""UPDATE {_CONTROL} SET reconciliation_evidence=CAST(:evidence AS jsonb)
                    WHERE {_WHERE}"""), {**_key_args(key), "evidence": json.dumps(dict(evidence), allow_nan=False)})
            await self._guard()(session)
        return state
