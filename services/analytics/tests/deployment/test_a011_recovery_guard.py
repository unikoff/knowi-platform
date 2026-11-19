"""Offline routing checks for authenticated projection archives."""
from copy import deepcopy
from pathlib import Path
import sys
from unittest.mock import Mock
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import synthetic_backup as backup
import unseal_a010


@pytest.mark.parametrize("head,chain", [("a012", ["a012", "a011", "a010", "a006"])])
def test_authenticated_day_archive_refused_before_target_mutation(monkeypatch, head, chain):
    stage = Mock()
    history = {"head": head, "chain": chain, "sha256": "a" * 64, "runtime_sha256": "b" * 64}
    metadata = {"synthetic_only": True, "history": history,
                "format": "pg16-custom-analytics-data-only",
                "source_database": "analytics_test_" + "a" * 32,
                "source_id": str(uuid4()), "fixture_state_sha256": "c" * 64}
    monkeypatch.setattr(backup, "key_for", lambda _: b"x" * 32)
    monkeypatch.setattr(backup.crypto, "decrypt_verified", lambda *_: ({"metadata": metadata}, stage))
    monkeypatch.setattr(backup, "history_for_revision", lambda _: deepcopy(history))
    calls = []
    for name in ("connection_config", "provision", "write_record", "clear_fresh_defaults", "verify_container"):
        monkeypatch.setattr(backup, name, lambda *_, name=name, **__: calls.append(name))
    with pytest.raises(ValueError, match="restore revision is not supported"):
        backup.restore(str(uuid4()), str(uuid4()), str(uuid4()))
    assert calls == []
    stage.unlink.assert_called_once_with(missing_ok=True)


def test_exact_a011_routes_through_quarantine_and_transaction(monkeypatch):
    stage = Mock()
    history = {"head": "a011", "chain": ["a011", "a010", "a006"], "sha256": "a" * 64, "runtime_sha256": "b" * 64}
    metadata = {"synthetic_only": True, "history": history, "format": "pg16-custom-analytics-data-only",
                "source_database": "analytics_test_" + "a" * 32, "source_id": str(uuid4()),
                "fixture_state_sha256": "c" * 64}
    record = {"database": "analytics_test_" + "b" * 32, "nonce": "d" * 64,
              "urls": {"migrate": "postgresql://owner:password@host/database"}}
    events = []
    monkeypatch.setattr(backup, "key_for", lambda _: b"x" * 32)
    monkeypatch.setattr(backup.crypto, "decrypt_verified", lambda *_: ({"metadata": metadata}, stage))
    monkeypatch.setattr(backup, "history_for_revision", lambda _: deepcopy(history))
    monkeypatch.setattr(backup, "connection_config", lambda: {})
    monkeypatch.setattr(backup, "provision", lambda *_a, **_k: record)
    monkeypatch.setattr(backup, "write_record", lambda _id, r: events.append(("record", deepcopy(r))))
    monkeypatch.setattr(backup, "clear_fresh_defaults_a011", lambda *_: events.append(("quarantine", None)))
    monkeypatch.setattr(backup, "clear_fresh_defaults", lambda *_: pytest.fail("legacy cleanup invoked"))
    monkeypatch.setattr(backup, "verify_container", lambda: None)
    monkeypatch.setattr(backup, "restore_a011_transaction", lambda *_: events.append(("restore", None)))
    monkeypatch.setattr(backup, "restore_a010_transaction", lambda *_: pytest.fail("a010 restore invoked"))
    monkeypatch.setattr(backup, "current", lambda *_: None)
    backup.restore(str(uuid4()), str(uuid4()), str(uuid4()))
    assert [x[0] for x in events] == ["record", "quarantine", "record", "restore", "record"]
    assert events[2][1]["application_access"] == "revoked"
    assert events[-1][1]["state"] == "restored_unverified"
    assert events[-1][1]["privacy_opened"] is False


def test_a011_restore_unknown_outcome_stays_pending_without_retry(monkeypatch):
    stage = Mock()
    history = {"head": "a011", "chain": ["a011", "a010", "a006"], "sha256": "a" * 64, "runtime_sha256": "b" * 64}
    metadata = {"synthetic_only": True, "history": history, "format": "pg16-custom-analytics-data-only",
                "source_database": "analytics_test_" + "a" * 32, "source_id": str(uuid4()),
                "fixture_state_sha256": "c" * 64}
    record = {"database": "analytics_test_" + "b" * 32, "nonce": "d" * 64,
              "urls": {"migrate": "postgresql://owner:password@host/database"}}
    writes, calls = [], []
    monkeypatch.setattr(backup, "key_for", lambda _: b"x" * 32)
    monkeypatch.setattr(backup.crypto, "decrypt_verified", lambda *_: ({"metadata": metadata}, stage))
    monkeypatch.setattr(backup, "history_for_revision", lambda _: deepcopy(history))
    monkeypatch.setattr(backup, "connection_config", lambda: {})
    monkeypatch.setattr(backup, "provision", lambda *_a, **_k: record)
    monkeypatch.setattr(backup, "write_record", lambda _id, r: writes.append(deepcopy(r)))
    monkeypatch.setattr(backup, "clear_fresh_defaults_a011", lambda *_: None)
    monkeypatch.setattr(backup, "verify_container", lambda: None)
    def lost(*_):
        calls.append(True)
        raise TimeoutError("lost response")
    monkeypatch.setattr(backup, "restore_a011_transaction", lost)
    with pytest.raises(TimeoutError): backup.restore(str(uuid4()), str(uuid4()), str(uuid4()))
    assert len(calls) == 1
    assert writes[-1]["state"] == "restore_pending" and writes[-1]["data_restored"] is False
    assert writes[-1]["application_access"] == "revoked"
    assert writes[-1]["restore_failure"]["type"] == "TimeoutError"


def test_a010_unseal_rejects_day_restore():
    record = {"kind": "restore", "state": "parity_verified", "history": {"head": "a011"},
              "parity_verified": True, "privacy_opened": False, "projection_rebuild_required": True,
              "application_access": "revoked", "database": "analytics_test_" + "a" * 32,
              "nonce": "b" * 64}
    with pytest.raises(ValueError, match="not eligible"):
        unseal_a010.validate(record, uuid4())


@pytest.mark.parametrize("head,chain", [("a006", ["a006"]), ("a010", ["a010", "a006"])])
def test_older_archives_continue_to_target_validation(monkeypatch, head, chain):
    stage = Mock()
    history = {"head": head, "chain": chain, "sha256": "a" * 64, "runtime_sha256": "b" * 64}
    metadata = {"synthetic_only": True, "history": history,
                "format": "pg16-custom-analytics-data-only",
                "source_database": "analytics_test_" + "a" * 32,
                "source_id": str(uuid4()), "fixture_state_sha256": "c" * 64}
    monkeypatch.setattr(backup, "key_for", lambda _: b"x" * 32)
    monkeypatch.setattr(backup.crypto, "decrypt_verified", lambda *_: ({"metadata": metadata}, stage))
    monkeypatch.setattr(backup, "history_for_revision", lambda _: deepcopy(history))
    class ReachedConnection(Exception):
        pass
    def stop():
        raise ReachedConnection
    monkeypatch.setattr(backup, "connection_config", stop)
    with pytest.raises(ReachedConnection):
        backup.restore(str(uuid4()), str(uuid4()), str(uuid4()))
    stage.unlink.assert_not_called()


def test_day_target_cannot_directly_clear_seeds(monkeypatch):
    record = {"kind": "restore", "state": "restore_pending", "history": {"head": "a011"}}
    calls = []
    monkeypatch.setattr(backup, "reject_retained", lambda *_: calls.append("database validation"))
    with pytest.raises(ValueError, match="a011 restore is not supported"):
        backup.clear_fresh_defaults({}, record)
    assert calls == []
