"""Offline grouped-reader tests with an instrumented read-only transaction."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import time
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy.exc import DBAPIError

from pymentor_analytics.adapters.postgres import activity_projection_reports as reports
from pymentor_analytics.contracts.auth_v1 import AuthorizationError, VerifiedGrant
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.reports_v1 import ActivityQuery

START = datetime(2026, 11, 27, tzinfo=timezone.utc)
RUN = UUID(int=200)
A = UUID(int=1)
B = UUID(int=2)
C = UUID(int=3)


def grant(actor, *, expires_at=None):
    return VerifiedGrant(
        sub=actor, role="user", scope="report:read", course_ids=(f"course-{actor.int}",),
        subject_refs=(), global_read=False, expires_at=expires_at or int(time.time()) + 60,
    )


def query(offset=0):
    start = START + timedelta(minutes=offset)
    return ActivityQuery(from_time=start, to_time=start + timedelta(minutes=30), course_id=None, subject_ref=None)


class Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def scalars(self):
        return self

    def scalar_one(self):
        return self.value

    def one(self):
        return self.value

    def all(self):
        return self.value


class Session:
    def __init__(self, database):
        self.database = database
        self.calls = []
        self.guards = 0

    async def connection(self):
        if self.database.savepoint_failure is not None:
            raise self.database.savepoint_failure

    @asynccontextmanager
    async def begin_nested(self):
        self.calls.append(("savepoint", None))
        try:
            yield
        except Exception:
            self.calls.append(("rollback_savepoint", None))
            self.database.rolling_back.set()
            await self.database.allow_rollback.wait()
            if self.database.rollback_failure is not None:
                raise self.database.rollback_failure
            raise
        self.calls.append(("release_savepoint", None))
        if self.database.release_failure is not None:
            raise self.database.release_failure

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "SELECT DISTINCT subject_ref FROM analytics.activity_projection_buckets" in sql and not sql.lstrip().startswith("WITH"):
            self.calls.append(("discovery", list(params["scope_subjects"])))
            self.database.discovered.set()
            await self.database.release_discovery.wait()
            return Result([actor for actor in params["scope_subjects"] if actor not in self.database.missing])
        if "pg_try_advisory_xact_lock_shared" in sql:
            self.calls.append(("try_locks", list(params["keys"])))
            return Result([(key, key not in self.database.blocked) for key in params["keys"]])
        if "pg_advisory_xact_lock_shared" in sql:
            self.calls.append(("locks", list(params["keys"])))
            return Result(None)
        if "lock_activity_projection_run" in sql:
            self.calls.append(("generation", None))
            return Result(True)
        self.calls.append(("snapshot", params))
        actor = params["actor"]
        if actor in self.database.failed:
            raise DBAPIError("read failed", (), ValueError("statement failed"),
                             connection_invalidated=actor in self.database.invalidated)
        error = self.database.row_error.get(actor)
        if error == "projection_pending" and actor not in self.database.pending_once:
            self.database.pending_once.add(actor)
        elif error == "projection_pending":
            error = None
        value = {"unique_visitors": 1, "active_ms": actor.int, "theory_ms": actor.int, "practice_ms": 0}
        return Result({"error": error, "as_of": START, "totals": value,
                       "daily": [{"date": START.date().isoformat(), **value}], "breakdown": []})


class Database:
    timeout = 1

    def __init__(self, *, pool_max=2):
        self.pool_max = pool_max
        self.permits = asyncio.Semaphore(pool_max)
        self.sessions = []
        self.discovered = asyncio.Event()
        self.release_discovery = asyncio.Event()
        self.release_discovery.set()
        self.before_exit_guard = asyncio.Event()
        self.release_exit = asyncio.Event()
        self.release_exit.set()
        self.fail_guard = None
        self.transaction_exit_guard = self.transaction_guard
        self.blocked = set()
        self.failed = set()
        self.invalidated = set()
        self.savepoint_failure = None
        self.rollback_failure = None
        self.release_failure = None
        self.rolling_back = asyncio.Event()
        self.allow_rollback = asyncio.Event()
        self.allow_rollback.set()
        self.missing = set()
        self.row_error = {}
        self.pending_once = set()

    async def transaction_guard(self, session):
        session.guards += 1
        session.calls.append(("guard", session.guards))
        if self.fail_guard == session.guards:
            raise PrivacyUnavailable()

    @asynccontextmanager
    async def transaction(self):
        async with self.permits:
            session = Session(self)
            self.sessions.append(session)
            await self.transaction_guard(session)
            yield session
            self.before_exit_guard.set()
            await self.release_exit.wait()
            if self.transaction_exit_guard is not None:
                await self.transaction_exit_guard(session)
            session.calls.append(("committed", None))


def batched_read(report, grant, query):
    return report.read_batched_activity(grant, query, timeout=report.database.timeout)


def reader(database):
    return reports.PostgresProjectionActivityReport(database, None, expected_run_id=RUN, pending_wait_seconds=.2)


def test_batch_keeps_own_scope_locks_limits_and_no_cached_result():
    async def run():
        database = Database()
        report = reader(database)
        values = await asyncio.gather(*(batched_read(report, grant(actor), query()) for actor in (A, B)))
        assert [value["totals"]["active_ms"] for value in values] == [1, 2]
        assert len(database.sessions) == 1
        trace = database.sessions[0].calls
        assert [kind for kind, _ in trace if kind not in {"savepoint", "release_savepoint"}] == [
            "guard", "discovery", "try_locks", "guard", "generation", "guard",
            "snapshot", "snapshot", "guard", "committed",
        ]
        assert trace.count(("savepoint", None)) == 1
        assert trace.count(("release_savepoint", None)) == 1
        assert trace[1] == ("discovery", [A, B])
        assert trace[2] == ("try_locks", sorted((subject_lock_key(A), subject_lock_key(B))))
        for position, params in enumerate(item for kind, item in trace if kind == "snapshot"):
            actor = (A, B)[position]
            assert params["actor"] == actor
            assert params["scope_subjects"] == params["locked_subjects"] == [actor]
            assert params["courses"] == [f"course-{actor.int}"]
            assert params["max_buckets"] == reports.MAX_BUCKET_ROWS
        await batched_read(report, grant(A), query())
        assert len(database.sessions) == 2
        assert database.sessions[1].calls[2][0] == "locks"
    asyncio.run(asyncio.wait_for(run(), timeout=3))


@pytest.mark.parametrize(("error", "expected"), [
    ("actor_or_target_deleted", AuthorizationError),
    ("candidate_changed", reports.ProjectionUnavailable),
    ("bucket_limit", reports.ReportLimitExceeded),
    ("statement", DBAPIError),
])
def test_one_bad_read_does_not_mask_other_subject(error, expected):
    async def run():
        database = Database()
        if error == "statement":
            database.failed.add(A)
        else:
            database.row_error[A] = error
        report = reader(database)
        result = await asyncio.gather(
            batched_read(report, grant(A), query()),
            batched_read(report, grant(B), query()), return_exceptions=True,
        )
        assert isinstance(result[0], expected)
        assert result[1]["totals"]["active_ms"] == 2
        trace = database.sessions[0].calls
        assert trace.count(("rollback_savepoint", None)) == (2 if error == "statement" else 0)
        assert trace[-1] == ("committed", None)
    asyncio.run(asyncio.wait_for(run(), timeout=3))


def test_entry_guard_alone_keeps_original_single_transactions():
    async def run():
        database = Database()
        database.transaction_exit_guard = None
        report = reader(database)
        values = await asyncio.gather(*(batched_read(report, grant(actor), query()) for actor in (A, B)))
        assert [value["totals"]["active_ms"] for value in values] == [1, 2]
        assert len(database.sessions) == 2
        assert all(session.guards == 3 for session in database.sessions)
        assert {tuple(keys) for session in database.sessions for kind, keys in session.calls if kind == "locks"} == {
            (subject_lock_key(A),), (subject_lock_key(B),),
        }
        assert not report._self_reports.running and not report._self_reports.pending
    asyncio.run(asyncio.wait_for(run(), timeout=3))


def test_group_cannot_release_after_privacy_failure():
    async def run():
        database = Database()
        database.fail_guard = 4
        database.release_exit.clear()
        report = reader(database)
        tasks = [asyncio.create_task(batched_read(report, grant(actor), query())) for actor in (A, B)]
        await database.before_exit_guard.wait()
        assert all(not task.done() for task in tasks)
        database.release_exit.set()
        values = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(value, PrivacyUnavailable) for value in values)
        assert ("committed", None) not in database.sessions[0].calls
    asyncio.run(asyncio.wait_for(run(), timeout=3))


def test_contended_actor_uses_own_original_read():
    async def run():
        database = Database()
        database.blocked.add(subject_lock_key(A))
        report = reader(database)
        values = await asyncio.gather(*(batched_read(report, grant(actor), query()) for actor in (A, B)))
        assert [value["totals"]["active_ms"] for value in values] == [1, 2]
        assert len(database.sessions) == 2
        assert [params["actor"] for kind, params in database.sessions[0].calls if kind == "snapshot"] == [B]
        assert ("locks", [subject_lock_key(A)]) in database.sessions[1].calls
    asyncio.run(asyncio.wait_for(run(), timeout=3))


def test_late_arrival_and_cancellation_do_not_change_frozen_batch():
    async def run():
        database = Database(pool_max=1)
        database.release_discovery.clear()
        report = reader(database)
        first = [asyncio.create_task(batched_read(report, grant(actor), query())) for actor in (A, B)]
        await database.discovered.wait()
        late = asyncio.create_task(batched_read(report, grant(C), query()))
        await asyncio.sleep(0)
        first[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await first[0]
        assert not first[1].done() and not late.done()
        database.release_discovery.set()
        result = await asyncio.gather(first[1], late)
        assert [value["totals"]["active_ms"] for value in result] == [2, 3]
        assert len(database.sessions) == 2
        assert ("discovery", [A, B]) in database.sessions[0].calls
        assert [params["actor"] for kind, params in database.sessions[0].calls if kind == "snapshot"] == [B]
        assert ("discovery", [C]) in database.sessions[1].calls
    asyncio.run(asyncio.wait_for(run(), timeout=3))


def test_expiry_at_release_is_per_grant(monkeypatch):
    async def run():
        wall = time.time()
        clock = SimpleNamespace(value=wall)
        monkeypatch.setattr(reports, "time", SimpleNamespace(time=lambda: clock.value))
        database = Database()
        database.release_exit.clear()
        report = reader(database)
        tasks = [asyncio.create_task(batched_read(report, grant(actor, expires_at=int(wall) + offset), query()))
                 for actor, offset in ((A, 2), (B, 60))]
        await database.before_exit_guard.wait()
        assert all(not task.done() for task in tasks)
        clock.value += 3
        database.release_exit.set()
        values = await asyncio.gather(*tasks, return_exceptions=True)
        assert isinstance(values[0], AuthorizationError)
        assert values[1]["totals"]["active_ms"] == 2
    asyncio.run(asyncio.wait_for(run(), timeout=3))


def test_pending_retries_separately_with_fresh_guards_and_keeps_original_timeout():
    async def run():
        database = Database()
        database.row_error[A] = "projection_pending"
        report = reader(database)
        values = await asyncio.gather(*(batched_read(report, grant(actor), query()) for actor in (A, B)))
        assert [value["totals"]["active_ms"] for value in values] == [1, 2]
        assert [session.guards for session in database.sessions] == [4, 4]
        database.release_discovery.clear()
        database.timeout = .03
        values = await asyncio.gather(*(batched_read(report, grant(actor), query()) for actor in (A, B)),
                                      return_exceptions=True)
        assert all(isinstance(value, TimeoutError) for value in values)
        await report.close_self_reports()
        assert ("committed", None) not in database.sessions[-1].calls
    asyncio.run(asyncio.wait_for(run(), timeout=3))


def test_max_group_size_and_compatible_window():
    async def run():
        database = Database()
        report = reader(database)
        inputs = [(UUID(int=index), query(index % 2)) for index in range(1, 25)]
        results = await asyncio.gather(*(batched_read(report, grant(actor), key) for actor, key in inputs))
        assert [result["totals"]["active_ms"] for result in results] == list(range(1, 25))
        groups = [subjects for session in database.sessions for kind, subjects in session.calls if kind == "discovery"]
        assert all(1 <= len(subjects) <= 8 for subjects in groups)
        assert len(groups) == 4
        assert all(len({subject.int % 2 for subject in subjects}) == 1 for subjects in groups)
    asyncio.run(asyncio.wait_for(run(), timeout=3))


def test_one_deadline_cancels_only_that_waiter_and_resources_are_released():
    async def run():
        database = Database(pool_max=1)
        database.release_discovery.clear()
        report = reader(database)
        short = asyncio.create_task(report.read_batched_activity(grant(A), query(), timeout=.03))
        long = asyncio.create_task(report.read_batched_activity(grant(B), query(), timeout=1))
        await database.discovered.wait()
        with pytest.raises(TimeoutError):
            await short
        assert not long.done()
        database.release_discovery.set()
        result = await long
        assert result["totals"]["active_ms"] == 2
        assert not report._self_reports.pending and not report._self_reports.running
        await asyncio.wait_for(database.permits.acquire(), timeout=.1)
        database.permits.release()
        await report.close_self_reports()
    asyncio.run(asyncio.wait_for(run(), timeout=3))



def test_sql_rollback_discards_tentative_results_until_isolated_replay_commits():
    async def run():
        database = Database()
        database.failed.add(A)
        database.allow_rollback.clear()
        report = reader(database)
        leader = asyncio.create_task(batched_read(report, grant(B), query()))
        failing = asyncio.create_task(batched_read(report, grant(A), query()))
        await database.rolling_back.wait()
        trace = database.sessions[0].calls
        assert [params["actor"] for kind, params in trace if kind == "snapshot"] == [B, A]
        assert not leader.done() and not failing.done()
        database.allow_rollback.set()
        b, a = await asyncio.gather(leader, failing, return_exceptions=True)
        assert b["totals"]["active_ms"] == 2
        assert isinstance(a, DBAPIError)
        assert [params["actor"] for kind, params in trace if kind == "snapshot"] == [B, A, B, A]
        assert trace.count(("rollback_savepoint", None)) == 2
        assert trace[-1] == ("committed", None)
    asyncio.run(asyncio.wait_for(run(), timeout=3))


@pytest.mark.parametrize("point", ["connection_invalidated", "savepoint", "rollback", "release"])
def test_group_transaction_failures_cannot_release_a_previously_materialized_row(point):
    async def run():
        database = Database()
        if point == "connection_invalidated":
            database.failed.add(A)
            database.invalidated.add(A)
        elif point == "savepoint":
            database.savepoint_failure = DBAPIError("savepoint", (), ValueError("could not open"))
        elif point == "rollback":
            database.failed.add(A)
            database.rollback_failure = DBAPIError("rollback", (), ValueError("could not undo"))
        else:
            database.release_failure = DBAPIError("release", (), ValueError("could not release"))
        report = reader(database)
        values = await asyncio.gather(*(batched_read(report, grant(actor), query()) for actor in (B, A)),
                                      return_exceptions=True)
        assert all(isinstance(value, DBAPIError) for value in values)
        trace = database.sessions[0].calls
        snapshots = [params["actor"] for kind, params in trace if kind == "snapshot"]
        assert snapshots == ([] if point == "savepoint" else [B, A])
        assert ("committed", None) not in trace
    asyncio.run(asyncio.wait_for(run(), timeout=3))


def test_privacy_failure_after_group_rollback_blocks_replay():
    async def run():
        database = Database()
        database.failed.add(A)
        database.fail_guard = 4
        report = reader(database)
        values = await asyncio.gather(*(batched_read(report, grant(actor), query()) for actor in (B, A)),
                                      return_exceptions=True)
        assert all(isinstance(value, PrivacyUnavailable) for value in values)
        trace = database.sessions[0].calls
        assert [params["actor"] for kind, params in trace if kind == "snapshot"] == [B, A]
        assert trace[-2:] == [("rollback_savepoint", None), ("guard", 4)]
    asyncio.run(asyncio.wait_for(run(), timeout=3))
