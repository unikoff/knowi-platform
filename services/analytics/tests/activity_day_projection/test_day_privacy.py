"""Offline privacy coordination checks; PostgreSQL behaviour is tested separately."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from pymentor_analytics.adapters.postgres import core_transfer, privacy
from pymentor_analytics.adapters.postgres.activity_day_projection import has_activity_day_projection_schema
from pymentor_analytics.contracts.privacy_control_v1 import JournalEntry, deletion_hash
from pymentor_analytics.contracts.v2 import DeletionFence
from pymentor_analytics.modules.collector.ports import IntakeUnavailable


class Result:
    def __init__(self, value=None):
        self.value = value

    def one(self): return self.value
    def one_or_none(self): return self.value
    def scalar_one(self): return self.value
    def first(self): return self.value
    def all(self): return self.value


class PrivacyDatabase:
    def __init__(self, *, days=0, sources=0, buckets=0):
        self.remaining = {"activity_projection_days": days,
                          "activity_projection_day_sources": sources,
                          "activity_projection_buckets": buckets}
        self.applied = 0
        self.receipt = None
        self.fence = None
        self.operations = []

    @asynccontextmanager
    async def transaction(self):
        saved = (self.remaining.copy(), self.applied, self.receipt, self.fence)
        try:
            yield self
        except BaseException:
            self.remaining, self.applied, self.receipt, self.fence = saved
            raise

    async def execute(self, statement, args=None):
        sql = str(statement)
        args = args or {}
        if "SELECT authority_id,applied_through" in sql:
            return Result(SimpleNamespace(authority_id="authority", applied_through=self.applied))
        if "SELECT seq,payload_hash" in sql or "SELECT seq,control_id" in sql:
            return Result(self.receipt)
        if "SELECT epoch,deleted FROM analytics.subject_fences" in sql:
            return Result(self.fence)
        if "SELECT subject_ref FROM analytics.subject_fences" in sql:
            return Result(None)
        if "INSERT INTO analytics.subject_fences" in sql:
            self.operations.append("fence")
            self.fence = SimpleNamespace(epoch=args["epoch"], deleted=True)
        elif "INSERT INTO analytics.privacy_receipts" in sql:
            self.receipt = SimpleNamespace(seq=args["seq"], control_id=args["control"],
                                           payload_hash=args["hash"], subject_ref=args["subject"],
                                           fence_epoch=args["epoch"], phase="purging")
        elif "DELETE FROM analytics." in sql:
            table = sql.split("DELETE FROM analytics.", 1)[1].split()[0]
            self.operations.append(table)
            assert self.fence and self.fence.deleted
            count = min(args["limit"], self.remaining.get(table, 0))
            if table in self.remaining:
                self.remaining[table] -= count
            return Result([args["subject"]] * count)
        elif "EXISTS(SELECT 1 FROM analytics.activity_facts" in sql:
            return Result(any(count and table in sql for table, count in self.remaining.items()))
        elif "UPDATE analytics.privacy_receipts SET phase='complete'" in sql:
            self.receipt.phase = "complete"
        elif "UPDATE analytics.privacy_state SET applied_through" in sql:
            self.applied = args["seq"]
        return Result(None)


def entry():
    now = datetime.now(timezone.utc)
    control = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(),
                            subject_ref=uuid4(), epoch=1, occurred_at=now)
    return JournalEntry(seq=1, producer="core", control=control, received_at=now,
                        payload_hash=deletion_hash("core", control))


def schemas(monkeypatch, *, day=True, activity=True):
    async def yes(_): return True
    async def no(_): return False
    async def day_state(_):
        if day == "partial": raise IntakeUnavailable("incomplete day schema")
        return day
    monkeypatch.setattr(privacy, "has_snapshot_proof_schema", no)
    monkeypatch.setattr(privacy, "has_core_schema", no)
    monkeypatch.setattr(privacy, "has_activity_projection_schema", yes if activity else no)
    monkeypatch.setattr(privacy, "has_activity_day_projection_schema", day_state)
    monkeypatch.setattr(privacy, "has_activity_course_projection_schema", no)


def test_purge_is_bounded_and_retry_after_lost_reply_completes(monkeypatch):
    schemas(monkeypatch)
    db, item = PrivacyDatabase(days=3, sources=5, buckets=3), entry()
    class LostReply(privacy.PostgresPrivacyStore):
        async def _after_commit(self): raise RuntimeError("lost reply")
    async def scenario():
        with pytest.raises(RuntimeError, match="lost reply"):
            await LostReply(db).apply("authority", item, limit=2)
        assert db.applied == 0 and db.receipt.phase == "purging"
        assert db.remaining == {"activity_projection_days": 1,
                                "activity_projection_day_sources": 3,
                                "activity_projection_buckets": 1}
        assert db.operations.index("fence") < db.operations.index("activity_projection_buckets")
        store = privacy.PostgresPrivacyStore(db)
        assert not (await store.apply("authority", item, limit=2)).complete
        assert (await store.apply("authority", item, limit=2)).complete
        assert not any(db.remaining.values()) and db.applied == 1
        assert (await store.apply("authority", item, limit=2)).complete
    asyncio.run(scenario())


@pytest.mark.parametrize("remaining", ["activity_projection_days", "activity_projection_day_sources"])
def test_either_day_table_prevents_early_completion(monkeypatch, remaining):
    schemas(monkeypatch)
    db = PrivacyDatabase()
    db.remaining[remaining] = 3
    result = asyncio.run(privacy.PostgresPrivacyStore(db).apply("authority", entry(), limit=1))
    assert not result.complete and db.applied == 0 and db.remaining[remaining] == 2


@pytest.mark.parametrize("remaining", ["activity_course_buckets", "activity_course_days"])
def test_course_rows_prevent_early_privacy_completion(monkeypatch, remaining):
    schemas(monkeypatch)
    async def yes(_): return True
    monkeypatch.setattr(privacy, "has_activity_course_projection_schema", yes)
    db = PrivacyDatabase()
    db.remaining[remaining] = 3
    result = asyncio.run(privacy.PostgresPrivacyStore(db).apply("authority", entry(), limit=1))
    assert not result.complete and db.applied == 0 and db.remaining[remaining] == 2


def test_a010_without_day_tables_and_partial_schema_failure(monkeypatch):
    item = entry()
    schemas(monkeypatch, day=False, activity=True)
    db = PrivacyDatabase()
    assert asyncio.run(privacy.PostgresPrivacyStore(db).apply("authority", item)).complete
    assert "activity_projection_days" not in db.operations
    schemas(monkeypatch, day="partial")
    db = PrivacyDatabase(days=1)
    with pytest.raises(IntakeUnavailable):
        asyncio.run(privacy.PostgresPrivacyStore(db).apply("authority", item))
    assert db.applied == 0 and db.fence is None and db.remaining["activity_projection_days"] == 1


@pytest.mark.parametrize("state", [(False, False, False), (True, True, True),
                                   (True, False, False), (False, True, False), (False, False, True),
                                   (False, True, True), (True, False, True), (True, True, False)])
def test_real_schema_detector_rejects_every_partial_combination(state):
    class Session:
        async def execute(self, _statement): return Result(state)
    if len(set(state)) == 1:
        assert asyncio.run(has_activity_day_projection_schema(Session())) is state[0]
    else:
        with pytest.raises(IntakeUnavailable):
            asyncio.run(has_activity_day_projection_schema(Session()))


@pytest.mark.parametrize("remaining", ["activity_projection_days", "activity_projection_day_sources"])
def test_checked_status_waits_for_each_day_table(monkeypatch, remaining):
    # Reuse the real signed authority client; only the local database is mocked.
    path = Path(__file__).resolve().parents[1] / "core_transfer/test_deletion_status.py"
    spec = importlib.util.spec_from_file_location("day_privacy_status_helpers", path)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    item = entry()
    class Database(helpers.Database):
        async def execute(self, statement, args=None):
            sql = str(statement)
            if "EXISTS(SELECT 1 FROM analytics.activity_facts" in sql:
                assert "analytics.activity_projection_days" in sql
                assert "analytics.activity_projection_day_sources" in sql
                return helpers.Result(self.remaining and remaining in sql)
            return await super().execute(statement, args)
    async def day_schema(_): return True
    monkeypatch.setattr(core_transfer, "has_activity_day_projection_schema", day_schema)
    async def no_course(_): return False
    monkeypatch.setattr(core_transfer, "has_activity_course_projection_schema", no_course)
    async def scenario():
        db = Database(item)
        checker = core_transfer.CheckedDeletionStatus(db, helpers.signed_client(item),
                                                     producer_by_namespace={"server-core": "core"})
        async def status():
            return await checker.status("server-core", item.control.control_id, item.control.subject_ref,
                                        1, deadline=time.monotonic() + 2)
        db.remaining = True
        assert await status() == "pending"
        db.remaining = False
        assert await status() == "accepted"
    asyncio.run(scenario())


def test_checked_status_rejects_partial_day_schema(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "core_transfer/test_deletion_status.py"
    spec = importlib.util.spec_from_file_location("day_privacy_status_partial", path)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    item = entry()
    async def partial(_): raise IntakeUnavailable("incomplete day schema")
    monkeypatch.setattr(core_transfer, "has_activity_day_projection_schema", partial)
    async def scenario():
        checker = core_transfer.CheckedDeletionStatus(helpers.Database(item), helpers.signed_client(item),
                                                     producer_by_namespace={"server-core": "core"})
        with pytest.raises(IntakeUnavailable):
            await checker.status("server-core", item.control.control_id, item.control.subject_ref,
                                 1, deadline=time.monotonic() + 2)
    asyncio.run(scenario())
