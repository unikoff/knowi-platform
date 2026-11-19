"""Opt-in synthetic a011 dense sample; optional real day backfill/worker path."""
import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text

from pymentor_analytics.adapters.postgres.activity_day_reports import _DAY_SNAPSHOT
from pymentor_analytics.adapters.postgres.activity_day_projection import PostgresActivityDayProjection
from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport
from pymentor_analytics.app.activity_day_projection_runtime import ActivityDayProjectionRuntime
from pymentor_analytics.contracts.auth_v1 import VerifiedGrant
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.reports_v1 import ActivityQuery

from test_day_reports_pg import g, helpers

pytestmark = pytest.mark.skipif(
    os.environ.get("ANALYTICS_RUN_ACTIVITY_DAY_DENSE") != "1" or os.environ.get("ANALYTICS_BENCHMARK_GO") != "1",
    reason="separate disposable a011 dense sample GO required",
)


class CaptureDay:
    def __init__(self, reader, captured):
        self.reader, self.captured, self.transaction_guard = reader, captured, reader.transaction_guard
    @asynccontextmanager
    async def transaction(self):
        captured = self.captured
        class Proxy:
            def __init__(self, session): self.session = session
            async def execute(self, statement, params=None):
                if str(statement) == _DAY_SNAPSHOT: captured.clear(); captured.update(params)
                return await self.session.execute(statement, params)
        async with self.reader.transaction() as session:
            yield Proxy(session)


async def _day_backlog(worker):
    async with worker.transaction() as session:
        row=(await session.execute(text("""SELECT b.run_id,b.phase,b.cursor_subject,b.cursor_bucket,b.cursor_metric,
          (SELECT count(*) FROM analytics.activity_projection_day_sources) AS sources,
          (SELECT count(*) FROM analytics.activity_projection_day_sources WHERE state='ready') AS ready,
          (SELECT count(*) FROM analytics.activity_projection_day_sources WHERE state='dirty') AS dirty,
          (SELECT count(*) FROM analytics.activity_projection_day_sources WHERE state='claimed') AS claimed,
          (SELECT count(*) FROM analytics.activity_projection_day_sources WHERE state='quarantined') AS quarantined,
          (SELECT count(*) FROM analytics.activity_projection_days) AS payloads
          FROM analytics.activity_projection_day_backfill b WHERE singleton=true"""))).mappings().one()
        return dict(row)


async def _build_day_projection(worker, run_id, monitor, *, max_cycles=5000, deadline_seconds=600, stall_seconds=60):
    """Use the real runtime cycle with explicit aggregate deadline/progress bounds."""
    if not 1 <= max_cycles <= 5000 or not 0 < deadline_seconds <= 600 or not 0 < stall_seconds <= 60:
        raise ValueError("invalid dense day build bounds")
    class Ready:
        async def is_ready(self): return True  # fixture-only privacy/readiness stub
    runtime=ActivityDayProjectionRuntime(PostgresActivityDayProjection(worker,expected_run_id=run_id),
                                         Ready(),enabled=True,operation_timeout=20)
    started=time.monotonic(); deadline=started+deadline_seconds
    try:
        monitor.check()
        async with asyncio.timeout(deadline_seconds): initial=await _day_backlog(worker)
        if (initial["phase"]!="scanning" or initial["run_id"] is not None or initial["sources"]<=0
                or initial["ready"] or initial["claimed"] or initial["quarantined"] or initial["payloads"]
                or initial["dirty"]!=initial["sources"]):
            raise RuntimeError("dense day build target is not fresh")
        last_change=started; previous=None; cycles=0; final=initial
        while cycles < max_cycles:
            monitor.check()
            remaining=deadline-time.monotonic()
            if remaining<=0: raise TimeoutError("dense day build deadline reached")
            async with asyncio.timeout(remaining): progressed=await runtime._cycle()
            cycles+=1
            if runtime.failures or runtime.backfill_failures:
                raise RuntimeError("dense day runtime reported a failure")
            if cycles%10==0 or not progressed:
                async with asyncio.timeout(max(0,deadline-time.monotonic())):
                    final=await _day_backlog(worker)
                if final["quarantined"]:
                    raise RuntimeError("dense day build has quarantined sources")
                marker=(final["phase"],final["cursor_subject"],final["cursor_bucket"],final["cursor_metric"],final["ready"],final["payloads"])
                if marker!=previous: previous=marker; last_change=time.monotonic()
                elif time.monotonic()-last_change>=stall_seconds: raise RuntimeError("dense day build made no progress")
                if final["phase"]=="complete" and final["run_id"]==run_id:
                    if (final["sources"]!=final["ready"] or final["payloads"]!=final["sources"]
                            or final["dirty"] or final["claimed"] or runtime.completed!=final["sources"]):
                        raise RuntimeError("dense day build is incomplete")
                    elapsed=time.monotonic()-started
                    return {"elapsed_ms":elapsed*1000,"cycles":cycles,"completed":runtime.completed,
                            "failures":runtime.failures,"backfill_failures":runtime.backfill_failures,
                            "initial":initial,"final":final,
                            "ready_per_second":final["ready"]/elapsed if elapsed>0 else None}
            if not progressed: await asyncio.sleep(.1)
        raise RuntimeError("dense day build cycle limit reached")
    finally:
        await runtime.stop()


def test_dense_day_query_only_explain(activity_day_projection_target):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import benchmark_activity as bench
    _,name,urls,run_id,sentinel = activity_day_projection_target
    parsed = urlsplit(urls["migrate"])
    if (not bench.bootstrap.PATTERN.fullmatch(name) or parsed.scheme != "postgresql"
            or parsed.hostname != "127.0.0.1" or parsed.port != 57392 or parsed.path != "/"+name
            or unquote(parsed.username or "") != name+"_migrate" or not parsed.password or parsed.query or parsed.fragment
            or not isinstance(sentinel,str) or len(sentinel)!=64):
        raise RuntimeError("unsafe dense sample target")
    monitor=bench.resource_floors(os.environ); monitor.check()
    build_mode=os.environ.get("ANALYTICS_RUN_ACTIVITY_DAY_DENSE_BUILD")=="1"
    subjects=[UUID(int=100+i) for i in range(50)]
    def encode(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)
    with psycopg.connect(urls["migrate"],connect_timeout=3,autocommit=True,
                         options="-c statement_timeout=30000 -c lock_timeout=1000") as conn:
        identity=conn.execute("SELECT current_database(),pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone()
        assert identity==(name,name+"_migrate") and conn.execute("SELECT token FROM public.analytics_test_sentinel").fetchall()==[(sentinel,)]
        assert not conn.execute("SELECT EXISTS(SELECT 1 FROM analytics.activity_projection_buckets)").fetchone()[0]
        before_bytes=conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        statement="""INSERT INTO analytics.activity_projection_buckets
            (subject_ref,bucket_start,metric_version,generation,projection_generation,subject_epoch,state,
             winner_intervals,presence_intervals,presence_points,contributions,presence_labels,
             compact_item_count,edge_item_count,has_presence)
            VALUES (%s,%s,'activity-v1',1,1,0,'ready',%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,true)"""
        pending=[]
        for bucket_index in range(31*96):
            if bucket_index%96==0: monitor.check()
            start=g.BASE+timedelta(minutes=15*bucket_index)
            stamp=int(start.timestamp())*1000
            templates=[]
            for overlap in (False,True):
                winner="backend_copy" if overlap else "backend"
                winners=[{"start_ms":stamp,"end_ms":stamp+900000,"course_id":winner,"lesson_id":"same-lesson","section":"theory"}]
                presence=[{"start_ms":stamp+i*15000,"end_ms":stamp+(i+1)*15000,"course_id":course,
                           "lesson_id":"same-lesson","section":"theory"} for i in range(60)
                          for course in (("backend","backend_copy") if overlap else ("backend",))]
                contributions=[{"course_id":winner,"lesson_id":"same-lesson","section":"theory","active_ms":900000}]
                labels=[{"course_id":course,"lesson_id":lesson} for course in (("backend","backend_copy") if overlap else ("backend",))
                        for lesson in (None,"same-lesson")]
                templates.append((*[encode(v) for v in (winners,presence,[],contributions,labels)],len(contributions)+len(labels),len(winners)+len(presence)))
            pending.extend((subject,start,*templates[i<6]) for i,subject in enumerate(subjects[:30]))
            if len(pending)>=120:
                with conn.transaction():
                    with conn.cursor() as cursor: cursor.executemany(statement,pending)
                pending.clear()
        if pending:
            with conn.transaction():
                with conn.cursor() as cursor: cursor.executemany(statement,pending)
        conn.execute("UPDATE analytics.activity_projection_backfill SET phase='complete' WHERE singleton=true AND run_id=%s",(run_id,))
        bucket_bytes=conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
        if not build_mode:
            # Synthetic already-normalized rollups: measures query only, not worker/backfill.
            conn.execute("""INSERT INTO analytics.activity_projection_days
            (subject_ref,local_day,metric_version,generation,subject_epoch,run_id,contributions,presence_labels,
             source_bucket_count,source_compact_items,source_compact_bytes)
            SELECT s.subject_ref,s.local_day,s.metric_version,s.generation,0,%s,
              jsonb_build_array(jsonb_build_object('course_id',(array_agg(b.contributions))[1]->0->>'course_id',
                 'lesson_id','same-lesson','section','theory','active_ms',count(*)*900000)),
              (array_agg(b.presence_labels))[1],count(*),sum(b.compact_item_count),sum(b.compact_json_bytes)
            FROM analytics.activity_projection_day_sources s JOIN analytics.activity_projection_buckets b
              ON b.subject_ref=s.subject_ref AND b.metric_version=s.metric_version
             AND (b.bucket_start AT TIME ZONE 'UTC'+interval '3 hours')::date=s.local_day
            GROUP BY s.subject_ref,s.local_day,s.metric_version,s.generation""",(run_id,))
            conn.execute("UPDATE analytics.activity_projection_day_sources SET state='ready'")
            conn.execute("UPDATE analytics.activity_projection_day_backfill SET phase='complete',run_id=%s WHERE singleton=true",(run_id,))
    async def run():
        reader=helpers.db(urls["query"]); captured={}; samples=[]
        query=ActivityQuery(from_time=g.BASE,to_time=g.BASE+timedelta(days=31),course_id=None,subject_ref=None)
        actor=uuid4(); report=PostgresProjectionActivityReport(CaptureDay(reader,captured),None,expected_run_id=run_id,day_projection_enabled=True)
        try:
            build=None
            if build_mode:
                worker=helpers.db(urls["processor"])
                try:
                    build=await _build_day_projection(worker,run_id,monitor)
                    build["source_buckets_per_second"]=(31*96*30)/(build["elapsed_ms"]/1000) if build["elapsed_ms"]>0 else None
                finally: await worker.close()
            async with reader.transaction() as session:
                after_bytes=(await session.execute(text("SELECT pg_database_size(current_database())"))).scalar_one()
                relation_sizes=(await session.execute(text("""SELECT
                  pg_total_relation_size('analytics.activity_projection_buckets'::regclass),
                  pg_total_relation_size('analytics.activity_projection_day_sources'::regclass),
                  pg_total_relation_size('analytics.activity_projection_days'::regclass)"""))).one()
            for _ in range(20):
                monitor.check()
                grant=VerifiedGrant(sub=actor,role="admin",scope="report:read",course_ids=(),subject_refs=(),global_read=True,expires_at=int(time.time())+60)
                started=time.monotonic(); result=await report.read_activity(grant,query); samples.append((time.monotonic()-started)*1000)
                assert result["totals"]=={"unique_visitors":30,"active_ms":80352000000,"theory_ms":80352000000,"practice_ms":0}
            async with reader.transaction() as session:
                for lock in sorted({subject_lock_key(s) for s in captured["locked_subjects"]}|{subject_lock_key(actor)}):
                    await session.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"),{"key":lock})
                assert (await session.execute(text("SELECT analytics.lock_activity_projection_run(:run)"),{"run":run_id})).scalar_one() is True
                explain=(await session.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "+_DAY_SNAPSHOT),captured)).scalar_one()
            ordered=sorted(samples)
            print(json.dumps({"dataset":"synthetic-a011-real-day-build-50subject-30active-31day-dense" if build_mode else "synthetic-a011-query-only-50subject-30active-31day-dense",
                "bucket_rows":31*96*30,"presence_intervals":36*60*96*31,"queries":len(samples),
                "p50_ms":ordered[9],"p95_ms":ordered[18],"before_database_bytes":before_bytes,
                "bucket_database_bytes":bucket_bytes,"database_bytes":after_bytes,"day_build":build,
                "projection_relation_bytes":{"buckets":relation_sizes[0],"day_sources":relation_sizes[1],"days":relation_sizes[2]},
                "warmup_queries":0,"privacy_guard":"fixture_stub","source_ingestion":"synthetic_ready_buckets",
                "memory_observation":"sampled system-free physical memory, not process or container peak",
                "resources":monitor.result(),"explain":explain},default=str))
        finally:
            await reader.close()
    asyncio.run(run())
