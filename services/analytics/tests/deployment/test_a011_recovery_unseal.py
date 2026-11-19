"""Offline failures must leave a011 recovery pending and access closed."""
import asyncio
from contextlib import asynccontextmanager, nullcontext
from copy import deepcopy
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import unseal_a011 as recovery

NAME = "analytics_test_" + "a" * 32
BASE = {"kind": "restore", "state": "parity_verified", "history": {"head": "a011"},
        "parity_verified": True, "privacy_opened": False, "projection_rebuild_required": True,
        "application_access": "revoked", "database": NAME, "nonce": "b" * 64,
        "urls": {"migrate": "private", "privacy": "private"}}


class Result:
    def __init__(self, value): self.value = value
    def one(self): return self.value
    def scalar_one(self): return self.value


def test_authority_failure_after_durable_intent_never_rebuilds(monkeypatch):
    writes, sql, run = [], [], uuid4()
    previous = uuid4()
    class Session:
        async def execute(self, statement, params=None):
            query = str(statement)
            sql.append(query)
            if "current_database(),current_user" in query: return Result((NAME, NAME + "_migrate"))
            if "has_database_privilege" in query: return Result(False)
            if "pg_stat_activity" in query: return Result(0)
            if "SELECT run_id FROM" in query: return Result(previous)
            if "GRANT CONNECT" in query and '"' + NAME + '_privacy"' in query: return Result(None)
            raise AssertionError("unexpected SQL")
    class Database:
        def __init__(self, _): self.transaction_guard = None
        @asynccontextmanager
        async def transaction(self): yield Session()
        async def close(self): pass
    class Gate:
        def __init__(self, *_args, **_kwargs): pass
        async def tick(self): raise RuntimeError("authority unavailable")
        async def stop(self): pass
    monkeypatch.setattr(recovery.old, "operation_lock", lambda _: nullcontext())
    monkeypatch.setattr(recovery.backup, "read_record", lambda *_: deepcopy(BASE))
    monkeypatch.setattr(recovery.backup, "reject_retained", lambda *_: None)
    monkeypatch.setattr(recovery.backup, "connection_config", lambda: {})
    monkeypatch.setattr(recovery.backup, "history_for_revision", lambda _: {"head": "a011"})
    monkeypatch.setattr(recovery.backup, "current", lambda *_: None)
    monkeypatch.setattr(recovery.backup.db, "connect", lambda *_: nullcontext())
    monkeypatch.setattr(recovery.backup, "assert_a011_privileges", lambda *_: None)
    monkeypatch.setattr(recovery.backup, "write_record", lambda _, value: writes.append(deepcopy(value)))
    monkeypatch.setattr(recovery, "Settings", lambda **kwargs: kwargs)
    monkeypatch.setattr(recovery, "Database", Database)
    monkeypatch.setattr(recovery, "PostgresPrivacyStore", lambda _: object())
    monkeypatch.setattr(recovery, "AuthorityClient", lambda _: object())
    monkeypatch.setattr(recovery, "PrivacyRuntime", Gate)
    authority = recovery.AuthorityConfig.model_construct()
    with pytest.raises(RuntimeError, match="authority unavailable"):
        asyncio.run(recovery.recover(str(uuid4()), authority, run))
    assert [item["state"] for item in writes] == ["unseal_pending"]
    assert writes[0]["unseal_run_id"] == str(run)
    assert sum("GRANT CONNECT" in item for item in sql) == 1
    assert not any("UPDATE " in item for item in sql)
    with pytest.raises(ValueError): recovery.validate(writes[0], uuid4())


@pytest.mark.parametrize("previous_index", [0, 1])
def test_old_bucket_or_day_run_cannot_be_reused(monkeypatch, previous_index):
    run, calls = uuid4(), []
    values = [uuid4(), uuid4()]
    values[previous_index] = run
    class Session:
        async def execute(self, statement, params=None):
            query = str(statement)
            if "current_database(),current_user" in query: return Result((NAME, NAME + "_migrate"))
            if "has_database_privilege" in query: return Result(False)
            if "pg_stat_activity" in query: return Result(0)
            if "activity_projection_day_backfill" in query: return Result(values[1])
            if "activity_projection_backfill" in query: return Result(values[0])
            raise AssertionError("unexpected SQL")
    class Database:
        def __init__(self, _): self.transaction_guard = None
        @asynccontextmanager
        async def transaction(self): yield Session()
        async def close(self): pass
    class Gate:
        def __init__(self, *_args, **_kwargs): pass
        async def stop(self): pass
    monkeypatch.setattr(recovery.old, "operation_lock", lambda _: nullcontext())
    monkeypatch.setattr(recovery.backup, "read_record", lambda *_: deepcopy(BASE))
    monkeypatch.setattr(recovery.backup, "reject_retained", lambda *_: None)
    monkeypatch.setattr(recovery.backup, "connection_config", lambda: {})
    monkeypatch.setattr(recovery.backup, "history_for_revision", lambda _: {"head": "a011"})
    monkeypatch.setattr(recovery.backup, "current", lambda *_: None)
    monkeypatch.setattr(recovery.backup.db, "connect", lambda *_: nullcontext())
    monkeypatch.setattr(recovery.backup, "assert_a011_privileges", lambda *_: None)
    monkeypatch.setattr(recovery.backup, "write_record", lambda *_: calls.append("write"))
    monkeypatch.setattr(recovery, "Settings", lambda **kwargs: kwargs)
    monkeypatch.setattr(recovery, "Database", Database)
    monkeypatch.setattr(recovery, "PostgresPrivacyStore", lambda _: object())
    monkeypatch.setattr(recovery, "AuthorityClient", lambda _: object())
    monkeypatch.setattr(recovery, "PrivacyRuntime", Gate)
    with pytest.raises(ValueError, match="new dataset identity"):
        asyncio.run(recovery.recover(str(uuid4()), recovery.AuthorityConfig.model_construct(), run))
    assert calls == []


def test_lost_grant_reply_stays_pending_without_retry(monkeypatch):
    run, writes, calls = uuid4(), [], []
    record = {**deepcopy(BASE), "state": "unseal_grant_ready", "unseal_run_id": str(run)}
    class Gate:
        async def tick(self): calls.append("tick")
        async def is_ready(self): return True
        async def check_in_transaction(self, _session): pass
    gate = Gate()
    class Owner:
        transaction_guard = gate.check_in_transaction
        @asynccontextmanager
        async def transaction(self):
            class Session:
                async def execute(self, statement, params=None):
                    query = str(statement)
                    if "current_database(),current_user" in query: return Result((NAME, NAME + "_migrate"))
                    if "GRANT CONNECT" in query:
                        calls.append("grant")
                        raise TimeoutError("lost reply")
                    raise AssertionError("unexpected SQL")
            yield Session()
    async def noop(*_args, **_kwargs): pass
    monkeypatch.setattr(recovery.backup, "reject_retained", lambda *_: None)
    monkeypatch.setattr(recovery.backup, "current", lambda *_: None)
    monkeypatch.setattr(recovery.backup.db, "connect", lambda *_: nullcontext())
    monkeypatch.setattr(recovery.backup, "assert_a011_privileges", lambda *_: None)
    monkeypatch.setattr(recovery.backup, "history_for_revision", lambda _: BASE["history"])
    monkeypatch.setattr(recovery.backup, "write_record", lambda _, value: writes.append(deepcopy(value)))
    monkeypatch.setattr(recovery.old, "operation_lock", lambda _: nullcontext())
    def read_record(*_):
        if record["state"] != "unseal_grant_ready": raise ValueError("pending")
        return record
    monkeypatch.setattr(recovery.backup, "read_record", read_record)
    monkeypatch.setattr(recovery, "PostgresActivityDayProjection", lambda *_a, **_k: object())
    monkeypatch.setattr(recovery, "reconcile_days", noop)
    monkeypatch.setattr(recovery, "guarded_reader_check", noop)
    monkeypatch.setattr(recovery, "_closed", noop)
    monkeypatch.setattr(recovery, "_ready_state", noop)
    with pytest.raises(TimeoutError):
        asyncio.run(recovery._grant_after_verified_rebuild(str(uuid4()), Owner(), gate, {}, NAME, run))
    assert [x["state"] for x in writes] == ["unseal_grant_pending"]
    assert calls.count("grant") == 1
    with pytest.raises(ValueError):
        asyncio.run(recovery._grant_after_verified_rebuild(str(uuid4()), Owner(), gate, {}, NAME, run))


def test_missing_day_source_stops_before_any_reader_or_grant():
    class Store:
        async def reconciliation_step(self, **_kwargs): return None, ((uuid4(),),)
    class Database:
        @asynccontextmanager
        async def transaction(self):
            pytest.fail("missing source must stop before payload check")
            yield
    with pytest.raises(ValueError, match="source reconciliation"):
        asyncio.run(recovery.reconcile_days(Database(), Store(), uuid4(), deadline=time.monotonic() + 5))


@pytest.mark.parametrize("stale", [False, True])
def test_reconciliation_checks_actual_day_against_rebuilt_buckets(stale):
    run = uuid4()
    subject = uuid4()
    row = SimpleNamespace(subject_ref=subject, local_day="2026-11-15", metric_version="activity-v1",
        state="ready", generation=2, payload_generation=2, run_id=run, subject_epoch=0, current_epoch=0,
        deleted=False, source_bucket_count=1, source_compact_items=0, source_compact_bytes=4,
        bucket_count=1, bucket_items=0, bucket_bytes=4)
    day = {"contributions": [], "presence_labels": ([{"course_id": "test", "lesson_id": None}] if stale else []),
           "source_bucket_count": 1, "source_compact_items": 0, "source_compact_bytes": 4}
    bucket = {"contributions": [], "presence_labels": [], "compact_item_count": 0, "compact_json_bytes": 4}
    class Rows:
        def __init__(self, value): self.value = value
        def scalar_one(self): return self.value
        def all(self): return self.value
        def one(self): return self.value
        def mappings(self): return self
    class Session:
        async def execute(self, statement, _args=None):
            query = str(statement)
            if "SELECT EXISTS" in query: return Rows(False)
            if "FROM analytics.activity_projection_day_sources s" in query: return Rows([row])
            if "FROM analytics.activity_projection_days" in query: return Rows(day)
            if "FROM analytics.activity_projection_buckets WHERE" in query: return Rows([bucket])
            raise AssertionError("unexpected SQL")
    class Database:
        @asynccontextmanager
        async def transaction(self): yield Session()
    class Store:
        async def reconciliation_step(self, **_kwargs): return None, ()
    if stale:
        with pytest.raises(ValueError):
            asyncio.run(recovery.reconcile_days(Database(), Store(), run, deadline=time.monotonic() + 5))
    else:
        asyncio.run(recovery.reconcile_days(Database(), Store(), run, deadline=time.monotonic() + 5))


@pytest.mark.parametrize("lost_reply", [False, True])
def test_default_recover_grants_only_after_both_rebuilds_and_reader(monkeypatch, lost_reply):
    run, old_run, events = uuid4(), uuid4(), []
    class Session:
        async def execute(self, statement, params=None):
            query = str(statement)
            if "current_database(),current_user" in query: return Result((NAME, NAME + "_migrate"))
            if "has_database_privilege" in query: return Result(False)
            if "pg_stat_activity" in query: return Result(0)
            if "SELECT run_id FROM" in query: return Result(old_run)
            if "SELECT run_id,phase FROM" in query: return Result((run, "complete"))
            if "SELECT count(*)" in query: return Result(0)
            if "GRANT CONNECT" in query:
                events.append("privacy_grant")
                return Result(None)
            raise AssertionError("unexpected SQL")
    class Database:
        def __init__(self, _settings): self.transaction_guard = None
        @asynccontextmanager
        async def transaction(self): yield Session()
        async def close(self): pass
    class Gate:
        def __init__(self, *_a, **_k): pass
        async def tick(self): events.append("privacy_tick")
        async def is_ready(self): return True
        async def check_in_transaction(self, _session): pass
        async def stop(self): pass
    class Worker:
        def __init__(self, *_a, **_k): self.day = False
        def start(self): events.append("bucket_start")
        async def stop(self): events.append("bucket_stop")
    class DayWorker(Worker):
        def start(self): events.append("day_start")
        async def stop(self): events.append("day_stop")
    async def begin(*_a, **_k): events.append("begin_run")
    async def invalidate(*_a, **_k): events.append("invalidate")
    async def reconcile(*_a, **_k): events.append("reconcile")
    async def reader(*_a, **_k): events.append("reader")
    async def grant(_identity, record, *_a):
        assert record["state"] == "unseal_grant_ready" and record["application_access"] == "revoked"
        events.append("grant")
        if lost_reply:
            record["state"] = "unseal_grant_pending"
            recovery.backup.write_record(_identity, record)
            raise TimeoutError("lost grant reply")
    async def noop(*_a, **_k): pass
    monkeypatch.setattr(recovery.old, "operation_lock", lambda _: nullcontext())
    monkeypatch.setattr(recovery.backup, "read_record", lambda *_: deepcopy(BASE))
    monkeypatch.setattr(recovery.backup, "reject_retained", lambda *_: None)
    monkeypatch.setattr(recovery.backup, "connection_config", lambda: {})
    monkeypatch.setattr(recovery.backup, "history_for_revision", lambda _: BASE["history"])
    monkeypatch.setattr(recovery.backup, "current", lambda *_: None)
    monkeypatch.setattr(recovery.backup.db, "connect", lambda *_: nullcontext())
    monkeypatch.setattr(recovery.backup, "assert_a011_privileges", lambda *_: None)
    monkeypatch.setattr(recovery.backup, "write_record", lambda _id, value: events.append(value["state"]))
    monkeypatch.setattr(recovery, "Settings", lambda **kwargs: kwargs)
    monkeypatch.setattr(recovery, "Database", Database)
    monkeypatch.setattr(recovery, "PostgresPrivacyStore", lambda _: object())
    monkeypatch.setattr(recovery, "AuthorityClient", lambda _: object())
    monkeypatch.setattr(recovery, "PrivacyRuntime", Gate)
    monkeypatch.setattr(recovery, "PostgresActivityProjection", lambda *_a, **_k: object())
    monkeypatch.setattr(recovery, "PostgresActivityDayProjection", lambda *_a, **_k: object())
    monkeypatch.setattr(recovery, "ActivityProjectionRuntime", Worker)
    monkeypatch.setattr(recovery, "ActivityDayProjectionRuntime", DayWorker)
    monkeypatch.setattr(recovery, "begin_activity_projection_backfill", begin)
    monkeypatch.setattr(recovery, "invalidate_restored_buckets", invalidate)
    monkeypatch.setattr(recovery, "reconcile_days", reconcile)
    monkeypatch.setattr(recovery, "guarded_reader_check", reader)
    monkeypatch.setattr(recovery, "_closed", noop)
    monkeypatch.setattr(recovery, "_ready_state", noop)
    monkeypatch.setattr(recovery, "_grant_locked", grant)
    if lost_reply:
        with pytest.raises(TimeoutError, match="lost grant reply"):
            asyncio.run(recovery.recover(str(uuid4()), recovery.AuthorityConfig.model_construct(), run))
        assert events[-1] == "unseal_grant_pending" and events.count("grant") == 1
    else:
        result = asyncio.run(recovery.recover(str(uuid4()), recovery.AuthorityConfig.model_construct(), run))
        assert result["status"] == "unsealed"
    assert events.index("unseal_pending") < events.index("privacy_grant") < events.index("begin_run")
    assert events.index("begin_run") < events.index("invalidate") < events.index("bucket_start")
    assert events.index("bucket_stop") < events.index("day_start") < events.index("day_stop")
    assert events.index("day_stop") < events.index("reconcile") < events.index("reader") < events.index("grant")


def test_cli_runs_unseal_by_default_without_an_extra_approval(monkeypatch, capsys):
    restore_id, authority_id, run_id = (str(uuid4()) for _ in range(3))
    path = Mock()
    path.stat.return_value = SimpleNamespace(st_size=3, st_nlink=1)
    path.read_bytes.return_value = b"{}"
    authority = recovery.AuthorityConfig.model_construct()
    received = []
    async def recover(identity, supplied_authority, supplied_run):
        received.append((identity, supplied_authority, str(supplied_run)))
        return {"status": "unsealed", "privacy_opened": False}
    monkeypatch.setattr(sys, "argv", ["unseal_a011.py", "--restore", restore_id,
                                     "--authority", authority_id, "--run-id", run_id])
    monkeypatch.setattr(recovery.backup, "tree", lambda: None)
    monkeypatch.setattr(recovery.backup, "path_for", lambda *_: path)
    monkeypatch.setattr(recovery.AuthorityConfig, "from_json", lambda _: authority)
    monkeypatch.setattr(recovery, "recover", recover)
    recovery.main()
    assert received == [(restore_id, authority, run_id)]
    assert '"status":"unsealed"' in capsys.readouterr().out
