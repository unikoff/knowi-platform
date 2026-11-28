import asyncio
import base64
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.factory import create_app
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable


@pytest.mark.parametrize(("role", "guarded_probe", "readiness_checks"), [
    ("collector", None, 2),
    ("collector", "catalog", 2),
    ("query", "projection", 2),
])
@pytest.mark.parametrize("interruption", ["unavailable", "unapplied_deletion"])
def test_local_reader_checks_source_before_and_after_transaction_and_readiness(
    monkeypatch, role, guarded_probe, readiness_checks, interruption,
):
    import pymentor_analytics.app.factory as factory
    import pymentor_analytics.app.privacy as privacy
    import pymentor_analytics.adapters.privacy_authority.client as authority
    import pymentor_analytics.adapters.core_privacy.client as source
    import pymentor_analytics.adapters.core_privacy.guard as guard
    name = "analytics_live_" + uuid4().hex
    data = {"url": "http://127.0.0.1:18041/api/internal/analytics/deletions", "instance_id": str(uuid4()),
            "secret": base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode()}
    role_options = {}
    if guarded_probe == "catalog":
        role_options = {
            "catalog_enabled": True,
            "catalog_namespace": "local-catalog",
            "core_transfer_namespaces_json": json.dumps({"core-local": "local-catalog"}),
        }
    elif guarded_probe == "projection":
        role_options = {
            "activity_projection_enabled": True,
            "activity_day_query_enabled": True,
            "activity_projection_run_id": uuid4(),
        }
    settings = Settings(database_url=f"postgresql://{name}_{role}@127.0.0.1:57392/{name}",
        real_collection=True, issuer_mode="local", trust_manifest_json="{}",
        privacy_authority_json='{"fixture_mode":false,"runtime_mode":"local"}', core_deletion_source_json=json.dumps(data),
        core_deletion_status_database_url=f"postgresql://{name}_transfer_status@127.0.0.1:57392/{name}",
        **role_options)
    class State:
        checks = []
        available = True
        source_revision = applied_revision = 0
        schema_available = True
    class Result:
        def __init__(self, count, value):
            self.count = count
            self.value = value
        def one(self): return (self.value,) + (True,) * (self.count - 1)
        def scalar_one(self): return self.value
        def scalar_one_or_none(self): return self.value
    class Session:
        async def execute(self, sql, params=None):
            statement = str(sql)
            count = 8 if "to_regclass('analytics.activity_projection_buckets')" in statement else 3
            available = State.schema_available if "to_regclass(" in statement else True
            return Result(count, available)
    class DB:
        def __init__(self, settings): self.transaction_guard = None; self.transaction_exit_guard = None
        @asynccontextmanager
        async def transaction(self):
            session = Session()
            if self.transaction_guard: await self.transaction_guard(session)
            yield session
            if self.transaction_exit_guard: await self.transaction_exit_guard(session)
        query_readiness_transaction = transaction
        async def close(self): pass
        def pool_snapshot(self): return None
    class Privacy:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        async def stop(self): pass
        async def is_ready(self): return True
        async def check_readiness_in_transaction(self, session): pass
        async def check_in_transaction(self, session): State.checks.append("barrier")
    class Guard:
        def __init__(self, *args): pass
        async def check(self, session):
            State.checks.append("source")
            if not State.available or State.source_revision != State.applied_revision:
                raise PrivacyUnavailable()
    monkeypatch.setattr(factory, "Database", DB)
    producer = SimpleNamespace(_allowed_producers={"core-local"})
    monkeypatch.setattr(factory, "load_verifiers", lambda *a, **k: (None, producer))
    monkeypatch.setattr(factory, "load_manifest", lambda *a: (None, None, "fingerprint"))
    monkeypatch.setattr(factory, "validate_authority_key_separation", lambda *a: None)
    monkeypatch.setattr(privacy, "PrivacyRuntime", Privacy)
    monkeypatch.setattr(authority.AuthorityConfig, "from_json", classmethod(lambda cls, raw: SimpleNamespace(public_keys={})))
    monkeypatch.setattr(authority, "AuthorityClient", lambda config: object())
    monkeypatch.setattr(guard, "CoreDeletionGuard", Guard)
    if role == "collector":
        with pytest.raises(ValueError): create_app(role, settings, intake=object())
    app = create_app(role, settings)
    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200
        if role == "collector": assert app.state.intake.subscriptions == ()
        assert State.checks.count("source") == readiness_checks
        State.checks.clear()
        async def tx():
            async with app.state.database.transaction(): State.checks.append("work")
        asyncio.run(tx())
        assert State.checks == ["barrier", "source", "barrier", "work", "barrier", "source", "barrier"]

        released = []
        State.checks.clear()

        async def lose_source_proof_during_work():
            async with app.state.database.transaction():
                State.checks.append("work")
                if interruption == "unavailable":
                    State.available = False
                else:
                    State.source_revision += 1
            released.append(role)

        with pytest.raises(PrivacyUnavailable):
            asyncio.run(lose_source_proof_during_work())
        assert State.checks[-3:] == ["work", "barrier", "source"]
        assert released == []
        assert client.get("/ready").status_code == 503

        State.available = True
        State.applied_revision = State.source_revision
        assert client.get("/ready").status_code == 200
        asyncio.run(tx())

        State.available = False
        assert client.get("/ready").status_code == 503
        with pytest.raises(PrivacyUnavailable): asyncio.run(tx())

        if guarded_probe is not None:
            State.available = True
            State.schema_available = False
            assert client.get("/ready").status_code == 503
