"""Isolated PostgreSQL migrations; never import application database settings or ORM metadata."""

import asyncio
import os

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine


# No .env loading or DATABASE_URL fallback is permitted here.
_MIGRATION_ENV_VAR = "PYMENTOR_MIGRATION_URL"
_RUNTIME_MIGRATIONS = context.config.get_main_option("core_runtime_migrations") == "true"
_EXPECTED_DATABASE = (
    os.environ.get("KNOWI_MIGRATION_DATABASE", "") if _RUNTIME_MIGRATIONS else "pymentor_test"
)
_EXPECTED_USER = (
    os.environ.get("KNOWI_MIGRATION_USER", "") if _RUNTIME_MIGRATIONS else "pymentor_test"
)
_EXPECTED_PORT = 57152
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Explicitly limited to the role-admin expansion. Historical tables keep their
# existing privileges; no default privileges or schema-wide grants are changed.
_ROLE_ADMIN_TABLES = {
    "role_admin_cutover": "SELECT, UPDATE",
    "course_leadership": "SELECT, INSERT, UPDATE",
    "role_admin_revisions": "SELECT, INSERT, UPDATE",
    "role_admin_bootstrap_markers": "SELECT, INSERT, UPDATE",
    "booking_history": "SELECT, INSERT, UPDATE",
    "role_admin_audit": "SELECT, INSERT, UPDATE",
    "role_admin_commands": "SELECT, INSERT, UPDATE",
    "course_leader_bindings": "SELECT, INSERT, UPDATE",
}
_OWNER = os.environ.get("KNOWI_MIGRATION_OWNER", "") if _RUNTIME_MIGRATIONS else ""
_APP_ROLE = os.environ.get("KNOWI_MIGRATION_APP_ROLE", "") if _RUNTIME_MIGRATIONS else ""
if bool(_OWNER) != bool(_APP_ROLE) or any(
    value and (value.strip() != value or '\x00' in value) for value in (_OWNER, _APP_ROLE)
):
    raise RuntimeError("Both migration owner and application role must be specified together")
if _OWNER and (_OWNER == _APP_ROLE or _APP_ROLE == _EXPECTED_USER):
    raise RuntimeError("Application role must be distinct from migration owner and login")


def _runtime_migration_url() -> str:
    raw = os.environ.get("KNOWI_MIGRATION_URL", "")
    expected_host = os.environ.get("KNOWI_MIGRATION_HOST", "")
    try:
        url = make_url(raw)
        valid = (
            all(value and value.strip() == value
                for value in (expected_host, _EXPECTED_DATABASE, _EXPECTED_USER))
            and url.drivername == "postgresql+asyncpg"
            and url.host == expected_host
            and url.database == _EXPECTED_DATABASE
            and url.username == _EXPECTED_USER
            and not url.query
        )
    except Exception:
        valid = False
    if not valid:
        raise RuntimeError(
            "Runtime migration target rejected: explicit PostgreSQL URL, host, database and user must agree"
        ) from None
    return raw


def _migration_url() -> str:
    if _RUNTIME_MIGRATIONS:
        return _runtime_migration_url()
    raw = os.environ.get(_MIGRATION_ENV_VAR, "")
    if not raw:
        raise RuntimeError(
            "PYMENTOR_MIGRATION_URL is required; this migration never reads DATABASE_URL."
        )

    try:
        url = make_url(raw)
    except Exception as exc:
        raise RuntimeError("Invalid PYMENTOR_MIGRATION_URL.") from exc

    if (
        url.drivername != "postgresql+asyncpg"
        or url.host not in _ALLOWED_HOSTS
        or url.port != _EXPECTED_PORT
        or url.database != _EXPECTED_DATABASE
        or url.username != _EXPECTED_USER
        or bool(url.query)
    ):
        raise RuntimeError(
            "Migration URL rejected: only postgresql+asyncpg to localhost:57152/"
            "pymentor_test as user pymentor_test is permitted."
        )
    return raw


MIGRATION_URL = _migration_url()


def run_migrations_offline() -> None:
    """Generate SQL only after the same allowlist validation as online execution."""
    if _OWNER:
        raise RuntimeError("Managed migration permissions require an online database connection")
    context.configure(
        url=MIGRATION_URL,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection) -> None:
    # Verify actual server-reported identity in addition to validating the URL.
    database, user = connection.execute(
        text("SELECT current_database(), current_user")
    ).one()
    if (database, user) != (_EXPECTED_DATABASE, _EXPECTED_USER):
        raise RuntimeError("Connected PostgreSQL database identity did not match test allowlist.")

    # SELECT starts an implicit transaction; end it before Alembic owns DDL.
    connection.commit()
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        if _OWNER:
            connection.exec_driver_sql("SET LOCAL ROLE " + connection.dialect.identifier_preparer.quote(_OWNER))
        context.run_migrations()
        if _RUNTIME_MIGRATIONS:
            _grant_role_admin_permissions(connection)


def _grant_role_admin_permissions(connection) -> None:
    """Validate ownership and grant only the objects created by c527/c528."""
    objects = connection.execute(text("""
        SELECT c.relname, pg_get_userbyid(c.relowner)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p')
          AND c.relname = ANY(:names)
    """), {"names": list(_ROLE_ADMIN_TABLES)}).all()
    if not objects:
        return  # Historical targets, including c504, remain supported.
    if not _OWNER:
        if make_url(MIGRATION_URL).host not in _ALLOWED_HOSTS:
            raise RuntimeError("Remote role-admin migrations require an explicit owner and application role")
        return
    quote = connection.dialect.identifier_preparer.quote
    schema = connection.execute(text("SELECT current_schema()")).scalar_one()
    for name, owner in objects:
        if owner != _OWNER:
            raise RuntimeError("Role-admin object has an unexpected owner; manual reconciliation is required")
        relation = f"{quote(schema)}.{quote(name)}"
        connection.exec_driver_sql(f"GRANT {_ROLE_ADMIN_TABLES[name]} ON TABLE {relation} TO {quote(_APP_ROLE)}")
        if name in {"course_leadership", "booking_history", "role_admin_audit", "role_admin_commands"}:
            sequence = connection.execute(text("SELECT pg_get_serial_sequence(:relation, 'id')"),
                                          {"relation": relation}).scalar_one()
            if not sequence:
                raise RuntimeError("Role-admin sequence is missing; manual reconciliation is required")
            sequence_owner = connection.execute(text("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=to_regclass(:sequence)"),
                                                {"sequence": sequence}).scalar_one()
            if sequence_owner != _OWNER:
                raise RuntimeError("Role-admin sequence has an unexpected owner; manual reconciliation is required")
            connection.exec_driver_sql(f"GRANT USAGE ON SEQUENCE {sequence} TO {quote(_APP_ROLE)}")


async def _run_migrations_async() -> None:
    engine = create_async_engine(MIGRATION_URL, poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(_run_migrations_async())
