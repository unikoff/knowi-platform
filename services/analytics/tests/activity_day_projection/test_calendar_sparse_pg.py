"""Sparse calendar uses metadata for reused days on a disposable PostgreSQL DB."""
import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from pymentor_analytics.adapters.postgres.activity import ActivityStorage
from pymentor_analytics.adapters.postgres.activity_calendar import read_calendar
from pymentor_analytics.adapters.postgres.activity_course_projection import backfill_course_projection_step
from pymentor_analytics.adapters.postgres.activity_day_projection import PostgresActivityDayProjection
from pymentor_analytics.adapters.postgres.activity_projection import PostgresActivityProjection
from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport, ProjectionPending
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.auth_v1 import VerifiedGrant
from pymentor_analytics.contracts.reports_v1 import ActivityQuery
from pymentor_analytics.modules.analytics.api.activity_router import CalendarRevision
from test_day_reports_pg import _drain_day, g, helpers


@pytest.mark.parametrize("revision", ["a011", "a014"])
def test_reused_history_never_reaches_payload_and_empty_day_is_reused(activity_day_projection_target, revision):
    _, _, urls, run_id, _ = activity_day_projection_target
    if revision == "a014":
        upgrade(Settings(database_url=urls["migrate"]), target_revision="a014")

    async def run():
        writer, worker, reader, migrate = (helpers.db(urls[role]) for role in ("collector", "processor", "query", "migrate"))
        statements = []
        original_transaction = reader.transaction

        @asynccontextmanager
        async def traced():
            async with original_transaction() as session:
                class Proxy:
                    async def execute(self, statement, params=None):
                        statements.append((str(statement), params))
                        return await session.execute(statement, params)
                yield Proxy()

        reader.transaction = traced
        store = PostgresProjectionActivityReport(reader, None, expected_run_id=run_id, day_projection_enabled=True)
        grant = VerifiedGrant(g.SUBJECT, "user", "report:read", ("backend",), (), False, int(time.time())+120)
        today = (datetime.now(timezone.utc)+timedelta(hours=3)).date()
        start = datetime.combine(today-timedelta(days=29), datetime.min.time(), timezone.utc)-timedelta(hours=3)
        query = ActivityQuery(from_time=start, to_time=start+timedelta(days=30), course_id="backend", subject_ref=None)
        event_start = start+timedelta(days=24, hours=12)
        try:
            async with writer.transaction() as session:
                await ActivityStorage(session).insert(g.event(event_start,event_start+timedelta(seconds=10)))
            await helpers.drain(PostgresActivityProjection(worker, expected_run_id=run_id))
            await _drain_day(PostgresActivityDayProjection(worker, expected_run_id=run_id))
            cold = await read_calendar(store, grant, query, None, [])
            assert len(cold["revisions"]) == 28 and len(cold["daily"]) == 1
            assert all(item["token"] for item in cold["revisions"])
            known = [CalendarRevision.model_validate(item) for item in cold["revisions"]]
            statements.clear()
            warm = await read_calendar(store, grant, query, cold["history_generation"], known)
            assert len(warm["reused_dates"]) == 28 and warm["daily"] == []
            payload = [(sql, params) for sql, params in statements if params and "fetch_days" in params]
            assert len(payload) == 1
            assert payload[0][1]["fetch_days"] == [today-timedelta(days=1), today]
            assert "s.local_day=ANY(CAST(:fetch_days AS date[]))" in payload[0][0]
            assert all(day not in payload[0][1]["fetch_days"] for day in (today-timedelta(days=5), today-timedelta(days=6)))
            registry = next(sql for sql, _ in statements if "SELECT m.*, days.day, s.generation, s.state" in sql)
            assert "activity_projection_days" not in registry
            assert "contributions" not in registry and "presence_labels" not in registry
            historical = query.model_copy(update={"to_time": start+timedelta(days=28)})
            statements.clear()
            historical_warm = await read_calendar(store, grant, historical, cold["history_generation"], known)
            assert len(historical_warm["reused_dates"]) == 28 and historical_warm["daily"] == []
            assert historical_warm["as_of"] is not None and historical_warm["as_of"].microsecond % 1000 == 0
            assert not any(params and "fetch_days" in params for _, params in statements)
            statements.clear()
            reset = await read_calendar(store, grant, historical, "0"*64, known)
            assert reset["reused_dates"] == [] and len(reset["daily"]) == 1
            assert len(next(params["fetch_days"] for _, params in statements if params and "fetch_days" in params)) == 28
            new_start = start+timedelta(days=23, hours=12)
            async with writer.transaction() as session:
                await ActivityStorage(session).insert(g.event(new_start,new_start+timedelta(seconds=10), number=2))
            with pytest.raises(ProjectionPending):
                await read_calendar(store, grant, query, cold["history_generation"], known)
            await helpers.drain(PostgresActivityProjection(worker, expected_run_id=run_id))
            await _drain_day(PostgresActivityDayProjection(worker, expected_run_id=run_id))
            statements.clear()
            changed = await read_calendar(store, grant, query, cold["history_generation"], known)
            new_day = new_start.astimezone(timezone.utc).date().isoformat()
            assert new_day not in changed["reused_dates"]
            assert len(changed["reused_dates"]) == 27 and len(changed["daily"]) == 1
            assert changed["daily"][0]["active_ms"] == 10000
            payload = [params for _, params in statements if params and "fetch_days" in params]
            assert payload[0]["fetch_days"] == [new_start.date(), today-timedelta(days=1), today]
        finally:
            for db in (writer, worker, reader, migrate): await db.close()

    asyncio.run(run())


def test_a014_fast_course_cold_warm_matches_get_with_zero_time_presence(activity_day_projection_target):
    _, _, urls, run_id, _ = activity_day_projection_target
    upgrade(Settings(database_url=urls["migrate"]), target_revision="a014")

    async def run():
        writer, worker, reader, migrate = (helpers.db(urls[role]) for role in ("collector", "processor", "query", "migrate"))
        captured = []
        original_transaction = reader.transaction

        @asynccontextmanager
        async def traced():
            async with original_transaction() as session:
                class Proxy:
                    async def execute(self, statement, params=None):
                        captured.append((str(statement), params))
                        return await session.execute(statement, params)
                yield Proxy()

        reader.transaction = traced
        store = PostgresProjectionActivityReport(reader, None, expected_run_id=run_id,
                                                 day_projection_enabled=True, course_projection_enabled=True)
        grant = VerifiedGrant(g.SUBJECT, "user", "report:read", ("backend",), (), False, int(time.time())+120)
        today = (datetime.now(timezone.utc)+timedelta(hours=3)).date()
        start = datetime.combine(today-timedelta(days=29), datetime.min.time(), timezone.utc)-timedelta(hours=3)
        query = ActivityQuery(from_time=start, to_time=start+timedelta(days=30), course_id="backend", subject_ref=None)
        active = start + timedelta(days=22, hours=12)
        visit = start + timedelta(days=23, hours=12)
        try:
            async with writer.transaction() as session:
                await ActivityStorage(session).insert(g.event(active, active+timedelta(seconds=10), number=21))
                await ActivityStorage(session).insert(g.event(visit, visit, number=22, opened=True))
            await helpers.drain(PostgresActivityProjection(worker, expected_run_id=run_id))
            await _drain_day(PostgresActivityDayProjection(worker, expected_run_id=run_id))
            for _ in range(24):
                if await backfill_course_projection_step(worker, run_id=run_id, deadline=time.monotonic()+5) == "complete":
                    break
            else:
                raise AssertionError("course backfill incomplete")
            expected = await store.read_activity(grant, query)
            captured.clear()
            cold = await read_calendar(store, grant, query, None, [])
            assert cold["daily"] == expected["daily"]
            assert len(cold["daily"]) == 2
            assert any(row["active_ms"] == 0 and row["unique_visitors"] == 1 for row in cold["daily"])
            assert any(row["active_ms"] == 10000 and row["theory_ms"] == 10000 for row in cold["daily"])
            payload_sql = [sql for sql, params in captured if params and "fetch_days" in params]
            assert len(payload_sql) == 1 and "activity_course_days" in payload_sql[0]
            known = [CalendarRevision.model_validate(item) for item in cold["revisions"]]
            captured.clear()
            warm = await read_calendar(store, grant, query, cold["history_generation"], known)
            retained = {row["date"]: row for row in cold["daily"] if row["date"].isoformat() in warm["reused_dates"]}
            merged = sorted([*retained.values(), *warm["daily"]], key=lambda row: row["date"])
            assert merged == expected["daily"]
            assert warm["as_of"] is not None and warm["as_of"].microsecond % 1000 == 0
            assert len(warm["reused_dates"]) == 28
            payload = [params for _, params in captured if params and "fetch_days" in params]
            assert payload and all(params["fetch_days"] == [today-timedelta(days=1), today] for params in payload)
        finally:
            for db in (writer, worker, reader, migrate): await db.close()

    asyncio.run(run())
