import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from cryptography.exceptions import InvalidTag

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import synthetic_backup as backup


SOURCE = "11111111-1111-4111-8111-111111111111"
RESTORE = "22222222-2222-4222-8222-222222222222"
KEY = "33333333-3333-4333-8333-333333333333"
ARCHIVE = "44444444-4444-4444-8444-444444444444"
NAME = "analytics_test_" + "a" * 32
TARGET = "analytics_test_" + "b" * 32
HISTORY = {"head": "a006", "chain": ["a006", "a005"], "sha256": "a" * 64, "runtime_sha256": "b" * 64}


def test_authentication_failure_precedes_any_target_sql(tmp_path):
    with patch.object(backup, "BASE", tmp_path), patch.object(backup, "key_for", return_value=b"x" * 32), \
         patch.object(backup.crypto, "decrypt_verified", side_effect=InvalidTag), \
         patch.object(backup, "connection_config") as config, patch.object(backup, "provision") as provision:
        with pytest.raises(InvalidTag):
            backup.restore(ARCHIVE, KEY, RESTORE)
        config.assert_not_called()
        provision.assert_not_called()


def test_a010_restore_wraps_only_derivative_trigger_and_quarantines_roles(tmp_path, monkeypatch):
    (tmp_path / "staging").mkdir()
    archive = tmp_path / "source.dump"
    archive.write_bytes(b"synthetic archive")
    record = {"kind": "restore", "state": "restore_pending", "history": {"head": "a010"},
              "database": NAME, "nonce": "b" * 64}
    monkeypatch.setattr(backup, "BASE", tmp_path)
    monkeypatch.setattr(backup, "reject_retained", lambda *_: None)
    monkeypatch.setattr(backup, "validate_db", lambda *_: None)
    extracted = b"COPY analytics.activity_facts (subject_ref) FROM stdin;\n\\.\n"
    seen = {}
    def popen(args, **kwargs):
        if "pg_restore" in args:
            seen["extract_args"] = args
            return MagicMock(stdout=io.BytesIO(extracted), stderr=io.BytesIO(b""), returncode=0)
        seen["args"] = args
        seen["script"] = kwargs["stdin"].read().decode("ascii")
        return MagicMock(stderr=io.BytesIO(b""), returncode=0)
    monkeypatch.setattr(backup.subprocess, "Popen", popen)
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.side_effect = [SimpleNamespace(fetchall=lambda: [("O",)])] + [
        SimpleNamespace(fetchone=lambda: (False,)) for _ in range(4)]
    monkeypatch.setattr(backup.db, "connect", lambda *_: connection)
    backup.restore_a010_transaction(archive, record, {}, "secret-not-in-command")
    script = seen["script"]
    assert script.index("DISABLE TRIGGER activity_projection_fact_inserted") < script.index("COPY analytics.activity_facts")
    assert script.index("COPY analytics.activity_facts") < script.index("ENABLE TRIGGER activity_projection_fact_inserted")
    assert "DISABLE TRIGGER ALL" not in script and script.count("REVOKE CONNECT") == 4
    assert "pg_stat_activity" in script and "analytics_test_sentinel" in script
    assert "--single-transaction" in seen["args"] and "ON_ERROR_STOP=on" in seen["args"]
    assert "--file=-" in seen["extract_args"] and not any("--dbname" in arg for arg in seen["extract_args"])
    assert all("secret-not-in-command" not in part for part in seen["args"])
    assert list((tmp_path / "staging").iterdir()) == []


def test_a010_failed_psql_outcome_is_classified_and_needs_reconciliation(tmp_path, monkeypatch):
    (tmp_path / "staging").mkdir()
    archive = tmp_path / "source.dump"
    archive.write_bytes(b"synthetic archive")
    record = {"kind": "restore", "state": "restore_pending", "history": {"head": "a010"},
              "database": NAME, "nonce": "b" * 64}
    monkeypatch.setattr(backup, "BASE", tmp_path)
    monkeypatch.setattr(backup, "reject_retained", lambda *_: None)
    monkeypatch.setattr(backup, "validate_db", lambda *_: None)
    def popen(args, **_kwargs):
        if "pg_restore" in args:
            return MagicMock(stdout=io.BytesIO(b"SELECT 1;\n"), stderr=io.BytesIO(b""), returncode=0)
        return MagicMock(stderr=io.BytesIO(b"ERROR: permission denied for table analytics_test_sentinel private"), returncode=1)
    monkeypatch.setattr(backup.subprocess, "Popen", popen)
    with pytest.raises(RuntimeError) as failure:
        backup.restore_a010_transaction(archive, record, {}, "private-password")
    assert str(failure.value) == "restore outcome needs reconciliation: sentinel permission denied"
    assert list((tmp_path / "staging").iterdir()) == []


def test_a010_restore_rejects_wrong_target_before_command(tmp_path, monkeypatch):
    monkeypatch.setattr(backup.subprocess, "Popen", lambda *_a, **_k: pytest.fail("must not execute"))
    with pytest.raises(ValueError, match="unsafe a010 restore"):
        backup.restore_a010_transaction(tmp_path / "archive", {"kind": "restore", "state": "restore_pending",
             "history": {"head": "a010"}, "database": "core", "nonce": "b" * 64}, {}, "private")


def test_a010_restore_reports_only_allowlisted_extraction_reason(tmp_path, monkeypatch):
    (tmp_path / "staging").mkdir()
    archive = tmp_path / "source.dump"
    archive.write_bytes(b"fixture")
    record = {"kind": "restore", "state": "restore_pending", "history": {"head": "a010"},
              "database": NAME, "nonce": "b" * 64}
    monkeypatch.setattr(backup, "BASE", tmp_path)
    monkeypatch.setattr(backup, "reject_retained", lambda *_: None)
    monkeypatch.setattr(backup, "validate_db", lambda *_: None)
    process = MagicMock(stdout=io.BytesIO(b""), stderr=io.BytesIO(b"non-seekable input file\nprivate-data"), returncode=1)
    monkeypatch.setattr(backup.subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(backup.subprocess, "run", lambda *_a, **_k: pytest.fail("must not start psql"))
    with pytest.raises(RuntimeError) as failure:
        backup.restore_a010_transaction(archive, record, {}, "private-password")
    assert str(failure.value) == "restore extraction failed: non-seekable archive"
    assert list((tmp_path / "staging").iterdir()) == []


def test_incompatible_authenticated_history_refuses_before_target_creation(tmp_path):
    stage = tmp_path / "stage"
    stage.write_bytes(b"fixture")
    header = {"metadata": {"synthetic_only": True, "history": {"head": "other"},
                            "format": "pg16-custom-analytics-data-only", "source_database": NAME}}
    with patch.object(backup, "BASE", tmp_path), patch.object(backup, "key_for", return_value=b"x" * 32), \
         patch.object(backup.crypto, "decrypt_verified", return_value=(header, stage)), \
         patch.object(backup, "history", return_value=HISTORY), patch.object(backup, "provision") as provision:
        with pytest.raises(ValueError):
            backup.restore(ARCHIVE, KEY, RESTORE)
        provision.assert_not_called()
    assert not stage.exists()


def test_dump_arguments_do_not_contain_password_or_urls():
    command = backup.docker_db_args("pg_dump", NAME, "analytics_bootstrap", ["--format=custom"])
    assert "PGPASSWORD" in command and all("password-value" not in part for part in command)
    assert not any("://" in part for part in command)
    with pytest.raises(ValueError):
        backup.docker_db_args("pg_restore", "core", "analytics_bootstrap", [])


def test_unknown_container_identity_is_refused():
    with patch.object(backup.check_k3s, "local_docker_env"), \
         patch.object(backup, "docker", return_value="other|postgres|true|true") as docker:
        with pytest.raises(ValueError):
            backup.verify_container()
        assert docker.call_count == 1


def test_core_port_binding_is_refused_before_dump():
    valid_label = "knowi-analytics-01a1df75|analytics-postgres|true|true"
    with patch.object(backup.check_k3s, "local_docker_env"), \
         patch.object(backup, "docker", side_effect=[valid_label, '[{"HostIp":"127.0.0.1","HostPort":"57382"}]']) as docker:
        with pytest.raises(ValueError):
            backup.verify_container()
        assert docker.call_count == 2


def test_retained_runtime_source_is_refused():
    with patch.object(backup.runtime.RECORD.__class__, "exists", return_value=True), \
         patch.object(backup.runtime, "read_record", return_value={"database": NAME}):
        with pytest.raises(ValueError):
            backup.reject_retained({"database": NAME})


def test_default_cleanup_refuses_non_fresh_operation():
    with patch.object(backup.db, "connect") as connect:
        with pytest.raises(ValueError):
            backup.clear_fresh_defaults({}, {"kind": "fixture", "state": "fixture_ready"})
        connect.assert_not_called()


def test_default_cleanup_refuses_nonempty_fresh_target():
    connection = MagicMock()
    conn = connection.__enter__.return_value
    conn.execute.side_effect = [SimpleNamespace(fetchall=lambda: [("admission_budget",), ("activity_facts",)]),
                                SimpleNamespace(fetchone=lambda: (1,)),
                                SimpleNamespace(fetchone=lambda: (1,))]
    record = {"kind": "restore", "state": "restore_pending", "database": TARGET}
    with patch.object(backup, "reject_retained"), patch.object(backup, "validate_db"), \
         patch.object(backup.db, "connect", return_value=connection):
        with pytest.raises(ValueError):
            backup.clear_fresh_defaults({}, record)
    assert all("DELETE" not in str(call.args[0]).upper() for call in conn.execute.call_args_list)


def test_restore_records_pending_before_any_cleanup_or_restore(tmp_path):
    stage = tmp_path / ".verified-dump-abc.tmp"
    stage.write_bytes(b"fixture")
    header = {"metadata": {"synthetic_only": True, "history": HISTORY, "source_id": SOURCE,
                            "format": "pg16-custom-analytics-data-only", "source_database": NAME,
                            "fixture_state_sha256": "c" * 64}}
    record = {"database": TARGET, "nonce": "b" * 64, "urls": {"migrate": f"postgresql://{TARGET}_migrate:secret@127.0.0.1:57392/{TARGET}"},
              "kind": "restore", "state": "fresh_migrated"}
    snapshots = []
    def save(identity, value):
        snapshots.append(dict(value))
    def cleanup(config, value):
        assert snapshots[-1]["state"] == "restore_pending"
        assert snapshots[-1]["privacy_opened"] is False
        raise RuntimeError("unknown outcome")
    with patch.object(backup, "BASE", tmp_path), patch.object(backup, "key_for", return_value=b"x" * 32), \
         patch.object(backup.crypto, "decrypt_verified", return_value=(header, stage)), \
         patch.object(backup, "history", return_value=HISTORY), patch.object(backup, "connection_config", return_value={}), \
         patch.object(backup, "provision", return_value=record), patch.object(backup, "write_record", side_effect=save), \
         patch.object(backup, "clear_fresh_defaults", side_effect=cleanup), patch.object(backup.subprocess, "run") as run:
        with pytest.raises(RuntimeError):
            backup.restore(ARCHIVE, KEY, RESTORE)
        run.assert_not_called()
    assert stage.exists() and snapshots[-1]["state"] == "restore_pending"


def test_a010_records_quarantine_before_extraction_failure(tmp_path):
    stage = tmp_path / ".verified-dump-abc.tmp"
    stage.write_bytes(b"fixture")
    history = {**HISTORY, "head": "a010"}
    header = {"metadata": {"synthetic_only": True, "history": history, "source_id": SOURCE,
                            "format": "pg16-custom-analytics-data-only", "source_database": NAME,
                            "fixture_state_sha256": "c" * 64}}
    record = {"database": TARGET, "nonce": "b" * 64, "urls": {"migrate": f"postgresql://{TARGET}_migrate:secret@127.0.0.1:57392/{TARGET}"},
              "kind": "restore", "state": "fresh_migrated"}
    snapshots = []
    def extraction(*_args):
        assert snapshots[-1]["state"] == "restore_pending"
        assert snapshots[-1]["application_access"] == "revoked"
        assert snapshots[-1]["projection_rebuild_required"] is True
        raise RuntimeError("synthetic extraction failure")
    with patch.object(backup, "BASE", tmp_path), patch.object(backup, "key_for", return_value=b"x" * 32), \
         patch.object(backup.crypto, "decrypt_verified", return_value=(header, stage)), \
         patch.object(backup, "history", return_value=history), patch.object(backup, "connection_config", return_value={}), \
         patch.object(backup, "provision", return_value=record), \
         patch.object(backup, "write_record", side_effect=lambda _identity, value: snapshots.append(dict(value))), \
         patch.object(backup, "clear_fresh_defaults"), patch.object(backup, "verify_container"), \
         patch.object(backup, "restore_a010_transaction", side_effect=extraction):
        with pytest.raises(RuntimeError, match="synthetic extraction failure"):
            backup.restore(ARCHIVE, KEY, RESTORE)
    assert snapshots[-1]["restore_failure"] == {"type": "RuntimeError", "category": "unclassified"}


def test_parity_uses_authenticated_fixture_digest_without_source_database(tmp_path):
    (tmp_path / "staging").mkdir()
    stage = tmp_path / "staging" / (".verified-dump-" + "c" * 32 + ".tmp")
    stage.write_bytes(b"fixture")
    rows = {"activity_facts": (1, "a" * 64)}
    record = {"history": HISTORY, "fixture_state_sha256": backup.state_digest(rows),
              "stage": stage.name, "database": TARGET}
    with patch.object(backup, "BASE", tmp_path), patch.object(backup, "connection_config", return_value={}), \
         patch.object(backup, "read_record", return_value=record) as read, \
         patch.object(backup, "history", return_value=HISTORY), patch.object(backup, "current"), \
         patch.object(backup, "table_digests", return_value=rows), patch.object(backup, "write_record") as write:
        backup.parity(RESTORE)
    assert read.call_count == 1 and read.call_args.args == (RESTORE, "restore", "restored_unverified")
    assert write.call_args.args[1]["parity_verified"] is True
    assert write.call_args.args[1]["privacy_opened"] is False
    assert not stage.exists()
