import asyncio
import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import text

spec = importlib.util.spec_from_file_location("projection_phase_fixture", Path(__file__).with_name("test_projection_reports_pg.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def test_phase_instrumentation_attributes_queries_and_keeps_snapshot_arguments():
    class Session:
        async def execute(self, statement, params=None):
            return (str(statement), params)
    class Reader:
        transaction_guard = None
        @asynccontextmanager
        async def transaction(self):
            yield Session()
    async def run():
        metrics, captured = {}, {}
        async with fixture.CaptureDB(Reader(), captured, metrics).transaction() as session:
            await session.execute(text(fixture._DISCOVER), {"limit": 101})
            await session.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": 1})
            await session.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": 2})
            await session.execute(text("SELECT analytics.lock_activity_projection_run(:run_id)"), {"run_id": "synthetic"})
            await session.execute(text(fixture._SNAPSHOT), {"synthetic": True})
        assert captured == {"synthetic": True}
        assert metrics["lock_calls"] == 2
        assert all(metrics[name] >= 0 for name in ("discovery", "locks", "other_sql", "snapshot"))
    asyncio.run(run())
