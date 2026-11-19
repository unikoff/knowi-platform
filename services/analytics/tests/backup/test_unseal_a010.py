"""Offline eligibility and unknown-outcome safety for isolated a010 recovery."""
from copy import deepcopy
import asyncio
from contextlib import asynccontextmanager, nullcontext
import importlib.util
from pathlib import Path
import sys
from uuid import uuid4
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import unseal_a010 as recovery

NAME = "analytics_test_" + "a" * 32
BASE = {"kind": "restore", "state": "parity_verified", "history": {"head": "a010"},
        "parity_verified": True, "privacy_opened": False, "projection_rebuild_required": True,
        "application_access": "revoked", "database": NAME, "nonce": "b" * 64}


def test_requires_exact_closed_restore_and_new_external_run_id():
    assert recovery.validate(BASE, uuid4()) == NAME
    for change in ({"state": "unseal_pending"}, {"parity_verified": False}, {"privacy_opened": True},
                   {"projection_rebuild_required": False}, {"application_access": "granted"},
                   {"unseal_run_id": str(uuid4())}, {"database": "core"}, {"history": {"head": "a006"}}):
        with pytest.raises(ValueError):
            recovery.validate({**deepcopy(BASE), **change}, uuid4())
    with pytest.raises(ValueError):
        recovery.validate(BASE, "not-a-uuid")


def test_lock_serializes_and_releases_without_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery.backup, "BASE", tmp_path)
    (tmp_path / "operations").mkdir()
    operation = str(uuid4())
    with recovery.operation_lock(operation):
        with pytest.raises(FileExistsError):
            with recovery.operation_lock(operation):
                pass
    assert not list((tmp_path / "operations").iterdir())


def test_failed_signed_authority_never_grants_or_retries(monkeypatch):
    saved, queries = [], []
    record = {**deepcopy(BASE), "urls": {"migrate": "private", "privacy": "private"}}
    class Result:
        def __init__(self, value): self.value = value
        def one(self): return self.value
        def scalar_one(self): return self.value
    class Session:
        async def execute(self, query, params=None):
            value = str(query)
            queries.append(value)
            if "SELECT current_database(),current_user" in value: return Result((NAME, NAME + "_migrate"))
            if "has_database_privilege" in value: return Result(False)
            if "SELECT run_id" in value: return Result(uuid4())
            raise AssertionError("unexpected SQL")
    class Database:
        def __init__(self, _settings): self.transaction_guard = None
        @asynccontextmanager
        async def transaction(self): yield Session()
        async def close(self): pass
    class Gate:
        def __init__(self, *_args, **_kwargs): pass
        async def tick(self): raise RuntimeError("authority unavailable")
        async def stop(self): pass
    monkeypatch.setattr(recovery, "operation_lock", lambda _identity: nullcontext())
    monkeypatch.setattr(recovery.backup, "read_record", lambda *_args: deepcopy(record))
    monkeypatch.setattr(recovery.backup, "reject_retained", lambda *_: None)
    monkeypatch.setattr(recovery.backup, "connection_config", lambda: {})
    monkeypatch.setattr(recovery.backup, "history_for_revision", lambda _revision: {"head": "a010"})
    monkeypatch.setattr(recovery.backup, "current", lambda *_: None)
    monkeypatch.setattr(recovery.backup, "write_record", lambda _identity, value: saved.append(deepcopy(value)))
    monkeypatch.setattr(recovery, "Settings", lambda **kwargs: kwargs)
    monkeypatch.setattr(recovery, "Database", Database)
    monkeypatch.setattr(recovery, "PostgresPrivacyStore", lambda _db: object())
    monkeypatch.setattr(recovery, "AuthorityClient", lambda _authority: object())
    monkeypatch.setattr(recovery, "PrivacyRuntime", Gate)
    authority = recovery.AuthorityConfig.model_construct()
    with pytest.raises(RuntimeError, match="authority unavailable"):
        asyncio.run(recovery.recover(str(uuid4()), authority, uuid4()))
    assert [item["state"] for item in saved] == ["unseal_pending"]
    assert not any("GRANT CONNECT" in item for item in queries)
    with pytest.raises(ValueError):
        recovery.validate(saved[0], uuid4())


def test_isolated_budget_and_source_drift_fail_closed(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "backup_postgres/test_a010_source_down.py"
    spec = importlib.util.spec_from_file_location("offline_a010_resource_guard", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "source_fingerprint", lambda: {"fixture": "same"})
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: SimpleNamespace(free=8192 * 1024**2))
    monkeypatch.setattr(module.backup.check_k3s, "available_memory", lambda: 2048 * 1024**2)
    module.resource_guard({"fixture": "same"}, prestart=True)
    with pytest.raises(ValueError, match="source changed"):
        module.resource_guard({"fixture": "different"})
    monkeypatch.setattr(module.backup.check_k3s, "available_memory", lambda: 2048 * 1024**2 - 1)
    with pytest.raises(ValueError, match="resources"):
        module.resource_guard({"fixture": "same"}, prestart=True)
    monkeypatch.setattr(module.backup.check_k3s, "available_memory", lambda: 1536 * 1024**2 - 1)
    with pytest.raises(ValueError, match="resources"):
        module.resource_guard({"fixture": "same"})
