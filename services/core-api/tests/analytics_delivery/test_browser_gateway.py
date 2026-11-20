import asyncio

import pytest
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pymentor_core.app.web import configure_web
from pymentor_core.app import analytics_browser_gateway as gateway


@pytest.mark.parametrize("origin", ["http://collector.internal", "https://localhost", "https://127.0.0.1",
    "https://[::1]", "https://[::ffff:127.0.0.1]", "https://localhost.", "https://2130706433", "https://0x7f000001",
    "https://collector.internal/", "https://collector.internal/path",
    "https://collector.internal?", "https://collector.internal#", "https://u@collector.internal",
    "https://collector.internal:0", "https://collector.internal:", "https://collector.internal\\bad"])
def test_invalid_origins_rejected(origin):
    with pytest.raises(ValueError):
        gateway.configure({"PYMENTOR_ANALYTICS_BROWSER_COLLECTOR_ORIGIN": origin})


def test_opt_in_requires_mtls(monkeypatch):
    assert gateway.configure({}) is None
    env = {"PYMENTOR_ANALYTICS_BROWSER_COLLECTOR_ORIGIN": "https://collector.internal:8443"}
    with pytest.raises(ValueError):
        gateway.configure(env)
    env.update({"PYMENTOR_ANALYTICS_BROWSER_COLLECTOR" + suffix: "test" for suffix in
                ("_CA_FILE", "_CLIENT_CERT_FILE", "_CLIENT_KEY_FILE")})
    monkeypatch.setattr(gateway, "client_tls", lambda *args: "tls")
    result = gateway.configure(env)
    assert result["endpoint"] == "https://collector.internal:8443" + gateway.PATH
    assert result["verify"] == "tls"


def test_forwarding_limits_and_origin(monkeypatch):
    calls = []
    async def send(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return 200, b'{"events":[]}', {"Retry-After": "2"}
    monkeypatch.setattr(gateway, "http_request", send)
    app = FastAPI()
    app.state.analytics_browser_gateway = {"endpoint": "https://collector.internal" + gateway.PATH,
        "verify": "tls", "slots": asyncio.Semaphore(1)}
    configure_web(app)
    app.include_router(gateway.router)
    headers = {"Authorization": "Bearer abc.def", "Content-Type": "application/json", "Cookie": "session=secret",
               "X-Actor": "forged", "Origin": "http://testserver"}
    with TestClient(app) as client:
        response = client.post(gateway.PATH, content=b'{}', headers=headers)
        assert response.status_code == 200 and response.headers["retry-after"] == "2"
        assert calls[0][2]["headers"] == {"Authorization": "Bearer abc.def", "Content-Type": "application/json"}
        assert calls[0][2]["body"] == b'{}'
        assert client.post(gateway.PATH, content=b'x' * (gateway.MAX_BODY + 1), headers=headers).status_code == 413
        assert client.post(gateway.PATH, content=b'{}', headers={**headers, "Authorization": "Basic test"}).status_code == 401
        assert client.post(gateway.PATH + "?other=1", content=b'{}', headers=headers).status_code == 400
        assert client.post(gateway.PATH, content=b'{}', headers={**headers, "Origin": "https://evil.example"}).status_code == 403
        assert client.get(gateway.PATH).status_code == 405
        assert len(calls) == 1


def test_http_response_bound_and_safe_headers():
    from pymentor_core.adapters.analytics_http.http import request
    async def run():
        seen = []
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{}'
        def handler(req):
            seen.append(str(req.url))
            return httpx.Response(429, stream=Stream(), headers={"Retry-After": "2", "Set-Cookie": "secret",
                "WWW-Authenticate": "Bearer", "Location": "https://elsewhere.invalid"})
        transport = httpx.MockTransport(handler)
        result = await request("POST", "https://collector.internal" + gateway.PATH, headers={}, body=b'{}',
            deadline_seconds=1, max_bytes=16, transport=transport, include_safe_headers=True)
        assert result == (429, b'{}', {"Retry-After": "2", "WWW-Authenticate": "Bearer"})
        assert len(seen) == 1
        assert await request("POST", "https://collector.internal", headers={}, body=b'{}',
            deadline_seconds=1, max_bytes=1, transport=transport, include_safe_headers=True) is None
    asyncio.run(run())


def test_gateway_deadline_and_unexpected_status(monkeypatch):
    async def slow(*args, **kwargs):
        await asyncio.sleep(1)
    app = FastAPI()
    app.state.analytics_browser_gateway = {"endpoint": "https://collector.internal" + gateway.PATH,
        "verify": True, "slots": asyncio.Semaphore(1)}
    app.include_router(gateway.router)
    monkeypatch.setattr(gateway, "DEADLINE", 0.01)
    monkeypatch.setattr(gateway, "http_request", slow)
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer abc", "Content-Type": "application/json"}
        assert client.post(gateway.PATH, content=b'{}', headers=headers).status_code == 503
        async def redirected(*args, **kwargs):
            return 302, b'{}', {}
        monkeypatch.setattr(gateway, "http_request", redirected)
        assert client.post(gateway.PATH, content=b'{}', headers=headers).status_code == 503


def test_saturation_fails_closed():
    async def run():
        app = FastAPI()
        slots = asyncio.Semaphore(1)
        app.state.analytics_browser_gateway = {"endpoint": "https://collector.internal" + gateway.PATH,
            "verify": True, "slots": slots}
        app.include_router(gateway.router)
        await slots.acquire()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(gateway.PATH, content=b'{}', headers={"Authorization": "Bearer abc",
                "Content-Type": "application/json"})
            assert response.status_code == 503
        slots.release()
    asyncio.run(run())
