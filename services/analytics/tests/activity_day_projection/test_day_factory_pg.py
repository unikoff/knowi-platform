"""Opt-in disposable PG capability check; the privacy authority is stubbed.

This checks the actual factory probe against the real processor role and grants,
not the external authority or end-to-end privacy readiness.
"""
import importlib.util
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.factory import create_app

spec = importlib.util.spec_from_file_location("day_factory_pg_helpers", Path(__file__).parents[1] / "core_transfer/test_core_factory.py")
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


def test_real_processor_grants_and_day_only_readiness(activity_day_projection_target, monkeypatch):
    import pymentor_analytics.app.privacy as privacy
    import pymentor_analytics.app.activity_day_projection_runtime as day

    _, _, urls, run_id, _ = activity_day_projection_target
    migrate = create_engine(urls["migrate"].replace("postgresql://", "postgresql+psycopg://", 1))
    processor = create_engine(urls["processor"].replace("postgresql://", "postgresql+psycopg://", 1))

    class Privacy:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        async def stop(self): pass
        async def is_ready(self): return True
        async def check_in_transaction(self, session): pass
    class Worker:
        def __init__(self, *args, **kwargs): self.backfill_failures = 0
        def start(self): pass
        async def stop(self): pass
    monkeypatch.setattr(privacy, "PrivacyRuntime", Privacy)
    monkeypatch.setattr(day, "ActivityDayProjectionRuntime", Worker)
    try:
        with processor.connect() as conn:
            assert conn.execute(text("SELECT has_function_privilege(current_user, 'analytics.lock_activity_projection_run(uuid)', 'EXECUTE')")).scalar_one() is False
            assert conn.execute(text("SELECT run_id=:run AND phase='complete' FROM analytics.activity_projection_backfill WHERE singleton=true"), {"run": run_id}).scalar_one() is False
        _, _, manifest, authority = helpers.material()
        app = create_app("processor", Settings(database_url=urls["processor"], trust_manifest_json=manifest,
            privacy_authority_json=authority, activity_projection_enabled=True, activity_projection_run_id=run_id,
            activity_day_worker_enabled=True, processor_bucket_worker_enabled=False, processor_generic_worker_enabled=False))
        with TestClient(app) as client:
            assert client.get("/ready").status_code == 503
            with migrate.begin() as conn:
                conn.execute(text("UPDATE analytics.activity_projection_backfill SET phase='complete' WHERE singleton=true"))
            assert client.get("/ready").status_code == 200
    finally:
        migrate.dispose()
        processor.dispose()
