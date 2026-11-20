import asyncio
import os

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pymentor_core.app import analytics_browser_gateway as collector
from pymentor_core.app import analytics_browser_query_gateway as gateway


def config():
    return {"origin": "https://query.internal:8443", "verify": "pinned-mtls", "slots": asyncio.Semaphore(1)}


def app_with_gateway():
    app = FastAPI()
    app.state.analytics_browser_query_gateway = config()
    app.include_router(gateway.router)
    return app


def test_opt_in_requires_private_pinned_mtls(monkeypatch):
    assert gateway.configure({}) is None
    with pytest.raises(ValueError):
        gateway.configure({"PYMENTOR_ANALYTICS_BROWSER_QUERY_ENABLED": "true"})
    env = {"PYMENTOR_ANALYTICS_BROWSER_QUERY_ENABLED": "1", "PYMENTOR_ANALYTICS_QUERY_URL": "https://query.internal:8443/"}
    with pytest.raises(ValueError):
        gateway.configure(env)
    env.update({"PYMENTOR_ANALYTICS_QUERY" + suffix: "test" for suffix in ("_CA_FILE", "_CLIENT_CERT_FILE", "_CLIENT_KEY_FILE")})
    seen = []
    monkeypatch.setattr(collector, "client_tls", lambda env, prefix, origin: seen.append((env, prefix, origin)) or "tls")
    assert gateway.configure(env)["verify"] == "tls"
    assert seen[0][2] == "https://query.internal:8443"
    assert seen[0][0][seen[0][1] + "_CA_FILE"] == "test"
    for url in ("http://query.internal", "https://localhost", "https://127.0.0.1", "https://user@query.internal", "https://query.internal/path", "https://query.internal?", "https://query.internal#", "https://query.internal//"):
        with pytest.raises(ValueError):
            gateway.configure({**env, "PYMENTOR_ANALYTICS_QUERY_URL": url})


def test_scoped_student_report_and_exact_allowlist(monkeypatch):
    calls = []
    async def send(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return 200, b'{"status":"ok","quality":"partial"}', {}
    monkeypatch.setattr(gateway, "http_request", send)
    headers = {"Authorization": "Bearer abc.def", "Cookie": "session=secret", "Origin": "https://site.test"}
    with TestClient(app_with_gateway()) as client:
        response = client.get(gateway.PREFIX + "?course_id=backend&subject_ref=123", headers=headers)
        assert response.status_code == 200 and response.json()["quality"] == "partial"
        assert response.headers["cache-control"] == "no-store"
        method, url, args = calls[-1]
        assert (method, url) == ("GET", "https://query.internal:8443" + gateway.PREFIX + "?course_id=backend&subject_ref=123")
        assert args["headers"] == {"Authorization": "Bearer abc.def", "Accept": "application/json"}
        assert args["body"] is None and args["verify"] == "pinned-mtls"
        response = client.post(gateway.PREFIX + "/outline/lifetime/student", content=b'{"course_id":"backend"}',
                               headers={**headers, "Content-Type": "application/json"})
        assert response.status_code == 200 and calls[-1][2]["body"] == b'{"course_id":"backend"}'
        assert client.get(gateway.PREFIX + "/unknown", headers=headers).status_code == 404
        assert client.post(gateway.PREFIX, headers=headers).status_code == 405
        assert client.get(gateway.PREFIX, headers={"Authorization": "Basic abc"}).status_code == 401
        assert client.get(gateway.PREFIX, headers=[("Authorization", "Bearer one"), ("Authorization", "Bearer two")]).status_code == 401
        assert client.post(gateway.PREFIX + "/calendar?x=1", headers={**headers, "Content-Type": "application/json"}).status_code == 400
        assert client.get(gateway.PREFIX + "?x=" + "a" * gateway.MAX_QUERY, headers=headers).status_code == 400
        assert client.post(gateway.PREFIX + "/calendar", content=b'x' * (gateway.MAX_BODY + 1), headers={**headers, "Content-Type": "application/json"}).status_code == 413
        assert len(calls) == 2


def test_routes_match_browser_client():
    expected = {gateway.PREFIX: "GET", gateway.PREFIX + "/details": "GET",
        **{gateway.PREFIX + suffix: "POST" for suffix in ("/calendar", "/chapters", "/outline", "/outline/lifetime", "/outline/lifetime/student")}}
    assert gateway.ROUTES == expected
    with TestClient(FastAPI()) as client:
        assert client.get(gateway.PREFIX).status_code == 404


def test_factory_registration_and_openapi_fallback(monkeypatch):
    from pathlib import Path
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    from pymentor_core.app.factory import create_app
    values = {"DATABASE_URL": "postgresql+psycopg://synthetic@127.0.0.1:1/isolated",
              "REDIS_URL": "redis://127.0.0.1:1/0", "KNOWI_REDIS_PREFIX": "query-gateway-test",
              "KNOWI_LOGIN_RECOVERY_KEY": "a" * 64}
    for name, value in values.items():
        if name not in os.environ:
            monkeypatch.setenv(name, value)
    monkeypatch.delenv("PYMENTOR_ANALYTICS_BROWSER_QUERY_ENABLED", raising=False)
    monkeypatch.delenv("PYMENTOR_ANALYTICS_GRANTS_ENABLED", raising=False)
    off = create_app()
    assert off.state.analytics_browser_query_gateway is None
    assert TestClient(off).get(gateway.PREFIX).status_code == 404
    monkeypatch.setattr(gateway, "configure", lambda env: config())
    with pytest.raises(RuntimeError, match="requires grant signing"):
        create_app()
    private_key = Ed25519PrivateKey.generate().private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda path: private_key if str(path) == "query-test-key" else original(path))
    for name, value in {"PYMENTOR_ANALYTICS_GRANTS_ENABLED": "1", "PYMENTOR_ANALYTICS_GRANT_KEY_FILE": "query-test-key",
                        "PYMENTOR_ANALYTICS_GRANT_ISSUER": "test", "PYMENTOR_ANALYTICS_GRANT_KID": "test"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(gateway, "configure", lambda env: None)
    baseline = create_app()
    monkeypatch.setattr(gateway, "configure", lambda env: config())
    on = create_app()
    assert TestClient(on).get(gateway.PREFIX).status_code == 401
    # Gateway routes are transparent and intentionally absent from Core's generated contract.
    assert baseline.openapi() == on.openapi()


def test_unavailable_redirect_deadline_and_saturation(monkeypatch):
    async def send(*args, **kwargs):
        return 302, b'{}', {"Location": "https://evil.test"}
    monkeypatch.setattr(gateway, "http_request", send)
    app = app_with_gateway()
    headers = {"Authorization": "Bearer valid"}
    with TestClient(app) as client:
        response = client.get(gateway.PREFIX, headers=headers)
        assert response.status_code == 503 and "location" not in response.headers
        async def denied(*args, **kwargs):
            return 403, b'{"detail":"denied"}', {}
        monkeypatch.setattr(gateway, "http_request", denied)
        assert client.get(gateway.PREFIX, headers=headers).status_code == 403
        async def unavailable(*args, **kwargs):
            return None
        monkeypatch.setattr(gateway, "http_request", unavailable)
        assert client.get(gateway.PREFIX, headers=headers).status_code == 503
        async def slow(*args, **kwargs):
            await asyncio.sleep(1)
        monkeypatch.setattr(gateway, "http_request", slow)
        monkeypatch.setattr(gateway, "DEADLINE", 0.01)
        assert client.get(gateway.PREFIX, headers=headers).status_code == 503

    async def saturated():
        app = app_with_gateway()
        await app.state.analytics_browser_query_gateway["slots"].acquire()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(gateway.PREFIX, headers=headers)
            assert response.status_code == 503
    asyncio.run(saturated())
