"""Offline SQL contract and ACL checks with fake sessions, not live PG evidence."""
import asyncio
import importlib.util
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
import pytest
from pymentor_analytics.adapters.postgres.activity_reports import MAX_FACTS, MAX_SUBJECTS, PostgresActivityReport, ReportLimitExceeded
from pymentor_analytics.contracts.auth_v1 import AuthorizationError, VerifiedGrant
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.reports_v1 import ActivityQuery

spec=importlib.util.spec_from_file_location("golden_aggregate", Path(__file__).parents[1]/"reference/test_activity_v1.py")
g=importlib.util.module_from_spec(spec); spec.loader.exec_module(g)

class Result:
    def __init__(self, value): self.value=value
    def scalar_one(self): return self.value
    def scalar_one_or_none(self): return self.value
    def one(self): return self.value
    def scalars(self): return self
    def all(self): return self.value
class Session:
    def __init__(self, candidates, records, deleted=()):
        self.candidates=candidates; self.records=records; self.deleted=set(deleted); self.calls=[]
    async def execute(self, statement, params=None):
        sql=str(statement); self.calls.append((sql,params))
        if "pg_catalog.pg_attribute" in sql: return Result((False,False,False))
        if "clock_timestamp" in sql: return Result(g.BASE+timedelta(days=8))
        if "SELECT DISTINCT subject_ref" in sql: return Result(self.candidates)
        if "SELECT deleted" in sql: return Result(params["subject"] in self.deleted)
        if "SELECT accepted_event" in sql: return Result([r.model_dump(mode="json") for r in self.records])
        return Result(None)
class DB:
    transaction_guard=object()
    def __init__(self,s): self.s=s
    @asynccontextmanager
    async def transaction(self): yield self.s
class Fence: pass

def query(course="backend", subject=None):
    return ActivityQuery(from_time=g.BASE, to_time=g.BASE+timedelta(hours=1), course_id=course, subject_ref=subject)
def grant(role="leader", subjects=(g.SUBJECT,g.OTHER)):
    return VerifiedGrant(sub=g.OTHER, role=role, scope="report:read", course_ids=("backend",) if role=="leader" else (),
                         subject_refs=subjects if role=="leader" else (), global_read=role=="admin", expires_at=int(time.time())+60)

def test_global_competition_before_scope_and_distinct_subjects():
    records=[g.event(g.BASE,g.BASE+timedelta(seconds=15), number=1, subject=g.SUBJECT, course="backend"),
             g.event(g.BASE,g.BASE+timedelta(seconds=15), number=2, subject=g.SUBJECT, course="backend_copy"),
             g.event(g.BASE,g.BASE+timedelta(seconds=15), number=3, subject=g.OTHER, course="backend")]
    session=Session([g.SUBJECT,g.OTHER],records)
    out=asyncio.run(PostgresActivityReport(DB(session),Fence()).read_activity(grant(),query()))
    assert out["totals"] == {"unique_visitors":2,"active_ms":15000,"theory_ms":15000,"practice_ms":0}
    assert out["daily"][0]["unique_visitors"]==2 and out["quality"]=="unknown"
    candidate=next((sql,p) for sql,p in session.calls if "SELECT DISTINCT subject_ref" in sql)
    fact=next((sql,p) for sql,p in session.calls if "SELECT accepted_event" in sql)
    assert candidate[1]["subjects"]==[g.SUBJECT,g.OTHER] and candidate[1]["courses"]==["backend"]
    assert "course_id" not in fact[0] and fact[1]["limit"]==MAX_FACTS+1
    locks=[p["key"] for sql,p in session.calls if "pg_advisory_xact_lock_shared" in sql]
    from pymentor_analytics.contracts.locking_v1 import subject_lock_key
    assert locks==sorted({subject_lock_key(v) for v in (g.SUBJECT,g.OTHER)})

def test_scope_denial_bounds_and_empty_scoped_snapshot():
    with pytest.raises(AuthorizationError):
        asyncio.run(PostgresActivityReport(DB(Session([],[])),Fence()).read_activity(grant(subjects=()),query(subject=g.SUBJECT)))
    with pytest.raises(AuthorizationError):
        asyncio.run(PostgresActivityReport(DB(Session([],[])),Fence()).read_activity(grant(),query(course="backend_copy")))
    with pytest.raises(ReportLimitExceeded):
        asyncio.run(PostgresActivityReport(DB(Session([g.SUBJECT]*(MAX_SUBJECTS+1),[])),Fence()).read_activity(grant("admin"),query()))
    record=g.event(g.BASE,g.BASE+timedelta(seconds=15))
    with pytest.raises(ReportLimitExceeded):
        asyncio.run(PostgresActivityReport(DB(Session([g.SUBJECT],[record]*(MAX_FACTS+1))),Fence()).read_activity(grant("admin"),query()))
    out=asyncio.run(PostgresActivityReport(DB(Session([],[])),Fence()).read_activity(grant(),query()))
    assert out["as_of"] is None and out["quality"]=="unknown" and out["totals"]["unique_visitors"]==0

def test_deleted_fence_and_guard_fail_closed():
    with pytest.raises(AuthorizationError):
        asyncio.run(PostgresActivityReport(DB(Session([g.SUBJECT],[],deleted=[g.SUBJECT])),Fence()).read_activity(grant(),query(subject=g.SUBJECT)))
    db=DB(Session([],[])); db.transaction_guard=None
    with pytest.raises(PrivacyUnavailable):
        asyncio.run(PostgresActivityReport(db,Fence()).read_activity(grant(),query()))

def test_expanded_core_schema_filters_both_reads_and_breakdown():
    class Expanded(Session):
        async def execute(self,statement,params=None):
            sql=str(statement)
            if "pg_catalog.pg_attribute" in sql:
                self.calls.append((sql,params)); return Result((True,True,True))
            return await super().execute(statement,params)
    record=g.event(g.BASE,g.BASE+timedelta(seconds=15),number=1,subject=g.SUBJECT)
    session=Expanded([g.SUBJECT],[record])
    out=asyncio.run(PostgresActivityReport(DB(session),Fence()).read_activity(grant(),query(),include_breakdown=True))
    assert len(out["breakdown"])==2 and out["breakdown"][1]["lesson_id"]=="same-lesson"
    relevant=[sql for sql,p in session.calls if "SELECT DISTINCT subject_ref" in sql or "SELECT accepted_event" in sql]
    assert len(relevant)==2 and all("source_kind='browser_v2'" in sql for sql in relevant)

def test_lock_order_follows_advisory_keys_not_uuid_order():
    from uuid import UUID
    from pymentor_analytics.contracts.locking_v1 import subject_lock_key
    first,second=UUID(int=1),UUID(int=4)
    assert first.int < second.int and subject_lock_key(first) > subject_lock_key(second)
    signed=VerifiedGrant(sub=first,role="admin",scope="report:read",course_ids=(),subject_refs=(),global_read=True,expires_at=int(time.time())+60)
    session=Session([second],[])
    asyncio.run(PostgresActivityReport(DB(session),Fence()).read_activity(signed,query()))
    keys=[p["key"] for sql,p in session.calls if "pg_advisory_xact_lock_shared" in sql]
    assert keys==[subject_lock_key(second),subject_lock_key(first)]
