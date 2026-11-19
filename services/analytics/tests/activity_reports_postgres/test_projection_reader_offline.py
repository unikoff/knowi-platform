"""One-statement reader contract tests with a fake DB, never live PostgreSQL."""
import asyncio
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
import pytest
from sqlalchemy import text
from pymentor_analytics.adapters.postgres.activity_projection_reports import (
    PostgresProjectionActivityReport, ProjectionUnavailable, ReportLimitExceeded, _SNAPSHOT, _DISCOVER)
from pymentor_analytics.contracts.auth_v1 import AuthorizationError, VerifiedGrant
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.reports_v1 import ActivityQuery

UTC=timezone.utc
ACTOR=UUID(int=1); SUBJECT=UUID(int=4); RUN=UUID(int=9)
START=datetime(2026,1,1,tzinfo=UTC)
class Result:
    def __init__(self, value): self.value=value
    def scalars(self): return self
    def scalar_one(self): return self.value
    def all(self): return self.value
    def mappings(self): return self
    def one(self): return self.value
class Session:
    def __init__(self,candidates=None,error=None,visitors=1):
        self.candidates=[SUBJECT] if candidates is None else candidates; self.error=error; self.visitors=visitors; self.calls=[]
    async def execute(self,stmt,params=None):
        sql=str(stmt); self.calls.append((sql,params))
        if "SELECT DISTINCT subject_ref FROM analytics.activity_projection_buckets" in sql and not sql.lstrip().startswith("WITH"):
            return Result(self.candidates)
        if "lock_activity_projection_run" in sql: return Result(True)
        if sql.lstrip().startswith("WITH"):
            total={"unique_visitors":self.visitors,"active_ms":15 if self.visitors else 0,
                   "theory_ms":5 if self.visitors else 0,"practice_ms":10 if self.visitors else 0}
            return Result({"error":self.error,"as_of":START+timedelta(days=1),"totals":total,
                           "daily":[{"date":"2026-01-01",**total}] if self.visitors else [],
                           "breakdown":[{"level":"course","course_id":"backend","lesson_id":None,**total}] if self.visitors else []})
        return Result(None)
class DB:
    def __init__(self,session,guard=True): self.session=session; self.guards=[]; self.transaction_guard=self.guard if guard else None
    async def guard(self,session): self.guards.append(len(session.calls))
    @asynccontextmanager
    async def transaction(self):
        await self.transaction_guard(self.session)
        yield self.session
        await self.transaction_guard(self.session)

def grant(role="leader"):
    return VerifiedGrant(sub=ACTOR,role=role,scope="report:read",course_ids=("backend",),subject_refs=(SUBJECT,),
                         global_read=False,expires_at=int(time.time())+60)
def query():
    return ActivityQuery(from_time=START,to_time=START+timedelta(days=1),course_id="backend",subject_ref=None)

def test_transaction_guard_and_numeric_locks_before_one_snapshot():
    session=Session(); db=DB(session)
    result=asyncio.run(PostgresProjectionActivityReport(db,None,expected_run_id=RUN).read_activity(grant(),query(),include_breakdown=True))
    locks=[(sql,p["keys"]) for sql,p in session.calls if "pg_advisory_xact_lock_shared" in sql]
    assert len(locks)==1
    assert locks[0][1]==sorted({subject_lock_key(ACTOR),subject_lock_key(SUBJECT)})
    assert "ORDER BY locks.lock_key" in locks[0][0]
    snapshot=[(sql,p) for sql,p in session.calls if sql.lstrip().startswith("WITH")]
    assert len(snapshot)==1 and snapshot[0][1]["locked_subjects"]==[SUBJECT]
    assert snapshot[0][1]["run_id"]==RUN and snapshot[0][1]["courses"]==["backend"]
    assert db.guards==[0,2,3,4]  # before work, after subject locks, after run lock, after SQL
    assert result["quality"]=="unknown" and result["lag_seconds"] is None
    assert result["daily"][0]["date"]==date(2026,1,1)
    assert "activity_facts" not in snapshot[0][0]

def test_selected_target_locked_even_without_matching_candidates():
    session=Session(candidates=[],visitors=0); db=DB(session)
    selected=query().model_copy(update={"subject_ref": SUBJECT})
    asyncio.run(PostgresProjectionActivityReport(db,None,expected_run_id=RUN).read_activity(grant(),selected))
    locks=[p["keys"] for sql,p in session.calls if "pg_advisory_xact_lock_shared" in sql]
    assert locks==[sorted({subject_lock_key(ACTOR),subject_lock_key(SUBJECT)})]
    snapshot=[p for sql,p in session.calls if sql.lstrip().startswith("WITH")]
    assert snapshot[0]["selected_subject"]==SUBJECT and snapshot[0]["locked_subjects"]==[]

def test_changed_candidates_dirty_unbackfilled_or_epoch_fail_closed():
    for error in ("candidate_changed","backfill_unavailable","projection_unavailable"):
        with pytest.raises(ProjectionUnavailable):
            asyncio.run(PostgresProjectionActivityReport(DB(Session(error=error)),None,expected_run_id=RUN).read_activity(grant(),query()))
    for error in ("subject_limit","bucket_limit","item_limit","breakdown_limit"):
        with pytest.raises(ReportLimitExceeded):
            asyncio.run(PostgresProjectionActivityReport(DB(Session(error=error)),None,expected_run_id=RUN).read_activity(grant(),query()))
    with pytest.raises(AuthorizationError):
        asyncio.run(PostgresProjectionActivityReport(DB(Session(error="actor_or_target_deleted")),None,expected_run_id=RUN).read_activity(grant(),query()))

def test_missing_guard_or_run_identity_closes_and_empty_has_no_asof():
    with pytest.raises(ValueError): PostgresProjectionActivityReport(DB(Session()),None,expected_run_id=None)
    with pytest.raises(PrivacyUnavailable):
        asyncio.run(PostgresProjectionActivityReport(DB(Session(),guard=False),None,expected_run_id=RUN).read_activity(grant(),query()))
    result=asyncio.run(PostgresProjectionActivityReport(DB(Session(candidates=[],visitors=0)),None,expected_run_id=RUN).read_activity(grant(),query()))
    assert result["as_of"] is None and result["quality"]=="unknown"

def test_sql_bound_parameters_and_single_statement_evidence():
    names=set(text(_SNAPSHOT)._bindparams)
    assert {"from_ms","to_ms","locked_subjects","run_id","bucket_limit","max_edge_items","max_full_items"}<=names
    assert "from_m" not in names and "to_m" not in names
    assert "activity_facts" not in _SNAPSHOT+_DISCOVER
    assert "statement_timestamp()" in _SNAPSHOT and "b.projection_generation IS DISTINCT FROM b.generation" in _SNAPSHOT
    assert "jsonb_to_recordset(b.contributions)" in _SNAPSHOT and "jsonb_to_recordset(b.winner_intervals)" in _SNAPSHOT
    assert "count(DISTINCT subject_ref)" in _SNAPSHOT and "array_agg(subject_ref ORDER BY subject_ref)" in _SNAPSHOT


def test_snapshot_clips_candidate_and_dirty_scope_before_json_expansion():
    # Static SQL contract only; the gated PG suite verifies execution and DDL.
    assert "bucket_start<LEAST(:to_time,date_trunc('milliseconds',statement_timestamp()))" in _DISCOVER
    assert ":from_time<LEAST(:to_time,date_trunc('milliseconds',statement_timestamp()))" in _DISCOVER
    assert "LEAST(CAST(:to_ms AS bigint), floor(extract(epoch FROM statement_timestamp())*1000)::bigint)" in _SNAPSHOT
    assert _SNAPSHOT.count("b.bucket_start<p.hi_time") == 2
    assert _SNAPSHOT.count("p.lo_ms<p.hi_ms") >= 4
    assert "v.at_ms<b.hi_ms" in _SNAPSHOT and "LEAST(v.end_ms,b.hi_ms)" in _SNAPSHOT
    assert _SNAPSHOT.index("item_counts AS MATERIALIZED") < _SNAPSHOT.index("jsonb_to_recordset")
    assert "sum(b.compact_item_count) FILTER (WHERE" in _SNAPSHOT
    assert "sum(b.edge_item_count) FILTER (WHERE" in _SNAPSHOT
    assert "sum(b.compact_json_bytes) FILTER (WHERE" in _SNAPSHOT
    assert "sum(b.edge_json_bytes) FILTER (WHERE" in _SNAPSHOT
    assert "(SELECT invalid FROM scoped_stats) THEN 'projection_unavailable'" in _SNAPSHOT
    assert "c.error IS NULL" in _SNAPSHOT
    assert "i.full_items<=:max_full_items AND i.edge_items<=:max_edge_items" in _SNAPSHOT
    assert "i.full_bytes<=:max_full_bytes AND i.edge_bytes<=:max_edge_bytes" in _SNAPSHOT
    assert _SNAPSHOT.index("ready AS MATERIALIZED") < _SNAPSHOT.index("jsonb_to_recordset")


def test_pg_sample_capture_proxy_preserves_guard_call_signature():
    import importlib.util
    from pathlib import Path
    path=Path(__file__).parents[1]/"activity_projection/test_projection_reports_pg.py"
    spec=importlib.util.spec_from_file_location("projection_sample_capture",path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    class GuardDB(DB):
        async def guard(self,session): self.guards.append(session)
    session=Session(); db=GuardDB(session); captured={}
    proxy=module.CaptureDB(db,captured)
    async def exercise():
        async with proxy.transaction() as wrapped:
            await proxy.transaction_guard(wrapped)
            await wrapped.execute(text(_SNAPSHOT),{"example":1})
    asyncio.run(exercise())
    assert len(db.guards)==3 and captured=={"example":1}

@pytest.mark.parametrize("fail_at",[2,3,4])
def test_guard_rechecked_after_lock_and_before_release(fail_at):
    session=Session()
    class FailingDB(DB):
        async def guard(self,session):
            self.guards.append(len(session.calls))
            if len(self.guards)==fail_at: raise PrivacyUnavailable()
    db=FailingDB(session)
    with pytest.raises(PrivacyUnavailable):
        asyncio.run(PostgresProjectionActivityReport(db,None,expected_run_id=RUN).read_activity(grant(),query()))
    snapshots=[sql for sql,_ in session.calls if sql.lstrip().startswith("WITH")]
    assert len(snapshots)==(0 if fail_at in (2,3) else 1)

def test_signed_http_uses_same_v1_json_and_dirty_is_503():
    import importlib.util
    from pathlib import Path
    path=Path(__file__).parents[1]/"activity_reports/test_activity_http.py"
    spec=importlib.util.spec_from_file_location("projection_http_fixture",path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    app,token=module.make_app()
    app.state.activity_reports=PostgresProjectionActivityReport(DB(Session(candidates=[module.USER])),None,expected_run_id=RUN)
    response=asyncio.run(module.request(app,token))
    assert response.status_code==200
    assert set(response.json())=={"schema_version","status","quality","as_of","lag_seconds","timezone","metric_version",
                                  "course_id","from_time","to_time","totals","daily","trusted_progress"}
    app.state.activity_reports=PostgresProjectionActivityReport(DB(Session(candidates=[module.USER],error="projection_unavailable")),None,expected_run_id=RUN)
    unavailable=asyncio.run(module.request(app,token))
    assert unavailable.status_code==503

def test_dataset_lock_mismatch_fails_before_snapshot():
    class WrongRun(Session):
        async def execute(self,stmt,params=None):
            if "lock_activity_projection_run" in str(stmt):
                self.calls.append((str(stmt),params)); return Result(False)
            return await super().execute(stmt,params)
    session=WrongRun()
    with pytest.raises(ProjectionUnavailable):
        asyncio.run(PostgresProjectionActivityReport(DB(session),None,expected_run_id=RUN).read_activity(grant(),query()))
    assert not any(sql.lstrip().startswith("WITH") for sql,_ in session.calls)

def test_inconsistent_projection_metrics_fail_closed():
    class Corrupt(Session):
        async def execute(self,stmt,params=None):
            result=await super().execute(stmt,params)
            if str(stmt).lstrip().startswith("WITH"):
                result.value["totals"]["unique_visitors"]=0
            return result
    with pytest.raises(ProjectionUnavailable):
        asyncio.run(PostgresProjectionActivityReport(DB(Corrupt()),None,expected_run_id=RUN).read_activity(grant(),query()))
