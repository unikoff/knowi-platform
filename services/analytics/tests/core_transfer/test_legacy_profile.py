"""Signed G1 profile transport and fenced target service contract."""
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pymentor_analytics.adapters.auth.verifier import GrantVerifier
from pymentor_analytics.adapters.postgres.core_transfer import PostgresCoreTransfer
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.modules.analytics.core_transfer.api.legacy_router import router
from pymentor_analytics.modules.analytics.core_transfer.compatibility import MOSCOW
from pymentor_analytics.modules.analytics.core_transfer.legacy_service import LegacyService
from pymentor_analytics.modules.collector.ports import IntakeForbidden, IntakeUnavailable

URL = "postgresql://analytics_test_query@127.0.0.1:57392/analytics_test"
PATH = "/internal/analytics/v1/legacy/admin/profile"


def app_and_tokens():
    actor, target = uuid4(), uuid4()
    key, producer = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    now = int(time.time())
    def signed(role="admin", *, key_=key, scope="report:read", exp=None):
        claims = {"grant_version": 1, "iss": "issuer", "aud": "analytics", "sub": str(actor),
                  "iat": now-2, "nbf": now-2, "exp": exp or now+30, "role": role, "scope": scope,
                  "course_ids": [] if role == "admin" else ["backend"], "subject_refs": [], "global_read": role == "admin"}
        return jwt.encode(claims, key_, algorithm="EdDSA", headers={"typ": "JWT", "kid": "g"})
    class Store:
        database = SimpleNamespace(transaction_guard=True)
        browser_run_id = uuid4()
        closed = False
        async def read_subject(self, namespace, subject, today, **kwargs):
            assert namespace == "core" and subject == target and kwargs["actor"] == actor
            assert kwargs["require_coverage"] and kwargs["include_all_visits"]
            if self.closed: raise IntakeUnavailable()
            visits = [{"activity_date": day.isoformat(), "visited_at": None} for day in
                      (today, today-timedelta(days=40), today+timedelta(days=1000))]
            coverage = {"subject_ref": str(target), "coverage_through_revision": 9,
                        "source_snapshot_at": "2026-01-01T00:00:00+00:00"}
            return visits, [], coverage, "2026-01-01T00:00:01+00:00"
    class Ready:
        async def is_ready(self): return True
    store = Store()
    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(database_url=URL)
    app.state.trust_manifest_validated = True
    app.state.grant_verifier = GrantVerifier(issuer="issuer", public_keys={"g": public})
    app.state.core_transfer_probe = Ready()
    # Profile always requires a proof, independently of the older self-read flag.
    app.state.legacy_service = LegacyService(store, "core", None, require_coverage=False)
    return app, store, actor, target, signed, producer


def test_profile_signed_admin_target_and_freshness():
    app, store, _, target, signed, _ = app_and_tokens()
    today = datetime.now(timezone.utc).astimezone(MOSCOW).date()
    params = {"subject_ref": str(target), "year": today.year, "month": today.month}
    client = TestClient(app)
    response = client.get(PATH, params=params, headers={"Authorization": "Bearer " + signed()})
    assert response.status_code == 200
    body = response.json()
    assert body["coverage"] == "complete" and body["coverage_through_revision"] == 9
    assert body["browser_quality"] == "unknown" and body["browser_as_of"].endswith("+00:00")
    data = body["data"]
    assert today.isoformat() in data["activity_days"] and data["activity_days_count"] == len(data["activity_days"])
    assert data["total_activity_days"] == 3 and data["last_activity_date"] == (today+timedelta(days=1000)).isoformat()
    assert data["activity_summary"]["visited_today"] is True and data["activity_summary"]["total_active_days"] == 2
    store.closed = True
    assert client.get(PATH, params=params, headers={"Authorization": "Bearer " + signed()}).status_code == 503


def test_profile_denies_nonglobal_and_bad_identity():
    app, _, _, target, signed, producer = app_and_tokens()
    client = TestClient(app)
    params = {"subject_ref": str(target), "year": "2026", "month": "11"}
    assert client.get(PATH, params=params, headers={"Authorization": "Bearer " + signed("user")}).status_code == 403
    now = int(time.time())
    p1 = jwt.encode({"grant_version": 1, "iss": "producer-issuer", "aud": "analytics-control", "sub": "core",
                     "iat": now-1, "nbf": now-1, "exp": now+30, "scope": "control:write"},
                    producer, algorithm="EdDSA", headers={"typ": "JWT", "kid": "g"})
    assert client.get(PATH, params=params, headers={"Authorization": "Bearer " + p1}).status_code == 401
    assert client.get(PATH, params=params, headers={"Authorization": "Bearer " + signed(exp=int(time.time())-60)}).status_code == 401
    for bad in ({**params, "subject_ref": str(target).upper()}, {**params, "month": "13"},
                {**params, "subject_ref": "not-a-uuid"}):
        assert client.get(PATH, params=bad, headers={"Authorization": "Bearer " + signed()}).status_code == 400
    duplicate = [("subject_ref", str(target)), ("subject_ref", str(target)), ("year", "2026"), ("month", "11")]
    assert client.get(PATH, params=duplicate, headers={"Authorization": "Bearer " + signed()}).status_code == 400


def test_profile_core_decoder_and_signed_query_contract():
    pytest.importorskip("pymentor_core")
    pytest.importorskip("bcrypt", reason="requires the Core test environment")
    import asyncio
    import httpx
    from pymentor_core.adapters.analytics_http.legacy_read import LegacyReadClient
    app, _, _, target, signed, _ = app_and_tokens()
    today = datetime.now(timezone.utc).astimezone(MOSCOW).date()
    async def run():
        client = LegacyReadClient("http://127.0.0.1:12345", transport=httpx.ASGITransport(app=app))
        response = await client.read("profile", signed(), subject_ref=str(target), year=today.year,
                                     month=today.month, deadline_seconds=2)
        assert response["coverage"] == "complete" and response["coverage_through_revision"] == 9
        assert response["browser_quality"] == "unknown" and response["data"]["total_activity_days"] == 3
    asyncio.run(run())


@pytest.mark.parametrize("deleted_actor", [True, False])
def test_profile_fences_actor_and_target_in_numeric_lock_order(deleted_actor):
    actor, target = UUID("00000000-0000-0000-0000-000000000001"), UUID("00000000-0000-0000-0000-000000000004")
    class Result:
        def __init__(self, value): self.value = value
        def scalar_one_or_none(self): return self.value
        def one_or_none(self): return self.value
    class Database:
        def __init__(self): self.locks = []
        @asynccontextmanager
        async def transaction(self): yield self
        async def execute(self, statement, args=None):
            sql = str(statement)
            if "pg_advisory_xact_lock" in sql:
                self.locks.append(args["key"])
                return Result(None)
            if "SELECT deleted" in sql: return Result(deleted_actor)
            if "SELECT epoch,deleted" in sql: return Result(SimpleNamespace(epoch=1, deleted=not deleted_actor))
            raise AssertionError("deleted identity must be rejected before coverage or projection")
    async def run():
        database = Database()
        store = PostgresCoreTransfer(database)
        with pytest.raises(IntakeForbidden):
            await store.read_subject("core", target, datetime.now(timezone.utc).date(), deadline=time.monotonic()+2,
                                     actor=actor, require_coverage=True, include_all_visits=True)
        assert database.locks == sorted({subject_lock_key(actor), subject_lock_key(target)})
    import asyncio
    asyncio.run(run())
