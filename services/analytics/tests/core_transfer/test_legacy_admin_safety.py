import asyncio
from contextlib import asynccontextmanager
import json
import time
from types import SimpleNamespace
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from pymentor_analytics.adapters.postgres.core_transfer import PostgresCoreTransfer
from pymentor_analytics.adapters.postgres.processor import PostgresSyntheticCounts
from pymentor_analytics.app.config import Settings, core_transfer_config
from pymentor_analytics.contracts.auth_v1 import VerifiedGrant
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.modules.analytics.core_transfer.api.legacy_router import router
from pymentor_analytics.modules.analytics.core_transfer.legacy_service import LegacyService

ACTOR = UUID("00000000-0000-0000-0000-000000000001")
SUBJECT = UUID("00000000-0000-0000-0000-000000000004")
URL = "postgresql://analytics_test_query@127.0.0.1:57392/analytics_test"


class Result:
    def __init__(self, value): self.value = value
    def one(self): return self.value
    def one_or_none(self): return self.value
    def scalar_one_or_none(self): return self.value
    def scalar_one(self): return self.value
    def first(self): return self.value


class Database:
    def __init__(self, *, deleted=False):
        self.transaction_guard = object()
        self.deleted = deleted
        self.locks = []
        self.read_projection = False

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def execute(self, statement, args=None):
        sql = str(statement)
        if "pg_attribute" in sql: return Result((False, False, False))
        if "pg_advisory_xact_lock" in sql:
            self.locks.append(args["key"])
            return Result(None)
        if "EXISTS(SELECT 1 FROM analytics.subject_fences" in sql: return Result(self.deleted)
        if "FROM analytics.subject_fences" in sql:
            if "SELECT epoch,deleted" in sql:
                return Result(SimpleNamespace(epoch=0, deleted=False))
            if "SELECT deleted" in sql:
                return Result(False if self.deleted else SimpleNamespace(deleted=False))
        if "core_transfer_projection" in sql:
            self.read_projection = True
            raise AssertionError("deleted roster must be rejected before reading projection")
        return Result(None)


def test_synthetic_counts_locks_numeric_keys_not_uuid_order():
    async def scenario():
        database = Database()
        assert subject_lock_key(ACTOR) > subject_lock_key(SUBJECT)
        value = await PostgresSyntheticCounts(database).read_counts("synthetic.activity", 1, ACTOR, SUBJECT, ("backend",))
        assert value is None
        assert database.locks == sorted({subject_lock_key(ACTOR), subject_lock_key(SUBJECT)})
    asyncio.run(scenario())


def test_deleted_roster_fails_closed_before_aggregation():
    database = Database(deleted=True)
    store = PostgresCoreTransfer(database)
    roster = {"subjects": (SUBJECT,), "students": frozenset({str(SUBJECT)}), "expires_at": int(time.time()) + 60}
    grant = VerifiedGrant(ACTOR, "admin", "report:read", (), (), True, int(time.time()) + 60)
    class Verifier:
        def verify(self, token): return grant
    class Ready:
        async def is_ready(self): return True
    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(database_url=URL)
    app.state.trust_manifest_validated = True
    app.state.grant_verifier = Verifier()
    app.state.core_transfer_probe = Ready()
    app.state.legacy_service = LegacyService(store, "core", roster)
    response = TestClient(app).get("/internal/analytics/v1/legacy/admin/activity", headers={"Authorization": "Bearer synthetic"})
    assert response.status_code == 503 and response.json() == {"error": "unavailable"}
    assert database.read_projection is False
    assert database.locks == sorted({subject_lock_key(ACTOR), subject_lock_key(SUBJECT)})


def test_roster_rejects_student_outside_subject_set():
    roster = {"complete": True, "expires_at": int(time.time()) + 60,
              "subjects": [str(ACTOR)], "students": [str(SUBJECT)]}
    settings = Settings(database_url=URL, trust_manifest_json="{}", privacy_authority_json="{}",
                        core_transfer_enabled=True, core_transfer_namespace="core",
                        core_transfer_roster_json=json.dumps(roster))
    with pytest.raises(ValueError):
        core_transfer_config(settings, "query")
