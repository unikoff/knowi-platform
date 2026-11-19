"""Versioned, opt-in synthetic schema installation; never invoked by startup."""
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from sqlalchemy import CheckConstraint, Integer, inspect, text

MIGRATIONS = {
    1: ("001_additive.sql", "b42ea238e7fe1f627d420417ebcd101e95a2b7a8287695b590e045bbb84487b2"),
    2: ("002_snapshot_proof.sql", "2e1587c1af693ab44339e324a0e70e883075a02a5b5494c1bc1ed0b5ab351971"),
}
MARKER = "analytics_transfer_schema_version"


def _metadata(version):
    from pymentor_core.adapters.sql.analytics_delivery import metadata as delivery
    from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_sql import metadata as snapshot
    return list(delivery.tables.values()) + (list(snapshot.tables.values()) if version == 2 else [])


def _version(connection, inspector, *, allow_absent=False):
    names = set(inspector.get_table_names(schema="public"))
    addons = {name for name in names if name.startswith("analytics_transfer_")}
    if MARKER not in addons:
        if allow_absent and not addons:
            return 0
        raise ValueError("addon version unavailable or partial")
    columns = inspector.get_columns(MARKER, schema="public")
    pk = inspector.get_pk_constraint(MARKER, schema="public").get("constrained_columns")
    if {item["name"] for item in columns} != {"singleton", "version", "migration_hash"} or pk != ["singleton"]:
        raise ValueError("invalid addon version marker")
    checks = inspector.get_check_constraints(MARKER, schema="public")
    if not any((item.get("sqltext") or "").replace("(", "").replace(")", "").replace(" ", "").lower() == "singleton=1" for item in checks):
        raise ValueError("invalid addon version marker")
    rows = connection.execute(text("SELECT singleton,version,migration_hash FROM public.analytics_transfer_schema_version LIMIT 2")).all()
    if len(rows) != 1 or rows[0][0] != 1 or type(rows[0][1]) is not int or rows[0][1] not in MIGRATIONS or rows[0][2] != MIGRATIONS[rows[0][1]][1]:
        raise ValueError("unknown addon version")
    version = rows[0][1]
    if addons != {MARKER, *(table.name for table in _metadata(version))}:
        raise ValueError("addon schema is partial or unknown")
    return version


def check_version(connection, minimum):
    if minimum not in (1, 2):
        raise ValueError("invalid required addon version")
    version = _version(connection, inspect(connection))
    if version < minimum:
        raise ValueError("addon version is too old")
    _check_schema(connection, version)
    return version


def _check_schema(connection, version):
    inspector = inspect(connection)
    if _version(connection, inspector) != version:
        raise ValueError("unexpected addon version")
    dialect = connection.dialect
    widened = {("analytics_transfer_outbox", "revision"), ("analytics_transfer_revisions", "revision"),
               ("analytics_transfer_checkpoints", "high_watermark"), ("analytics_transfer_checkpoints", "cursor")}
    for table in _metadata(version):
        actual = {column["name"]: column for column in inspector.get_columns(table.name, schema="public")}
        if set(actual) != set(table.c.keys()) or set(inspector.get_pk_constraint(table.name, schema="public").get("constrained_columns") or ()) != {column.name for column in table.primary_key.columns}:
            raise ValueError("addon table is incomplete")
        for column in table.columns:
            expected = Integer() if version == 1 and (table.name, column.name) in widened else column.type
            expected_sql = dialect.type_compiler.process(expected).upper()
            actual_sql = dialect.type_compiler.process(actual[column.name]["type"]).upper()
            if actual_sql != expected_sql or actual[column.name]["nullable"] != column.nullable:
                raise ValueError("addon column mismatch")
        indexes = {item["name"] for item in inspector.get_indexes(table.name, schema="public")}
        if not {index.name for index in table.indexes}.issubset(indexes):
            raise ValueError("addon index missing")
        checks = {item["name"] for item in inspector.get_check_constraints(table.name, schema="public")}
        expected_checks = {item.name for item in table.constraints if isinstance(item, CheckConstraint) and item.name}
        if not expected_checks.issubset(checks):
            raise ValueError("addon check missing")
    for name, columns in (("analytics_transfer_outbox", {"entity_key", "revision"}),
                          *(([("analytics_transfer_snapshot_proofs", {"subject_ref", "cut_revision"})]) if version == 2 else [])):
        if not any(set(item.get("column_names") or ()) == columns for item in inspector.get_unique_constraints(name, schema="public")):
            raise ValueError("addon uniqueness missing")
    if version == 2:
        checks = inspector.get_check_constraints("analytics_transfer_activity_preparation", schema="public")
        if not any((item.get("sqltext") or "").replace("(", "").replace(")", "").replace(" ", "").lower() == "singleton=1" for item in checks):
            raise ValueError("addon preparation constraint missing")


def _statements(raw):
    # Immutable bundled artifacts use line comments and contain no semicolons in literals.
    sql = "\n".join(line.split("--", 1)[0] for line in raw.decode("utf-8").splitlines())
    quoted = False
    for char in sql:
        if char == "'":
            quoted = not quoted
        elif char == ";" and quoted:
            raise ValueError("unsupported migration format")
    if quoted:
        raise ValueError("unsupported migration format")
    return [part.strip() for part in sql.split(";") if part.strip()]


def _artifact_digest(raw):
    # Git checkouts may normalize LF to CRLF without changing SQL content.
    normalized = raw.replace(b"\r\n", b"\n")
    if b"\r" in normalized:
        raise ValueError("invalid migration line endings")
    return sha256(normalized).hexdigest()


def migrate(connection, *, expected_db, expected_owner, sentinel, target_version):
    """Caller supplies a fresh transaction; errors must roll back the whole transaction."""
    if (target_version not in (1, 2) or not isinstance(expected_db, str)
            or not expected_db.startswith("analytics_delivery_test_") or not isinstance(expected_owner, str)
            or not expected_owner or str(UUID(sentinel)) != sentinel or connection.dialect.name != "postgresql"):
        raise ValueError("invalid installation identity")
    identity = connection.execute(text("""SELECT current_database(),current_user,
        pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()""")).one()
    if tuple(identity) != (expected_db, expected_owner, expected_owner):
        raise ValueError("database identity mismatch")
    if not connection.execute(text("SELECT pg_try_advisory_xact_lock(1820814, 917234) ")).scalar_one():
        raise ValueError("addon migration already running")
    rows = connection.execute(text("SELECT token FROM public.analytics_delivery_install_sentinel LIMIT 2")).scalars().all()
    if len(rows) != 1 or str(rows[0]) != sentinel:
        raise ValueError("synthetic sentinel mismatch")
    return _install_locked(connection, target_version)


def _install_locked(connection, target_version):
    """Install bundled SQL after the caller has validated identity and taken the lock."""
    connection.execute(text("SET LOCAL search_path TO public, pg_catalog"))
    current = _version(connection, inspect(connection), allow_absent=True)
    if current > target_version:
        raise ValueError("addon downgrade prohibited")
    if current:
        _check_schema(connection, current)
    else:
        connection.exec_driver_sql("""CREATE TABLE public.analytics_transfer_schema_version (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL,
            migration_hash VARCHAR(64) NOT NULL)""")
    root = Path(__file__).resolve().parents[4] / "tools" / "analytics_transfer"
    for version in range(current + 1, target_version + 1):
        filename, expected = MIGRATIONS[version]
        raw = (root / filename).read_bytes()
        if _artifact_digest(raw) != expected:
            raise ValueError("bundled migration checksum mismatch")
        for statement in _statements(raw):
            connection.exec_driver_sql(statement)
        connection.execute(text("""INSERT INTO public.analytics_transfer_schema_version(singleton,version,migration_hash)
            VALUES (1,:version,:digest) ON CONFLICT (singleton) DO UPDATE SET version=:version,migration_hash=:digest"""),
            {"version": version, "digest": expected})
        _check_schema(connection, version)
    return target_version
