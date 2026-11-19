import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import time
from uuid import UUID

import pytest

from pymentor_analytics.adapters.postgres.core_completion_summary import read_completion_summary
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.modules.collector.ports import IntakeUnavailable

ACTOR = UUID(int=1)
FIRST = UUID(int=2)
SECOND = UUID(int=3)
NOW = datetime(2026, 11, 17, 21, 30, tzinfo=timezone.utc)


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def all(self):
        return self.value


class Session:
    def __init__(self, rows=(), deleted=False):
        self.rows, self.deleted, self.calls = rows, deleted, []

    async def execute(self, query, params):
        sql = str(query)
        self.calls.append((sql, params))
        if "subject_fences" in sql:
            return Result(self.deleted)
        if "WITH selected" in sql:
            return Result(self.rows)
        return Result(None)


class Store:
    def __init__(self, rows=(), deleted=False, proof_error=False):
        self.session = Session(rows, deleted)
        self.database = SimpleNamespace(transaction=self.transaction, transaction_guard=object())
        self.proof_error = proof_error
        self.coverage_call = None

    @asynccontextmanager
    async def transaction(self):
        yield self.session

    async def _coverage(self, session, namespace, subjects, revisions):
        self.coverage_call = (session, namespace, subjects, revisions)
        if self.proof_error:
            raise IntakeUnavailable("snapshot coverage unavailable")
        return [{"subject_ref": str(subject), "coverage_through_revision": revisions[subject]} for subject in subjects]


def run(store, roster=None, **kwargs):
    return asyncio.run(read_completion_summary(store, "core", roster if roster is not None else {FIRST: 4, SECOND: 5},
        ACTOR, NOW, deadline=time.monotonic() + 5, **kwargs))


def test_aggregate_boundaries_and_authorized_mapping():
    # PostgreSQL grouped rows: 21:30 UTC falls on the following Moscow day.
    rows = [SimpleNamespace(subject_ref=FIRST, day=date(2026, 11, 18), total=2, weekly=1, fact_count=3),
            SimpleNamespace(subject_ref=SECOND, day=date(2026, 11, 17), total=1, weekly=1, fact_count=3)]
    store = Store(rows)
    result = run(store, {SECOND: 5, ACTOR: 9, FIRST: 4}, course_id="python")
    assert len(result["daily_lessons_completed"]) == 30
    assert result["daily_lessons_completed"][0] == {"date": "2026-10-20", "lessons_completed": 0}
    assert result["daily_lessons_completed"][-1] == {"date": "2026-11-18", "lessons_completed": 2}
    assert result["lessons_completed_7d"] == 2
    assert result["weekly_by_subject"] == {FIRST: 1, SECOND: 1}
    assert store.coverage_call[2] == [ACTOR, FIRST, SECOND]
    locks = [args["key"] for sql, args in store.session.calls if "pg_advisory_xact_lock" in sql]
    assert locks == sorted(subject_lock_key(subject) for subject in (ACTOR, FIRST, SECOND))
    sql, args = store.session.calls[-1]
    assert "core_transfer_projection" in sql and "lesson_progress" in sql and "LIMIT 100001" in sql
    assert args["course"] == "python" and args["subjects"] == [FIRST, SECOND]
    assert args["week_start"] == NOW - timedelta(days=7)
    assert args["trend_start"] == datetime(2026, 10, 19, 21, tzinfo=timezone.utc)


def test_fail_closed_for_deleted_uncovered_and_oversized():
    for store in (Store(deleted=True), Store(proof_error=True),
                  Store([SimpleNamespace(subject_ref=FIRST, day=date(2026, 11, 18), total=1, weekly=1, fact_count=100001)])):
        with pytest.raises(IntakeUnavailable):
            run(store)
    with pytest.raises(ValueError):
        run(Store(), {UUID(int=i + 10): 0 for i in range(81)})


def test_empty_roster_and_guard_required():
    store = Store()
    result = run(store, {})
    assert result["lessons_completed_7d"] == 0 and result["weekly_by_subject"] == {}
    store.database.transaction_guard = None
    with pytest.raises(IntakeUnavailable):
        run(store)
