"""Course snapshot regression on the ordinary a011 worker and a012 backfill."""

import asyncio
import time
from datetime import timedelta

import pytest
from sqlalchemy import text

from pymentor_analytics.adapters.postgres.activity import ActivityStorage
from pymentor_analytics.adapters.postgres.activity_course_projection import backfill_course_projection_step
from pymentor_analytics.adapters.postgres.activity_course_reports import _COURSE_SNAPSHOT
from pymentor_analytics.adapters.postgres.activity_day_projection import PostgresActivityDayProjection
from pymentor_analytics.adapters.postgres.activity_projection import PostgresActivityProjection
from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport, ProjectionPending, ProjectionUnavailable
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.auth_v1 import AuthorizationError, VerifiedGrant
from pymentor_analytics.contracts.reports_v1 import ActivityQuery

from test_day_reports_pg import _drain_day, _grant, _seed, g, helpers


@pytest.mark.parametrize("upgrade_first", [False, True])
def test_course_reader_parity_acl_rollback_and_stale_source(activity_day_projection_target, monkeypatch, upgrade_first):
    _, _, urls, run_id, _ = activity_day_projection_target

    async def run():
        if upgrade_first:
            upgrade(Settings(database_url=urls["migrate"]), target_revision="a012")
        start, _ = await _seed(urls, run_id)
        if not upgrade_first:
            upgrade(Settings(database_url=urls["migrate"]), target_revision="a012")
        reader = helpers.db(urls["query"])
        worker = helpers.db(urls["processor"])
        legacy = PostgresProjectionActivityReport(reader, None, expected_run_id=run_id, day_projection_enabled=True)
        fast = PostgresProjectionActivityReport(reader, None, expected_run_id=run_id,
                                                day_projection_enabled=True, course_projection_enabled=True)
        admin = _grant()
        leader = VerifiedGrant(sub=admin.sub, role="leader", scope="report:read", course_ids=("backend",),
                               subject_refs=(g.SUBJECT, g.OTHER), global_read=False, expires_at=admin.expires_at)
        intervals = [(start, start + timedelta(days=1)),
                     (start - timedelta(seconds=4), start + timedelta(seconds=2)),
                     (start - timedelta(minutes=15), start + timedelta(days=1, seconds=10))]
        cases = [(admin, ActivityQuery(from_time=lo, to_time=hi, course_id=course, subject_ref=None))
                 for lo, hi in intervals for course in (None, "backend", "backend_copy")]
        cases.append((leader, cases[1][1]))

        def values(row):
            return row["totals"], row["daily"], row["quality"]

        try:
            expected = [values(await legacy.read_activity(actor, query)) for actor, query in cases]
            assert values(await fast.read_activity(*cases[0])) == expected[0]  # older projections still readable
            for _ in range(24):
                if await backfill_course_projection_step(worker, run_id=run_id, deadline=time.monotonic() + 5) == "complete":
                    break
            else:
                pytest.fail("course materialization did not finish")
            assert "activity_course_days" in _COURSE_SNAPSHOT
            with monkeypatch.context() as patch:
                from pymentor_analytics.adapters.postgres import activity_day_reports
                # Make any unplanned fallback observable rather than calling it parity.
                patch.setattr(activity_day_reports, "_DAY_SNAPSHOT", "SELECT 'unexpected fallback' AS error")
                assert [values(await fast.read_activity(actor, query)) for actor, query in cases] == expected
                with pytest.raises(AuthorizationError):
                    await fast.read_activity(leader, cases[2][1])
            async with worker.transaction() as session:
                await session.execute(text("UPDATE analytics.activity_projection_day_sources SET state='dirty'"))
            with pytest.raises(ProjectionUnavailable):
                await fast.read_activity(*cases[0])
        finally:
            await reader.close()
            await worker.close()

    asyncio.run(run())


@pytest.mark.parametrize("full_day", [False, True])
def test_report_waits_for_late_fact_without_holding_worker_locks(activity_day_projection_target, monkeypatch, full_day):
    _, _, urls, run_id, _ = activity_day_projection_target

    async def run():
        upgrade(Settings(database_url=urls["migrate"]), target_revision="a012")
        start, _ = await _seed(urls, run_id)
        reader, collector, worker = (helpers.db(urls[role]) for role in ("query", "collector", "processor"))
        store = PostgresProjectionActivityReport(reader, None, expected_run_id=run_id, day_projection_enabled=True,
                                                 course_projection_enabled=True, pending_wait_seconds=2)
        grant = _grant()
        event_start = start + timedelta(minutes=1)
        to_time = start + (timedelta(days=1) if full_day else timedelta(minutes=15))
        query = ActivityQuery(from_time=start, to_time=to_time, course_id=None, subject_ref=None)
        pending = asyncio.Event()
        attempt = store._read_snapshot

        async def observe(*args, **kwargs):
            try:
                return await attempt(*args, **kwargs)
            except ProjectionPending:
                pending.set()
                raise

        monkeypatch.setattr(store, "_read_snapshot", observe)
        task = None
        try:
            before = await store.read_activity(grant, query)
            event = g.event(event_start, event_start + timedelta(seconds=60), number=912)
            async with collector.transaction() as session:
                assert await ActivityStorage(session).insert(event) == "inserted"
            task = asyncio.create_task(store.read_activity(grant, query))
            await asyncio.wait_for(pending.wait(), 2)
            assert not task.done()
            await helpers.drain(PostgresActivityProjection(worker, expected_run_id=run_id))
            if full_day:
                await _drain_day(PostgresActivityDayProjection(worker, expected_run_id=run_id))
            after = await task
            assert after["totals"]["active_ms"] == before["totals"]["active_ms"] + 60_000
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await reader.close()
            await collector.close()
            await worker.close()

    asyncio.run(run())
