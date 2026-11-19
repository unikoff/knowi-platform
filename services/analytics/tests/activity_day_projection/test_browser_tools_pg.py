"""Verify tool facts stay outside study projections on real PostgreSQL."""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import text

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.intake import PostgresIntake
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.v2 import AcceptedEvent, BrowserEvent, ToolPayload

from test_day_reports_pg import g


def test_a014_stores_tool_once_without_scheduling_work(activity_day_projection_target):
    _, _, urls, _, _ = activity_day_projection_target
    migrate = Settings(database_url=urls["migrate"])
    started = datetime.now(timezone.utc).replace(microsecond=0)
    ended = started + timedelta(seconds=2)
    legacy = g.event(started, ended, subject=uuid4())
    tool = AcceptedEvent(
        producer_namespace="browser", subject_ref=uuid4(), source="browser", received_at=ended,
        event=BrowserEvent(
            schema_version=2, event_id=uuid4(), type="tool_segment", course_id="platform-tools",
            catalog_version="platform-tools-v1", lesson_id=None, section=None, session_id=uuid4(),
            started_at=started, ended_at=ended, last_action_at=started,
            visible=True, focused=True, active_ms=2000, payload=ToolPayload(tool_id="catalog"),
        ),
    )

    async def run():
        collector = Database(Settings(database_url=urls["collector"], db_timeout_seconds=5))
        observer = Database(migrate)
        intake = PostgresIntake(collector)
        try:
            assert await intake.accept(legacy, deadline=time.monotonic() + 5) == "accepted"
            upgrade(migrate, target_revision="a014")
            assert await intake.accept(legacy, deadline=time.monotonic() + 5) == "duplicate"
            assert await intake.accept(tool, deadline=time.monotonic() + 5) == "accepted"
            assert await intake.accept(tool, deadline=time.monotonic() + 5) == "duplicate"
            async with observer.transaction() as session:
                facts = (await session.execute(text("""
                    SELECT source_kind, course_id, lesson_id, accepted_event->'event' AS event
                    FROM analytics.activity_facts WHERE subject_ref=:subject
                """), {"subject": tool.subject_ref})).one()
                assert (facts.source_kind, facts.course_id, facts.lesson_id) == (
                    "browser_tool_v1", "platform-tools", None,
                )
                assert facts.event == tool.event.model_dump(mode="json")
                for table in ("processing_jobs", "activity_projection_buckets"):
                    subjects = (await session.execute(text(f"""
                        SELECT DISTINCT subject_ref FROM analytics.{table}
                    """))).scalars().all()
                    assert subjects == [legacy.subject_ref]
        finally:
            await collector.close()
            await observer.close()

    asyncio.run(run())
