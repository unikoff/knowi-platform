import asyncio
import json
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from pymentor_analytics.app.config import Settings, load_settings
from pymentor_analytics.app.factory import create_app
from pymentor_analytics.contracts.v2 import MAX_BATCH_BYTES, canonical_hash
from pymentor_analytics.modules.collector.ports import IntakeForbidden, IntakeOverloaded

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/browser-batch.json").read_text(encoding="utf-8"))["events"][0]
SUBJECT = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
URL = "postgresql://analytics_user@localhost:57392/analytics_test"


def pem(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


@pytest.fixture
def context():
    grant, producer = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    manifest = {"grant": {"issuer": "synthetic-issuer", "keys": {"g": pem(grant)}},
                "producer": {"issuer": "synthetic-control", "keys": {"p": pem(producer)}, "allowlist": ["synthetic-producer"]}}
    return grant, producer, manifest


def settings(manifest, **kwargs):
    return Settings(database_url=URL, trust_manifest_json=json.dumps(manifest), **kwargs)


def token(key, *, scope="collect", role="user", course_ids=None):
    now = int(time.time())
    claims = {"grant_version": 1, "iss": "synthetic-issuer", "aud": "analytics", "sub": str(SUBJECT),
              "iat": now - 1, "nbf": now - 1, "exp": now + (299 if scope == "collect" else 59),
              "role": role, "scope": scope, "course_ids": course_ids or ["backend"], "subject_refs": [], "global_read": False}
    if role == "admin":
        claims.update(course_ids=[], global_read=True)
    return jwt.encode(claims, key, algorithm="EdDSA", headers={"typ": "JWT", "kid": "g"})


def auth(key, **kwargs): return {"Authorization": "Bearer " + token(key, **kwargs)}


class Fence:
    def __init__(self, allowed=True): self.allowed = allowed
    async def permits(self, subject): return self.allowed


class Catalog:
    async def known(self, *, course_id, catalog_version, lesson_id, deadline):
        return course_id == "backend" and catalog_version == "catalog-1" and lesson_id in (None, "lesson-1")


class Ready:
    async def is_ready(self): return True


class MemoryIntake:
    """Test double: NOT a transactional durable store."""
    def __init__(self): self.records = {}; self.deadlines = []
    async def accept(self, record, *, deadline):
        self.deadlines.append(deadline)
        key = (record.producer_namespace, record.event.event_id)
        prior = self.records.get(key)
        digest = canonical_hash(record)
        if prior is not None: return "duplicate" if prior == digest else "conflict"
        self.records[key] = digest
        return "accepted"


def app_for(context, *, intake=None, catalog=None, fence=None, **kwargs):
    return create_app("collector", settings(context[2], **kwargs), readiness=Ready(), intake=intake, catalog=catalog, fence=fence)


def event(**changes): return {**FIXTURE, **changes}


def test_expiry_after_commit_reports_unknown_and_retry_preserves_ids(context, monkeypatch):
    from pymentor_analytics.modules.collector.api import router as route_module
    store = MemoryIntake()
    app = app_for(context, intake=store, catalog=Catalog(), fence=Fence())
    batch = {"events": [event(), event(event_id=str(uuid4()))]}
    checks = iter((False, True))
    monkeypatch.setattr(route_module, "_grant_is_expired", lambda grant: next(checks))
    with TestClient(app) as client:
        response = client.post("/api/analytics/events/batch", headers=auth(context[0]), json=batch)
        assert response.status_code == 503
        assert "Outcome unknown" in response.json()["message"]
        assert "refresh credential" in response.json()["message"]
        assert len(store.records) == 1
        monkeypatch.setattr(route_module, "_grant_is_expired", lambda grant: False)
        retry = client.post("/api/analytics/events/batch", headers=auth(context[0]), json=batch)
        assert [item["status"] for item in retry.json()["items"]] == ["duplicate", "accepted"]
        assert len(store.records) == 2


@pytest.mark.parametrize("deny_first, expected", [(False, 503), (True, 403)])
def test_fence_denial_after_possible_commit_is_explicit(context, deny_first, expected):
    class DeniedIntake(MemoryIntake):
        async def accept(self, record, *, deadline):
            if deny_first or self.records:
                raise IntakeForbidden()
            return await super().accept(record, deadline=deadline)
    store = DeniedIntake()
    app = app_for(context, intake=store, catalog=Catalog(), fence=Fence())
    with TestClient(app) as client:
        response = client.post("/api/analytics/events/batch", headers=auth(context[0]),
                               json={"events": [event(), event(event_id=str(uuid4()))]})
        assert response.status_code == expected
        assert len(store.records) == (0 if deny_first else 1)
        if not deny_first:
            assert "Outcome unknown" in response.json()["message"]
            assert "same event IDs" in response.json()["message"]


def test_partial_batch_accepted_duplicate_conflict_invalid_unknown(context):
    store = MemoryIntake()
    app = app_for(context, intake=store, catalog=Catalog(), fence=Fence())
    first = event()
    changed = event(catalog_version="catalog-1", session_id=str(uuid4()))
    unknown = event(event_id=str(uuid4()), catalog_version="unknown")
    invalid = event(event_id="not-uuid", source="core")
    unsupported = event(event_id=str(uuid4()), schema_version=3)
    with TestClient(app) as client:
        response = client.post("/api/analytics/events/batch", headers=auth(context[0]), json={"events": [first, first, changed, unknown, invalid, unsupported]})
        assert response.status_code == 200
        body = response.json()["items"]
        assert [(x["status"], x["code"]) for x in body] == [("accepted", "committed"), ("duplicate", "same_payload"), ("conflict", "payload_conflict"), ("pending", "unknown_catalog"), ("rejected", "invalid"), ("rejected", "unsupported_version")]
        assert [x["index"] for x in body] == list(range(6)) and body[4]["event_id"] is None
        assert len(store.records) == 1 and all(d > 0 for d in store.deadlines)


def test_auth_forbidden_scope_forgery_and_closed_default(context):
    store = MemoryIntake()
    app = app_for(context, intake=store, catalog=Catalog(), fence=Fence())
    with TestClient(app) as client:
        path = "/api/analytics/events/batch"
        assert client.post(path, json={"events": [event()]}).status_code == 401
        assert client.post(path, headers={"Authorization": "Bearer invalid"}, json={"events": [event()]}).headers["www-authenticate"] == "Bearer"
        assert client.post(path, headers=auth(context[0], scope="report:read"), json={"events": [event()]}).status_code == 403
        for role in ("leader", "admin"):
            assert client.post(path, headers=auth(context[0], scope="report:read", role=role), json={"events": [event()]}).status_code == 403
        credential = "Bearer " + token(context[0])
        assert client.post(path, headers=[("Authorization", credential), ("authorization", credential)], json={"events": [event()]}).status_code == 401
        good, out_of_scope = event(), event(event_id=str(uuid4()), course_id="other")
        assert client.post(path, headers=auth(context[0]), json={"events": [good, out_of_scope]}).status_code == 403
        assert not store.records
        for attr in ("subject_ref", "source", "role"):
            response = client.post(path, headers=auth(context[0]), json={"events": [event(**{attr: "core"})]})
            assert response.status_code == 200 and response.json()["items"][0]["code"] == "invalid"
        assert client.post("/api/analytics/control", headers=auth(context[0]), json={}).status_code == 404
    missing = create_app("collector", Settings(database_url=URL))
    with TestClient(missing) as client:
        assert client.post("/api/analytics/events/batch").status_code == 503
    unavailable = app_for(context, catalog=Catalog(), fence=Fence())
    with TestClient(unavailable) as client:
        assert client.post("/api/analytics/events/batch", headers=auth(context[0]), json={"events": [event()]}).status_code == 503
    closed = create_app("collector", settings(context[2]), intake=store, catalog=Catalog(), fence=Fence())
    with TestClient(closed) as client:
        assert client.post("/api/analytics/events/batch", headers=auth(context[0]), json={"events": [event()]}).status_code == 503
    deleted = app_for(context, intake=store, catalog=Catalog(), fence=Fence(False))
    with TestClient(deleted) as client:
        assert client.post("/api/analytics/events/batch", headers=auth(context[0]), json={"events": [event()]}).status_code == 403


def test_wire_errors_and_partial_oversize(context):
    app = app_for(context, intake=MemoryIntake(), catalog=Catalog(), fence=Fence())
    header = {**auth(context[0]), "Content-Type": "application/json"}
    with TestClient(app) as client:
        path = "/api/analytics/events/batch"
        for content in (b'\xff', b'{', b'{"events":[]}', b'{"events":[],"events":[]}', b'{"events":[' + b'0,' * 101 + b'0]}'):
            assert client.post(path, headers=header, content=content).status_code == 400
        assert client.post(path, headers=header, content=b' ' * (MAX_BATCH_BYTES + 1)).status_code == 413
        assert client.post(path, headers=auth(context[0]), content=b'{}').status_code == 400
        large = b'{' + b' ' * 4097 + json.dumps(event()).encode()[1:]
        mixed = b'{"events":[' + large + b',' + json.dumps(event()).encode() + b']}'
        result = client.post(path, headers=header, content=mixed)
        assert result.status_code == 200 and [x["code"] for x in result.json()["items"]] == ["too_large", "committed"]


def test_trust_manifest_consistency_and_no_independent_rings(context):
    _, _, manifest = context
    env = {"ANALYTICS_COLLECTOR_DATABASE_URL": URL, "ANALYTICS_QUERY_DATABASE_URL": URL, "ANALYTICS_TRUST_MANIFEST_JSON": json.dumps(manifest)}
    collector = create_app("collector", load_settings("collector", env))
    query = create_app("query", load_settings("query", env))
    with TestClient(collector), TestClient(query):
        assert collector.state.trust_manifest_fingerprint == query.state.trust_manifest_fingerprint
        assert query.state.grant_verifier is not None and query.state.producer_verifier is None
    other = json.loads(json.dumps(manifest)); other["producer"]["keys"] = {"bad": pem(context[0])}
    with pytest.raises(ValueError, match="invalid authentication configuration"):
        with TestClient(app_for((context[0], context[1], other))): pass
    with pytest.raises(ValueError, match="invalid authentication configuration"):
        with TestClient(create_app("collector", settings(manifest, grant_issuer="synthetic-issuer"))): pass
    with pytest.raises(ValueError):
        load_settings("query", {**env, "ANALYTICS_QUERY_TRUST_MANIFEST_JSON": json.dumps(manifest)})


def test_timeout_retry_same_id_and_concurrency_limit(context):
    class SlowStore(MemoryIntake):
        def __init__(self): super().__init__(); self.started = asyncio.Event(); self.block = asyncio.Event()
        async def accept(self, record, *, deadline):
            result = await super().accept(record, deadline=deadline)
            self.started.set()
            await self.block.wait()
            return result
    async def scenario():
        store = SlowStore()
        app = app_for(context, intake=store, catalog=Catalog(), fence=Fence(), concurrency=1, db_timeout_seconds=0.15)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                first = asyncio.create_task(client.post("/api/analytics/events/batch", headers=auth(context[0]), json={"events": [event()]}))
                await store.started.wait()
                limited = await client.post("/api/analytics/events/batch", headers=auth(context[0]), json={"events": [event()]})
                assert limited.status_code == 429 and limited.headers["retry-after"] == "1"
                assert (await first).status_code == 503
                store.block.set()
                retry = await client.post("/api/analytics/events/batch", headers=auth(context[0]), json={"events": [event()]})
                assert retry.status_code == 200 and retry.json()["items"][0]["status"] == "duplicate"
    asyncio.run(scenario())


def test_partial_commit_error_never_fabricates_ack(context):
    class FailingStore(MemoryIntake):
        async def accept(self, record, *, deadline):
            if self.records:
                raise RuntimeError("sensitive internal error")
            return await super().accept(record, deadline=deadline)
    store = FailingStore()
    app = app_for(context, intake=store, catalog=Catalog(), fence=Fence())
    with TestClient(app) as client:
        second = event(event_id=str(uuid4()))
        response = client.post("/api/analytics/events/batch", headers=auth(context[0]), json={"events": [event(), second]})
        assert response.status_code == 503 and "sensitive" not in response.text and "items" not in response.json()
        assert "same event IDs" in response.json()["message"] and len(store.records) == 1


def test_storage_backpressure_header(context):
    class Overloaded:
        async def accept(self, record, *, deadline): raise IntakeOverloaded(3)
    app = app_for(context, intake=Overloaded(), catalog=Catalog(), fence=Fence())
    with TestClient(app) as client:
        response = client.post("/api/analytics/events/batch", headers=auth(context[0]), json={"events": [event()]})
        assert response.status_code == 429 and response.headers["retry-after"] == "3"


def test_asgi_chunked_body_is_limited_before_full_read(context):
    async def scenario():
        store = MemoryIntake()
        app = app_for(context, intake=store, catalog=Catalog(), fence=Fence())
        supplied = [b'{"events":[', b' ' * MAX_BATCH_BYTES, b'{}]}']
        received = []; sent = []
        async def receive():
            part = supplied[len(received)]
            received.append(len(part))
            return {"type": "http.request", "body": part, "more_body": len(received) < len(supplied)}
        async def send(message): sent.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                 "method": "POST", "scheme": "http", "path": "/api/analytics/events/batch",
                 "raw_path": b"/api/analytics/events/batch", "query_string": b"", "root_path": "",
                 "headers": [(b"content-type", b"application/json"), (b"authorization", ("Bearer " + token(context[0])).encode())],
                 "client": ("127.0.0.1", 1), "server": ("localhost", 80), "http_version": "1.1"}
        async with app.router.lifespan_context(app):
            await app(scope, receive, send)
        assert len(received) == 2 and sent[0]["status"] == 413 and not store.records
    asyncio.run(scenario())


def test_live_openapi_matches_actual_collector_response_contract(context):
    app = app_for(context)
    schema = app.openapi()
    post = schema["paths"]["/api/analytics/events/batch"]["post"]
    assert post["security"] == [{"AnalyticsGrant": []}]
    assert set(("200", "400", "401", "403", "413", "429", "503")) <= set(post["responses"])
    assert post["requestBody"]["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/WireBatch"
