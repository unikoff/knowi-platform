from datetime import datetime, timezone
from hashlib import sha256
import json
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from pymentor_core.app import analytics_windows_gateway as gateway
from pymentor_core.app.web import configure_web
from pymentor_core.modules.analytics_delivery import windows_grants as grants
from pymentor_core.modules.analytics_delivery.signing import Signer

SUBJECT = "605a0000-0000-4000-8000-000000000004"
WRITER = "a1111111-1111-4111-8111-111111111111"
REQUEST = {"protocol_version": 1, "writer_id": WRITER, "writer_generation": 1}
T0 = "2026-01-01T00:00:00Z"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    path = tmp_path / "scope.json"

    def set_scope(courses, *, platform=False):
        updated = path.with_suffix(".tmp")
        tools = [{"tool_id": "catalog", "course_id": None}] if platform else []
        updated.write_text(json.dumps({SUBJECT: {"course_ids": courses, "tool_scopes": tools}}), encoding="utf-8")
        updated.replace(path)

    set_scope(["backend", "sql"], platform=True)
    env = {"ANALYTICS_WINDOWS_INTAKE_ENABLED": "1", "ANALYTICS_WINDOWS_COHORT": SUBJECT,
           "ANALYTICS_WINDOWS_T0": T0, "ANALYTICS_WINDOWS_SCOPE_FILE": str(path),
           "PYMENTOR_ANALYTICS_LOCAL_COLLECTION_ENABLED": "1",
           "ANALYTICS_WINDOWS_LOCAL_COLLECTOR_ORIGIN": "http://127.0.0.1:18042"}
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    app = FastAPI()
    app.state.analytics_windows_gateway = gateway.configure(env)
    app.state.analytics_grant_signer = Signer(issuer="core-test", kid="k", private_pem=pem)
    configure_web(app)
    app.include_router(gateway.router)
    calls, authorities = [], []
    principal = SimpleNamespace(account_ref=SUBJECT, subscription_until=None)

    async def identity(request):
        if request.cookies.get("session") != "synthetic":
            raise HTTPException(401, "Authentication required")
        return principal

    async def run_sql(_, operation):
        return operation(object())

    def policy(db, actor, *, purpose, course_id, target_ref):
        authorities.append((actor.account_ref, purpose, course_id, target_ref))
        if actor.account_ref != SUBJECT:
            raise HTTPException(401, "Lifecycle changed")

    def catalog(course):
        return SimpleNamespace(catalog=SimpleNamespace(LESSONS=[
            {"id": "lesson-1", "access": "registered"}, {"id": "locked", "access": "subscription"},
        ]))

    async def collector(method, endpoint, **kwargs):
        calls.append((method, endpoint, kwargs))
        return 200, b'{"protocol_version":1,"items":[]}', {}

    monkeypatch.setattr(grants, "request_principal", identity)
    monkeypatch.setattr(grants, "run_request_sql", run_sql)
    monkeypatch.setattr(grants, "authorize_grant", policy)
    monkeypatch.setattr(grants, "enabled_course_ids", lambda: ["backend", "sql", "unassigned"])
    monkeypatch.setattr(gateway, "default_runtime", lambda: SimpleNamespace(snapshot=catalog))
    monkeypatch.setattr(gateway, "http_request", collector)
    return app, set_scope, calls, authorities, key, env


def credential(client):
    response = client.post(gateway.GRANT_PATH, json=REQUEST)
    assert response.status_code == 200
    return response.json()["grant"]


def body(*stats):
    return {"protocol_version": 1, "windows": [{"stats": list(stats)}]}


def proof(key, call):
    headers = call[2]["headers"]
    return jwt.decode(headers[grants.ATTESTATION_HEADER], key.public_key(), algorithms=["EdDSA"],
                      issuer="core-test", audience=grants.GATEWAY_AUDIENCE)


def test_window_grant_uses_current_scope_and_clock(runtime, monkeypatch):
    app, _, _, authorities, key, _ = runtime
    mark = datetime(2026, 11, 27, 7, tzinfo=timezone.utc)
    monkeypatch.setattr(grants, "window_clock", lambda: mark)
    with TestClient(app, cookies={"session": "synthetic"}) as client:
        response = client.post(gateway.GRANT_PATH, json=REQUEST)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    data = response.json()
    assert data["subject_ref"] == SUBJECT and data["server_time"] == mark.isoformat()
    claims = jwt.decode(data["grant"], key.public_key(), algorithms=["EdDSA"],
                        issuer="core-test", audience=grants.AUDIENCE)
    assert claims["sub"] == SUBJECT and claims["writer_id"] == WRITER
    assert claims["scope"] == "windows:collect"
    assert claims["writer_generation"] == 1
    assert claims["course_ids"] == ["backend", "sql"]
    assert claims["tool_scopes"] == [{"tool_id": "catalog", "course_id": None}]
    assert claims["exp"] - claims["iat"] == 300
    assert len(authorities) == 1


def test_same_signed_grant_is_rechecked_after_course_withdrawal(runtime):
    app, set_scope, calls, authorities, key, _ = runtime
    backend = {"course_id": "backend", "lesson_id": "lesson-1", "tool_id": "theory"}
    sql = dict(backend, course_id="sql")
    values = body(backend, sql)
    values["windows"].append(body(backend)["windows"][0])
    raw = json.dumps(values).encode()
    with TestClient(app, cookies={"session": "synthetic"}) as client:
        grant = credential(client)
        headers = {"Authorization": "Bearer " + grant, "Content-Type": "application/json",
                   grants.ATTESTATION_HEADER: "forged", "X-Actor": "forged"}
        assert client.post(gateway.BATCH_PATH, content=raw, headers=headers).status_code == 200
        set_scope(["backend"], platform=True)
        assert client.post(gateway.BATCH_PATH, content=raw, headers=headers).status_code == 200
    assert len(authorities) == 3
    assert proof(key, calls[0])["rejected"] == [None, None]
    assert proof(key, calls[1])["rejected"] == ["course_forbidden", None]
    assert calls[1][1] == "http://127.0.0.1:18042" + gateway.BATCH_PATH
    assert calls[1][2]["body"] == raw
    assert proof(key, calls[1])["body_sha256"] == sha256(raw).hexdigest()
    assert proof(key, calls[1])["grant_sha256"] == sha256(grant.encode()).hexdigest()
    assert set(calls[1][2]["headers"]) == {"Authorization", "Content-Type", grants.ATTESTATION_HEADER}


def test_lesson_and_global_tool_permissions_are_current(runtime):
    app, set_scope, calls, _, key, _ = runtime
    values = body({"course_id": "backend", "lesson_id": "locked", "tool_id": "theory"})
    values["windows"].append(body({"course_id": None, "lesson_id": None, "tool_id": "catalog"})["windows"][0])
    with TestClient(app, cookies={"session": "synthetic"}) as client:
        headers = {"Authorization": "Bearer " + credential(client)}
        assert client.post(gateway.BATCH_PATH, json=values, headers=headers).status_code == 200
        set_scope(["backend", "sql"], platform=False)
        assert client.post(gateway.BATCH_PATH, json=values, headers=headers).status_code == 200
    assert proof(key, calls[0])["rejected"] == ["course_forbidden", None]
    assert proof(key, calls[1])["rejected"] == ["course_forbidden", "tool_forbidden"]


def test_gateway_rejects_cross_origin_missing_session_and_v2_grant(runtime):
    app, _, calls, _, _, _ = runtime
    signer = app.state.analytics_grant_signer
    token = signer.grant(subject=SUBJECT, role="user", scope="collect", courses=["backend"], subjects=[], global_read=False)
    with TestClient(app, cookies={"session": "synthetic"}) as client:
        grant = credential(client)
        response = client.post(gateway.BATCH_PATH, json=body(), headers={"Authorization": "Bearer " + token})
        assert response.status_code == 401
        response = client.post(gateway.BATCH_PATH, json=body(), headers={
            "Authorization": "Bearer " + grant, "Origin": "https://foreign.invalid",
        })
        assert response.status_code == 403
    with TestClient(app) as client:
        response = client.post(gateway.BATCH_PATH, json=body(), headers={"Authorization": "Bearer " + grant})
        assert response.status_code == 401
    assert not calls


def test_gateway_bounds_wire_bytes_and_rejects_duplicate_fields(runtime):
    app, _, calls, _, _, _ = runtime
    with TestClient(app, cookies={"session": "synthetic"}) as client:
        headers = {"Authorization": "Bearer " + credential(client), "Content-Type": "application/json"}
        oversized = client.post(gateway.BATCH_PATH, content=b" " * (64 * 1024 + 1), headers=headers)
        assert oversized.status_code == 413
        duplicated = client.post(gateway.BATCH_PATH,
            content=b'{"protocol_version":1,"windows":[{"stats":[],"stats":[]}]}', headers=headers)
        assert duplicated.status_code == 400
        assert duplicated.json()["code"] == "invalid_request"
        unsupported = client.post(gateway.BATCH_PATH, json={"protocol_version": 9, "windows": []}, headers=headers)
        assert unsupported.status_code == 400
        assert unsupported.json()["code"] == "unsupported_version"
    assert not calls


def test_gateway_passes_retry_after_and_sanitizes_unavailable(runtime, monkeypatch):
    app, _, _, _, _, _ = runtime

    async def limited(*args, **kwargs):
        return 429, b'{"code":"rate_limited"}', {"Retry-After": "6"}

    async def lost(*args, **kwargs):
        return None

    with TestClient(app, cookies={"session": "synthetic"}) as client:
        headers = {"Authorization": "Bearer " + credential(client)}
        monkeypatch.setattr(gateway, "http_request", limited)
        response = client.post(gateway.BATCH_PATH, json=body(), headers=headers)
        assert response.status_code == 429 and response.headers["Retry-After"] == "6"
        monkeypatch.setattr(gateway, "http_request", lost)
        response = client.post(gateway.BATCH_PATH, json=body(), headers=headers)
        assert response.status_code == 503 and response.json()["code"] == "unavailable"


def test_configuration_default_off_and_local_mode_explicit(runtime):
    _, _, _, _, _, env = runtime
    assert gateway.configure({}) is None
    with pytest.raises(ValueError, match="Invalid local"):
        gateway.configure({**env, "ANALYTICS_WINDOWS_LOCAL_COLLECTOR_ORIGIN": "http://192.0.2.1:1"})
    with pytest.raises(ValueError, match="Invalid local"):
        gateway.configure({**env, "PYMENTOR_ANALYTICS_LOCAL_COLLECTION_ENABLED": "0"})
    with pytest.raises(ValueError, match="policy configuration"):
        gateway.configure({**env, "ANALYTICS_WINDOWS_T0": "2026-01-01T03:00:00+03:00"})


@pytest.mark.parametrize("legacy_enabled", [False, True])
def test_full_core_disables_windows_before_the_frontend_fallback(monkeypatch, tmp_path, legacy_enabled):
    import os

    from pymentor_core.app import analytics_browser_gateway as legacy
    from pymentor_core.app import web
    from pymentor_core.app.asgi import create_app

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>Core SPA</title>", encoding="utf-8")
    monkeypatch.setattr(web, "DIST_DIR", tmp_path)
    for name in os.environ.copy():
        if name.startswith("PYMENTOR_ANALYTICS_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("ANALYTICS_WINDOWS_INTAKE_ENABLED", "0")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("KNOWI_REDIS_PREFIX", "corp607-off-fixture")
    monkeypatch.setenv("KNOWI_LOGIN_RECOVERY_KEY", "1" * 64)
    sent = []
    if legacy_enabled:
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        signer = tmp_path / "legacy-test-key.pem"
        signer.write_bytes(pem)
        monkeypatch.setenv("PYMENTOR_ANALYTICS_GRANTS_ENABLED", "1")
        monkeypatch.setenv("PYMENTOR_ANALYTICS_GRANT_KEY_FILE", str(signer))
        monkeypatch.setenv("PYMENTOR_ANALYTICS_GRANT_ISSUER", "test-collector")
        monkeypatch.setenv("PYMENTOR_ANALYTICS_GRANT_KID", "k1")
        prefix = "PYMENTOR_ANALYTICS_BROWSER_COLLECTOR"
        for suffix in ("_CA_FILE", "_CLIENT_CERT_FILE", "_CLIENT_KEY_FILE"):
            monkeypatch.setenv(prefix + suffix, "synthetic")
        monkeypatch.setenv(prefix + "_ORIGIN", "https://collector.internal")
        monkeypatch.setattr(legacy, "client_tls", lambda *args: True)

        async def forward(method, url, **kwargs):
            sent.append((method, url))
            return 200, b'{"items":[{"status":"accepted","code":"committed"}]}', {}

        monkeypatch.setattr(legacy, "http_request", forward)

    app = create_app()
    client = TestClient(app)
    assert client.get("/backend").text == "<!doctype html><title>Core SPA</title>"
    for path in (gateway.GRANT_PATH, gateway.BATCH_PATH):
        response = client.post(path, json={})
        assert response.status_code == 404
        assert response.json()["code"] == "capability_off"
        assert response.headers["Cache-Control"] == "no-store"
        assert path not in app.openapi()["paths"]

    v2 = client.post(legacy.PATH, json={}, headers={"Authorization": "Bearer abc.def"})
    if legacy_enabled:
        assert v2.status_code == 200
        assert v2.json()["items"][0]["status"] == "accepted"
        assert sent == [("POST", "https://collector.internal" + legacy.PATH)]
    else:
        assert v2.status_code == 405 and not sent


def test_all_eligible_uses_normal_identity_and_live_rights(runtime, monkeypatch):
    app, _, calls, _, signing_key, env = runtime
    env = {name: value for name, value in env.items()
           if name not in {"ANALYTICS_WINDOWS_COHORT", "ANALYTICS_WINDOWS_SCOPE_FILE"}}
    env["ANALYTICS_WINDOWS_ALL_ELIGIBLE"] = "1"
    app.state.analytics_windows_gateway = gateway.configure(env)
    newly_admitted = "605a0000-0000-4000-8000-000000000099"
    identities = {"student": SimpleNamespace(account_ref=SUBJECT, subscription_until=None, role="user")}

    async def identity(request):
        actor = identities.get(request.cookies.get("session"))
        if actor is None:
            raise HTTPException(401)
        return actor

    def policy(_, actor, **kw):
        if actor.role != "user":
            raise HTTPException(403)

    monkeypatch.setattr(grants, "request_principal", identity)
    monkeypatch.setattr(grants, "authorize_grant", policy)
    with TestClient(app, cookies={"session": "new"}) as client:
        assert client.post(gateway.GRANT_PATH, json=REQUEST).status_code == 401
        identities["new"] = SimpleNamespace(account_ref=newly_admitted, subscription_until=None, role="user")
        token = credential(client)
        claims = jwt.decode(token, signing_key.public_key(), algorithms=["EdDSA"],
                            issuer="core-test", audience=grants.AUDIENCE)
        assert claims["sub"] == newly_admitted and "backend" in claims["course_ids"]
        scopes = {(item["tool_id"], item["course_id"]) for item in claims["tool_scopes"]}
        assert ("catalog", None) in scopes and ("interpreter", "backend") in scopes
        assert ("catalog", "backend") not in scopes and ("interpreter", None) not in scopes

        headers = {"Authorization": "Bearer " + token}
        allowed = {"tool_id": "theory", "course_id": "backend", "lesson_id": "lesson-1"}
        locked = {**allowed, "lesson_id": "locked"}
        foreign_tool = {"tool_id": "catalog", "course_id": "backend", "lesson_id": None}
        assert client.post(gateway.BATCH_PATH, json=body(allowed), headers=headers).status_code == 200
        assert proof(signing_key, calls[-1])["rejected"] == [None]
        assert client.post(gateway.BATCH_PATH, json=body(locked), headers=headers).status_code == 200
        assert proof(signing_key, calls[-1])["rejected"] == ["course_forbidden"]
        assert client.post(gateway.BATCH_PATH, json=body(foreign_tool), headers=headers).status_code == 200
        assert proof(signing_key, calls[-1])["rejected"] == ["tool_forbidden"]
        monkeypatch.setattr(grants, "enabled_course_ids", lambda: ["sql"])
        assert client.post(gateway.BATCH_PATH, json=body(allowed), headers=headers).status_code == 200
        assert proof(signing_key, calls[-1])["rejected"] == ["course_forbidden"]

        for role in ("leader", "admin"):
            identities["new"].role = role
            assert client.post(gateway.GRANT_PATH, json=REQUEST).status_code == 403
            assert client.post(gateway.BATCH_PATH, json=body(allowed), headers=headers).status_code == 403


def test_all_eligible_policy_rejects_conflicting_and_incomplete_config(runtime):
    _, _, _, _, _, env = runtime
    all_env = {name: value for name, value in env.items()
               if name not in {"ANALYTICS_WINDOWS_COHORT", "ANALYTICS_WINDOWS_SCOPE_FILE"}}
    all_env["ANALYTICS_WINDOWS_ALL_ELIGIBLE"] = "1"
    for change in ({"ANALYTICS_WINDOWS_COHORT": SUBJECT},
                   {"ANALYTICS_WINDOWS_SCOPE_FILE": env["ANALYTICS_WINDOWS_SCOPE_FILE"]},
                   {"ANALYTICS_WINDOWS_ALL_ELIGIBLE": "true"}, {"ANALYTICS_WINDOWS_T0": ""}):
        with pytest.raises(ValueError):
            grants.configure_policy({**all_env, **change})
    before_start = grants.configure_policy({**all_env, "ANALYTICS_WINDOWS_T0": "2100-01-01T00:00:00Z"})
    assert before_start is not None and not before_start.permits(SUBJECT)
    assert grants.configure_policy({**all_env, "ANALYTICS_WINDOWS_INTAKE_ENABLED": "0"}) is None
