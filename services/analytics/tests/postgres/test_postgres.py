"""Live synthetic PostgreSQL checks. Invoked only by tools/test_postgres.py."""
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, TimeoutError

from pymentor_analytics.adapters.postgres.activity import ActivityStorage
from pymentor_analytics.adapters.postgres.database import ClosedPrivacyReadiness, Database
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.factory import create_app
from pymentor_analytics.contracts.v2 import AcceptedEvent, parse_browser_item
from tools.bootstrap_postgres import cleanup, config, connect, create
from tools.check_image import PROBE

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def target():
    if os.environ.get("ANALYTICS_RUN_SYNTHETIC_POSTGRES") != "1":
        pytest.skip("explicit synthetic PostgreSQL runner required")
    data = config(ROOT / ".local" / "postgres-connection.json")
    name, token, urls = create(data)
    try:
        yield data, name, urls
    finally:
        cleanup(data, name, token)


def migrate(url):
    env = os.environ.copy()
    env["ANALYTICS_MIGRATE_DATABASE_URL"] = url
    return subprocess.run([sys.executable, "-m", "pymentor_analytics.app.cli", "migrate"],
                          cwd=ROOT, env=env, capture_output=True, timeout=30)


def event():
    raw = json.loads((ROOT / "contracts/fixtures/browser-batch.json").read_text(encoding="utf-8"))["events"][0]
    raw["event_id"] = str(uuid4())
    return AcceptedEvent(producer_namespace="synthetic", subject_ref=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
                         source="browser", event=parse_browser_item(json.dumps(raw).encode()),
                         received_at=datetime.now(timezone.utc))


def test_empty_upgrade_repeat_and_closed_readiness(target):
    data, name, urls = target
    settings = Settings(database_url=urls["query"], db_timeout_seconds=1)
    async def check_schema(expected):
        db = Database(settings)
        try:
            if expected:
                assert await ClosedPrivacyReadiness(db).schema_available()
            else:
                with pytest.raises(DBAPIError):
                    await ClosedPrivacyReadiness(db).schema_available()
        finally:
            await db.close()
    asyncio.run(check_schema(False))
    with TestClient(create_app("query", settings)) as client:
        assert client.get("/ready").status_code == 503
    env = os.environ.copy()
    env["ANALYTICS_MIGRATE_DATABASE_URL"] = urls["migrate"]
    processes = [subprocess.Popen([sys.executable, "-m", "pymentor_analytics.app.cli", "migrate"],
                                  cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for _ in range(2)]
    for process in processes:
        _, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr.decode()
    assert migrate(urls["migrate"]).returncode == 0
    with connect(data, name) as conn:
        assert conn.execute("SELECT version_num FROM public.alembic_version").fetchone() == ("a006",)
        assert conn.execute("SELECT count(*) FROM analytics.activity_facts").fetchone() == (0,)
    asyncio.run(check_schema(True))
    with TestClient(create_app("query", settings)) as client:
        assert client.get("/ready").status_code == 503


def test_concurrent_migrator_is_serialized(target):
    data, name, urls = target
    with connect(data, name) as conn:
        conn.execute("SELECT pg_advisory_lock(9137823461001)")
        env = os.environ.copy()
        env["ANALYTICS_MIGRATE_DATABASE_URL"] = urls["migrate"]
        env["ANALYTICS_MIGRATE_DB_TIMEOUT_SECONDS"] = "0.2"
        blocked = subprocess.run([sys.executable, "-m", "pymentor_analytics.app.cli", "migrate"],
                                 cwd=ROOT, env=env, capture_output=True, timeout=15)
        assert blocked.returncode != 0
        conn.execute("SELECT pg_advisory_unlock(9137823461001)")
    env = os.environ.copy()
    env["ANALYTICS_MIGRATE_DATABASE_URL"] = urls["migrate"]
    processes = [subprocess.Popen([sys.executable, "-m", "pymentor_analytics.app.cli", "migrate"],
                                  cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for _ in range(2)]
    for process in processes:
        _, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr.decode()


def test_grants_insert_duplicate_conflict_rollback_cancel_and_pool(target):
    data, name, urls = target
    async def run():
        collector = Database(Settings(database_url=urls["collector"], pool_size=1, pool_overflow=0, db_timeout_seconds=0.2))
        processor = Database(Settings(database_url=urls["processor"]))
        query = Database(Settings(database_url=urls["query"]))
        privacy = Database(Settings(database_url=urls["privacy"]))
        record = event()
        try:
            async with collector.transaction() as session:
                assert await ActivityStorage(session).insert(record) == "inserted"
            async with collector.transaction() as session:
                storage = ActivityStorage(session)
                assert await storage.insert(record.model_copy(update={"received_at": datetime.now(timezone.utc)})) == "duplicate"
                assert await storage.insert(record.model_copy(update={"subject_ref": uuid4()})) == "conflict"
            for db in (collector, processor, query):
                with pytest.raises(DBAPIError):
                    async with db.transaction() as session:
                        await session.execute(text("UPDATE analytics.activity_facts SET course_id='wrong'"))
                with pytest.raises(DBAPIError):
                    async with db.transaction() as session:
                        await session.execute(text("DELETE FROM analytics.activity_facts"))
            for db in (processor, query, privacy):
                with pytest.raises(DBAPIError):
                    async with db.transaction() as session:
                        await ActivityStorage(session).insert(event())
            with pytest.raises(DBAPIError):
                async with privacy.transaction() as session:
                    await session.execute(text("UPDATE analytics.activity_facts SET course_id='wrong'"))
            with pytest.raises(RuntimeError):
                async with collector.transaction() as session:
                    await ActivityStorage(session).insert(event())
                    raise RuntimeError("rollback")
            started = asyncio.Event()
            async def cancelled_write():
                async with collector.transaction() as session:
                    await ActivityStorage(session).insert(event())
                    started.set()
                    await asyncio.sleep(60)
            task = asyncio.create_task(cancelled_write())
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(asyncio.TimeoutError):
                async with collector.transaction() as session:
                    await ActivityStorage(session).insert(event())
                    await asyncio.sleep(5)
            async with collector.engine.connect() as first:
                await first.execute(text("SELECT 1"))
                with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                    async with collector.transaction() as second:
                        await second.execute(text("SELECT 1"))
            async with query.transaction() as session:
                assert (await session.execute(text("SELECT count(*) FROM analytics.activity_facts"))).scalar_one() == 1
            async with privacy.transaction() as session:
                await session.execute(text("DELETE FROM analytics.activity_facts"))
            async with query.transaction() as session:
                assert (await session.execute(text("SELECT count(*) FROM analytics.activity_facts"))).scalar_one() == 0
        finally:
            for db in (collector, processor, query, privacy):
                await db.close()
    asyncio.run(run())
    with connect(data, name) as conn:
        for role in ("collector", "processor", "query", "privacy"):
            actual = conn.execute("SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=%s", (f"{name}_{role}",)).fetchone()
            assert actual == (False, False, False)
        assert conn.execute("SELECT count(*) FROM pg_database WHERE datname=%s", ("c527_role_ci_49dbc777e3384b4282436118f594bd89",)).fetchone() == (0,)


def test_bootstrap_rejects_unsafe_targets(tmp_path):
    data = {"synthetic_only": True, "host": "127.0.0.1", "port": 57382, "user": "analytics_bootstrap", "database": "analytics_bootstrap", "password": "dummy", "project": "knowi-analytics-01a1df75"}
    path = tmp_path / "connection.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        config(path)
    with pytest.raises(ValueError):
        cleanup(data, "c527_role_ci_49dbc777e3384b4282436118f594bd89", "0" * 64)


def test_image_migration_on_fresh_synthetic_database():
    image = os.environ.get("ANALYTICS_TEST_IMAGE")
    if os.environ.get("ANALYTICS_RUN_SYNTHETIC_POSTGRES") != "1" or image != "knowi-analytics:corp63-synthetic":
        pytest.skip("explicit synthetic image check required")
    data = config(ROOT / ".local" / "postgres-connection.json")
    name, token, urls = create(data)
    try:
        env = os.environ.copy()
        env["ANALYTICS_MIGRATE_DATABASE_URL"] = urls["migrate"].replace(
            "@127.0.0.1:57392/", "@analytics-postgres:5432/")
        result = subprocess.run(["docker", "run", "--rm", "--network", "knowi-analytics-01a1df75-net",
                                 "--env", "ANALYTICS_MIGRATE_DATABASE_URL", image, "migrate"],
                                cwd=ROOT, env=env, capture_output=True, timeout=45)
        assert result.returncode == 0, result.stderr.decode()
        with connect(data, name) as conn:
            assert conn.execute("SELECT version_num FROM public.alembic_version").fetchone() == ("a006",)
        env["ANALYTICS_QUERY_DATABASE_URL"] = urls["query"].replace(
            "@127.0.0.1:57392/", "@analytics-postgres:5432/")
        baseline_probe = PROBE.replace(
            'env[f"ANALYTICS_{role.upper()}_DATABASE_URL"] = "postgresql://synthetic:synthetic@unreachable:5432/analytics_smoke"',
            "")
        baseline = subprocess.run(["docker", "run", "--rm", "--network", "knowi-analytics-01a1df75-net",
                                   "--env", "ANALYTICS_QUERY_DATABASE_URL", "--entrypoint", "python",
                                   "knowi-analytics:an1-local", "-B", "-c", baseline_probe, "query"],
                                  cwd=ROOT, env=env, capture_output=True, timeout=45)
        assert baseline.returncode == 0, baseline.stderr.decode()
        assert json.loads(baseline.stdout)["ready"] == 503
    finally:
        cleanup(data, name, token)
