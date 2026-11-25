"""Isolated production catalog primitives; no network or real course data."""
import asyncio
import json
import ssl
import sys
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pymentor_core.app import analytics_catalog as catalog
from pymentor_core.adapters.analytics_catalog import publication


@pytest.fixture
def receipt_dir():
    folder = (Path("output") / ("production-catalog-test-" + uuid4().hex)).resolve()
    folder.mkdir(parents=True)
    try:
        yield folder
    finally:
        for item in folder.iterdir():
            item.unlink()
        folder.rmdir()


def setup_binding(tmp_path):
    key = Ed25519PrivateKey.generate()
    fingerprint = sha256(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).hexdigest()
    env = {"PYMENTOR_ANALYTICS_PRODUCTION_CATALOG_ENABLED": "1",
           "PYMENTOR_ANALYTICS_PRODUCTION_CATALOG_DATASET_ID": str(uuid4()),
           "PYMENTOR_ANALYTICS_PRODUCTION_CATALOG_INSTANCE_ID": str(uuid4()),
           "PYMENTOR_ANALYTICS_PRODUCTION_CATALOG_NAMESPACE": "test-core",
           "PYMENTOR_ANALYTICS_PRODUCTION_CATALOG_URL": "https://collector.example.test",
           "PYMENTOR_ANALYTICS_PRODUCTION_CATALOG_PRODUCER_KEY_SHA256": fingerprint,
           "PYMENTOR_ANALYTICS_PRODUCTION_CATALOG_RECEIPT_DIR": str(tmp_path.resolve()),
           "PYMENTOR_ANALYTICS_PRODUCER_ID": "test-core",
           "PYMENTOR_ANALYTICS_PRODUCER_ISSUER": "test-issuer",
           "PYMENTOR_ANALYTICS_PRODUCER_KID": "test-key"}
    binding = catalog.production_binding_from_env(env)
    (tmp_path / "binding.json").write_bytes(publication.canonical(binding.value()))
    signer = SimpleNamespace(_key=key, issuer="test-issuer", kid="test-key", _purpose="producer",
                             producer=lambda _: "test-token")
    source = SimpleNamespace(context=SimpleNamespace(course_id="test-course"),
        catalog=SimpleNamespace(TRACKS=[{"id": "track", "title": "Track", "order": 1, "lessons": []}]))
    return env, binding, signer, source


def test_binding_is_opt_in_and_rejects_wrong_identity_and_endpoint(receipt_dir):
    tmp_path = receipt_dir
    assert catalog.production_binding_from_env({}) is None
    env, binding, _, _ = setup_binding(tmp_path)
    assert catalog.production_root(binding) == tmp_path.resolve()
    env["PYMENTOR_ANALYTICS_PRODUCTION_CATALOG_URL"] = "http://127.0.0.1:1234"
    with pytest.raises(ValueError):
        catalog.production_binding_from_env(env)
    (tmp_path / "binding.json").write_bytes(b"{}")
    with pytest.raises(ValueError):
        catalog.production_root(binding)


def test_metadata_uses_only_the_configured_production_receipt(receipt_dir, monkeypatch):
    _, binding, _, source = setup_binding(receipt_dir)
    source.catalog.LESSONS = []
    router = import_module("pymentor_core.modules.learning.api.analytics_catalog_router")

    async def authorize_fixture(*args):
        return None

    monkeypatch.setattr(router, "request_principal", authorize_fixture)
    monkeypatch.setattr(router, "run_request_sql", authorize_fixture)
    monkeypatch.setattr(router, "read_published_catalog", lambda *args: pytest.fail("local receipt fallback"))
    monkeypatch.setattr(router, "read_production_catalog", lambda course, selected: (
        source, {"catalog_version": "published", "catalog": {"tracks": [], "lessons": []}}))
    app = FastAPI()
    app.include_router(router.analytics_catalog_router, prefix="/api")
    app.state.analytics_production_catalog = binding
    app.state.analytics_browser_gateway = object()
    with TestClient(app) as client:
        path = "/api/learning/analytics-catalog?course_id=test-course"
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["X-Analytics-Collection-Mode"] == "parallel"
        assert response.json()["catalog_version"] == "published"
        app.state.analytics_production_catalog = None
        unavailable = client.get(path)
        assert "X-Analytics-Collection-Mode" not in unavailable.headers
        assert unavailable.json()["catalog_version"] is None


def test_reconcile_lost_reply_and_fail_closed_on_changed_source(receipt_dir, monkeypatch):
    tmp_path = receipt_dir
    env, binding, signer, source = setup_binding(tmp_path)
    monkeypatch.setattr(publication, "current_digest", lambda _: "a" * 64)
    monkeypatch.setattr(catalog, "client_tls", lambda *args: ssl.create_default_context())
    calls = []
    replies = iter(["absent", "unknown", "unknown", "accepted"])
    async def request(method, url, **kwargs):
        calls.append((method, kwargs["verify"], kwargs["body"]))
        status = next(replies)
        return None if status == "unknown" else (200, json.dumps({"status": status}).encode())
    monkeypatch.setattr(catalog, "request", request)
    assert asyncio.run(catalog.publish_production(binding, source, signer, env)) == "unknown"
    pending = catalog._production_read(binding, "test-course", pending=True)
    with pytest.raises(FileNotFoundError):
        catalog._production_read(binding, "test-course")
    assert asyncio.run(catalog.publish_production(binding, source, signer, env)) == "accepted"
    assert [call[0] for call in calls] == ["GET", "POST", "GET", "GET"]
    assert all(isinstance(call[1], ssl.SSLContext) for call in calls)
    assert catalog._production_read(binding, "test-course") == pending
    monkeypatch.setattr(catalog, "default_runtime", lambda: SimpleNamespace(snapshot=lambda _: source))
    assert catalog.read_production_catalog("test-course", binding)[1] == pending["message"]
    monkeypatch.setattr(publication, "current_digest", lambda _: "b" * 64)
    with pytest.raises(ValueError, match="no longer matches"):
        catalog.read_production_catalog("test-course", binding)


def test_invalid_tls_and_producer_fail_before_any_request(receipt_dir, monkeypatch):
    tmp_path = receipt_dir
    env, binding, signer, source = setup_binding(tmp_path)
    monkeypatch.setattr(catalog, "client_tls", lambda *args: True)
    with pytest.raises(ValueError, match="mutual TLS"):
        asyncio.run(catalog.publish_production(binding, source, signer, env))
    signer.kid = "other"
    with pytest.raises(ValueError, match="identity"):
        asyncio.run(catalog.publish_production(binding, source, signer, env))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["binding.json"]


def test_conflict_preserves_pending_and_no_confirmed_receipt(receipt_dir, monkeypatch):
    tmp_path = receipt_dir
    env, binding, signer, source = setup_binding(tmp_path)
    monkeypatch.setattr(publication, "current_digest", lambda _: "a" * 64)
    monkeypatch.setattr(catalog, "client_tls", lambda *args: ssl.create_default_context())
    async def request(*args, **kwargs):
        return 409, b'{"status":"conflict"}'
    monkeypatch.setattr(catalog, "request", request)
    assert asyncio.run(catalog.publish_production(binding, source, signer, env)) == "conflict"
    assert catalog._production_read(binding, "test-course", pending=True)
    with pytest.raises(FileNotFoundError):
        catalog._production_read(binding, "test-course")


def test_command_requires_explicit_production_configuration(receipt_dir, monkeypatch):
    env, binding, signer, source = setup_binding(receipt_dir)
    monkeypatch.setattr(sys, "argv", ["analytics_catalog", "--production", "--course-id", "test-course"])
    monkeypatch.delenv("PYMENTOR_ANALYTICS_PRODUCTION_CATALOG_ENABLED", raising=False)
    with pytest.raises(ValueError, match="disabled"):
        catalog.main()
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    key_file = receipt_dir / "key.pem"
    key_file.write_bytes(b"test-key")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_PRODUCER_KEY_FILE", str(key_file))
    monkeypatch.setattr(catalog, "Signer", lambda **kwargs: signer)
    monkeypatch.setattr(catalog, "default_runtime", lambda: SimpleNamespace(snapshot=lambda _: source))
    called = []
    async def publish(selected, selected_source, selected_signer, selected_env):
        called.append((selected, selected_source, selected_signer))
        return "accepted"
    monkeypatch.setattr(catalog, "publish_production", publish)
    catalog.main()
    assert called == [(binding, source, signer)]
    monkeypatch.setattr(sys, "argv", ["analytics_catalog", "--course-id", "test-course"])
    monkeypatch.delenv("PYMENTOR_ANALYTICS_LOCAL_COLLECTION_ENABLED", raising=False)
    with pytest.raises(ValueError, match="Local Analytics collection is disabled"):
        catalog.main()


def test_versioned_publication_keeps_legacy_and_older_receipts(receipt_dir, monkeypatch):
    env, binding, signer, source = setup_binding(receipt_dir)
    source.digest = "a" * 64
    monkeypatch.setattr(publication, "current_digest", lambda value: value.digest)
    monkeypatch.setattr(catalog, "client_tls", lambda *args: ssl.create_default_context())
    calls = []

    async def reconcile(endpoint, message, fingerprint, token, **kwargs):
        calls.append((message["sequence"], message["catalog_version"], fingerprint))
        return "accepted"

    monkeypatch.setattr(catalog, "_reconcile", reconcile)
    assert asyncio.run(catalog.publish_production(binding, source, signer, env)) == "accepted"
    legacy = catalog._production_read(binding, "test-course")
    source.digest = "b" * 64
    source.catalog.TRACKS[0]["title"] = "Updated"
    assert asyncio.run(catalog.publish_production(binding, source, signer, env, versioned=True)) == "accepted"
    head = catalog._production_read(binding, "test-course", suffix=".head.json")
    assert head["message"]["sequence"] == 2
    assert catalog._production_read(binding, "test-course") == legacy
    assert catalog._production_read(binding, "test-course", suffix=catalog._source_suffix("a" * 64)) == legacy
    monkeypatch.setattr(catalog, "default_runtime", lambda: SimpleNamespace(snapshot=lambda _: source))
    assert catalog.read_production_catalog("test-course", binding)[1] == head["message"]
    source.digest = "a" * 64
    assert catalog.read_production_catalog("test-course", binding)[1] == legacy["message"]
    assert asyncio.run(catalog.publish_production(binding, source, signer, env, versioned=True)) == "accepted"
    assert catalog._production_read(binding, "test-course", suffix=".head.json") == head
    assert len(calls) == 3
    assert calls[-1] == calls[0]
    with pytest.raises(ValueError, match="versioned catalog publication"):
        asyncio.run(catalog.publish_production(binding, source, signer, env))


def test_versioned_same_catalog_new_source_retains_both(receipt_dir, monkeypatch):
    env, binding, signer, source = setup_binding(receipt_dir)
    source.digest = "a" * 64
    monkeypatch.setattr(publication, "current_digest", lambda value: value.digest)
    monkeypatch.setattr(catalog, "client_tls", lambda *args: ssl.create_default_context())

    async def reconcile(*args, **kwargs):
        return "accepted"

    monkeypatch.setattr(catalog, "_reconcile", reconcile)
    assert asyncio.run(catalog.publish_production(binding, source, signer, env, versioned=True)) == "accepted"
    first = catalog._production_read(binding, "test-course", suffix=".head.json")
    source.digest = "b" * 64
    assert asyncio.run(catalog.publish_production(binding, source, signer, env, versioned=True)) == "accepted"
    second = catalog._production_read(binding, "test-course", suffix=".head.json")
    assert first["message"] == second["message"]
    assert catalog._production_read(binding, "test-course", suffix=catalog._source_suffix("a" * 64)) == first
    assert catalog._production_read(binding, "test-course", suffix=catalog._source_suffix("b" * 64)) == second


def test_versioned_recovers_ack_and_then_publishes_current(receipt_dir, monkeypatch):
    env, binding, signer, source = setup_binding(receipt_dir)
    source.digest = "a" * 64
    monkeypatch.setattr(publication, "current_digest", lambda value: value.digest)
    monkeypatch.setattr(catalog, "client_tls", lambda *args: ssl.create_default_context())
    calls = []
    outcomes = iter(["unknown", "accepted", "accepted"])

    async def reconcile(endpoint, message, fingerprint, token, **kwargs):
        calls.append((message["sequence"], fingerprint))
        return next(outcomes)

    monkeypatch.setattr(catalog, "_reconcile", reconcile)
    assert asyncio.run(catalog.publish_production(binding, source, signer, env, versioned=True)) == "unknown"
    source.digest = "b" * 64
    source.catalog.TRACKS[0]["title"] = "Newer"
    assert asyncio.run(catalog.publish_production(binding, source, signer, env, versioned=True)) == "accepted"
    assert calls[0] == calls[1]
    assert calls[2][0] == 2
    assert catalog._production_read(binding, "test-course", suffix=".head.json")["source_digest"] == "b" * 64
    assert not catalog._production_path(binding, "test-course", ".pending.json").exists()


def test_versioned_recovers_after_ack_before_head_write(receipt_dir, monkeypatch):
    env, binding, signer, source = setup_binding(receipt_dir)
    monkeypatch.setattr(publication, "current_digest", lambda _: "a" * 64)
    monkeypatch.setattr(catalog, "client_tls", lambda *args: ssl.create_default_context())
    calls = []

    async def reconcile(endpoint, message, fingerprint, token, **kwargs):
        calls.append((message["sequence"], fingerprint))
        return "accepted"

    monkeypatch.setattr(catalog, "_reconcile", reconcile)
    original = catalog._production_write
    fail = True

    def interrupted(*args, **kwargs):
        nonlocal fail
        if kwargs.get("suffix") == ".head.json" and fail:
            fail = False
            raise OSError("simulated interruption")
        return original(*args, **kwargs)

    monkeypatch.setattr(catalog, "_production_write", interrupted)
    with pytest.raises(OSError, match="simulated interruption"):
        asyncio.run(catalog.publish_production(binding, source, signer, env, versioned=True))
    assert catalog._production_read(binding, "test-course", pending=True)
    assert asyncio.run(catalog.publish_production(binding, source, signer, env, versioned=True)) == "accepted"
    assert len(calls) == 2 and calls[0] == calls[1]
    assert catalog._production_read(binding, "test-course", suffix=".head.json")["source_digest"] == "a" * 64
    assert not catalog._production_path(binding, "test-course", ".pending.json").exists()


def test_versioned_existing_receipt_requires_collector_confirmation(receipt_dir, monkeypatch):
    env, binding, signer, source = setup_binding(receipt_dir)
    monkeypatch.setattr(publication, "current_digest", lambda _: "a" * 64)
    monkeypatch.setattr(catalog, "client_tls", lambda *args: ssl.create_default_context())
    outcomes = iter(["accepted", "unknown"])

    async def reconcile(*args, **kwargs):
        return next(outcomes)

    monkeypatch.setattr(catalog, "_reconcile", reconcile)
    assert asyncio.run(catalog.publish_production(binding, source, signer, env, versioned=True)) == "accepted"
    assert asyncio.run(catalog.publish_production(binding, source, signer, env, versioned=True)) == "unknown"


def test_versioned_command_selects_production_publisher(receipt_dir, monkeypatch):
    env, binding, signer, source = setup_binding(receipt_dir)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    key_file = receipt_dir / "key.pem"
    key_file.write_bytes(b"test-key")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_PRODUCER_KEY_FILE", str(key_file))
    monkeypatch.setattr(catalog, "Signer", lambda **kwargs: signer)
    monkeypatch.setattr(catalog, "default_runtime", lambda: SimpleNamespace(snapshot=lambda _: source))
    called = []

    async def publish(selected, selected_source, selected_signer, selected_env, *, versioned=False):
        called.append((selected, selected_source, selected_signer, versioned))
        return "accepted"

    monkeypatch.setattr(catalog, "publish_production", publish)
    monkeypatch.setattr(sys, "argv", ["analytics_catalog", "--production", "--course-id", "test-course", "--versioned"])
    catalog.main()
    assert called == [(binding, source, signer, True)]
    monkeypatch.setattr(sys, "argv", ["analytics_catalog", "--course-id", "test-course", "--versioned"])
    with pytest.raises(ValueError, match="requires production"):
        catalog.main()
