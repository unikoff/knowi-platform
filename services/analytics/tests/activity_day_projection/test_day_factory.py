import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.factory import create_app

spec = importlib.util.spec_from_file_location("core_factory_helpers", Path(__file__).parents[1] / "core_transfer/test_core_factory.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


@pytest.fixture
def env(monkeypatch):
    import pymentor_analytics.app.factory as factory
    import pymentor_analytics.app.privacy as privacy
    import pymentor_analytics.app.activity_projection_runtime as bucket
    import pymentor_analytics.app.activity_day_projection_runtime as day
    import pymentor_analytics.app.processor as processor

    class Result:
        def __init__(self, value): self.value = value
        def one(self): return self.value
        def scalar_one(self): return self.value
        def scalar_one_or_none(self): return self.value
    class Session:
        def __init__(self, role): self.role = role
        async def execute(self, statement, params=None):
            sql = str(statement)
            State.statements.append((self.role, sql))
            if "SELECT 1 FROM analytics.activity_facts LIMIT 0" in sql and not State.capability:
                raise PermissionError("source access unavailable")
            if "to_regclass('analytics.activity_projection_buckets')" in sql: return Result((State.bucket,) * 8)
            if "to_regclass('analytics.activity_projection_day_sources')" in sql: return Result(State.day_schema)
            if "to_regclass('analytics.activity_course_buckets')" in sql: return Result((State.course, State.course))
            if "to_regclass('analytics.core_snapshot_proofs')" in sql: return Result((False,) * 4)
            if "pg_catalog.pg_attribute" in sql: return Result((False,) * 3)
            if "lock_activity_projection_run" in sql:
                if self.role == "processor": raise PermissionError("query-only function")
                return Result(State.run)
            if "phase='complete'" in sql and "activity_projection_day_backfill" in sql: return Result(State.day_complete)
            if "phase='complete'" in sql and "activity_projection_backfill" in sql: return Result(State.run)
            if "has_table_privilege" in sql or "has_column_privilege" in sql: return Result(State.grants)
            return Result(True)
    class State:
        bucket = True
        day_schema = (True, True, True)
        run = True
        day_complete = False
        course = True
        grants = True
        privacy_ready = True
        capability = True
        statements = []
        role = "query"
    class DB:
        def __init__(self, settings):
            self.transaction_guard = None
            self.sessions = self
            # These tests use the shared fixture URL, with role selected below.
            self.role = State.role
        @asynccontextmanager
        async def transaction(self):
            session = Session(self.role)
            if self.transaction_guard:
                await self.transaction_guard(session)
            yield session
        @asynccontextmanager
        async def begin(self): yield Session(self.role)
        async def close(self): pass
        def pool_snapshot(self): return None
    class Privacy:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        async def stop(self): pass
        async def is_ready(self): return State.privacy_ready
        async def check_in_transaction(self, session):
            State.statements.append((session.role, "privacy_guard"))
            if not State.privacy_ready: raise RuntimeError("privacy unavailable")
    class Worker:
        def __init__(self, *args, **kwargs):
            self.probe = args[1]; self.backfill_failures = 0
            self.owner = uuid4(); self.stopped = False
            self.course_backfill = kwargs.get("course_backfill")
            self.poll_seconds = kwargs.get("poll_seconds")
        def start(self): pass
        async def stop(self): self.stopped = True
    monkeypatch.setattr(factory, "Database", DB)
    monkeypatch.setattr(privacy, "PrivacyRuntime", Privacy)
    monkeypatch.setattr(bucket, "ActivityProjectionRuntime", Worker)
    monkeypatch.setattr(day, "ActivityDayProjectionRuntime", Worker)
    monkeypatch.setattr(processor, "ProcessorRuntime", Worker)
    return State


def settings(**flags):
    _, _, manifest, authority = helpers.material()
    return Settings(database_url=helpers.URL, trust_manifest_json=manifest, privacy_authority_json=authority,
                    activity_projection_enabled=True, activity_projection_run_id=uuid4(), **flags)


def test_query_day_requires_complete_schema_run_and_privileges(env):
    app = create_app("query", settings(activity_day_query_enabled=True))
    with TestClient(app) as client:
        assert app.state.activity_reports.day_projection_enabled
        assert client.get("/ready").status_code == 503
        env.day_complete = True
        assert client.get("/ready").status_code == 200
        env.day_schema = (True, False, True)
        assert client.get("/ready").status_code == 503
        env.day_schema = (True, True, True)
        env.run = False
        assert client.get("/ready").status_code == 503
        assert any("SELECT analytics.lock_activity_projection_run" in sql for role, sql in env.statements if role == "query")


def test_day_worker_isolated_from_bucket_and_generic_probes(env):
    import asyncio
    env.role = "processor"
    app = create_app("processor", settings(activity_day_worker_enabled=True, processor_activity_poll_seconds=0.1))
    with TestClient(app) as client:
        assert app.state.activity_projection_runtime.poll_seconds == 0.1
        bucket_workers = app.state.activity_projection_runtimes
        assert len(bucket_workers) == 2 and bucket_workers[0] is app.state.activity_projection_runtime
        assert bucket_workers[0].owner != bucket_workers[1].owner
        assert app.state.activity_day_runtime.poll_seconds == 0.1
        assert client.get("/ready").status_code == 200
        assert asyncio.run(app.state.activity_day_probe.is_ready())
        app.state.activity_day_runtime.backfill_failures = 3
        app.state.activity_projection_runtime.backfill_failures = 2
        bucket_workers[1].backfill_failures = 4
        metrics = client.get("/metrics").text
        assert 'analytics_activity_projection_backfill_failures_total{lane="day"} 3' in metrics
        assert 'analytics_activity_projection_backfill_failures_total{lane="bucket"} 6' in metrics
        assert not any("SELECT analytics.lock_activity_projection_run" in sql for role, sql in env.statements if role == "processor")
        assert any("activity_projection_backfill" in sql and "phase='complete'" in sql for role, sql in env.statements if role == "processor")
        env.day_schema = (False, False, False)
        assert not asyncio.run(app.state.activity_day_probe.is_ready())
        assert client.get("/ready").status_code == 200
        env.day_schema = (True, True, True)
        env.grants = False
        assert not asyncio.run(app.state.activity_day_probe.is_ready())
        assert client.get("/ready").status_code == 200
        env.grants = True
        env.run = False
        assert not asyncio.run(app.state.activity_day_probe.is_ready())
    assert all(worker.stopped for worker in bucket_workers)


def test_one_bucket_worker_for_single_connection_pool(env):
    env.role = "processor"
    app = create_app("processor", settings(pool_size=1))
    with TestClient(app):
        workers = app.state.activity_projection_runtimes
        assert len(workers) == 1 and workers[0] is app.state.activity_projection_runtime
    assert workers[0].stopped


def test_processor_probes_guard_capabilities_and_fail_closed(env):
    import asyncio
    env.role = "processor"
    app = create_app("processor", settings(activity_day_worker_enabled=True))
    with TestClient(app):
        for probe in (app.state.activity_projection_probe, app.state.activity_day_probe):
            env.statements.clear()
            assert asyncio.run(probe.is_ready())
            statements = [sql for _, sql in env.statements]
            assert statements[0] == "privacy_guard"
            assert "SELECT 1 FROM analytics.activity_facts LIMIT 0" in statements[1]
            assert "SELECT 1 FROM analytics.subject_fences LIMIT 0" in statements[1]
            env.privacy_ready = False
            assert not asyncio.run(probe.is_ready())
            env.privacy_ready = True
            env.capability = False
            assert not asyncio.run(probe.is_ready())
            env.capability = True


def test_day_only_processor_and_privacy_capabilities(env):
    env.role = "processor"
    app = create_app("processor", settings(activity_day_worker_enabled=True,
        processor_bucket_worker_enabled=False, processor_generic_worker_enabled=False))
    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200
        env.day_schema = (False, False, False)
        assert client.get("/ready").status_code == 503
        env.day_schema = (True, True, True)
        env.grants = False
        assert client.get("/ready").status_code == 503
        env.grants = True
        assert hasattr(app.state, "activity_day_runtime")
        assert not hasattr(app.state, "activity_projection_runtime")
        assert not hasattr(app.state, "processor_runtime")
    _, _, manifest, authority = helpers.material()
    env.role = "privacy"
    app = create_app("privacy", Settings(database_url=helpers.URL, trust_manifest_json=manifest, privacy_authority_json=authority))
    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200
        env.grants = False
        assert client.get("/ready").status_code == 503
        env.day_schema = (False, False, False)
        assert client.get("/ready").status_code == 200
        env.day_schema = (True, False, True)
        assert client.get("/ready").status_code == 503


def test_course_worker_is_explicit_and_requires_schema(env):
    env.role = "processor"
    app = create_app("processor", settings(activity_day_worker_enabled=True, activity_course_worker_enabled=True,
        processor_bucket_worker_enabled=False, processor_generic_worker_enabled=False))
    with TestClient(app) as client:
        assert callable(app.state.activity_day_runtime.course_backfill)
        assert client.get("/ready").status_code == 200
        env.course = False
        assert client.get("/ready").status_code == 503
