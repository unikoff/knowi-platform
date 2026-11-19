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


@pytest.mark.parametrize("role", ["collector", "query"])
def test_local_reader_checks_source_before_and_after_transaction_and_readiness(monkeypatch, role):
    import pymentor_analytics.app.factory as factory
    import pymentor_analytics.app.privacy as privacy
    import pymentor_analytics.adapters.privacy_authority.client as authority
    import pymentor_analytics.adapters.core_privacy.client as source
    import pymentor_analytics.adapters.core_privacy.guard as guard
    name = "analytics_live_" + uuid4().hex
    data = {"url": "http://127.0.0.1:18041/api/internal/analytics/deletions", "instance_id": str(uuid4()),
            "secret": base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode()}
    settings = Settings(database_url=f"postgresql://{name}_{role}@127.0.0.1:57392/{name}",
        real_collection=True, issuer_mode="local", trust_manifest_json="{}",
        privacy_authority_json='{"fixture_mode":false,"runtime_mode":"local"}', core_deletion_source_json=json.dumps(data),
        core_deletion_status_database_url=f"postgresql://{name}_transfer_status@127.0.0.1:57392/{name}",
        **({"activity_projection_enabled": True, "activity_day_query_enabled": True, "activity_projection_run_id": uuid4()} if role == "query" else {}))
    class State:
        checks = []
        available = True
    class Result:
        def __init__(self, count): self.count = count
        def one(self): return (True,) * self.count
        def scalar_one(self): return True
        def scalar_one_or_none(self): return True
    class Session:
        async def execute(self, sql, params=None):
            return Result(8 if "to_regclass('analytics.activity_projection_buckets')" in str(sql) else 3)
    class DB:
        def __init__(self, settings): self.transaction_guard = None; self.transaction_exit_guard = None
        @asynccontextmanager
        async def transaction(self):
            session = Session()
            if self.transaction_guard: await self.transaction_guard(session)
            yield session
            if self.transaction_exit_guard: await self.transaction_exit_guard(session)
        async def close(self): pass
        def pool_snapshot(self): return None
    class Privacy:
        def __init__(self, *args, **kwargs): pass
        def start(self): pass
        async def stop(self): pass
        async def is_ready(self): return True
        async def check_in_transaction(self, session): State.checks.append("barrier")
    class Guard:
        def __init__(self, *args): pass
        async def check(self, session):
            State.checks.append("source")
            if not State.available: raise PrivacyUnavailable()
    monkeypatch.setattr(factory, "Database", DB)
    monkeypatch.setattr(factory, "load_verifiers", lambda *a, **k: (None, None))
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
        assert State.checks.count("source") >= 2
        State.checks.clear()
        async def tx():
            async with app.state.database.transaction(): State.checks.append("work")
        asyncio.run(tx())
        assert State.checks == ["barrier", "source", "barrier", "work", "barrier", "source", "barrier"]
        State.available = False
        assert client.get("/ready").status_code == 503
        with pytest.raises(PrivacyUnavailable): asyncio.run(tx())
