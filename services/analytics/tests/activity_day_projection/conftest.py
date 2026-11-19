"""Disposable opt-in a010 fixture using the canonical migration chain."""
import os
import secrets
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pytest
from psycopg import sql
from sqlalchemy import create_engine, text

from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.app.config import Settings
from tools.bootstrap_postgres import cleanup, config, connect, create

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def activity_day_projection_target():
    if os.environ.get("ANALYTICS_RUN_ACTIVITY_DAY_PROJECTION_SYNTHETIC") != "1":
        pytest.skip("explicit disposable a010 PostgreSQL opt-in required")
    data = config(ROOT / ".local/postgres-connection.json")
    name, sentinel, urls = create(data)
    status_role = name + "_transfer_status"
    status_created = False
    try:
        password = secrets.token_urlsafe(32)
        with connect(data) as connection:
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS")
                               .format(sql.Identifier(status_role), sql.Literal(password)))
            status_created = True
            connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(name), sql.Identifier(status_role)))
        urls["transfer_status"] = f"postgresql://{status_role}:{quote(password, safe='')}@127.0.0.1:57392/{name}"
        upgrade(Settings(database_url=urls["migrate"]), target_revision="a011")
        engine = create_engine(urls["migrate"].replace("postgresql://", "postgresql+psycopg://", 1))
        run_id = uuid4()
        try:
            with engine.begin() as connection:
                connection.execute(text("SELECT analytics.begin_activity_projection_backfill(NULL,:run)"), {"run": run_id})
        finally:
            engine.dispose()
        yield data, name, urls, run_id, sentinel
    finally:
        cleanup(data, name, sentinel)
        if status_created:
            with connect(data) as connection:
                connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(status_role)))
        with connect(data) as connection:
            assert connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone() is None
            roles = [name + "_" + suffix for suffix in ("collector", "processor", "query", "privacy", "migrate", "transfer_status")]
            assert connection.execute("SELECT 1 FROM pg_roles WHERE rolname=ANY(%s) LIMIT 1", (roles,)).fetchone() is None
        print(f"a011-owned-target={name} cleaned=yes")
