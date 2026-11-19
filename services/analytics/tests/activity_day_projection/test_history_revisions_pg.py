"""Revision values come from the same guarded PostgreSQL report snapshot."""
import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from pymentor_analytics.adapters.postgres.activity import ActivityStorage
from pymentor_analytics.adapters.postgres.activity_projection import PostgresActivityProjection
from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport, ProjectionPending
from pymentor_analytics.contracts.auth_v1 import VerifiedGrant
from pymentor_analytics.contracts.reports_v1 import ActivityQuery
from test_day_reports_pg import g, helpers


def test_history_revisions_change_after_dirty_day_rebuild(activity_day_projection_target):
    _, _, urls, run_id, _ = activity_day_projection_target

    async def run():
        writer, worker, reader = (helpers.db(urls[role]) for role in ("collector", "processor", "query"))
        report = PostgresProjectionActivityReport(reader, None, expected_run_id=run_id)
        grant = VerifiedGrant(g.SUBJECT, "user", "report:read", ("backend",), (), False, int(time.time())+120)
        today = (datetime.now(timezone.utc) + timedelta(hours=3)).date()
        start = datetime.combine(today-timedelta(days=29), datetime.min.time(), timezone.utc)-timedelta(hours=3)
        query = ActivityQuery(from_time=start, to_time=start+timedelta(days=30), course_id="backend", subject_ref=None)
        event_start = start + timedelta(days=24, hours=12)
        try:
            async with writer.transaction() as session:
                assert await ActivityStorage(session).insert(g.event(event_start, event_start+timedelta(seconds=10), number=1)) == "inserted"
            await helpers.drain(PostgresActivityProjection(worker, expected_run_id=run_id))
            first = await report.read_activity(grant, query, include_history_revisions=True)
            assert len(first["history_generation"]) == 64
            revisions = first["history_revisions"]
            assert len(revisions) == 28
            assert sum(row["token"] is not None for row in revisions) == 1
            previous = next(row["token"] for row in revisions if row["token"])
            assert len(previous) == 64
            async with writer.transaction() as session:
                assert await ActivityStorage(session).insert(g.event(event_start+timedelta(seconds=20), event_start+timedelta(seconds=30), number=2)) == "inserted"
            with pytest.raises(ProjectionPending):
                await report.read_activity(grant, query, include_history_revisions=True)
            await helpers.drain(PostgresActivityProjection(worker, expected_run_id=run_id))
            corrected = await report.read_activity(grant, query, include_history_revisions=True)
            assert corrected["history_generation"] == first["history_generation"]
            assert corrected["totals"]["active_ms"] == 20000
            assert next(row["token"] for row in corrected["history_revisions"] if row["token"]) != previous
        finally:
            for database in (writer, worker, reader):
                await database.close()

    asyncio.run(run())
