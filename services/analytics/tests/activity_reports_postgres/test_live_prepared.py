"""Prepared live test: requires an explicit runner and creates a disposable synthetic DB.

The stub guard here is not a live privacy authority or deletion-race test.
Never point this test at retained data; the existing bootstrap enforces its own
synthetic naming/token cleanup checks. This test is skipped by default.
"""
import asyncio
import importlib.util
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import pytest
from pymentor_analytics.adapters.postgres.activity import ActivityStorage
from pymentor_analytics.adapters.postgres.activity_reports import PostgresActivityReport
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.auth_v1 import VerifiedGrant
from pymentor_analytics.contracts.reports_v1 import ActivityQuery
from tools.bootstrap_postgres import cleanup, config, create

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("live_golden",ROOT/"tests/reference/test_activity_v1.py")
g=importlib.util.module_from_spec(spec); spec.loader.exec_module(g)

@pytest.fixture
def target():
    if os.environ.get("ANALYTICS_RUN_ACTIVITY_REPORTS_POSTGRES") != "1":
        pytest.skip("explicit disposable activity-report PostgreSQL runner required")
    data=config(ROOT/".local/postgres-connection.json")
    name,token,urls=create(data)
    try:
        upgrade(Settings(database_url=urls["migrate"]), target_revision="a006")
        yield urls
    finally:
        cleanup(data,name,token)

def test_scoped_multi_subject_attribution_in_disposable_postgres(target):
    async def run():
        writer=Database(Settings(database_url=target["collector"],db_timeout_seconds=5))
        reader=Database(Settings(database_url=target["query"],db_timeout_seconds=5))
        async def synthetic_guard(session): pass
        reader.transaction_guard=synthetic_guard
        first,second,actor=uuid4(),uuid4(),uuid4()
        end=datetime.now(timezone.utc).replace(microsecond=0)-timedelta(seconds=1)
        start=end-timedelta(seconds=15)
        facts=[g.event(start,end,number=1,subject=first,course="backend"),
               g.event(start,end,number=2,subject=first,course="backend_copy"),
               g.event(start,end,number=3,subject=second,course="backend")]
        try:
            async with writer.transaction() as session:
                for fact in facts: assert await ActivityStorage(session).insert(fact)=="inserted"
            grant=VerifiedGrant(actor,"leader","report:read",("backend",),(first,second),False,int(time.time())+50)
            query=ActivityQuery(from_time=start-timedelta(seconds=1),to_time=end+timedelta(seconds=1),course_id="backend",subject_ref=None)
            result=await PostgresActivityReport(reader,None).read_activity(grant,query)
            assert result["totals"]["unique_visitors"]==2
            assert result["totals"]["active_ms"]==15000
            assert result["quality"]=="unknown"
            # Real signed G1 verification and ASGI route against the same isolated DB.
            http_spec=importlib.util.spec_from_file_location("http_signed_fixture", ROOT/"tests/activity_reports/test_activity_http.py")
            http_mod=importlib.util.module_from_spec(http_spec); http_spec.loader.exec_module(http_mod)
            app,token=http_mod.make_app(role="user",subject=first)
            app.state.activity_reports=PostgresActivityReport(reader,None)
            params={"from_time":(start-timedelta(seconds=1)).isoformat(),
                    "to_time":(end+timedelta(seconds=1)).isoformat(),"course_id":"backend",
                    "lesson_id":"same-lesson","limit":1}
            import httpx
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
                response=await client.get("/api/analytics/reports/activity/details",params=params,
                                          headers={"Authorization":f"Bearer {token}"})
                forbidden=await client.get("/api/analytics/reports/activity/details",params={**params,"subject_ref":str(second)},
                                           headers={"Authorization":f"Bearer {token}"})
            assert response.status_code==200 and response.json()["report"]["quality"]=="unknown"
            assert response.json()["report"]["totals"]["active_ms"]==0
            assert response.json()["next_offset"]==1 and forbidden.status_code==403
        finally:
            await writer.close(); await reader.close()
    asyncio.run(run())
