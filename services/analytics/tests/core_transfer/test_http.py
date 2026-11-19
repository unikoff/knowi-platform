import importlib.util
import time
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pymentor_analytics.adapters.auth.verifier import ProducerVerifier
from pymentor_analytics.contracts.core_transfer_v1 import entity_key, fingerprint, validate_event
from pymentor_analytics.modules.analytics.core_transfer.api.router import create_router
from pymentor_analytics.modules.analytics.core_transfer.service import TransferService
from pymentor_analytics.modules.collector.ports import IntakeForbidden

CORE_CONTRACT = Path(__file__).resolve().parents[3] / "core-api/pymentor_core/modules/analytics_delivery/contracts.py"
spec = importlib.util.spec_from_file_location("test_core_transfer_contract", CORE_CONTRACT)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class MemoryStore:
    def __init__(self):
        self.facts, self.entities, self.jobs, self.deleted = {}, {}, set(), set()
        self.lose_reply = False

    async def accept(self, namespace, event, digest, *, deadline):
        if event["subject_ref"] in self.deleted:
            raise IntakeForbidden()
        key = (namespace, event["event_id"])
        if key in self.facts:
            return "accepted" if self.facts[key][1] == digest else "conflict"
        validate_event(event)
        entity = (namespace, event["subject_ref"], entity_key(event), event["revision"])
        if entity in self.entities:
            return "conflict"
        self.facts[key], self.entities[entity] = (event, digest), key
        self.jobs.add(key)
        if self.lose_reply:
            self.lose_reply = False
            raise TimeoutError()
        return "accepted"

    async def receipt(self, namespace, event_id, digest, *, deadline):
        record = self.facts.get((namespace, str(event_id)))
        if not record:
            return "absent"
        if record[0]["subject_ref"] in self.deleted:
            raise IntakeForbidden()
        return "accepted" if record[1] == digest else "conflict"


class Ready:
    async def is_ready(self): return True


@pytest.fixture
def setup():
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    verifier = ProducerVerifier(issuer="test-issuer", public_keys={"key": public}, allowed_producers={"core"})
    store = MemoryStore()
    app = FastAPI()
    app.include_router(create_router(service=TransferService(store), verifier=verifier,
                       namespace_by_producer={"core": "server-core"}, readiness=Ready()))
    now = int(time.time())
    claims = {"grant_version": 1, "iss": "test-issuer", "aud": "analytics-control", "sub": "core",
              "iat": now - 1, "nbf": now - 1, "exp": now + 30, "scope": "control:write"}
    def headers(**changes):
        token = jwt.encode(dict(claims, **changes), key, algorithm="EdDSA", headers={"typ": "JWT", "kid": "key"})
        return {"Authorization": "Bearer " + token}
    return TestClient(app), store, headers


def test_exact_core_wire_and_event_validation():
    subject = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    for event in (core.activity(subject, date(2026, 11, 16), None, revision=0),
                  core.activity(subject, date(2026, 11, 16), datetime(2026, 11, 16, tzinfo=timezone.utc), revision=4),
                  core.progress(subject, "курс", "lesson:1", None, revision=3)):
        assert validate_event(event) == event
        assert fingerprint(event) == core.fingerprint(event)
    with pytest.raises(ValueError): validate_event(core.deletion(subject, revision=2))
    forged = core.activity(subject, date(2026, 11, 16), None, revision=0)
    forged["payload"]["visited_at"] = "2026-11-16T00:00:00Z"
    with pytest.raises(ValueError): validate_event(forged)


def test_signed_receiver_receipt_conflict_and_lost_reply(setup):
    client, store, headers = setup
    subject = uuid4()
    event = core.activity(subject, date(2026, 11, 16), None, revision=0)
    body = {"event": event, "fingerprint": core.fingerprint(event)}
    post = "/internal/core-transfer/v1/events"
    receipt = "/internal/core-transfer/v1/receipts/" + event["event_id"]
    assert client.get(receipt, headers=headers(), params={"fingerprint": body["fingerprint"]}).json() == {"status": "absent"}
    store.lose_reply = True
    assert client.post(post, headers=headers(), json=body).status_code == 503
    assert len(store.jobs) == 1
    assert client.get(receipt, headers=headers(), params={"fingerprint": body["fingerprint"]}).json() == {"status": "accepted"}
    assert client.post(post, headers=headers(), json=body).json() == {"status": "accepted"}
    assert client.get(receipt, headers=headers(), params={"fingerprint": "0" * 64}).json() == {"status": "conflict"}
    changed = core.activity(subject, date(2026, 11, 16), datetime(2026, 11, 16, tzinfo=timezone.utc), revision=0)
    assert client.post(post, headers=headers(), json={"event": changed, "fingerprint": core.fingerprint(changed)}).json() == {"status": "conflict"}
    changed["event_id"] = event["event_id"]
    assert client.post(post, headers=headers(), json={"event": changed, "fingerprint": core.fingerprint(changed)}).json() == {"status": "conflict"}
    assert next(iter(store.facts))[0] == "server-core"
    store.deleted.add(str(subject))
    assert client.post(post, headers=headers(), json=body).status_code == 403
    assert client.get(receipt, headers=headers(), params={"fingerprint": body["fingerprint"]}).status_code == 403


def test_auth_input_and_deletion_closed(setup):
    client, store, headers = setup
    event = core.progress(uuid4(), "backend", "lesson-1", None, revision=2)
    body = {"event": event, "fingerprint": core.fingerprint(event)}
    post = "/internal/core-transfer/v1/events"
    assert client.post(post, json=body).status_code == 401
    assert client.post(post, headers=headers(aud="analytics"), json=body).status_code == 401
    assert client.post(post, headers=headers(sub="unlisted"), json=body).status_code == 401
    assert client.post(post, headers=headers(scope="collect"), json=body).status_code == 401
    assert client.post(post, headers={**headers(), "Content-Type": "application/json"},
                       content=b'{"event":{},"event":{},"fingerprint":"x"}').status_code == 400
    deletion = "/internal/core-transfer/v1/deletions/" + str(uuid4())
    assert client.get(deletion, headers=headers(), params={"subject_ref": event["subject_ref"], "epoch": "1"}).status_code == 503
    assert not store.facts


def test_confirmed_task_wire_is_distinct_from_a_lesson_and_retries_without_double_effect(setup):
    client, store, headers = setup
    subject = uuid4()
    when = datetime(2026, 11, 16, tzinfo=timezone.utc)
    lesson = core.progress(subject, "backend", "shared-id", when, revision=1)
    task = core.task_progress(subject, "backend", "shared-id", "revision-2", when, revision=2)
    assert entity_key(lesson) != entity_key(task)
    assert entity_key(task) == entity_key(core.task_progress(subject, "backend", "shared-id", "revision-3", when, revision=3))
    for event in (lesson, task, task):
        result = client.post("/internal/core-transfer/v1/events", headers=headers(),
                             json={"event": event, "fingerprint": core.fingerprint(event)})
        assert result.status_code == 200 and result.json() == {"status": "accepted"}
    assert len(store.facts) == len(store.jobs) == 2
    task["payload"]["lesson_id"] = "unexpected"
    assert client.post("/internal/core-transfer/v1/events", headers=headers(),
                       json={"event": task, "fingerprint": core.fingerprint(task)}).status_code == 400


def test_standalone_openapi_describes_auth_and_wire(setup):
    client, _, _ = setup
    schema = client.get("/openapi.json").json()
    post = schema["paths"]["/internal/core-transfer/v1/events"]["post"]
    assert post["security"] == [{"CoreProducer": []}]
    assert set(post["requestBody"]["content"]["application/json"]["schema"]["required"]) == {"event", "fingerprint"}
    assert "task_progress" in post["requestBody"]["content"]["application/json"]["schema"]["properties"]["event"]["properties"]["kind"]["enum"]
    receipt = schema["paths"]["/internal/core-transfer/v1/receipts/{event_id}"]["get"]
    assert any(p["name"] == "fingerprint" and p["required"] for p in receipt["parameters"])
    assert schema["components"]["securitySchemes"]["CoreProducer"]["scheme"] == "bearer"


def test_injected_deletion_status_only_receives_server_namespace(setup):
    _, _, headers = setup
    from pymentor_analytics.contracts.auth_v1 import VerifiedProducer
    class Verifier:
        def verify(self, token):
            return VerifiedProducer("core", "control:write", int(time.time()) + 20)
    class Status:
        async def status(self, namespace, control_id, subject_ref, epoch, *, deadline):
            assert namespace == "server-core" and epoch == 3
            assert isinstance(control_id, UUID) and isinstance(subject_ref, UUID)
            return "pending"
    app = FastAPI()
    app.include_router(create_router(service=TransferService(MemoryStore()), verifier=Verifier(),
                       namespace_by_producer={"core": "server-core"}, readiness=Ready(), deletion_status=Status()))
    client = TestClient(app)
    path = "/internal/core-transfer/v1/deletions/" + str(uuid4())
    assert client.get(path, headers=headers(), params={"subject_ref": str(uuid4()), "epoch": "3"}).json() == {"status": "pending"}
    assert client.get(path, headers=headers(), params={"subject_ref": str(uuid4()), "epoch": "03"}).status_code == 400
