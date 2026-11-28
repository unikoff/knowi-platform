"""Off-host contract and round-trip probes for the Core deletion snapshot."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from pymentor_core.modules.analytics_delivery import local_collection as local


SUBJECT = "5f79f235-5035-4c89-9d0b-40ac7112307f"
STAMP = datetime(2026, 11, 28, 11, tzinfo=timezone.utc)
IDENTITY = ("core_fixture", False, "reader")
COLUMNS = [
    ["lifecycle_id", "character varying(36)", True],
    ["user_id", "integer", True],
    ["deleted_at", "timestamp with time zone", True],
]
SCHEMA_OUTBOX = Path(__file__).resolve().parents[2] / "migrations/versions/c426_deletion_outbox.py"


def deletion_functions():
    bodies = {}
    for node in ast.walk(ast.parse(SCHEMA_OUTBOX.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call) or not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        sql = node.args[0].value
        if not isinstance(sql, str):
            continue
        for label in ("pymentor_capture_account_deletion(p_user_id integer)", "pymentor_capture_account_deletion_trigger()"):
            if "CREATE FUNCTION " + label in sql:
                bodies[label] = sql.split(" AS $$", 1)[1].split("$$ LANGUAGE", 1)[0]
    return [bodies["pymentor_capture_account_deletion_trigger()"], bodies["pymentor_capture_account_deletion(p_user_id integer)"]]


class Result:
    def __init__(self, value=None):
        self.value = value

    def one(self):
        return self.value

    def all(self):
        return self.value

    def scalar_one(self):
        return self.value


class NoNetworkConnection:
    """Serve the same catalogue facts to both SQL shapes and record actual calls to execute."""

    def __init__(self):
        self.steps = []
        self.sql = []
        self.reader_checks = [True] * 8
        self.early, self.late = IDENTITY, IDENTITY
        self.columns = deepcopy(COLUMNS)
        self.pk = ["lifecycle_id"]
        self.triggers = [["O", 11, *deletion_functions()]]
        self.clock = STAMP
        self.rows = []
        self.active = False
        self.identity_reads = 0
        self.connections = 0
        self.rollbacks = 0
        self.commits = 0

    def connect(self):
        self.connections += 1
        return self

    def begin(self):
        self.active = True
        self.identity_reads = 0
        return self

    def __enter__(self):
        return self

    def __exit__(self, kind, *_):
        if self.active:
            self.active = False
            if kind:
                self.rollbacks += 1
            else:
                self.commits += 1

    def execute(self, stmt, parameters=None):
        assert self.active
        sql = str(stmt)
        self.sql.append(sql)
        if "snapshot preflight" in sql:
            self.steps.append("preflight")
            self.identity_reads += 1
            strict = parameters["private_reader"]
            return Result((*self.early, self.columns, self.pk, self.triggers,
                           all(self.reader_checks) if strict else True, self.clock))
        if sql == "SET TRANSACTION READ ONLY":
            step, value = "read-only", None
        elif "current_database()" in sql:
            self.identity_reads += 1
            step, value = ("initial-identity", self.early) if self.identity_reads == 1 else ("final-identity", self.late)
        elif "pg_attribute a JOIN" in sql:
            step, value = "columns", self.columns
        elif "array_agg(a.attname" in sql:
            step, value = "primary-key", self.pk
        elif "FROM pg_catalog.pg_trigger" in sql:
            step, value = "trigger", self.triggers
        elif "FROM pg_catalog.pg_roles" in sql:
            step, value = "private-role", self.reader_checks
        elif "clock_timestamp()" in sql:
            step, value = "timestamp", self.clock
        else:
            assert 'ORDER BY lifecycle_id COLLATE "C" LIMIT 1025' in sql
            step, value = "events", self.rows
        self.steps.append(step)
        return Result(value)


@pytest.fixture
def harness(monkeypatch):
    connection = NoNetworkConnection()
    engine_options = []
    monkeypatch.setattr(local, "create_engine", lambda url, **kw: engine_options.append(kw) or connection)

    def config(*, private=False):
        return local.LocalCollection(SUBJECT, "core_fixture", "postgresql+psycopg://synthetic:fixture@127.0.0.1:1/core_fixture",
                                     b"x" * 32, expected_user="reader" if private else None)

    return connection, engine_options, config


@pytest.mark.parametrize("private", [False, True], ids=["local", "private"])
def test_fresh_read_is_four_executes_without_reusing_an_answer(harness, private):
    connection, engines, config = harness
    settings = config(private=private)
    assert local.read_snapshot(settings) == ("2026-11-28T11:00:00.000000Z", [])
    assert len(connection.sql) == 4, connection.steps
    assert connection.steps == ["read-only", "preflight", "events", "final-identity"]
    connection.rows = [(SUBJECT, STAMP + timedelta(milliseconds=30))]
    connection.clock = STAMP + timedelta(milliseconds=15)
    as_of, rows = local.read_snapshot(settings)
    assert as_of == "2026-11-28T11:00:00.015000Z"
    assert rows == [{"subject_ref": SUBJECT, "deleted_at": "2026-11-28T11:00:00.030000Z"}]
    assert connection.steps[4:] == connection.steps[:4]
    assert connection.commits == 2
    assert connection.connections == 2
    assert len(engines) == 1
    assert engines[0]["isolation_level"] == "READ COMMITTED"
    assert engines[0]["pool_pre_ping"]
    assert (engines[0]["pool_size"], engines[0]["max_overflow"]) == (2, 0)


@pytest.mark.parametrize("field,replacement", [
    ("early", ("other_db", False, "reader")),
    ("early", ("core_fixture", True, "reader")),
    ("early", ("core_fixture", False, "other_user")),
    ("columns", [["lifecycle_id", "text", True], *COLUMNS[1:]]),
    ("columns", [*COLUMNS, ["extra", "integer", False]]),
    ("columns", [*COLUMNS[:2], ["deleted_at", "timestamp with time zone", False]]),
    ("pk", ["lifecycle_id", "user_id"]),
    ("triggers", []),
])
def test_preflight_stops_before_event_select(harness, field, replacement):
    connection, _, config = harness
    setattr(connection, field, replacement)
    with pytest.raises(ValueError, match="source unavailable"):
        local.read_snapshot(config(private=True))
    assert connection.steps == ["read-only", "preflight"]
    assert connection.rollbacks == 1


@pytest.mark.parametrize("piece,value", [(0, "D"), (1, 0), (2, "select 1"), (3, "select 1")])
def test_modified_trigger_tuple_stops_before_event_select(harness, piece, value):
    connection, _, config = harness
    connection.triggers[0][piece] = value
    with pytest.raises(ValueError, match="source unavailable"):
        local.read_snapshot(config())
    assert connection.steps == ["read-only", "preflight"]


@pytest.mark.parametrize("permission", range(8))
def test_private_rejects_every_invalid_role_privilege(harness, permission):
    connection, _, config = harness
    connection.reader_checks[permission] = False
    with pytest.raises(ValueError, match="source unavailable"):
        local.read_snapshot(config(private=True))
    assert connection.steps == ["read-only", "preflight"]


@pytest.mark.parametrize("late", [
    ("other_db", False, "reader"),
    ("core_fixture", True, "reader"),
    ("core_fixture", False, "other_user"),
])
def test_post_read_identity_checks_the_same_database_role_and_primary(harness, late):
    connection, _, config = harness
    connection.late = late
    connection.rows = [(SUBJECT, STAMP)]
    with pytest.raises(ValueError, match="source changed"):
        local.read_snapshot(config(private=True))
    assert connection.steps == ["read-only", "preflight", "events", "final-identity"]
    assert connection.rollbacks == 1


@pytest.mark.parametrize("size", [1024, 1025])
def test_event_limit_is_fail_closed_without_truncating_output(harness, size):
    connection, _, config = harness
    connection.rows = [(str(UUID(int=i + 1)), STAMP) for i in range(size)]
    if size == 1025:
        with pytest.raises(ValueError, match="source overflow"):
            local.read_snapshot(config())
        return
    _, events = local.read_snapshot(config())
    assert [row["subject_ref"] for row in events] == [row[0] for row in connection.rows]
