"""Actual custom-format restore into a separate sentinel-guarded synthetic database."""
import asyncio
import os
import subprocess
import time
from datetime import datetime, time as midnight, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit, unquote
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
import httpx
from fastapi import FastAPI
from psycopg import sql

from pymentor_analytics.adapters.postgres.activity import ActivityStorage
from pymentor_analytics.adapters.postgres.activity_projection import PostgresActivityProjection
from pymentor_analytics.adapters.postgres.activity_day_projection import PostgresActivityDayProjection
from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.auth_v1 import VerifiedGrant
from pymentor_analytics.contracts.reports_v1 import ActivityQuery
from pymentor_analytics.modules.analytics.api.activity_router import router
from tools.bootstrap_postgres import cleanup, connect, create
from test_day_reports_pg import _drain_day, g, helpers


CONTAINER = "knowi-analytics-01a1df75-analytics-postgres-1"


def _docker_pg(tool, database, username, password, options, *, content=None):
    # Password is passed via the inherited environment, never as a command argument.
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    return subprocess.run(["docker", "exec", "-i", "--env", "PGPASSWORD", CONTAINER, tool,
                           "--host", "127.0.0.1", "--username", username,
                           "--dbname", database, *options], input=content, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True, env=env, timeout=120).stdout


def _table_digests(connection):
    tables = connection.execute("""SELECT schemaname, tablename FROM pg_tables
        WHERE schemaname IN ('analytics','public')
        AND NOT (schemaname='public' AND tablename='analytics_test_sentinel')
        ORDER BY 1,2""").fetchall()
    result = {}
    for schema, table in tables:
        statement = sql.SQL("SELECT count(*), md5(COALESCE(string_agg(row_to_json(t)::text, E'\\n' ORDER BY row_to_json(t)::text), '')) FROM {}.{} t").format(sql.Identifier(schema), sql.Identifier(table))
        result[(schema, table)] = connection.execute(statement).fetchone()
    return result


@pytest.mark.skipif(os.environ.get("ANALYTICS_RUN_FULL_RESTORE_SYNTHETIC") != "1",
                    reason="explicit full synthetic PostgreSQL restore opt-in required")
def test_a014_full_archive_restore_changes_history_revision(activity_day_projection_target):
    data, source_name, urls, run_id, source_sentinel = activity_day_projection_target
    upgrade(Settings(database_url=urls["migrate"]), target_revision="a014")
    target_name, target_sentinel, target_urls = create(data, revision="a014")
    try:
        async def seed_and_read(url, *, seed, known=None):
            db = helpers.db(url)
            moscow = ZoneInfo("Europe/Moscow")
            today = datetime.now(moscow).date()
            start = datetime.combine(today - timedelta(days=29), midnight.min, moscow)
            end = datetime.combine(today + timedelta(days=1), midnight.min, moscow)
            grant = VerifiedGrant(g.SUBJECT, "user", "report:read", ("backend",), (), False, int(time.time()) + 120)
            query = ActivityQuery(from_time=start.astimezone(timezone.utc), to_time=end.astimezone(timezone.utc),
                                  course_id="backend", subject_ref=None)
            try:
                if seed:
                    worker = helpers.db(urls["processor"])
                    writer = helpers.db(urls["collector"])
                    try:
                        at = (start + timedelta(days=20, hours=12)).astimezone(timezone.utc)
                        async with writer.transaction() as session:
                            assert await ActivityStorage(session).insert(g.event(at, at + timedelta(seconds=10))) == "inserted"
                        await helpers.drain(PostgresActivityProjection(worker, expected_run_id=run_id))
                        await _drain_day(PostgresActivityDayProjection(worker, expected_run_id=run_id))
                    finally:
                        await writer.close()
                        await worker.close()
                store = PostgresProjectionActivityReport(db, None, expected_run_id=run_id, day_projection_enabled=True)
                app = FastAPI()
                app.include_router(router)
                app.state.trust_manifest_validated = True
                app.state.grant_verifier = SimpleNamespace(verify=lambda _: grant)
                app.state.probe = SimpleNamespace(is_ready=lambda: asyncio.sleep(0, result=True))
                app.state.settings = SimpleNamespace(db_timeout_seconds=10)
                app.state.activity_reports = store
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    response = await client.get("/api/analytics/reports/activity", headers={"Authorization": "Bearer fixture"},
                                                params={"from_time": query.from_time.isoformat(), "to_time": query.to_time.isoformat(),
                                                        "course_id": "backend", "history_revisions": "true"})
                    assert response.status_code == 200
                    assert response.headers["cache-control"] == "no-store"
                    body = {"course_id": "backend", "from_time": query.from_time.isoformat(),
                            "to_time": query.to_time.isoformat(), "known_generation": None, "known_revisions": []}
                    async def calendar(payload):
                        result = await client.post("/api/analytics/reports/activity/calendar",
                                                   headers={"Authorization": "Bearer fixture"}, json=payload)
                        assert result.status_code == 200
                        assert result.headers["cache-control"] == "no-store"
                        return result.json()
                    if known is None:
                        cold = await calendar(body)
                        held = {"known_generation": cold["history_generation"], "known_revisions": cold["revisions"]}
                        warm = await calendar({**body, **held})
                        assert len(warm["reused_dates"]) == 28 and warm["daily"] == []
                        assert warm["history_generation"] == cold["history_generation"]
                        return response.json(), cold, held
                    return response.json(), await calendar({**body, **known})
            finally:
                await db.close()

        before, cold_calendar, held = asyncio.run(seed_and_read(urls["migrate"], seed=True))
        assert cold_calendar["daily"] and cold_calendar["reused_dates"] == []
        with connect(data, source_name) as source, connect(data, target_name) as target:
            for conn, name, sentinel in ((source, source_name, source_sentinel), (target, target_name, target_sentinel)):
                assert conn.execute("SELECT current_database(),pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone() == (name, name + "_migrate")
                assert conn.execute("SELECT token FROM public.analytics_test_sentinel").fetchall() == [(sentinel,)]
            assert target.execute("SELECT to_regnamespace('analytics')").fetchone() == (None,)
            original = _table_digests(source)
            assert original[("analytics", "activity_facts")][0] > 0
            assert source.execute("SELECT run_id FROM analytics.activity_projection_backfill WHERE singleton").fetchone() == (run_id,)

        archive = _docker_pg("pg_dump", source_name, data["user"], data["password"],
                             ["--format=custom", "--no-owner", "--no-acl", "--exclude-table=public.analytics_test_sentinel"])
        assert archive.startswith(b"PGDMP")
        parsed = urlsplit(target_urls["migrate"])
        _docker_pg("pg_restore", target_name, parsed.username, unquote(parsed.password),
                   ["--exit-on-error", "--no-owner", "--no-acl"], content=archive)
        with connect(data, target_name) as target:
            assert target.execute("SELECT token FROM public.analytics_test_sentinel").fetchall() == [(target_sentinel,)]
            assert target.execute("SELECT version_num FROM public.alembic_version").fetchone() == ("a014",)
            assert target.execute("SELECT run_id FROM analytics.activity_projection_backfill WHERE singleton").fetchone() == (run_id,)
            assert _table_digests(target) == original
        after, restored_calendar = asyncio.run(seed_and_read(target_urls["migrate"], seed=False, known=held))
        assert before["daily"] == after["daily"]
        old = {row["date"]: row["token"] for row in before["history_revisions"] if row["token"]}
        new = {row["date"]: row["token"] for row in after["history_revisions"] if row["token"]}
        assert old and old.keys() == new.keys()
        assert all(old[day] != new[day] for day in old)
        assert before["history_generation"] and after["history_generation"]
        assert before["history_generation"] != after["history_generation"]
        assert restored_calendar["history_generation"] != cold_calendar["history_generation"]
        assert restored_calendar["reused_dates"] == []
        assert restored_calendar["daily"] == cold_calendar["daily"]
        assert len(restored_calendar["revisions"]) == len(cold_calendar["revisions"]) == 28
        assert all(a["date"] == b["date"] and a["token"] != b["token"]
                   for a, b in zip(cold_calendar["revisions"], restored_calendar["revisions"]))
    finally:
        cleanup(data, target_name, target_sentinel, revision="a014")
        with connect(data) as conn:
            assert conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (target_name,)).fetchone() is None
            roles = [target_name + "_" + role for role in ("migrate", "collector", "processor", "query", "privacy", "transfer_status")]
            assert conn.execute("SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (roles,)).fetchone() is None
        print(f"a014-full-restore-target={target_name} cleaned=yes")
