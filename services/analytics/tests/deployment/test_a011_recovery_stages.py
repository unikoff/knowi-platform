"""Offline fault checks for the unwired a011 recovery stages."""
from contextlib import contextmanager
import io
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import synthetic_backup as backup

NAME = "analytics_test_" + "a" * 32
RECORD = {"kind": "restore", "state": "restore_pending", "history": {"head": "a011"},
          "database": NAME, "nonce": "b" * 64}


class FreshConnection:
    def __init__(self, *, other_sessions=0, day_seed=None, dirty_table=None):
        self.events, self.commits = [], 0
        self.other_sessions, self.day_seed, self.dirty_table = other_sessions, day_seed, dirty_table

    def __enter__(self): return self
    def __exit__(self, *_): pass

    @contextmanager
    def transaction(self):
        self.events.append("begin")
        try:
            yield
        except BaseException:
            self.events.append("rollback")
            raise
        else:
            self.commits += 1
            self.events.append("commit")

    def execute(self, statement, params=None):
        query = str(statement)
        self.events.append(query)
        result = MagicMock()
        if "rolsuper OR" in query: result.fetchone.return_value = (True,)
        elif "has_database_privilege" in query: result.fetchone.return_value = (False,)
        elif "pg_stat_activity" in query: result.fetchone.return_value = (self.other_sessions,)
        elif "pg_catalog.pg_tables" in query:
            result.fetchall.return_value = [(x,) for x in ("admission_budget", "activity_projection_backfill", "activity_projection_day_backfill", "activity_facts")]
        elif "count(*)" in query:
            result.fetchone.return_value = (int("activity_facts" not in query or self.dirty_table == "activity_facts"),)
        elif "FROM analytics.admission_budget" in query:
            result.fetchall.return_value = [(True, 60, 600, 10000, 536870912, 80, 90, 0, 0, False)]
        elif "FROM analytics.activity_projection_day_backfill" in query:
            result.fetchall.return_value = self.day_seed if self.day_seed is not None else [(True, None, "scanning", None, None, None)]
        elif "FROM analytics.activity_projection_backfill" in query:
            result.fetchall.return_value = [(True, None, "scanning", None, None)]
        return result


@pytest.mark.parametrize("failure", [None, "session", "seed", "data"])
def test_seed_cleanup_only_after_committed_quarantine(monkeypatch, failure):
    conn = FreshConnection(other_sessions=int(failure == "session"),
                           day_seed=[(True, None, "complete", None, None, None)] if failure == "seed" else None,
                           dirty_table="activity_facts" if failure == "data" else None)
    monkeypatch.setattr(backup, "reject_retained", lambda *_: None)
    monkeypatch.setattr(backup, "validate_db", lambda *_: None)
    monkeypatch.setattr(backup, "assert_a011_privileges", lambda *_: None)
    monkeypatch.setattr(backup.db, "connect", lambda *_: conn)
    if failure:
        with pytest.raises(ValueError): backup.clear_fresh_defaults_a011({}, RECORD)
    else:
        backup.clear_fresh_defaults_a011({}, RECORD)
    deletes = [i for i, value in enumerate(conn.events) if "DELETE FROM analytics" in value]
    assert conn.commits >= 1
    assert all(index > conn.events.index("commit") for index in deletes)
    assert len(deletes) == (0 if failure else 3)
    assert conn.events[-1] == ("rollback" if failure else "commit")


def test_changed_trigger_is_rejected_before_revoke_or_seed_writes(monkeypatch):
    conn = FreshConnection()
    monkeypatch.setattr(backup, "reject_retained", lambda *_: None)
    monkeypatch.setattr(backup, "validate_db", lambda *_: None)
    monkeypatch.setattr(backup.db, "connect", lambda *_: conn)
    def invalid_trigger(_conn, _name):
        raise ValueError("a011 trigger definition differs")
    monkeypatch.setattr(backup, "assert_a011_privileges", invalid_trigger)
    with pytest.raises(ValueError, match="trigger definition differs"):
        backup.clear_fresh_defaults_a011({}, RECORD)
    assert not conn.events


def test_atomic_restore_requires_two_active_triggers_and_closed_roles(tmp_path, monkeypatch):
    (tmp_path / "staging").mkdir()
    archive = tmp_path / "archive"
    archive.write_bytes(b"synthetic archive")
    monkeypatch.setattr(backup, "BASE", tmp_path)
    monkeypatch.setattr(backup, "reject_retained", lambda *_: None)
    monkeypatch.setattr(backup, "validate_db", lambda *_: None)
    monkeypatch.setattr(backup, "assert_a011_privileges", lambda *_: None)
    captured = {}
    def popen(args, **kwargs):
        if "pg_restore" in args:
            return MagicMock(stdout=io.BytesIO(b"COPY analytics.activity_facts FROM stdin;\n\\.\n"),
                             stderr=io.BytesIO(), returncode=0)
        captured["args"] = args
        captured["script"] = kwargs["stdin"].read().decode("ascii")
        return MagicMock(stderr=io.BytesIO(), returncode=0)
    monkeypatch.setattr(backup.subprocess, "Popen", popen)
    conn = MagicMock()
    conn.__enter__.return_value = conn
    conn.execute.side_effect = [SimpleNamespace(fetchall=lambda: [("O",)]) for _ in range(2)] + [
        SimpleNamespace(fetchone=lambda: (False,)) for _ in range(5)]
    monkeypatch.setattr(backup.db, "connect", lambda *_: conn)
    backup.restore_a011_transaction(archive, RECORD, {}, "secret")
    script = captured["script"]
    for trigger in ("activity_projection_fact_inserted", "activity_projection_bucket_changed"):
        assert script.index("DISABLE TRIGGER " + trigger) < script.index("COPY analytics.activity_facts")
        assert script.index("COPY analytics.activity_facts") < script.index("ENABLE TRIGGER " + trigger)
    assert script.count("has_database_privilege") == 5
    assert "tgfoid=" in script and "DISABLE TRIGGER ALL" not in script
    assert "--single-transaction" in captured["args"] and "ON_ERROR_STOP=on" in captured["args"]


@pytest.mark.parametrize("drift", [None, "query_update", "public_function", "function_owner", "collector_to_processor", "collector_to_foreign", "trigger_when"])
def test_a011_acl_rejects_privilege_and_owner_drift(drift):
    columns = {
        "activity_projection_day_sources": "subject_ref local_day metric_version generation state claim_id owner lease_expires_at claim_generation claim_epoch claim_run_id attempts next_attempt_at last_failure".split(),
        "activity_projection_days": "subject_ref local_day metric_version generation subject_epoch run_id contributions presence_labels source_bucket_count source_compact_items source_compact_bytes".split(),
        "activity_projection_day_backfill": "singleton run_id phase cursor_subject cursor_bucket cursor_metric".split(),
    }
    updates = {"activity_projection_day_sources": set("state claim_id owner lease_expires_at claim_generation claim_epoch claim_run_id attempts next_attempt_at last_failure".split()),
               "activity_projection_day_backfill": set("phase cursor_subject cursor_bucket cursor_metric".split())}
    class Connection:
        def execute(self, query, args=None):
            result = MagicMock()
            if "SELECT attname" in query:
                result.fetchall.return_value = [(x,) for x in columns[args[0].split(".")[-1]]]
            elif query.startswith("SELECT has_table_privilege"):
                tests = re.findall(r"has_(?:table|column|function)_privilege\([^)]*\)", query)
                values, pos = [], 0
                for test in tests:
                    width = 4 if "column_privilege" in test else 3
                    entry, pos = args[pos:pos + width], pos + width
                    role = entry[0].removeprefix(NAME + "_")
                    target = entry[1].split(".")[-1]
                    privilege = entry[-1]
                    if "function_privilege" in test:
                        allowed = role == "processor" and target.startswith(("register_activity_day(", "begin_activity_day_backfill(", "invalidate_stale_activity_day("))
                    else:
                        column = entry[2] if width == 4 else None
                        allowed = ((privilege == "SELECT" and (role in ("processor", "query") or
                                   role == "privacy" and target != "activity_projection_day_backfill" or
                                   column == "subject_ref" and role == "transfer_status" and target != "activity_projection_day_backfill")) or
                                   role == "processor" and target == "activity_projection_days" and privilege in ("INSERT", "UPDATE") or
                                   width == 3 and role == "privacy" and target != "activity_projection_day_backfill" and privilege == "DELETE" or
                                   width == 4 and role == "processor" and privilege == "UPDATE" and column in updates.get(target, set()))
                        if drift == "query_update" and role == "query" and privilege == "UPDATE": allowed = True
                    values.append(allowed)
                result.fetchone.return_value = tuple(values)
            elif "FROM pg_auth_members" in query: result.fetchone.return_value = (drift in {"collector_to_processor", "collector_to_foreign"} and args[0] == NAME + "_collector",)
            elif "SELECT EXISTS(" in query: result.fetchone.return_value = (drift == "public_function",)
            elif "has_schema_privilege" in query: result.fetchone.return_value = (False,)
            elif "FROM pg_proc p WHERE" in query:
                result.fetchall.return_value = [(NAME + "_migrate", True, True)] * 4 + [("other" if drift == "function_owner" else NAME + "_migrate", True, True)]
            elif "pg_get_userbyid(c.relowner)" in query: result.fetchall.return_value = [(NAME + "_migrate",)] * 3
            elif "FROM pg_trigger" in query:
                assert "pg_get_expr" not in query
                assert "pg_get_triggerdef(oid,true)" in query
                assert "new.source_kind = ''browser_v2''::text" in query
                assert "EXECUTE FUNCTION analytics.activity_projection_fact_inserted()" in query
                result.fetchone.return_value = (drift != "trigger_when", True)
            else: raise AssertionError("unexpected ACL query")
            return result
    if drift:
        with pytest.raises(ValueError): backup.assert_a011_privileges(Connection(), NAME)
    else:
        backup.assert_a011_privileges(Connection(), NAME)
