"""Signed transport + real PostgreSQL; readiness/catalog/fence are test stubs."""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.factory import create_app
from pymentor_analytics.modules.collector.ports import IntakeUnavailable
from tools.bootstrap_postgres import cleanup, config, connect, create

ROOT = Path(__file__).resolve().parents[2]
ROUTE = "/api/analytics/events/batch"


@pytest.fixture(scope="module")
def target():
    if os.environ.get("ANALYTICS_RUN_SYNTHETIC_INTAKE") != "1":
        pytest.skip("explicit synthetic intake runner required")
    data = config(ROOT / ".local" / "postgres-connection.json")
    name, marker, urls = create(data)
    try:
        upgrade(Settings(database_url=urls["migrate"]), target_revision="a006")
        yield data, name, urls
    finally:
        cleanup(data, name, marker)


def public(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


class Ready:
    async def is_ready(self): return True


class Fence:
    async def permits(self, subject): return True


class Catalog:
    async def known(self, *, course_id, catalog_version, lesson_id, deadline):
        return course_id == "backend" and catalog_version == "catalog-1" and lesson_id is None


def context(url):
    grant_key, producer_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    manifest = {"grant": {"issuer": "synthetic-issuer", "keys": {"g": public(grant_key)}},
                "producer": {"issuer": "synthetic-control", "keys": {"p": public(producer_key)}, "allowlist": ["synthetic-producer"]}}
    subject = uuid4()
    now = int(time.time())
    claims = {"grant_version": 1, "iss": "synthetic-issuer", "aud": "analytics", "sub": str(subject),
              "iat": now - 1, "nbf": now - 1, "exp": now + 299, "role": "user", "scope": "collect",
              "course_ids": ["backend"], "subject_refs": [], "global_read": False}
    token = jwt.encode(claims, grant_key, algorithm="EdDSA", headers={"typ": "JWT", "kid": "g"})
    settings = Settings(database_url=url, trust_manifest_json=json.dumps(manifest), db_timeout_seconds=3)
    return settings, {"Authorization": "Bearer " + token}, subject


def event(offset=timedelta()):
    value = json.loads((ROOT / "contracts/fixtures/browser-batch.json").read_text(encoding="utf-8"))["events"][0]
    stamp = (datetime.now(timezone.utc) + offset).isoformat()
    value.update(event_id=str(uuid4()), started_at=stamp, ended_at=stamp, last_action_at=stamp)
    return value


def counts(data, name, value):
    with connect(data, name) as connection:
        facts = connection.execute("SELECT count(*) FROM analytics.activity_facts WHERE event_id=%s", (value["event_id"],)).fetchone()[0]
        jobs = connection.execute("SELECT count(*) FROM analytics.processing_jobs WHERE event_id=%s", (value["event_id"],)).fetchone()[0]
    return facts, jobs


def test_signed_http_real_storage_and_clock_rejections(target):
    data, name, urls = target
    settings, headers, _ = context(urls["collector"])
    app = create_app("collector", settings, readiness=Ready(), fence=Fence(), catalog=Catalog())
    first, future, late, malformed = event(), event(timedelta(minutes=3)), event(timedelta(days=-8)), event()
    changed = dict(first, session_id=str(uuid4()))
    malformed["event_id"] = "bad-id"
    with TestClient(app) as client:
        response = client.post(ROUTE, headers=headers, json={"events": [first, first, changed, future, late, malformed]})
        assert response.status_code == 200
        assert [(item["status"], item["code"]) for item in response.json()["items"]] == [
            ("accepted", "committed"), ("duplicate", "same_payload"), ("conflict", "payload_conflict"),
            ("rejected", "future_clock"), ("rejected", "too_late"), ("rejected", "invalid")]
    assert counts(data, name, first) == (1, 2)
    assert counts(data, name, future) == (0, 0)
    assert counts(data, name, late) == (0, 0)


def test_signed_http_duplicate_after_eight_days_and_conflict(target, monkeypatch):
    data, name, urls = target
    settings, headers, _ = context(urls["collector"])
    app = create_app("collector", settings, readiness=Ready(), fence=Fence(), catalog=Catalog())
    first = event()
    router = import_module("pymentor_analytics.modules.collector.api.router")
    build = router.build_record
    with TestClient(app) as client:
        assert client.post(ROUTE, headers=headers, json={"events": [first]}).json()["items"][0]["status"] == "accepted"
        monkeypatch.setattr(router, "build_record", lambda identity, value: build(
            identity, value, clock=lambda: datetime.now(timezone.utc) + timedelta(days=8)))
        response = client.post(ROUTE, headers=headers, json={"events": [first, dict(first, session_id=str(uuid4()))]})
        assert response.status_code == 200
        assert [item["status"] for item in response.json()["items"]] == ["duplicate", "conflict"]
    assert counts(data, name, first) == (1, 2)


def test_signed_http_lost_reply_retries_entire_batch(target):
    data, name, urls = target
    settings, headers, _ = context(urls["collector"])
    app = create_app("collector", settings, readiness=Ready(), fence=Fence(), catalog=Catalog())
    first, second = event(), event()
    with TestClient(app) as client:
        attempts = 0
        async def lost_reply():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise IntakeUnavailable("controlled lost reply after commit")
        app.state.intake._after_commit = lost_reply
        response = client.post(ROUTE, headers=headers, json={"events": [first, second]})
        assert response.status_code == 503 and "items" not in response.json()
        assert counts(data, name, first) == (1, 2)
        assert counts(data, name, second) == (0, 0)
        retry = client.post(ROUTE, headers=headers, json={"events": [first, second]})
        assert retry.status_code == 200
        assert [item["status"] for item in retry.json()["items"]] == ["duplicate", "accepted"]
    assert counts(data, name, first) == counts(data, name, second) == (1, 2)


def test_real_factory_default_privacy_is_closed(target):
    data, name, urls = target
    settings, headers, _ = context(urls["collector"])
    app = create_app("collector", settings, fence=Fence(), catalog=Catalog())
    item = event()
    with TestClient(app) as client:
        assert client.post(ROUTE, headers=headers, json={"events": [item]}).status_code == 503
    assert counts(data, name, item) == (0, 0)


def test_expiry_between_items_after_real_commit_is_unknown(target, monkeypatch):
    data, name, urls = target
    settings, headers, _ = context(urls["collector"])
    app = create_app("collector", settings, readiness=Ready(), fence=Fence(), catalog=Catalog())
    first, second = event(), event()
    router = import_module("pymentor_analytics.modules.collector.api.router")
    checks = iter((False, True))
    monkeypatch.setattr(router, "_grant_is_expired", lambda grant: next(checks))
    with TestClient(app) as client:
        response = client.post(ROUTE, headers=headers, json={"events": [first, second]})
        assert response.status_code == 503 and "refresh credential" in response.json()["message"]
        assert counts(data, name, first) == (1, 2)
        assert counts(data, name, second) == (0, 0)
        monkeypatch.setattr(router, "_grant_is_expired", lambda grant: False)
        retry = client.post(ROUTE, headers=headers, json={"events": [first, second]})
        assert [item["status"] for item in retry.json()["items"]] == ["duplicate", "accepted"]
    assert counts(data, name, first) == counts(data, name, second) == (1, 2)


def test_tombstone_between_items_after_real_commit_is_unknown(target):
    data, name, urls = target
    settings, headers, subject = context(urls["collector"])
    app = create_app("collector", settings, readiness=Ready(), fence=Fence(), catalog=Catalog())
    first, second = event(), event()
    with TestClient(app) as client:
        async def inject_fence_after_commit():
            # Controlled sequence after the first commit; not the full privacy protocol.
            with connect(data, name) as conn:
                conn.execute("INSERT INTO analytics.subject_fences(subject_ref,epoch,deleted,control_id) VALUES(%s,1,true,%s) ON CONFLICT(subject_ref) DO NOTHING", (subject, uuid4()))
        app.state.intake._after_commit = inject_fence_after_commit
        response = client.post(ROUTE, headers=headers, json={"events": [first, second]})
        assert response.status_code == 503 and "Outcome unknown" in response.json()["message"]
        assert counts(data, name, first) == (1, 2)
        assert counts(data, name, second) == (0, 0)
        # A fresh request has no partial commit and is denied immediately.
        assert client.post(ROUTE, headers=headers, json={"events": [second]}).status_code == 403
