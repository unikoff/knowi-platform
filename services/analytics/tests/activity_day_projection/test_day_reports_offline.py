import asyncio
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import ModuleType
from uuid import UUID, uuid4

import pytest

from pymentor_analytics.adapters.postgres.activity_projection_reports import (
    PostgresProjectionActivityReport, ProjectionPending, ProjectionUnavailable, _DISCOVER, _SNAPSHOT,
)
from pymentor_analytics.adapters.postgres.activity_day_reports import _DAY_DISCOVER, _DAY_SNAPSHOT
from pymentor_analytics.adapters.postgres.activity_reports import ReportLimitExceeded
from pymentor_analytics.contracts.auth_v1 import AuthorizationError, VerifiedGrant
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.reports_v1 import ActivityQuery

SUBJECT = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
ZERO = {"unique_visitors": 0, "active_ms": 0, "theory_ms": 0, "practice_ms": 0}


class Result:
    def __init__(self, value): self.value = value
    def scalars(self): return self
    def all(self): return self.value
    def scalar_one(self): return self.value
    def mappings(self): return self
    def one(self): return self.value


class FakeDatabase:
    def __init__(self, *, day=False, error=None, count=1, run=True):
        self.day, self.error, self.count, self.run = day, error, count, run
        self.calls = []
        async def guard(_session): self.calls.append("guard")
        self.transaction_guard = guard
    @asynccontextmanager
    async def transaction(self):
        yield self
    async def execute(self, statement, params=None):
        sql = str(statement)
        if sql in (_DISCOVER, _DAY_DISCOVER):
            self.calls.append("day_discover" if sql == _DAY_DISCOVER else "bucket_discover")
            return Result([UUID(int=i + 100) for i in range(self.count)])
        if "pg_advisory_xact_lock_shared" in sql:
            self.calls.append("subject_lock")
            self.lock_keys = params["keys"]
            assert "ORDER BY locks.lock_key" in sql
            return Result(None)
        if "lock_activity_projection_run" in sql:
            self.calls.append("run_lock")
            return Result(self.run)
        if sql in (_SNAPSHOT, _DAY_SNAPSHOT):
            self.calls.append("day_snapshot" if sql == _DAY_SNAPSHOT else "bucket_snapshot")
            return Result({"error": self.error, "as_of": NOW, "totals": ZERO, "daily": [], "breakdown": []})
        raise AssertionError("unexpected SQL")


def request(db, *, day=False, pending_wait=0):
    grant = VerifiedGrant(sub=SUBJECT, role="admin", scope="report:read", course_ids=(), subject_refs=(),
                          global_read=True, expires_at=int(time.time()) + 120)
    query = ActivityQuery(from_time=NOW, to_time=NOW + timedelta(days=1), course_id=None, subject_ref=None)
    return PostgresProjectionActivityReport(db, None, expected_run_id=uuid4(), day_projection_enabled=day,
                                             pending_wait_seconds=pending_wait).read_activity(grant, query)


def test_flag_off_uses_only_a010_even_if_a011_unavailable(monkeypatch):
    module = ModuleType("pymentor_analytics.adapters.postgres.activity_day_projection")
    async def forbidden(_session): pytest.fail("day schema must not be checked with flag off")
    module.has_activity_day_projection_schema = forbidden
    monkeypatch.setitem(sys.modules, module.__name__, module)
    db = FakeDatabase()
    result = asyncio.run(request(db))
    assert result["totals"] == ZERO and result["quality"] == "unknown"
    assert "bucket_discover" in db.calls and "bucket_snapshot" in db.calls
    assert not any(call.startswith("day_") for call in db.calls)


@pytest.mark.parametrize("available", [False, "partial"])
def test_flag_on_missing_or_partial_schema_fails_without_fallback(monkeypatch, available):
    module = ModuleType("pymentor_analytics.adapters.postgres.activity_day_projection")
    async def schema(_session):
        if available == "partial": raise RuntimeError("partial schema")
        return available
    module.has_activity_day_projection_schema = schema
    monkeypatch.setitem(sys.modules, module.__name__, module)
    db = FakeDatabase(day=True)
    with pytest.raises(ProjectionUnavailable): asyncio.run(request(db, day=True))
    assert not db.calls


@pytest.mark.parametrize("error,expected", [
    (None, None), ("backfill_unavailable", ProjectionUnavailable),
    ("projection_unavailable", ProjectionUnavailable), ("candidate_changed", ProjectionUnavailable),
    ("projection_pending", ProjectionPending),
    ("subject_limit", ReportLimitExceeded), ("bucket_limit", ReportLimitExceeded),
    ("item_limit", ReportLimitExceeded), ("breakdown_limit", ReportLimitExceeded),
    ("actor_or_target_deleted", AuthorizationError),
])
def test_day_reader_keeps_locks_guards_and_fail_closed_error_mapping(monkeypatch, error, expected):
    module = ModuleType("pymentor_analytics.adapters.postgres.activity_day_projection")
    async def schema(_session): return True
    module.has_activity_day_projection_schema = schema
    monkeypatch.setitem(sys.modules, module.__name__, module)
    db = FakeDatabase(day=True, error=error)
    if expected is None:
        assert asyncio.run(request(db, day=True))["totals"] == ZERO
    else:
        with pytest.raises(expected): asyncio.run(request(db, day=True))
    assert db.calls[0] == "day_discover" and db.calls[-1] == "day_snapshot"
    assert db.calls.count("subject_lock") == 1
    assert db.lock_keys == sorted({subject_lock_key(SUBJECT), subject_lock_key(UUID(int=100))})
    assert db.calls[-4:] == ["guard", "run_lock", "guard", "day_snapshot"]
    assert "bucket_snapshot" not in db.calls


@pytest.mark.parametrize("case, error", [("privacy", PrivacyUnavailable), ("invalid", ProjectionUnavailable)])
def test_report_never_retries_a_privacy_or_invalid_snapshot_failure(case, error):
    db = FakeDatabase(error="projection_unavailable")
    if case == "privacy":
        async def failed_guard(_session):
            raise PrivacyUnavailable()
        db.transaction_guard = failed_guard
    with pytest.raises(error):
        asyncio.run(request(db, pending_wait=2))
    assert db.calls.count("bucket_discover") == 1


def test_day_statement_retains_a010_aggregates_and_original_budget_metadata():
    tail = _SNAPSHOT[_SNAPSHOT.index("raw_times AS NOT MATERIALIZED ("):]
    assert _DAY_SNAPSHOT.endswith(tail)
    assert "activity_projection_day_sources" in _DAY_DISCOVER and "activity_projection_buckets" in _DAY_DISCOVER
    assert "LEFT JOIN analytics.activity_projection_days" in _DAY_SNAPSHOT
    assert "sum(source_bucket_count)" in _DAY_SNAPSHOT
    assert "sum(source_compact_items)" in _DAY_SNAPSHOT
    assert "sum(source_compact_bytes)" in _DAY_SNAPSHOT


@pytest.mark.parametrize("case", ["too_many", "privacy", "wrong_run"])
def test_day_reader_stops_before_snapshot_on_guard_failure(monkeypatch, case):
    module = ModuleType("pymentor_analytics.adapters.postgres.activity_day_projection")
    async def schema(_session): return True
    module.has_activity_day_projection_schema = schema
    monkeypatch.setitem(sys.modules, module.__name__, module)
    db = FakeDatabase(day=True, count=101 if case == "too_many" else 1, run=case != "wrong_run")
    if case == "privacy":
        async def fail_guard(_session):
            db.calls.append("guard")
            raise PrivacyUnavailable()
        db.transaction_guard = fail_guard
    with pytest.raises(ReportLimitExceeded if case == "too_many" else PrivacyUnavailable if case == "privacy" else ProjectionUnavailable):
        asyncio.run(request(db, day=True))
    assert "day_snapshot" not in db.calls and "bucket_snapshot" not in db.calls
    if case == "too_many": assert "subject_lock" not in db.calls
    if case == "wrong_run": assert db.calls.count("guard") == 1


def test_day_flag_requires_boolean():
    with pytest.raises(ValueError):
        PostgresProjectionActivityReport(FakeDatabase(), None, expected_run_id=uuid4(), day_projection_enabled="1")
