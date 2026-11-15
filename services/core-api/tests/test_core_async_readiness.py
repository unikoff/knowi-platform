"""PostgreSQL runtime readiness checks; configure the isolated lab before pytest."""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import event

from pymentor_core.app.asgi import create_app
from pymentor_core.app.database import engine


def test_postgres_startup_and_readiness_dependency_failure_recovery():
    observed = []

    def record_sync(*args):
        observed.append(1)

    event.listen(engine, "before_cursor_execute", record_sync)
    app = create_app()
    try:
        with TestClient(app) as client:
            database = app.state.local_async_identity.database
            assert client.get("/health/ready").json() == {"status": "ready"}
            with patch("pymentor_core.app.factory.compatible_schema_revision", return_value=False):
                assert client.get("/health/ready").status_code == 503
            with patch.object(app.state.local_async_identity.redis, "ping", new=AsyncMock(return_value=False)):
                assert client.get("/health/ready").status_code == 503
            assert client.get("/health/ready").status_code == 200
            assert database.engine.pool.checkedout() == 0
            # The current deployment retains synchronous control-plane probes;
            # they run in the health route's worker thread, never as business SQL.
            assert observed
    finally:
        event.remove(engine, "before_cursor_execute", record_sync)


def test_real_postgres_startup_releases_resources_if_redis_fails(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import pytest
    import pymentor_core.adapters.runtime as runtime
    from pymentor_core.app.wiring import postgres_resources

    settings = create_app().state.settings
    closed = []
    real_database = runtime.RuntimeAsyncDatabase

    class TrackedDatabase(real_database):
        async def dispose(self):
            await super().dispose()
            closed.append("database")

    class FailingStore:
        def __init__(self, *args, **kwargs):
            pass

        async def ping(self):
            raise OSError("synthetic Redis unavailable")

        async def close(self):
            closed.append("redis")

    monkeypatch.setattr(runtime, "RuntimeAsyncDatabase", TrackedDatabase)
    monkeypatch.setattr(runtime, "RuntimeRedisSessionStore", FailingStore)
    app = SimpleNamespace(state=SimpleNamespace())

    async def run():
        async with postgres_resources(app, settings):
            pytest.fail("startup should fail")

    with pytest.raises(OSError, match="synthetic Redis"):
        asyncio.run(run())
    assert closed == ["redis", "database"]
    assert getattr(app.state, "local_async_identity", None) is None
