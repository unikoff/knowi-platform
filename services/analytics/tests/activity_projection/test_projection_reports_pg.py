"""Prepared a010 integration scenarios; only a fresh sentinel DB under explicit GO.

This is not a privacy-authority or load test: the transaction guard is a local
stub, and no test in this module runs before ANALYTICS_RUN_ACTIVITY_PROJECTION_SYNTHETIC=1.
"""
import asyncio
import importlib.util
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import text
from pymentor_analytics.adapters.postgres.activity import ActivityStorage
from pymentor_analytics.adapters.postgres.activity_projection import PostgresActivityProjection
from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport, ProjectionUnavailable
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.auth_v1 import AuthorizationError, VerifiedGrant
from pymentor_analytics.contracts.reports_v1 import ActivityQuery
from pymentor_analytics.modules.analytics.activity.bucket import normalize_subject_bucket
from pymentor_analytics.modules.analytics.activity.calculator import calculate
from pymentor_analytics.adapters.postgres.activity_projection_reports import _DISCOVER, _SNAPSHOT

pytestmark=pytest.mark.skipif(os.environ.get("ANALYTICS_RUN_ACTIVITY_PROJECTION_SYNTHETIC")!="1",reason="explicit disposable a010 PG GO required")
spec=importlib.util.spec_from_file_location("projection_golden",Path(__file__).parents[1]/"reference/test_activity_v1.py")
g=importlib.util.module_from_spec(spec); spec.loader.exec_module(g)


class CaptureDB:
    """Capture statement parameters without rebinding the reader's guard."""
    def __init__(self,reader,captured,metrics=None):
        self.reader,self.captured,self.metrics=reader,captured,metrics
        self.transaction_guard=reader.transaction_guard

    @asynccontextmanager
    async def transaction(self):
        captured=self.captured
        metrics=self.metrics
        class Proxy:
            def __init__(self,session): self.session=session
            async def execute(self,statement,params=None):
                sql=str(statement)
                if sql==_SNAPSHOT: captured.clear(); captured.update(params)
                if metrics is None:
                    return await self.session.execute(statement,params)
                phase = "discovery" if sql==_DISCOVER else "snapshot" if sql==_SNAPSHOT else "locks" if sql.startswith("SELECT pg_advisory_xact_lock_shared") else "other_sql"
                started=time.monotonic()
                try:
                    return await self.session.execute(statement,params)
                finally:
                    metrics[phase]=metrics.get(phase,0)+(time.monotonic()-started)*1000
                    if phase=="locks": metrics["lock_calls"]=metrics.get("lock_calls",0)+1
        async with self.reader.transaction() as session:
            yield Proxy(session)

def db(url):
    value=Database(Settings(database_url=url,pool_size=2,pool_overflow=0,db_timeout_seconds=5))
    async def fixture_guard(session): pass
    value.transaction_guard=fixture_guard
    return value

async def drain(store):
    owner=uuid4()
    for _ in range(200):
        phase=await store.backfill_step(deadline=time.monotonic()+5)
        while True:
            claim=await store.claim(owner,deadline=time.monotonic()+5)
            if claim is None: break
            loaded,records=await store.load(claim,deadline=time.monotonic()+5)
            out=normalize_subject_bucket(records,subject_ref=loaded.subject_ref,bucket_start=loaded.bucket_start)
            await store.commit(loaded,out,deadline=time.monotonic()+5)
        if phase=="complete": return
    raise AssertionError("bounded backfill did not complete")

def test_reports_backfill_overlap_filter_dirty_and_fence(activity_projection_target):
    _,_,urls,run_id,*_=activity_projection_target
    async def run():
        writer,worker,reader,privacy=(db(urls[role]) for role in ("collector","processor","query","privacy"))
        left=g.BASE+timedelta(hours=20,minutes=59,seconds=50)
        first=g.event(left,left+timedelta(seconds=20),number=1,course="backend",subject=g.SUBJECT)
        competing=g.event(left+timedelta(seconds=5),left+timedelta(seconds=20),number=2,course="backend_copy",subject=g.SUBJECT,section="practice")
        second=g.event(left,left+timedelta(seconds=20),number=3,course="backend",subject=g.OTHER)
        opened=g.event(left,left,opened=True,number=4,course="backend",subject=g.OTHER)
        events=[first,competing,second,opened]
        actor=uuid4()
        def grant():
            return VerifiedGrant(sub=actor,role="admin",scope="report:read",course_ids=(),subject_refs=(),global_read=True,expires_at=int(time.time())+60)
        query=ActivityQuery(from_time=left+timedelta(milliseconds=1),to_time=left+timedelta(seconds=20),course_id="backend",subject_ref=None)
        store=PostgresActivityProjection(worker,expected_run_id=run_id)
        reports=PostgresProjectionActivityReport(reader,None,expected_run_id=run_id)
        try:
            async with writer.transaction() as session:
                for event in events: assert await ActivityStorage(session).insert(event)=="inserted"
            with pytest.raises(ProjectionUnavailable): await reports.read_activity(grant(),query)
            await drain(store)
            actual=await reports.read_activity(grant(),query,include_breakdown=True)
            expected=calculate(events,start=query.from_time,end=query.to_time,as_of=actual["as_of"],course_id="backend",include_breakdown=True)
            assert actual["totals"]==expected["totals"] and actual["daily"]==expected["daily"] and actual["breakdown"]==expected["breakdown"]
            assert actual["quality"]=="unknown"
            # Actual Ed25519 G1 verification and ASGI transport, on this owned PG.
            import httpx
            helper_path=Path(__file__).parents[1]/"activity_reports/test_activity_http.py"
            helper_spec=importlib.util.spec_from_file_location("projection_signed_http",helper_path)
            helper=importlib.util.module_from_spec(helper_spec); helper_spec.loader.exec_module(helper)
            app,token=helper.make_app(role="user",subject=g.SUBJECT)
            app.state.activity_reports=reports
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
                response=await client.get("/api/analytics/reports/activity",params={"from_time":query.from_time.isoformat(),
                    "to_time":query.to_time.isoformat(),"course_id":"backend"},headers={"Authorization":"Bearer "+token})
            assert response.status_code==200 and response.json()["quality"]=="unknown"
            late=g.event(left+timedelta(seconds=7),left+timedelta(seconds=15),number=5,course="backend",subject=g.SUBJECT)
            async with writer.transaction() as session: assert await ActivityStorage(session).insert(late)=="inserted"
            with pytest.raises(ProjectionUnavailable): await reports.read_activity(grant(),query)
            await drain(store)
            actual=await reports.read_activity(grant(),query,include_breakdown=True)
            expected=calculate(events+[late],start=query.from_time,end=query.to_time,as_of=actual["as_of"],course_id="backend",include_breakdown=True)
            assert (actual["totals"],actual["daily"],actual["breakdown"])==(expected["totals"],expected["daily"],expected["breakdown"])
            async with privacy.transaction() as session:
                await session.execute(text("INSERT INTO analytics.subject_fences(subject_ref,epoch,deleted,control_id) VALUES (:subject,1,true,:control)"),
                                      {"subject":g.SUBJECT,"control":uuid4()})
            selected=query.model_copy(update={"subject_ref":g.SUBJECT})
            with pytest.raises(AuthorizationError): await reports.read_activity(grant(),selected)
        finally:
            for item in (writer,worker,reader,privacy): await item.close()
    asyncio.run(run())


def test_rollups_match_oracle_for_full_and_partial_buckets(activity_projection_target):
    """Checks early SQL aggregation across midnight, overlapping courses and visitors."""
    _,_,urls,run_id,*_=activity_projection_target
    async def run():
        writer,worker,reader=(db(urls[role]) for role in ("collector","processor","query"))
        start=g.BASE+timedelta(hours=20,minutes=45)
        midnight=start+timedelta(minutes=15)
        events=[
            g.event(midnight-timedelta(seconds=10),midnight+timedelta(seconds=10),number=101,course="backend"),
            g.event(midnight-timedelta(seconds=5),midnight+timedelta(seconds=10),number=102,course="backend_copy",section="practice"),
            g.event(midnight-timedelta(seconds=10),midnight+timedelta(seconds=10),number=103,subject=g.OTHER),
            g.event(midnight,midnight,number=104,subject=g.OTHER,opened=True),
        ]
        grant=VerifiedGrant(sub=uuid4(),role="admin",scope="report:read",course_ids=(),subject_refs=(),global_read=True,expires_at=int(time.time())+60)
        reports=PostgresProjectionActivityReport(reader,None,expected_run_id=run_id)
        store=PostgresActivityProjection(worker,expected_run_id=run_id)
        try:
            async with writer.transaction() as session:
                for event in events: assert await ActivityStorage(session).insert(event)=="inserted"
            await drain(store)
            # The v3 copy multiplier must remain exact beyond bigint, without
            # altering the overflow behaviour of SUM(bigint) in the old path.
            async with reader.transaction() as session:
                expression=_SNAPSHOT.split("SELECT b.local_day,v.course_id,v.lesson_id,v.section,",1)[1].split(" AS amount",1)[0]
                total=(await session.execute(text("SELECT "+expression+" FROM (SELECT 3::bigint AS copies) b "
                    "CROSS JOIN LATERAL (SELECT 4611686018427387904::bigint AS active_ms) v"))).scalar_one()
                assert total==3*(2**62)
            for lo,hi in ((start,start+timedelta(minutes=30)),
                          (midnight-timedelta(seconds=7),midnight+timedelta(seconds=7))):
                for course in (None,"backend","backend_copy"):
                    query=ActivityQuery(from_time=lo,to_time=hi,course_id=course,subject_ref=None)
                    actual=await reports.read_activity(grant,query,include_breakdown=True)
                    expected=calculate(events,start=lo,end=hi,as_of=actual["as_of"],course_id=course,include_breakdown=True)
                    assert (actual["totals"],actual["daily"],actual["breakdown"])==(expected["totals"],expected["daily"],expected["breakdown"])
        finally:
            for item in (writer,worker,reader): await item.close()
    asyncio.run(run())


def test_report_precheck_priority_over_item_limits(activity_projection_target, monkeypatch):
    """A dirty subject stays unavailable; an earlier bucket limit stays a limit error."""
    from pymentor_analytics.adapters.postgres import activity_projection_reports as report_module
    from pymentor_analytics.adapters.postgres.activity_reports import ReportLimitExceeded
    _,_,urls,run_id,*_=activity_projection_target
    async def run():
        from pymentor_analytics.contracts.locking_v1 import subject_lock_key
        writer,worker,reader,migrate=(db(urls[role]) for role in ("collector","processor","query","migrate"))
        start=g.BASE
        event=g.event(start,start+timedelta(seconds=10),number=201)
        grant=VerifiedGrant(sub=uuid4(),role="admin",scope="report:read",course_ids=(),subject_refs=(),global_read=True,expires_at=int(time.time())+60)
        captured={}
        reports=PostgresProjectionActivityReport(CaptureDB(reader,captured),None,expected_run_id=run_id)
        query=ActivityQuery(from_time=start,to_time=start+timedelta(minutes=15),course_id=None,subject_ref=None)
        try:
            async with writer.transaction() as session:
                assert await ActivityStorage(session).insert(event)=="inserted"
            await drain(PostgresActivityProjection(worker,expected_run_id=run_id))
            assert (await reports.read_activity(grant,query))["totals"]["unique_visitors"]==1
            async with reader.transaction() as session:
                for lock in sorted({subject_lock_key(s) for s in captured["locked_subjects"]}|{subject_lock_key(grant.sub)}):
                    await session.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"),{"key":lock})
                assert (await session.execute(text("SELECT analytics.lock_activity_projection_run(:run)"),{"run":run_id})).scalar_one() is True
                stale={**captured,"locked_subjects":[]}
                assert (await session.execute(text(_SNAPSHOT),stale)).mappings().one()["error"]=="candidate_changed"
            monkeypatch.setattr(report_module,"MAX_FULL_ITEMS",0)
            with pytest.raises(ReportLimitExceeded):
                await reports.read_activity(grant,query)
            async with migrate.transaction() as session:
                await session.execute(text("UPDATE analytics.activity_projection_buckets SET state='dirty'"))
            with pytest.raises(ProjectionUnavailable):
                await reports.read_activity(grant,query)
            monkeypatch.setattr(report_module,"MAX_BUCKET_ROWS",0)
            with pytest.raises(ReportLimitExceeded):
                await reports.read_activity(grant,query)
        finally:
            for item in (writer,worker,reader,migrate): await item.close()
    asyncio.run(run())


def test_generated_byte_counts_reject_forgery(activity_projection_target):
    """PostgreSQL owns byte metadata, even for a writer supplying an explicit value."""
    import psycopg
    _,_,urls,_,_=activity_projection_target
    with psycopg.connect(urls["migrate"],autocommit=True) as conn:
        sql="""INSERT INTO analytics.activity_projection_buckets
            (subject_ref,bucket_start,metric_version,generation,projection_generation,subject_epoch,state,
             winner_intervals,presence_intervals,presence_points,contributions,presence_labels,
             compact_item_count,edge_item_count,has_presence)
            VALUES (%s,%s,'activity-v1',1,1,0,'ready','[]','[]','[]','[]','[]',0,0,false)"""
        args=(uuid4(),g.BASE)
        conn.execute(sql,args)
        row=conn.execute("""SELECT compact_json_bytes,edge_json_bytes,
            octet_length(contributions::text)+octet_length(presence_labels::text),
            octet_length(winner_intervals::text)+octet_length(presence_intervals::text)+octet_length(presence_points::text)
            FROM analytics.activity_projection_buckets WHERE subject_ref=%s""",(args[0],)).fetchone()
        assert row==(4,6,4,6)
        with pytest.raises(psycopg.errors.GeneratedAlways):
            conn.execute("UPDATE analytics.activity_projection_buckets SET compact_json_bytes=0 WHERE subject_ref=%s",(args[0],))
        assert conn.execute("SELECT compact_json_bytes FROM analytics.activity_projection_buckets WHERE subject_ref=%s",(args[0],)).fetchone()==(4,)


def test_future_facts_and_future_dirty_bucket_excluded_from_snapshot(activity_projection_target):
    import psycopg
    _,_,urls,run_id,_=activity_projection_target
    async def run():
        writer,worker,reader=(db(urls[role]) for role in ("collector","processor","query"))
        now=datetime.now(timezone.utc).replace(microsecond=0)
        future=now+timedelta(seconds=90)
        events=[g.event(future,future,opened=True,number=301),
                g.event(future,future+timedelta(seconds=15),number=302,subject=g.OTHER)]
        store=PostgresActivityProjection(worker,expected_run_id=run_id)
        try:
            async with writer.transaction() as session:
                for event in events: assert await ActivityStorage(session).insert(event)=="inserted"
            await drain(store)
            # Separate far-future dirty row must not block a current snapshot.
            bucket_start=now.replace(minute=(now.minute//15)*15,second=0)+timedelta(minutes=30)
            with psycopg.connect(urls["migrate"],autocommit=True) as conn:
                conn.execute("""INSERT INTO analytics.activity_projection_buckets
                    (subject_ref,bucket_start,metric_version,generation,state)
                    VALUES (%s,%s,'activity-v1',1,'dirty')""",(uuid4(),bucket_start))
            grant=VerifiedGrant(sub=uuid4(),role="admin",scope="report:read",course_ids=(),subject_refs=(),
                                global_read=True,expires_at=int(time.time())+60)
            query=ActivityQuery(from_time=now-timedelta(seconds=10),to_time=bucket_start+timedelta(minutes=15),
                                course_id=None,subject_ref=None)
            report=await PostgresProjectionActivityReport(reader,None,expected_run_id=run_id).read_activity(grant,query)
            assert report["totals"]=={"unique_visitors":0,"active_ms":0,"theory_ms":0,"practice_ms":0}
            assert report["daily"]==[] and report["quality"]=="unknown"
        finally:
            for item in (writer,worker,reader): await item.close()
    asyncio.run(run())

@pytest.mark.skipif(os.environ.get("ANALYTICS_RUN_ACTIVITY_MONTH_SAMPLE")!="1",
                    reason="separate resource GO required for 31-day 50-subject sample")
def test_sparse_31day_50subject_explain(activity_projection_target):
    """12,400 facts exceeds old cap; not a 2.4 events/s month load claim."""
    import json
    from uuid import UUID
    from pymentor_analytics.contracts.locking_v1 import subject_lock_key
    _,_,urls,run_id,*_=activity_projection_target
    async def run():
        writer,worker,reader=(db(urls[role]) for role in ("collector","processor","query"))
        subjects=[UUID(int=100+i) for i in range(50)]
        store=PostgresActivityProjection(worker,expected_run_id=run_id)
        count=0; captured={}
        try:
            # Small commits + drain keep the real dirty-bucket backpressure in force.
            for day in range(31):
                async with writer.transaction() as session:
                    for subject in subjects:
                        for hour in range(0,24,3):
                            count+=1; start=g.BASE+timedelta(days=day,hours=hour)
                            record=g.event(start,start+timedelta(seconds=15),number=count,subject=subject,
                                           namespace="browser:"+str(subject),course="backend")
                            assert await ActivityStorage(session).insert(record)=="inserted"
                await drain(store)
            assert count==12400
            query=ActivityQuery(from_time=g.BASE,to_time=g.BASE+timedelta(days=31),course_id="backend",subject_ref=None)
            grant=VerifiedGrant(sub=uuid4(),role="admin",scope="report:read",course_ids=(),subject_refs=(),global_read=True,expires_at=int(time.time())+60)
            report=PostgresProjectionActivityReport(CaptureDB(reader,captured),None,expected_run_id=run_id)
            started=time.monotonic()
            result=await report.read_activity(grant,query)
            elapsed_ms=(time.monotonic()-started)*1000
            assert result["totals"]=={"unique_visitors":50,"active_ms":186000000,"theory_ms":186000000,"practice_ms":0}
            assert captured and len(captured["locked_subjects"])==50
            # This second query is an EXPLAIN ANALYZE on this same disposable DB;
            # it is an observed extra read, not the original request's timing.
            async with reader.transaction() as session:
                for key in sorted({subject_lock_key(s) for s in captured["locked_subjects"]}|{subject_lock_key(grant.sub)}):
                    await session.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"),{"key":key})
                assert (await session.execute(text("SELECT analytics.lock_activity_projection_run(:run)"),{"run":run_id})).scalar_one() is True
                explain=(await session.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "+_SNAPSHOT),captured)).scalar_one()
                database_bytes=(await session.execute(text("SELECT pg_database_size(current_database())"))).scalar_one()
            print(json.dumps({"dataset":"sparse-50-subject-31day","fact_count":count,"reader_ms":elapsed_ms,
                              "database_bytes":database_bytes,"explain":explain},default=str))
        finally:
            for item in (writer,worker,reader): await item.close()
    asyncio.run(run())

@pytest.mark.skipif(os.environ.get("ANALYTICS_RUN_ACTIVITY_MONTH_DENSE")!="1" or os.environ.get("ANALYTICS_BENCHMARK_GO")!="1",
                    reason="separate dense-data resource GO required")
def test_dense_31day_50subject_query_only_explain(activity_projection_target):
    """Owned synthetic ready-bucket dataset: query capacity, not ingestion/coverage proof."""
    import json
    import sys
    from uuid import UUID
    import psycopg
    from pymentor_analytics.contracts.locking_v1 import subject_lock_key
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"tools"))
    import benchmark_activity as bench
    data,name,urls,run_id,*owner_evidence=activity_projection_target
    if len(owner_evidence)!=1:
        raise RuntimeError("fixture must supply an independently recorded owner sentinel")
    sentinel=owner_evidence[0]
    if not isinstance(sentinel,str) or len(sentinel)!=64 or any(c not in "0123456789abcdef" for c in sentinel):
        raise RuntimeError("invalid independent owner sentinel")
    from urllib.parse import unquote, urlsplit
    parsed=urlsplit(urls["migrate"])
    if (not bench.bootstrap.PATTERN.fullmatch(name) or parsed.scheme!="postgresql"
            or parsed.hostname!="127.0.0.1" or parsed.port!=57392 or parsed.path!="/"+name
            or unquote(parsed.username or "")!=name+"_migrate" or not parsed.password or parsed.query or parsed.fragment):
        raise RuntimeError("unsafe dense sample database target")
    monitor=bench.resource_floors(os.environ); monitor.check()
    subjects=[UUID(int=100+i) for i in range(50)]
    def encode(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)
    def seed():
        options="-c statement_timeout=30000 -c lock_timeout=1000"
        with psycopg.connect(urls["migrate"],connect_timeout=3,autocommit=True,options=options) as conn:
            identity=conn.execute("SELECT current_database(),pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone()
            marker=conn.execute("SELECT token FROM public.analytics_test_sentinel").fetchall()
            assert identity==(name,name+"_migrate") and marker==[(sentinel,)]
            assert not conn.execute("SELECT EXISTS(SELECT 1 FROM analytics.activity_projection_buckets)").fetchone()[0]
            statement="""INSERT INTO analytics.activity_projection_buckets
                (subject_ref,bucket_start,metric_version,generation,projection_generation,subject_epoch,state,
                 winner_intervals,presence_intervals,presence_points,contributions,presence_labels,
                 compact_item_count,edge_item_count,has_presence)
                VALUES (%s,%s,'activity-v1',1,1,0,'ready',%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,true)"""
            pending=[]
            for bucket_index in range(31*96):
                if bucket_index%96==0: monitor.check()
                start=g.BASE+timedelta(minutes=15*bucket_index)
                start_ms=int(start.timestamp())*1000
                templates=[]
                for overlap in (False,True):
                    winner_course="backend_copy" if overlap else "backend"
                    winners=[{"start_ms":start_ms,"end_ms":start_ms+900000,"course_id":winner_course,"lesson_id":"same-lesson","section":"theory"}]
                    presence=[{"start_ms":start_ms+i*15000,"end_ms":start_ms+(i+1)*15000,"course_id":course,
                               "lesson_id":"same-lesson","section":"theory"}
                              for i in range(60) for course in (("backend","backend_copy") if overlap else ("backend",))]
                    points=[]
                    contributions=[{"course_id":winner_course,"lesson_id":"same-lesson","section":"theory","active_ms":900000}]
                    labels=[{"course_id":course,"lesson_id":lesson} for course in (("backend","backend_copy") if overlap else ("backend",))
                            for lesson in (None,"same-lesson")]
                    serialized=[encode(v) for v in (winners,presence,points,contributions,labels)]
                    compact_count=len(contributions)+len(labels); edge_count=len(winners)+len(presence)+len(points)
                    templates.append((*serialized,compact_count,edge_count))
                for i,subject in enumerate(subjects[:30]):
                    pending.append((subject,start,*templates[i<6]))
                if len(pending)>=120:
                    with conn.transaction():
                        with conn.cursor() as cursor: cursor.executemany(statement,pending)
                    pending.clear()
            if pending:
                with conn.transaction():
                    with conn.cursor() as cursor: cursor.executemany(statement,pending)
            # Test-only simulated complete set; it is not evidence of backfill or source coverage.
            conn.execute("UPDATE analytics.activity_projection_backfill SET phase='complete' WHERE singleton=true AND run_id=%s",(run_id,))
    seed()
    async def run():
        reader=db(urls["query"]); captured={}; samples=[]
        metrics={} if os.environ.get("ANALYTICS_PROJECTION_PROFILE_PHASES")=="1" else None
        phase_samples=[]
        query=ActivityQuery(from_time=g.BASE,to_time=g.BASE+timedelta(days=31),course_id=None,subject_ref=None)
        actor=uuid4(); report=PostgresProjectionActivityReport(CaptureDB(reader,captured,metrics),None,expected_run_id=run_id)
        try:
            for _ in range(20):
                monitor.check()
                if metrics is not None: metrics.clear()
                grant=VerifiedGrant(sub=actor,role="admin",scope="report:read",course_ids=(),subject_refs=(),global_read=True,expires_at=int(time.time())+60)
                started=time.monotonic(); result=await report.read_activity(grant,query); samples.append((time.monotonic()-started)*1000)
                assert result["totals"]=={"unique_visitors":30,"active_ms":80352000000,"theory_ms":80352000000,"practice_ms":0}
                if metrics is not None:
                    assert metrics.get("lock_calls")==31 and all(key in metrics for key in ("discovery","snapshot","other_sql"))
                    phase_samples.append({**metrics,"wall":samples[-1],"non_sql":max(0,samples[-1]-sum(metrics.get(key,0) for key in ("discovery","locks","snapshot","other_sql")))})
            async with reader.transaction() as session:
                for lock in sorted({subject_lock_key(s) for s in captured["locked_subjects"]}|{subject_lock_key(actor)}):
                    await session.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"),{"key":lock})
                assert (await session.execute(text("SELECT analytics.lock_activity_projection_run(:run)"),{"run":run_id})).scalar_one() is True
                explain=(await session.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "+_SNAPSHOT),captured)).scalar_one()
                database_bytes=(await session.execute(text("SELECT pg_database_size(current_database())"))).scalar_one()
            ordered=sorted(samples)
            phase_stats={key:{"p50_ms":sorted(item[key] for item in phase_samples)[9],
                              "p95_ms":sorted(item[key] for item in phase_samples)[18]}
                         for key in ("discovery","locks","snapshot","other_sql","non_sql","wall")} if phase_samples else None
            print(json.dumps({"dataset":"synthetic-query-only-50subject-30active-31day-dense",
                "bucket_rows":31*96*30,"presence_intervals":36*60*96*31,"queries":len(samples),
                "p50_ms":ordered[9],"p95_ms":ordered[18],"database_bytes":database_bytes,
                "resources":monitor.result(),"phases":phase_stats,"lock_calls_per_read":31 if phase_samples else None,"explain":explain},default=str))
        finally:
            await reader.close()
    asyncio.run(run())
