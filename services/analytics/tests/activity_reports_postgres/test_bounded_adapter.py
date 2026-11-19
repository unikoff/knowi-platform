"""Offline adapter scenarios using a fake session; these are not PostgreSQL tests."""
import asyncio
import importlib.util
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
import pytest
from pymentor_analytics.adapters.postgres.activity_reports import MAX_FACTS, PostgresActivityReport, ReportLimitExceeded
from pymentor_analytics.contracts.auth_v1 import AuthorizationError, VerifiedGrant
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.reports_v1 import ActivityQuery

spec = importlib.util.spec_from_file_location("golden_pg_helpers", Path(__file__).parents[1] / "reference/test_activity_v1.py")
g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g)

class Scalar:
    def __init__(self, value): self.value = value
    def scalar_one_or_none(self): return self.value
    def scalar_one(self): return self.value
    def one(self): return self.value
    def scalars(self): return self
    def all(self): return self.value

class Session:
    def __init__(self, rows, deleted=False): self.rows, self.deleted, self.calls = rows, deleted, []
    async def execute(self, statement, params=None):
        sql = str(statement); self.calls.append((sql, params))
        if "SELECT deleted" in sql: return Scalar(self.deleted)
        if "pg_catalog.pg_attribute" in sql: return Scalar((False,False,False))
        if "clock_timestamp" in sql: return Scalar(g.BASE + timedelta(days=8))
        if "accepted_event" in sql: return Scalar(self.rows)
        return Scalar(None)

class DB:
    def __init__(self, session, guard=True): self.session = session; self.transaction_guard = object() if guard else None; self.exited = False
    @asynccontextmanager
    async def transaction(self):
        yield self.session
        self.exited = True

class Fence:
    async def permits(self, subject): return True

def scope():
    grant = VerifiedGrant(sub=g.SUBJECT, role="user", scope="report:read", course_ids=("backend",), subject_refs=(), global_read=False, expires_at=int(time.time())+60)
    query = ActivityQuery(from_time=g.BASE, to_time=g.BASE+timedelta(hours=1), course_id="backend", subject_ref=g.SUBJECT)
    return grant, query

def test_locked_guarded_read_before_course_filter():
    records = [g.event(g.BASE, g.BASE+timedelta(seconds=15), number=1, course="backend"),
               g.event(g.BASE, g.BASE+timedelta(seconds=15), number=2, course="backend_copy")]
    session = Session([r.model_dump(mode="json") for r in records]); db = DB(session)
    grant, query = scope()
    result = asyncio.run(PostgresActivityReport(db, Fence()).read_subject(grant, query))
    assert db.exited and result["totals"]["active_ms"] == 0
    assert result["totals"]["unique_visitors"] == 1 and result["quality"] == "unknown" and result["lag_seconds"] is None
    sql, params = next((s, p) for s,p in session.calls if "SELECT accepted_event" in s)
    assert "course_id" not in sql and params["limit"] == MAX_FACTS + 1
    assert any("pg_advisory_xact_lock_shared" in s for s,p in session.calls)

def test_denial_guard_and_limit_fail_closed():
    grant, query = scope()
    with pytest.raises(PrivacyUnavailable):
        asyncio.run(PostgresActivityReport(DB(Session([]), guard=False), Fence()).read_subject(grant, query))
    with pytest.raises(AuthorizationError):
        asyncio.run(PostgresActivityReport(DB(Session([], deleted=True)), Fence()).read_subject(grant, query))
    with pytest.raises(ReportLimitExceeded):
        asyncio.run(PostgresActivityReport(DB(Session([None]*(MAX_FACTS+1))), Fence()).read_subject(grant, query))
    wrong = query.model_copy(update={"subject_ref": g.OTHER})
    with pytest.raises(AuthorizationError):
        asyncio.run(PostgresActivityReport(DB(Session([])), Fence()).read_subject(grant, wrong))

def test_leader_roster_and_admin_grants_are_scoped():
    _, query = scope()
    leader = VerifiedGrant(sub=g.OTHER, role="leader", scope="report:read", course_ids=("backend",),
                           subject_refs=(g.SUBJECT,), global_read=False, expires_at=int(time.time())+60)
    result = asyncio.run(PostgresActivityReport(DB(Session([])), Fence()).read_subject(leader, query))
    assert result["quality"] == "unknown"
    denied = VerifiedGrant(sub=g.OTHER, role="leader", scope="report:read", course_ids=("backend",),
                           subject_refs=(), global_read=False, expires_at=int(time.time())+60)
    with pytest.raises(AuthorizationError):
        asyncio.run(PostgresActivityReport(DB(Session([])), Fence()).read_subject(denied, query))
    admin = VerifiedGrant(sub=g.OTHER, role="admin", scope="report:read", course_ids=(),
                          subject_refs=(), global_read=True, expires_at=int(time.time())+60)
    assert asyncio.run(PostgresActivityReport(DB(Session([])), Fence()).read_subject(admin, query))["quality"] == "unknown"

def test_guard_failure_at_transaction_exit_does_not_release_result():
    class GuardFails(DB):
        @asynccontextmanager
        async def transaction(self):
            yield self.session
            raise PrivacyUnavailable()
    grant, query = scope()
    with pytest.raises(PrivacyUnavailable):
        asyncio.run(PostgresActivityReport(GuardFails(Session([])), Fence()).read_subject(grant, query))

def test_single_subject_lock_order_uses_advisory_keys():
    from uuid import UUID
    from pymentor_analytics.contracts.locking_v1 import subject_lock_key
    first,second=UUID(int=1),UUID(int=4)
    signed=VerifiedGrant(sub=first,role="admin",scope="report:read",course_ids=(),subject_refs=(),global_read=True,expires_at=int(time.time())+60)
    q=ActivityQuery(from_time=g.BASE,to_time=g.BASE+timedelta(hours=1),course_id="backend",subject_ref=second)
    session=Session([])
    asyncio.run(PostgresActivityReport(DB(session),Fence()).read_subject(signed,q))
    keys=[p["key"] for sql,p in session.calls if "pg_advisory_xact_lock_shared" in sql]
    assert keys==[subject_lock_key(second),subject_lock_key(first)]
