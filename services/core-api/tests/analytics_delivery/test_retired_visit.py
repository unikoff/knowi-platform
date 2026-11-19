from datetime import datetime, timezone
import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://synthetic@127.0.0.1:1/isolated")

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from pymentor_core.modules.identity.api import router


@pytest.mark.parametrize("async_identity", [False, True])
@pytest.mark.parametrize("retired", [False, True])
def test_visit_retirement_preserves_auth_and_response(monkeypatch, async_identity, retired):
    app = FastAPI()
    app.include_router(router.user_router, prefix="/api/user")
    app.state.analytics_retire_legacy_visits = retired
    clock = SimpleNamespace(now=lambda: datetime(2026, 11, 17, 22, 30, tzinfo=timezone.utc))
    app.state.composition_root = SimpleNamespace(for_request=lambda db: SimpleNamespace(clock=clock, uow=object()))
    monkeypatch.setattr(router, "_local_async_identity", lambda request: object() if async_identity else None)

    def authenticate(request, db):
        if request.headers.get("X-Test-Auth") != "yes":
            raise HTTPException(status_code=401)
        return SimpleNamespace(id=42)

    async def principal(request):
        return SimpleNamespace(user_id=authenticate(request, None).id, account_ref=None)

    async def run_sql(request, identity, operation):
        return operation(object())

    calls = []
    def write(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(activity_date=kwargs["activity_date"], visited_at=kwargs["visited_at"])

    monkeypatch.setattr(router.service, "get_current_user", authenticate)
    monkeypatch.setattr(router, "async_current_principal", principal)
    monkeypatch.setattr(router, "_run_identity_sql", run_sql)
    monkeypatch.setattr(router.activity, "record_activity_day", write)
    monkeypatch.setenv("PYMENTOR_ANALYTICS_DELIVERY_ENABLED", "0")
    client = TestClient(app)

    assert client.post("/api/user/activity/visit", json={}).status_code == 401
    assert calls == []
    for payload, expected_date in [({}, "2026-11-18"), ({"activity_date": "2026-10-01"}, "2026-10-01")]:
        result = client.post("/api/user/activity/visit", json=payload, headers={"X-Test-Auth": "yes"})
        assert result.status_code == 200
        assert result.json()["activity_date"] == expected_date
        if retired:
            assert result.json()["visited_at"] is None
            assert result.headers["X-Analytics-Legacy-Visit"] == "retired"
            assert calls == []
        else:
            assert result.json()["visited_at"] is not None
            assert "X-Analytics-Legacy-Visit" not in result.headers
    assert len(calls) == (0 if retired else 2)
