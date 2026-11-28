"""Privacy-guarded PostgreSQL scan and persisted reconciliation evidence."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import text

from pymentor_analytics.adapters.postgres.student_windows import PostgresStudentWindows
from pymentor_analytics.contracts.windows_v1 import WindowKey
from pymentor_analytics.modules.analytics.activity.student_windows_reconcile import ObservedWindow
from pymentor_analytics.modules.analytics.activity.student_windows_worker import WorkedWindow

_RANGE = """
WITH v2_buckets AS (
    SELECT fact.subject_ref AS student_id, (bucket AT TIME ZONE 'Europe/Moscow') AS local_start
    FROM analytics.activity_facts AS fact
    CROSS JOIN LATERAL generate_series(
        date_bin(interval '15 minutes', greatest(fact.started_at, CAST(:t0 AS timestamptz)), timestamptz '2000-01-01 00:00:00+00'),
        date_bin(interval '15 minutes', least((fact.accepted_event #>> '{event,ended_at}')::timestamptz,
                                             CAST(:through AS timestamptz)) - interval '1 microsecond',
                timestamptz '2000-01-01 00:00:00+00'),
        interval '15 minutes'
    ) AS bucket
    WHERE (:all_eligible OR fact.subject_ref=ANY(CAST(:subjects AS uuid[])))
      AND fact.source_kind IN ('browser_v2', 'browser_tool_v1')
      AND fact.started_at >= CAST(:t0 AS timestamptz) - interval '1 minute' AND fact.started_at < CAST(:through AS timestamptz)
      AND (fact.accepted_event #>> '{event,active_ms}')::integer > 0
), candidate_keys AS (
    SELECT student_id, local_start::date AS activity_date,
           (extract(hour FROM local_start)::integer * 4 + extract(minute FROM local_start)::integer / 15 + 1) AS slot_no
    FROM v2_buckets
    UNION
    SELECT student_id, activity_date, slot_no FROM analytics.student_activity_windows
    WHERE (:all_eligible OR student_id=ANY(CAST(:subjects AS uuid[])))
    UNION
    SELECT student_id, activity_date, slot_no FROM analytics.student_activity_window_control
    WHERE (:all_eligible OR student_id=ANY(CAST(:subjects AS uuid[])))
)
SELECT student_id, activity_date, slot_no FROM candidate_keys
WHERE ((activity_date + slot_no * interval '15 minutes') AT TIME ZONE 'Europe/Moscow') > CAST(:t0 AS timestamptz)
  AND ((activity_date + slot_no * interval '15 minutes') AT TIME ZONE 'Europe/Moscow') <= CAST(:through AS timestamptz)
ORDER BY activity_date, slot_no, student_id
LIMIT :limit
"""


class PostgresWindowDiagnosticStore(PostgresStudentWindows):
    async def diagnostic_keys(self, *, through: datetime, limit: int) -> tuple[WindowKey, ...]:
        if self.observation_start is None or through.utcoffset() is None or through <= self.observation_start:
            raise ValueError("diagnostic scan requires an aware observation interval")
        if not 1 <= limit <= 1001:
            raise ValueError("diagnostic key limit must be between 1 and 1001")
        if not (self.all_eligible or self.cohort):
            return ()
        async with self.database.transaction() as session:
            rows = (await session.execute(text(_RANGE), {
                "subjects": sorted(self.cohort), "all_eligible": self.all_eligible,
                "t0": self.observation_start, "through": through, "limit": limit,
            })).mappings().all()
            await self._guard()(session)
        return tuple(WindowKey.model_validate(dict(row)) for row in rows)

    async def repair_evidence(self, key: WindowKey) -> dict[str, object] | None:
        if not self._observes(key):
            return None
        async with self.database.transaction() as session:
            await self._live(session, key)
            observed = (await session.execute(text("""
                SELECT reconciliation_evidence
                FROM analytics.student_activity_window_control
                WHERE student_id=:student_id AND activity_date=:activity_date AND slot_no=:slot_no
            """), _key_values(key))).scalar_one_or_none()
            await self._guard()(session)
        return observed


def _key_values(key: WindowKey) -> dict[str, UUID | date | int]:
    return key.model_dump()


def diagnostic_document(item: WorkedWindow, repair_evidence: dict[str, object] | None) -> dict[str, object]:
    comparison = item.comparison
    row = {
        "key": item.key.model_dump(mode="json"), "status": item.status, "attempts": item.attempts,
        "age_ms": item.age_ms, "durable_repair": repair_evidence,
    }
    if comparison is not None:
        row.update({
            "diagnostics": comparison.diagnostics.model_dump(mode="json"), "outcome": comparison.outcome,
            "coverage_source": comparison.coverage_source, "late_deadline": comparison.late_deadline.isoformat(),
            "repairable": comparison.decision.expected_stats is not None,
            "expected_stats": (None if comparison.expected_stats is None else
                               [stat.model_dump(mode="json") for stat in comparison.expected_stats]),
            "expected_hash": comparison.expected_hash, "before": _observed_document(comparison.before),
            "after": None if item.after is None else _observed_document(item.after),
        })
    return row


def _observed_document(observed: ObservedWindow) -> dict[str, object]:
    return {
        "revision": observed.revision, "stats_hash": observed.stats_hash, "payload_hash": observed.payload_hash,
        "stats": [stat.model_dump(mode="json") for stat in observed.stats],
        "context_diff": None if observed.differences is None else [asdict(item) for item in observed.differences],
    }
