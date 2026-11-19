"""Explicit migration process with a transaction-scoped PostgreSQL advisory lock."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from pymentor_analytics.app.config import Settings

ROOT = Path(__file__).resolve().parents[3]
LOCK_KEY = 9137823461001


def upgrade(settings: Settings, *, target_revision: str = "head"):
    url = settings.database_url.get_secret_value()
    url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, pool_pre_ping=True,
                           connect_args={"connect_timeout": max(1, int(settings.db_timeout_seconds))})
    try:
        with engine.begin() as connection:
            connection.execute(text("SELECT set_config('lock_timeout', :timeout, true)"),
                               {"timeout": f"{max(1, int(settings.db_timeout_seconds * 1000))}ms"})
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})
            config = Config(str(ROOT / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, target_revision)
    finally:
        engine.dispose()
