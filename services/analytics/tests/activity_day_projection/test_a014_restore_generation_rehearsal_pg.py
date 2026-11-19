"""Disposable marker-copy and generation-rotation rehearsal, not a restore test."""
import asyncio
import time
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from pymentor_analytics.adapters.postgres.activity import ActivityStorage
from pymentor_analytics.adapters.postgres.activity_projection import PostgresActivityProjection
from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport, ProjectionUnavailable
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.auth_v1 import VerifiedGrant
from pymentor_analytics.contracts.reports_v1 import ActivityQuery
from test_day_reports_pg import g, helpers


def test_a014_copied_marker_and_explicit_generation_rotation(activity_day_projection_target):
    _, _, urls, old_run, _ = activity_day_projection_target
    upgrade(Settings(database_url=urls["migrate"]), target_revision="a014")
    new_run = uuid4()

    async def run():
        writer, worker, reader, migrate = (helpers.db(urls[role]) for role in
                                           ("collector", "processor", "query", "migrate"))
        original = PostgresProjectionActivityReport(reader, None, expected_run_id=old_run)
        replacement = PostgresProjectionActivityReport(reader, None, expected_run_id=new_run)
        grant = VerifiedGrant(g.SUBJECT, "user", "report:read", ("backend",), (), False, int(time.time()) + 120)
        start = g.BASE + timedelta(hours=21)
        query = ActivityQuery(from_time=start, to_time=start + timedelta(days=1), course_id="backend", subject_ref=None)
        try:
            async with writer.transaction() as session:
                assert await ActivityStorage(session).insert(g.event(start, start + timedelta(seconds=10))) == "inserted"
            await helpers.drain(PostgresActivityProjection(worker, expected_run_id=old_run))
            before = await original.read_activity(grant, query)
            assert before["totals"]["active_ms"] == 10000
            async with migrate.transaction() as session:
                # Transaction-local row copy models marker copying only; it is not pg_restore.
                await session.execute(text("CREATE TEMP TABLE copied_marker ON COMMIT DROP AS SELECT run_id, phase FROM analytics.activity_projection_backfill WHERE singleton=true"))
                copied = (await session.execute(text("SELECT run_id, phase FROM copied_marker"))).one()
                assert (copied.run_id, copied.phase) == (old_run, "complete")
                await session.execute(text("SELECT analytics.begin_activity_projection_backfill(:old,:new)"), {"old": old_run, "new": new_run})
            with pytest.raises(ProjectionUnavailable):
                await original.read_activity(grant, query)
            with pytest.raises(ProjectionUnavailable):
                await replacement.read_activity(grant, query)
            await helpers.drain(PostgresActivityProjection(worker, expected_run_id=new_run))
            with pytest.raises(ProjectionUnavailable):
                await original.read_activity(grant, query)
            after = await replacement.read_activity(grant, query)
            assert after["totals"] == before["totals"]
            assert after["daily"] == before["daily"]
        finally:
            for database in (writer, worker, reader, migrate):
                await database.close()

    asyncio.run(run())
