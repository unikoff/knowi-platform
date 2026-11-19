import json
import time
from contextlib import asynccontextmanager
from datetime import date
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from pymentor_analytics.app.config import Settings, load_settings
from pymentor_analytics.app.factory import create_app

URL = "postgresql://analytics_test_collector@127.0.0.1:57392/analytics_test"
STATUS_URL = "postgresql://analytics_test_transfer_status@127.0.0.1:57392/analytics_test"


def material():
    grant, producer, authority = (Ed25519PrivateKey.generate() for _ in range(3))
    def pem(key): return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    manifest = json.dumps({"grant": {"issuer": "grant-issuer", "keys": {"g": pem(grant)}},
        "producer": {"issuer": "producer-issuer", "keys": {"p": pem(producer)}, "allowlist": ["core"]}})
    authority_config = json.dumps({"url": "http://127.0.0.1:12345", "authority_id": "authority", "issuer": "authority-issuer",
        "public_keys": {"a": pem(authority)}, "reader_secret": "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE", "fixture_mode": True})
    return grant, producer, manifest, authority_config


def token(key, *, role="user", subject=None, producer=False, roster=None):
    now = int(time.time())
    value = {"grant_version": 1, "iss": "producer-issuer" if producer else "grant-issuer",
             "aud": "analytics-control" if producer else "analytics", "sub": "core" if producer else str(subject or uuid4()),
             "iat": now - 1, "nbf": now - 1, "exp": now + 30, "scope": "control:write" if producer else "report:read"}
    if not producer:
        value.update(role=role, course_ids=[] if role == "admin" else ["backend"], subject_refs=[], global_read=role == "admin")
        if roster is not None:
            value["legacy_admin_roster_v1"] = roster
    return {"Authorization": "Bearer " + jwt.encode(value, key, algorithm="EdDSA", headers={"typ": "JWT", "kid": "p" if producer else "g"})}


@pytest.fixture
def mock_runtime(monkeypatch):
    import pymentor_analytics.app.factory as factory
    import pymentor_analytics.app.privacy as privacy
    class Result:
        def __init__(self, value=None): self.value = value
        def one(self): return self.value if isinstance(self.value, tuple) else (FakeDatabase.schema_ready,) * 3
        def scalar_one(self): return self.value
        def scalar_one_or_none(self): return self.value
    class Session:
        async def execute(self, statement, args=None):
            sql = str(statement)
            if "to_regclass('analytics.core_snapshot_proofs')" in sql: return Result((FakeDatabase.proof_ready,) * 4)
            if "to_regclass('analytics.activity_projection_buckets')" in sql: return Result((FakeDatabase.projection_ready,) * 8)
            if "analytics.lock_activity_projection_run" in sql: return Result(FakeDatabase.projection_complete)
            return Result(FakeDatabase.catalog_ready)
    class FakeDatabase:
        schema_ready = True
        catalog_ready = True
        proof_ready = True
        projection_ready = True
        projection_complete = True
        def __init__(self, settings): self.transaction_guard = None
        @asynccontextmanager
        async def transaction(self):
            session = Session()
            if self.transaction_guard: await self.transaction_guard(session)
            yield session
            if self.transaction_guard: await self.transaction_guard(session)
        async def close(self): pass
        def pool_snapshot(self): return None
    class FakePrivacy:
        def __init__(self, client, database, **kwargs): self.client = client
        def start(self): pass
        async def stop(self): pass
        async def is_ready(self): return True
        async def check_in_transaction(self, session): pass
    monkeypatch.setattr(factory, "Database", FakeDatabase)
    monkeypatch.setattr(privacy, "PrivacyRuntime", FakePrivacy)
    return FakeDatabase


def test_role_settings_and_default_off_routes():
    app = create_app("collector", Settings(database_url=URL))
    assert "/internal/core-transfer/v1/events" not in app.openapi()["paths"]
    assert "/internal/analytics/catalog/v1" not in app.openapi()["paths"]
    query = create_app("query", Settings(database_url=URL))
    assert "/internal/analytics/v1/legacy/summary" not in query.openapi()["paths"]
    assert "/internal/analytics/v1/legacy/admin/profile" not in query.openapi()["paths"]
    parsed = load_settings("processor", {"ANALYTICS_PROCESSOR_DATABASE_URL": URL, "ANALYTICS_PROCESSOR_CORE_TRANSFER_ENABLED": "true"})
    assert parsed.core_transfer_enabled is True
    with pytest.raises(ValueError): create_app("processor", parsed)


def test_projection_query_factory_requires_complete_generation(mock_runtime):
    from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport
    _, _, manifest, authority = material()
    run_id = uuid4()
    settings = Settings(database_url=URL, trust_manifest_json=manifest, privacy_authority_json=authority,
                        activity_projection_enabled=True, activity_projection_run_id=run_id)
    app = create_app("query", settings)
    with TestClient(app) as client:
        assert isinstance(app.state.activity_reports, PostgresProjectionActivityReport)
        assert client.get("/ready").status_code == 200
        mock_runtime.projection_complete = False
        assert client.get("/ready").status_code == 503
        mock_runtime.projection_complete = True
        mock_runtime.projection_ready = False
        assert client.get("/ready").status_code == 503
    with pytest.raises(ValueError):
        create_app("query", settings, activity_reports=object())
    with pytest.raises(ValueError):
        create_app("query", Settings(database_url=URL, activity_projection_enabled=True))
    parsed = load_settings("processor", {"ANALYTICS_PROCESSOR_DATABASE_URL": URL,
        "ANALYTICS_PROCESSOR_ACTIVITY_PROJECTION_ENABLED": "true", "ANALYTICS_ACTIVITY_PROJECTION_RUN_ID": str(run_id)})
    assert parsed.activity_projection_enabled and parsed.activity_projection_run_id == run_id
    with pytest.raises(ValueError):
        load_settings("query", {"ANALYTICS_QUERY_DATABASE_URL": URL, "ANALYTICS_QUERY_ACTIVITY_PROJECTION_RUN_ID": str(run_id)})


def test_collector_factory_producer_auth_and_a007_fail_closed(mock_runtime):
    _, producer, manifest, authority = material()
    settings = Settings(database_url=URL, trust_manifest_json=manifest, privacy_authority_json=authority,
        core_transfer_enabled=True, core_transfer_namespaces_json=json.dumps({"core": "core-v1"}),
        core_transfer_status_database_url=STATUS_URL)
    app = create_app("collector", settings)
    class Store:
        async def accept(self, namespace, event, digest, *, deadline):
            assert namespace == "core-v1"
            return "accepted"
        async def receipt(self, namespace, event_id, digest, *, deadline): return "absent"
    class Deletion:
        async def status(self, namespace, control, subject, epoch, *, deadline): return "pending"
    with TestClient(app) as client:
        assert app.state.core_transfer_deletion_status.client is not app.state.privacy_runtime.client
        app.state.core_transfer_service, app.state.core_transfer_deletion_status = Store(), Deletion()
        path = "/internal/core-transfer/v1/events"
        assert client.post(path, json={"event": {}, "fingerprint": "x"}).status_code == 401
        headers = token(producer, producer=True)
        mock_runtime.schema_ready = False
        assert client.post(path, headers=headers, json={"event": {}, "fingerprint": "x"}).status_code == 503
        assert client.get("/ready").status_code == 503
        mock_runtime.schema_ready = True
        assert client.post(path, headers=headers, json={"event": {}, "fingerprint": "x"}).json() == {"status": "accepted"}
        response = client.get("/internal/core-transfer/v1/deletions/" + str(uuid4()), headers=headers,
                              params={"subject_ref": str(uuid4()), "epoch": 1})
        assert response.json() == {"status": "pending"}


def test_query_bridge_self_admin_roster_and_scope(mock_runtime):
    grant, producer, manifest, authority = material()
    subject, admin = uuid4(), uuid4()
    roster = {"complete": True, "expires_at": int(time.time()) + 60, "subjects": [str(subject)], "students": [str(subject)]}
    app = create_app("query", Settings(database_url=URL, trust_manifest_json=manifest, privacy_authority_json=authority,
        core_transfer_enabled=True, core_transfer_namespace="core-v1", core_transfer_roster_json=json.dumps(roster)))
    class Store:
        def __init__(self, db): self.database = db
        async def read_subject(self, namespace, ref, today, *, deadline, visit_through=None):
            assert ref == subject and namespace == "core-v1"
            return ([{"activity_date": today.isoformat(), "visited_at": None}], [])
        async def read_admin(self, namespace, subjects, today, *, deadline, actor=None):
            assert actor == admin and subjects == (subject,)
            return [{"subject_ref": str(subject), "activity_date": today.isoformat()}]
    with TestClient(app) as client:
        app.state.legacy_service.store = Store(app.state.database)
        user, administrator = token(grant, subject=subject), token(grant, role="admin", subject=admin)
        today = date.today()
        result = client.get("/internal/analytics/v1/legacy/summary", headers=user)
        assert result.status_code == 200 and result.json()["coverage"] == "unknown" and result.json()["data"]["total_active_days"] == 1
        assert client.get("/internal/analytics/v1/legacy/summary", headers=token(producer, producer=True)).status_code == 401
        assert client.get("/internal/analytics/v1/legacy/summary", headers=user, params={"subject_ref": str(uuid4())}).status_code == 400
        assert client.get("/internal/analytics/v1/legacy/admin/activity", headers=user).status_code == 403
        assert client.get("/internal/analytics/v1/legacy/admin/activity", headers=administrator).json()["data"]["legacy_dashboard_active_students_7d"] == 1
        data = client.get("/internal/analytics/v1/legacy/admin/activity", headers=administrator).json()["data"]["dashboard_activity"]
        assert data["active_students_7d"] == 1 and data["inactive_students_7d"] == 0 and len(data["daily_active_students"]) == 30
        app.state.legacy_service.roster["expires_at"] = 1
        assert client.get("/internal/analytics/v1/legacy/admin/activity", headers=administrator).status_code == 503
        assert client.get("/internal/analytics/v1/legacy/calendar", headers=user, params={"year": today.year, "month": today.month}).status_code == 200


def test_catalog_factory_separate_flag_shared_p1_and_a008_gate(mock_runtime):
    _, producer, manifest, authority = material()
    settings = Settings(database_url=URL, trust_manifest_json=manifest, privacy_authority_json=authority,
        catalog_enabled=True, catalog_namespace="core-v1", core_transfer_namespaces_json=json.dumps({"core": "core-v1"}))
    app = create_app("collector", settings)
    class Catalog:
        async def accept(self, namespace, message, digest, *, deadline):
            assert namespace == "core-v1"
            return "accepted"
        async def receipt(self, namespace, course_id, version, digest, *, deadline): return "absent"
    with TestClient(app) as client:
        assert app.state.catalog_service.store is app.state.catalog
        app.state.catalog_service = Catalog()
        path, headers = "/internal/analytics/catalog/v1", token(producer, producer=True)
        assert client.post(path, json={"message": {}, "fingerprint": "x"}).status_code == 401
        mock_runtime.catalog_ready = False
        assert client.post(path, headers=headers, json={"message": {}, "fingerprint": "x"}).status_code == 503
        assert client.get("/ready").status_code == 503
        mock_runtime.catalog_ready = True
        assert client.post(path, headers=headers, json={"message": {}, "fingerprint": "x"}).json() == {"status": "accepted"}
        assert client.get("/internal/core-transfer/v1/events", headers=headers).status_code == 404


def test_proof_routes_default_off_and_require_a009(mock_runtime):
    _, producer, manifest, authority = material()
    kwargs = dict(database_url=URL, trust_manifest_json=manifest, privacy_authority_json=authority,
        core_transfer_enabled=True, core_transfer_namespaces_json=json.dumps({"core": "core-v1"}),
        core_transfer_status_database_url=STATUS_URL)
    assert "/internal/core-transfer/v1/proofs" not in create_app("collector", Settings(**kwargs)).openapi()["paths"]
    app = create_app("collector", Settings(**kwargs, core_transfer_proof_enabled=True))
    class Proofs:
        async def accept(self, namespace, proof, digest, *, deadline):
            assert namespace == "core-v1"
            return "accepted"
        async def receipt(self, namespace, proof_id, digest, *, deadline):
            return {"status": "accepted", "coverage": "pending"}
    with TestClient(app) as client:
        app.state.core_snapshot_proof = Proofs()
        headers = token(producer, producer=True)
        mock_runtime.proof_ready = False
        assert client.post("/internal/core-transfer/v1/proofs", headers=headers, json={"proof": {}, "fingerprint": "x"}).status_code == 503
        mock_runtime.proof_ready = True
        assert client.post("/internal/core-transfer/v1/proofs", headers=headers, json={"proof": {}, "fingerprint": "x"}).json() == {"status": "accepted"}
        response = client.get("/internal/core-transfer/v1/proofs/" + str(uuid4()), headers=headers, params={"fingerprint": "a" * 64})
        assert response.json() == {"status": "accepted", "coverage": "pending"}


def test_complete_bridge_requires_g1_signed_request_roster_and_proof(mock_runtime):
    grant, producer, manifest, authority = material()
    subject, admin = uuid4(), uuid4()
    app = create_app("query", Settings(database_url=URL, trust_manifest_json=manifest, privacy_authority_json=authority,
        core_transfer_enabled=True, core_transfer_proof_enabled=True, core_transfer_namespace="core-v1"))
    class Store:
        def __init__(self, database): self.database, self.ready = database, True
        async def read_subject(self, namespace, ref, today, *, deadline, require_coverage=False, visit_through=None):
            from pymentor_analytics.modules.collector.ports import IntakeUnavailable
            if not self.ready: raise IntakeUnavailable()
            assert require_coverage and ref == subject
            return ([{"activity_date": today.isoformat(), "visited_at": None}], [],
                    {"subject_ref": str(subject), "coverage_through_revision": 3, "source_snapshot_at": "2026-11-17T00:00:00+00:00"})
        async def read_admin(self, namespace, subjects, today, *, deadline, actor=None, required_revisions=None):
            assert actor == admin and required_revisions == {subject: 3, admin: 2}
            return ([{"subject_ref": str(subject), "activity_date": today.isoformat()}],
                    [{"subject_ref": str(subject), "coverage_through_revision": 3, "source_snapshot_at": "2026-11-17T00:00:00+00:00"},
                     {"subject_ref": str(admin), "coverage_through_revision": 2, "source_snapshot_at": "2026-11-17T00:00:00+00:00"}])
    with TestClient(app) as client:
        store = Store(app.state.database)
        app.state.legacy_service.store = store
        user, administrator = token(grant, subject=subject), token(grant, role="admin", subject=admin)
        response = client.get("/internal/analytics/v1/legacy/summary", headers=user)
        assert response.status_code == 200 and response.json()["coverage"] == "complete"
        assert response.json()["coverage_through_revision"] == 3 and response.json()["source_hwm"] is None
        assert client.get("/internal/analytics/v1/legacy/summary", headers=token(grant, role="leader", subject=subject)).status_code == 200
        assert client.get("/internal/analytics/v1/legacy/summary", headers=token(grant, role="admin", subject=subject)).status_code == 200
        store.ready = False
        assert client.get("/internal/analytics/v1/legacy/summary", headers=user).status_code == 503
        assert client.get("/internal/analytics/v1/legacy/admin/activity", headers=administrator).status_code == 503
        claim = {"version": 1, "members": [{"subject_ref": str(subject), "revision": 3, "student": True},
                                           {"subject_ref": str(admin), "revision": 2, "student": False}]}
        administrator = token(grant, role="admin", subject=admin, roster=claim)
        data = client.get("/internal/analytics/v1/legacy/admin/activity", headers=administrator).json()
        assert data["coverage"] == "complete" and data["coverage_subjects"][0]["student"] is True
        assert data["coverage_subjects"][0]["coverage_through_revision"] == 3
        incomplete = {"version": 1, "members": claim["members"][:1]}
        assert client.get("/internal/analytics/v1/legacy/admin/activity", headers=token(grant, role="admin", subject=admin, roster=incomplete)).status_code == 503
        assert client.get("/internal/analytics/v1/legacy/admin/activity", headers=token(producer, producer=True)).status_code == 401
        duplicate = {"version": 1, "members": claim["members"] * 2}
        assert client.get("/internal/analytics/v1/legacy/admin/activity", headers=token(grant, role="admin", subject=admin, roster=duplicate)).status_code == 401
        too_many = {"version": 1, "members": [{"subject_ref": str(uuid4()), "revision": 0, "student": True} for _ in range(81)]}
        assert client.get("/internal/analytics/v1/legacy/admin/activity", headers=token(grant, role="admin", subject=admin, roster=too_many)).status_code == 401
        assert client.get("/internal/analytics/v1/legacy/summary", headers=token(grant, subject=subject, roster=claim)).status_code == 401
