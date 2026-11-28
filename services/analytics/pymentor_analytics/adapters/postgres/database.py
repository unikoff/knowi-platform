"""Bounded per-process engine and per-operation async transactions."""
from contextlib import asynccontextmanager, nullcontext
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pymentor_analytics.app.config import Settings


class Database:
    transaction_admission = nullcontext()

    def __init__(self, settings: Settings):
        self.timeout = settings.db_timeout_seconds
        self.pool_max = settings.pool_size + settings.pool_overflow if settings.pool_size > 0 and settings.pool_overflow >= 0 else None
        self.transaction_guard = None
        self.transaction_exit_guard = None
        url = settings.database_url.get_secret_value()
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        self.engine = create_async_engine(
            url, pool_size=settings.pool_size, max_overflow=settings.pool_overflow,
            pool_timeout=settings.db_timeout_seconds,
            connect_args={"timeout": settings.db_timeout_seconds,
                          "command_timeout": settings.db_timeout_seconds},
            pool_pre_ping=True,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    def pool_snapshot(self):
        """Best-effort current-process pool gauges; unsupported values are unknown."""
        try:
            pool = self.engine.pool
            values = (pool.size(), pool.checkedin(), pool.checkedout(), pool.overflow())
            if any(type(value) is not int for value in values) or any(value < 0 for value in values[:3]):
                return None
            return (*values[:3], self.pool_max, max(0, values[3]))
        except Exception:
            return None

    def transaction(self):
        # Hold workload admission until the session has returned its connection.
        return self._guarded_transaction(self.transaction_admission)

    def query_readiness_transaction(self):
        """Use the same pool and guards for the singleflight Query readiness check."""
        return self._guarded_transaction(nullcontext())

    @asynccontextmanager
    async def _guarded_transaction(self, admission):
        # SQLAlchemy rolls back on BaseException, including cancellation.
        async with asyncio.timeout(self.timeout), admission:
            async with self.sessions.begin() as session:
                if self.transaction_guard is not None:
                    await self.transaction_guard(session)
                yield session
                if self.transaction_exit_guard is not None:
                    await self.transaction_exit_guard(session)

    async def close(self):
        await self.engine.dispose()


class ClosedPrivacyReadiness:
    """Schema check is necessary, but never opens the unimplemented privacy barrier."""
    def __init__(self, database: Database):
        self.database = database

    async def is_ready(self) -> bool:
        # There is no authenticated, applied opening barrier yet. Do not wait on
        # the database for a result that cannot possibly be ready.
        return False

    async def schema_available(self) -> bool:
        async with self.database.transaction() as session:
            # Runtime roles do not own Alembic's history. Check the visible tables;
            # the future privacy adapter must prove a current external barrier.
            await session.execute(text("SELECT 1 FROM analytics.activity_facts LIMIT 1"))
            await session.execute(text("SELECT 1 FROM analytics.subject_fences LIMIT 1"))
        return True
