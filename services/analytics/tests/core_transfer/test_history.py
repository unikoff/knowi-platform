import time
import asyncio
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from pymentor_analytics.contracts.auth_v1 import VerifiedGrant
from pymentor_analytics.adapters.postgres.core_transfer import PostgresCoreTransfer
from pymentor_analytics.modules.analytics.core_transfer.api.legacy_router import router
from pymentor_analytics.modules.analytics.core_transfer.history import conditional_history_days
from pymentor_analytics.modules.analytics.core_transfer.legacy_service import LegacyService
from pymentor_analytics.modules.collector.ports import IntakeForbidden, IntakeUnavailable


def test_conditional_values_are_per_unique_date_and_not_measurements():
    days = conditional_history_days(["2026-11-17", "2026-11-16", "2026-11-17"])
    assert days == [{"date": d, "theory_ms": 3600000, "practice_ms": 3600000,
                     "provenance": "core_legacy_conditional"} for d in ("2026-11-16", "2026-11-17")]


def test_store_rejects_unbounded_range_before_database_access():
    import pytest
    store = PostgresCoreTransfer(SimpleNamespace(transaction_guard=True))
    with pytest.raises(ValueError):
        asyncio.run(store.read_history("core", uuid4(), date(2026, 11, 1), date(2026, 12, 2), deadline=time.monotonic() + 1))


def test_history_transport_scope_range_and_fail_closed():
    subject = uuid4()
    class Store:
        database = SimpleNamespace(transaction_guard=True)
        failure = None
        async def read_history(self, namespace, target, start, end, *, deadline):
            assert namespace == "core" and target == subject and start == end == date(2026, 11, 17)
            if self.failure:
                raise self.failure()
            return {"days": conditional_history_days(["2026-11-17"]), "coverage": {
                "subject_ref": str(subject), "coverage_through_revision": 2,
                "source_snapshot_at": "2026-11-17T00:00:00+00:00"}}
    class Verifier:
        grant = VerifiedGrant(subject, "user", "report:read", ("backend",), (), False, int(time.time()) + 60)
        def verify(self, token): return self.grant
    class Ready:
        async def is_ready(self): return True
    store, verifier = Store(), Verifier()
    app = FastAPI()
    app.include_router(router)
    app.state.trust_manifest_validated = True
    app.state.grant_verifier = verifier
    app.state.settings = SimpleNamespace(db_timeout_seconds=3)
    app.state.core_transfer_probe = Ready()
    app.state.legacy_service = LegacyService(store, "core", None)
    client = TestClient(app)
    path = "/internal/analytics/v1/legacy/history"
    headers = {"Authorization": "Bearer test"}
    params = {"start": "2026-11-17", "end": "2026-11-17"}
    result = client.get(path, params=params, headers=headers)
    assert result.status_code == 200 and result.json()["days"][0]["provenance"] == "core_legacy_conditional"
    assert client.get(path, params={**params, "end": "2026-12-18"}, headers=headers).status_code == 400
    assert client.get(path, params={**params, "subject_ref": str(subject)}, headers=headers).status_code == 400
    verifier.grant = VerifiedGrant(subject, "leader", "report:read", ("backend",), (), False, int(time.time()) + 60)
    assert client.get(path, params=params, headers=headers).status_code == 403
    verifier.grant = VerifiedGrant(subject, "user", "report:read", ("backend",), (), False, int(time.time()) + 60)
    for failure, status in ((IntakeForbidden, 403), (IntakeUnavailable, 503)):
        store.failure = failure
        assert client.get(path, params=params, headers=headers).status_code == status
    schema = client.get("/openapi.json").json()
    endpoint = schema["paths"][path]["get"]
    assert endpoint["security"] == [{"AnalyticsGrant": []}]
    assert {p["name"] for p in endpoint["parameters"]} == {"start", "end"}
    assert set(schema["components"]["schemas"]["HistoryResponse"]["required"]) == {"days", "coverage"}
