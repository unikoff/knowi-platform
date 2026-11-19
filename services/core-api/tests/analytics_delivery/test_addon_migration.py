"""Offline contract checks; PostgreSQL execution requires a fresh synthetic run."""
from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from pymentor_core.adapters.sql.analytics_delivery_ops import addon_migration as migration

SENTINEL = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class Result:
    def __init__(self, value): self.value = value
    def one(self): return self.value
    def all(self): return self.value
    def scalar_one(self): return self.value
    def scalars(self): return self


class Connection:
    dialect = postgresql.dialect()
    def __init__(self, identity=("analytics_delivery_test_case", "owner", "owner"), lock=True, sentinel=SENTINEL, row=None):
        self.identity, self.lock, self.sentinel, self.row = identity, lock, sentinel, row
        self.mutations = []
    def execute(self, query, *args):
        sql = str(query)
        if "pg_get_userbyid" in sql: return Result(self.identity)
        if "pg_try_advisory" in sql: return Result(self.lock)
        if "install_sentinel" in sql: return Result([self.sentinel])
        if "SELECT singleton,version" in sql: return Result([] if self.row is None else [self.row])
        self.mutations.append(sql)
        return Result(None)
    def exec_driver_sql(self, statement):
        self.mutations.append(statement)


class Inspector:
    def __init__(self, names): self.names = names
    def get_table_names(self, schema): return list(self.names)
    def get_columns(self, name, schema): return [{"name": key} for key in ("singleton", "version", "migration_hash")]
    def get_pk_constraint(self, name, schema): return {"constrained_columns": ["singleton"]}
    def get_check_constraints(self, name, schema): return [{"sqltext": "(singleton = 1)"}]


def test_bundled_sql_matches_immutable_hash_and_has_complete_statements():
    root = Path(__file__).resolve().parents[2] / "tools/analytics_transfer"
    for version, (filename, expected) in migration.MIGRATIONS.items():
        raw = (root / filename).read_bytes()
        assert sha256(raw).hexdigest() == expected
        assert migration._artifact_digest(raw.replace(b"\n", b"\r\n")) == expected
        assert migration._artifact_digest(raw + b"-- changed\n") != expected
        statements = migration._statements(raw)
        assert len(statements) == (9 if version == 1 else 8)
        assert all("--" not in item for item in statements)
    with pytest.raises(ValueError):
        migration._statements(b"SELECT 'unsafe;statement';")


def test_partial_missing_and_unknown_markers_fail_closed():
    connection = Connection()
    assert migration._version(connection, Inspector(set()), allow_absent=True) == 0
    with pytest.raises(ValueError):
        migration._version(connection, Inspector({"analytics_transfer_outbox"}), allow_absent=True)
    names = {migration.MARKER, *(table.name for table in migration._metadata(1))}
    connection.row = (1, 1, migration.MIGRATIONS[1][1])
    assert migration._version(connection, Inspector(names)) == 1
    with pytest.raises(ValueError):
        migration._version(connection, Inspector(names - {"analytics_transfer_outbox"}))
    with pytest.raises(ValueError):
        migration._version(connection, Inspector(names | {"analytics_transfer_unknown"}))
    connection.row = (1, 3, "unknown")
    with pytest.raises(ValueError):
        migration._version(connection, Inspector(names))


@pytest.mark.parametrize("identity,lock,sentinel", [
    (("other", "owner", "owner"), True, SENTINEL),
    (("analytics_delivery_test_case", "reader", "owner"), True, SENTINEL),
    (("analytics_delivery_test_case", "owner", "owner"), False, SENTINEL),
    (("analytics_delivery_test_case", "owner", "owner"), True, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
])
def test_wrong_target_lock_or_sentinel_stops_before_ddl(identity, lock, sentinel):
    connection = Connection(identity, lock, sentinel)
    with pytest.raises(ValueError):
        migration.migrate(connection, expected_db="analytics_delivery_test_case", expected_owner="owner",
                          sentinel=SENTINEL, target_version=2)
    assert connection.mutations == []


def test_sqlite_or_non_synthetic_url_rejected_without_connection():
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "tools/analytics_transfer/run.py"
    spec = importlib.util.spec_from_file_location("transfer_run_guard_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for url, expected in (("sqlite:///:memory:", "analytics_delivery_test_case"),
                          ("postgresql+psycopg://u@localhost/retained", "retained"),
                          ("postgresql+psycopg://u@remote/analytics_delivery_test_case", "analytics_delivery_test_case")):
        with pytest.raises(ValueError):
            module._synthetic_database(url, expected)


def test_versioned_apply_order_and_idempotent_repeat(monkeypatch):
    import re
    class InstallingConnection(Connection):
        def __init__(self):
            super().__init__()
            self.names = set()
            self.versions = []
        def execute(self, query, *args):
            if "INSERT INTO public.analytics_transfer_schema_version" in str(query):
                values = args[0]
                self.row = (1, values["version"], values["digest"])
                self.versions.append(values["version"])
                return Result(None)
            return super().execute(query, *args)
        def exec_driver_sql(self, statement):
            super().exec_driver_sql(statement)
            found = re.match(r"CREATE TABLE\s+(?:public\.)?(\w+)", statement, re.I)
            if found:
                self.names.add(found.group(1))
    connection = InstallingConnection()
    monkeypatch.setattr(migration, "inspect", lambda conn: Inspector(conn.names))
    monkeypatch.setattr(migration, "_check_schema", lambda conn, version: None)
    args = {"expected_db": "analytics_delivery_test_case", "expected_owner": "owner",
            "sentinel": SENTINEL, "target_version": 2}
    assert migration.migrate(connection, **args) == 2
    assert connection.versions == [1, 2]
    before = list(connection.mutations)
    assert migration.migrate(connection, **args) == 2
    assert connection.versions == [1, 2]
    assert not any(item.startswith(("CREATE", "ALTER")) for item in connection.mutations[len(before):])


@pytest.mark.parametrize("version", [1, 2])
def test_schema_validation_checks_columns_types_indexes_and_constraints(monkeypatch, version):
    from sqlalchemy import CheckConstraint, Integer
    tables = {table.name: table for table in migration._metadata(version)}
    class SchemaInspector(Inspector):
        missing = None
        def __init__(self): super().__init__({migration.MARKER, *tables})
        def get_columns(self, name, schema):
            if name == migration.MARKER:
                return super().get_columns(name, schema)
            result = []
            for column in tables[name].columns:
                if (name, column.name) == self.missing:
                    continue
                coltype = column.type
                if version == 1 and (name, column.name) in {
                    ("analytics_transfer_outbox", "revision"), ("analytics_transfer_revisions", "revision"),
                    ("analytics_transfer_checkpoints", "high_watermark"), ("analytics_transfer_checkpoints", "cursor")}:
                    coltype = Integer()
                result.append({"name": column.name, "type": coltype, "nullable": column.nullable})
            return result
        def get_pk_constraint(self, name, schema):
            if name == migration.MARKER: return super().get_pk_constraint(name, schema)
            return {"constrained_columns": [col.name for col in tables[name].primary_key.columns]}
        def get_indexes(self, name, schema): return [{"name": item.name} for item in tables[name].indexes]
        def get_check_constraints(self, name, schema):
            if name == migration.MARKER: return super().get_check_constraints(name, schema)
            result = [{"name": item.name} for item in tables[name].constraints if isinstance(item, CheckConstraint)]
            if name == "analytics_transfer_activity_preparation":
                result.append({"name": "synthetic", "sqltext": "(singleton = 1)"})
            return result
        def get_unique_constraints(self, name, schema):
            return [{"column_names": ["entity_key", "revision"] if name.endswith("outbox") else ["subject_ref", "cut_revision"]}]
    inspector = SchemaInspector()
    connection = Connection(row=(1, version, migration.MIGRATIONS[version][1]))
    monkeypatch.setattr(migration, "inspect", lambda conn: inspector)
    assert migration.check_version(connection, version) == version
    inspector.missing = ("analytics_transfer_outbox", "revision")
    with pytest.raises(ValueError, match="incomplete"):
        migration.check_version(connection, version)
