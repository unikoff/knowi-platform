"""a012/a013 round trip on a disposable database with two source kinds."""
import asyncio
from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
import time

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from pymentor_analytics.adapters.postgres.activity_boundary_credit import boundary_credit_step
from pymentor_analytics.adapters.postgres.core_transfer import PostgresCoreTransfer
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.app.config import Settings
from pymentor_analytics.modules.analytics.core_transfer.history import conditional_history_days


ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


day_helpers = _load("migration_day_helpers", Path(__file__).with_name("test_day_reports_pg.py"))
core = _load("migration_core_contract", ROOT.parent / "core-api/pymentor_core/modules/analytics_delivery/contracts.py")


def _snapshot(connection):
    """Compare durable sources and all measured a012 rows, excluding credit."""
    statements = (
        "SELECT producer_namespace,event_id,subject_ref,source_kind,course_id,catalog_version,lesson_id,started_at,canonical_hash,accepted_event FROM analytics.activity_facts ORDER BY producer_namespace,event_id",
        "SELECT subject_ref,bucket_start,metric_version,generation,projection_generation,subject_epoch,state,winner_intervals,contributions,course_projection_generation,course_projection_run_id FROM analytics.activity_projection_buckets ORDER BY subject_ref,bucket_start",
        "SELECT subject_ref,local_day,metric_version,generation,subject_epoch,run_id,contributions,course_projection_generation,course_projection_run_id FROM analytics.activity_projection_days ORDER BY subject_ref,local_day",
        "SELECT subject_ref,bucket_start,metric_version,course_id,generation,subject_epoch,run_id,winner_intervals,contributions,theory_ms,practice_ms,total_ms FROM analytics.activity_course_buckets ORDER BY subject_ref,bucket_start,course_id",
        "SELECT subject_ref,local_day,metric_version,course_id,generation,subject_epoch,run_id,theory_ms,practice_ms,total_ms FROM analytics.activity_course_days ORDER BY subject_ref,local_day,course_id",
    )
    return tuple(connection.execute(text(statement)).all() for statement in statements)


def test_a012_a013_round_trip_preserves_sources_and_measured_projections(activity_day_projection_target):
    _, _, urls, run_id, _ = activity_day_projection_target
    upgrade(Settings(database_url=urls["migrate"]), target_revision="a012")
    historical_day = date(2025, 12, 30)
    historical = core.activity(day_helpers.g.SUBJECT, historical_day,
                               datetime(2025, 12, 30, 12, tzinfo=timezone.utc), revision=0)

    async def seed():
        await day_helpers._seed(urls, run_id)
        collector = Database(Settings(database_url=urls["collector"], db_timeout_seconds=5))
        try:
            result = await PostgresCoreTransfer(collector).accept(
                "core", historical, core.fingerprint(historical), deadline=time.monotonic() + 5)
            assert result == "accepted"
        finally:
            await collector.close()

    async def drain_credit():
        database = Database(Settings(database_url=urls["processor"], db_timeout_seconds=5))
        try:
            for _ in range(100):
                if await boundary_credit_step(database, run_id=run_id, deadline=time.monotonic() + 5) == "idle":
                    return
            raise AssertionError("credit materialization did not drain")
        finally:
            await database.close()

    asyncio.run(seed())
    engine = create_engine(urls["migrate"].replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.begin() as connection:
            before = _snapshot(connection)
            assert {row.source_kind for row in before[0]} == {"browser_v2", "core_transfer_v1"}
            assert before[1] and before[2] and before[3] and before[4]
            assert all(row.local_day != historical_day for row in before[4])
            legacy = next(row for row in before[0] if row.source_kind == "core_transfer_v1")
            assert legacy.course_id is None and legacy.lesson_id is None
            assert legacy.accepted_event["payload"]["provenance"] == "legacy_global"
            assert conditional_history_days([legacy.accepted_event["payload"]["activity_date"]]) == [{
                "date": historical_day.isoformat(), "theory_ms": 3_600_000, "practice_ms": 3_600_000,
                "provenance": "core_legacy_conditional"}]

        for _ in range(2):
            upgrade(Settings(database_url=urls["migrate"]), target_revision="a013")
            asyncio.run(drain_credit())
            with engine.begin() as connection:
                assert _snapshot(connection) == before
                assert connection.execute(text("SELECT count(*) FROM analytics.activity_credit_windows WHERE state='ready'")).scalar_one() > 0
                assert connection.execute(text("SELECT count(*) FROM analytics.activity_credit_days WHERE state='ready'")).scalar_one() > 0
                assert connection.execute(text("SELECT count(*) FROM analytics.activity_credit_days WHERE local_day=:day"), {"day": historical_day}).scalar_one() == 0
                config = Config(str(ROOT / "alembic.ini"))
                config.attributes["connection"] = connection
                command.downgrade(config, "a012")
                assert connection.execute(text("SELECT to_regclass('analytics.activity_credit_windows')")).scalar_one() is None
                assert connection.execute(text("SELECT to_regclass('analytics.activity_credit_days')")).scalar_one() is None
                assert _snapshot(connection) == before
    finally:
        engine.dispose()
