"""DB-owned, transactionally shared browser admission budgets."""
import logging
import time
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from pymentor_analytics.modules.collector.ports import IntakeOverloaded, IntakeUnavailable

logger = logging.getLogger(__name__)


class PostgresBackpressure:
    """Called after subject fence + dedup, inside the fact/job transaction."""

    def __init__(self):
        self._last_size_warning = float("-inf")

    async def _clock_and_size(self, session: AsyncSession) -> tuple[int, int]:
        row = (await session.execute(text("""
            SELECT floor(extract(epoch from clock_timestamp()))::bigint,
                   pg_database_size(current_database())
        """))).one()
        return int(row[0]), int(row[1])

    async def check(self, session: AsyncSession, *, subject, new_fact: bool, new_jobs: int) -> None:
        if not new_fact and new_jobs == 0:
            return
        result = await session.execute(text("SELECT * FROM analytics.admission_budget WHERE singleton=true FOR UPDATE"))
        policy = result.mappings().one_or_none()
        if policy is None:
            raise IntakeUnavailable("admission policy unavailable")
        now, size = await self._clock_and_size(session)
        # A backwards wall-clock correction must not reopen an earlier window.
        window = max((now // 60) * 60, policy["window_epoch"])
        retry = max(1, min(60, window + 60 - now))
        if size * 100 >= policy["max_database_bytes"] * policy["warning_percent"]:
            elapsed = time.monotonic()
            if elapsed - self._last_size_warning >= 60:
                logger.warning("analytics database size warning: bytes=%d budget_bytes=%d", size, policy["max_database_bytes"])
                self._last_size_warning = elapsed
        if size * 100 >= policy["max_database_bytes"] * policy["critical_percent"]:
            raise IntakeOverloaded(1)
        pending = (await session.execute(text("""
            SELECT count(*) FROM (SELECT 1 FROM analytics.processing_jobs
                WHERE status <> 'completed' LIMIT :bound) AS outstanding
        """), {"bound": policy["max_outstanding_jobs"] + 1})).scalar_one()
        if pending + new_jobs > policy["max_outstanding_jobs"]:
            raise IntakeOverloaded(1)
        if not new_fact:
            return
        total = policy["accepted_count"] if policy["window_epoch"] == window else 0
        if total >= policy["global_per_minute"]:
            raise IntakeOverloaded(retry)
        subject_row = (await session.execute(text("""
            SELECT window_epoch,accepted_count FROM analytics.admission_subject_windows WHERE subject_ref=:subject
        """), {"subject": subject})).one_or_none()
        personal = subject_row.accepted_count if subject_row and subject_row.window_epoch == window else 0
        if personal >= policy["subject_per_minute"]:
            raise IntakeOverloaded(retry)
        await session.execute(text("UPDATE analytics.admission_budget SET window_epoch=:window,accepted_count=:count WHERE singleton=true"),
                              {"window": window, "count": total + 1})
        await session.execute(text("""
            INSERT INTO analytics.admission_subject_windows(subject_ref,window_epoch,accepted_count)
            VALUES (:subject,:window,:count) ON CONFLICT (subject_ref)
            DO UPDATE SET window_epoch=excluded.window_epoch,accepted_count=excluded.accepted_count
        """), {"subject": subject, "window": window, "count": personal + 1})
        # Skip rows held by a concurrent privacy transaction; never wait for an
        # unrelated subject while the singleton lock is held.
        await session.execute(text("""
            DELETE FROM analytics.admission_subject_windows WHERE subject_ref IN (
                SELECT subject_ref FROM analytics.admission_subject_windows
                WHERE window_epoch < :cutoff ORDER BY window_epoch
                LIMIT 64 FOR UPDATE SKIP LOCKED)
        """), {"cutoff": window - 60})
