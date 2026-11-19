"""Explicit runtime resource ownership; no connections on import."""

import asyncio
from contextlib import AsyncExitStack
from contextlib import asynccontextmanager


async def warm_course_snapshots():
    """Keep cold trusted authoring imports out of learner requests."""
    from pymentor_core.adapters.course_local.runtime import default_runtime

    courses = default_runtime()
    for entry in courses.registry.entries.values():
        if entry.enabled:
            await asyncio.to_thread(courses.snapshot, entry.course_id)


@asynccontextmanager
async def postgres_resources(app, settings):
    from fastapi import HTTPException
    from pymentor_core.adapters.runtime import RuntimeAsyncDatabase
    from pymentor_core.adapters.runtime import RuntimeRedisSessionStore
    from pymentor_core.adapters.runtime import RuntimeIdentitySessions
    from pymentor_core.adapters.postgres.deletion_cleanup import cleanup_loop
    from sqlalchemy import inspect
    from sqlalchemy import text
    from pymentor_core.app.database import engine
    from pymentor_core.app.database import matches_database_identity
    from pymentor_core.app.schema_policy import REQUIRED_TABLES
    from pymentor_core.app.schema_policy import SCHEMA_REVISION
    from pymentor_core.app.schema_policy import compatible_schema_revision

    # Check only. Alembic must have been run by a separate migration operation.
    with engine.connect() as connection:
        identity = tuple(
            connection.execute(text("SELECT current_database(), current_user")).one()
        )
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        inspector = inspect(connection)
        available_tables = set(inspector.get_table_names())
        from pymentor_core.adapters.sql.analytics_delivery_ops.integration import enabled as analytics_delivery_enabled
        if analytics_delivery_enabled():
            from pymentor_core.adapters.sql.analytics_delivery_ops.addon_migration import check_version
            from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_export import proof_enabled
            try:
                check_version(connection, 2 if proof_enabled() else 1)
            except (ValueError, TypeError):
                raise RuntimeError("Analytics delivery addon version is not ready") from None
            from pymentor_core.adapters.sql.analytics_delivery import metadata as analytics_delivery_metadata
            from pymentor_core.adapters.sql.analytics_delivery_ops.integration import ensure_mirror_ready, require_write_gate, write_mode
            if not set(analytics_delivery_metadata.tables).issubset(available_tables):
                raise RuntimeError("Analytics delivery schema is not ready")
            for name, table in analytics_delivery_metadata.tables.items():
                actual_columns = {column["name"] for column in inspector.get_columns(name)}
                expected_pk = {column.name for column in table.primary_key.columns}
                actual_pk = set(inspector.get_pk_constraint(name).get("constrained_columns") or ())
                if not set(table.columns.keys()).issubset(actual_columns) or actual_pk != expected_pk:
                    raise RuntimeError("Analytics delivery schema is not ready")
            constraints = inspector.get_unique_constraints("analytics_transfer_outbox")
            if not any(set(item.get("column_names") or ()) == {"entity_key", "revision"} for item in constraints):
                raise RuntimeError("Analytics delivery schema is not ready")
            if proof_enabled():
                from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_sql import barriers, preparation, proofs
                if any(table.name not in available_tables or not set(table.columns.keys()).issubset(
                        {column["name"] for column in inspector.get_columns(table.name)}) for table in (proofs, barriers, preparation)):
                    raise RuntimeError("Analytics snapshot proof schema is not ready")
                for table in (proofs, barriers, preparation):
                    if set(inspector.get_pk_constraint(table.name).get("constrained_columns") or ()) != {column.name for column in table.primary_key.columns}:
                        raise RuntimeError("Analytics snapshot proof schema is not ready")
                uniques = inspector.get_unique_constraints(proofs.name)
                if not any(set(item.get("column_names") or ()) == {"subject_ref", "cut_revision"} for item in uniques):
                    raise RuntimeError("Analytics snapshot proof schema is not ready")
            try:
                selected_mode = write_mode()
                require_write_gate(connection, selected_mode)
                if selected_mode == "mirror":
                    ensure_mirror_ready(connection)
            except HTTPException:
                raise RuntimeError("Analytics activity write gate unavailable") from None
        if (
            not matches_database_identity(identity, engine.url)
            or not compatible_schema_revision(revision, SCHEMA_REVISION)
            or not set(REQUIRED_TABLES).issubset(available_tables)
        ):
            raise RuntimeError("Configured PostgreSQL schema is not ready")
    async with AsyncExitStack() as resources:
        database = RuntimeAsyncDatabase(
            settings.async_database_url,
            pool_size=3,
            max_overflow=0,
            pool_timeout=3,
            connect_timeout=3,
            command_timeout=3,
            statement_timeout_ms=3000,
            lock_timeout_ms=1000,
        )
        resources.push_async_callback(database.dispose)
        store = RuntimeRedisSessionStore(
            settings.redis_url,
            prefix=settings.redis_prefix,
            max_connections=6,
            socket_timeout=2,
        )
        resources.push_async_callback(store.close)
        sessions = RuntimeIdentitySessions(
            database, store, recovery_key=bytes.fromhex(settings.login_recovery_key)
        )
        await sessions.check_async_database()
        if not await store.ping():
            raise RuntimeError("Configured Redis is not ready")
        await warm_course_snapshots()
        app.state.identity_sessions = sessions
        # Compatibility for existing lab tools; both names reference the same PG service.
        app.state.local_async_identity = sessions
        task = asyncio.create_task(cleanup_loop(database, store))

        async def stop_cleanup():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        resources.push_async_callback(stop_cleanup)
        try:
            yield
        finally:
            app.state.identity_sessions = None
            app.state.local_async_identity = None


from pymentor_core.app.context import CompositionRoot
from pymentor_core.app.defaults import DenyAllPermissions
from pymentor_core.app.defaults import SystemClock
from pymentor_core.app.defaults import UnconfiguredExternalAPI
from pymentor_core.app.defaults import UnconfiguredQueue


def build_default_composition() -> CompositionRoot:
    from pymentor_core.adapters.sql.unit_of_work import SqlAlchemyUnitOfWork

    return CompositionRoot(
        uow_factory=SqlAlchemyUnitOfWork,
        clock=SystemClock(),
        permissions=DenyAllPermissions(),
        queue=UnconfiguredQueue(),
        external_api=UnconfiguredExternalAPI(),
    )


def identity_repository(database, uow=None):
    from pymentor_core.adapters.sql.accounts import AccountReader

    return AccountReader(database, uow)


def role_identity_projection(database, user):
    from pymentor_core.adapters.sql.role_identity import project
    return project(database, user)


def lock_lifecycle_user(database, user_id, reference):
    from pymentor_core.adapters.sql.role_identity import lock_lifecycle_user as lock_user
    return lock_user(database, user_id, reference)


def role_admin_reads():
    from pymentor_core.adapters.sql import role_admin_reads as adapter
    return adapter


def role_admin_commands():
    from pymentor_core.adapters.sql import role_admin_commands as adapter
    return adapter


def scoped_booking():
    from pymentor_core.adapters.sql import scoped_booking as adapter
    return adapter


def course_analytics_client(endpoint, *, study_time=False):
    from pymentor_core.adapters.analytics_http.course_visitors import CourseStudyTimeClient, CourseVisitorsClient
    client_type = CourseStudyTimeClient if study_time else CourseVisitorsClient
    return client_type(endpoint)


def ensure_notification_schema(engine):
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Notification worker requires PostgreSQL")
    from sqlalchemy import inspect
    from sqlalchemy import text
    from pymentor_core.app.database import matches_database_identity
    from pymentor_core.app.schema_policy import REQUIRED_TABLES, SCHEMA_REVISION, compatible_schema_revision

    with engine.connect() as connection:
        identity = connection.execute(text("SELECT current_database(), current_user")).one()
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        if (not matches_database_identity(identity, engine.url)
                or not compatible_schema_revision(revision, SCHEMA_REVISION)
                or not set(REQUIRED_TABLES).issubset(inspect(connection).get_table_names())):
            raise RuntimeError("Database migration required before starting notification worker")


def configure_bindings():
    from pymentor_core.shared.bindings import Bindings
    from pymentor_core.shared.bindings import install_bindings
    from pymentor_core.adapters.sql.registration import Registration
    from pymentor_core.adapters.sql import activity
    from pymentor_core.adapters.sql import progress
    from pymentor_core.adapters.course_local.provider import LocalCourseProvider
    from pymentor_core.adapters.legacy_runner.provider import LegacyRunner
    from pymentor_core.adapters.sql import (
        bookings,
        administration,
        landings,
        landing_requests,
        notification_outbox,
        email_auth,
    )

    from pymentor_core.adapters.sql.analytics_delivery_ops import integration as analytics_delivery
    from pymentor_core.adapters.telegram import transport

    install_bindings(
        Bindings(
            registration=Registration(),
            activity=activity,
            progress=progress,
            content=LocalCourseProvider(),
            runner=LegacyRunner(),
            bookings=bookings,
            administration=administration,
            landings=landings,
            landing_requests=landing_requests,
            notifications=notification_outbox,
            telegram=transport,
            email_auth=email_auth,
            analytics_delivery=analytics_delivery,
        )
    )
