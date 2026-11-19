import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from uuid import UUID

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from pymentor_analytics.adapters.auth.fence import AuthorizationError, FenceUnavailable
from pymentor_analytics.adapters.auth.http import extract_bearer
from pymentor_analytics.adapters.auth.verifier import (AuthenticationError, GrantVerifier,
    ProducerVerifier, build_verifiers, MAX_TOKEN_BYTES)
from pymentor_analytics.adapters.auth.config import load_verifiers
from pymentor_analytics.app.config import Settings, load_settings
from pymentor_analytics.app.factory import create_app
from pymentor_analytics.modules.collector.application.admission import authorize_collection
from pymentor_analytics.modules.analytics.reports.auth import authorize_report

USER = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
LEADER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
OTHER = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")


def public(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


@pytest.fixture
def keys():
    return Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()


def claims(role="user", scope="collect"):
    now = int(time.time())
    return {"grant_version": 1, "iss": "synthetic-issuer", "aud": "analytics", "sub": str(USER),
            "iat": now - 1, "nbf": now - 1, "exp": now + (299 if scope == "collect" else 59),
            "role": role, "scope": scope, "course_ids": ["backend"], "subject_refs": [], "global_read": False}


def sign(key, body, kid="key-1", headers=None):
    return jwt.encode(body, key, algorithm="EdDSA", headers={"typ": "JWT", "kid": kid, **(headers or {})})


def verify(key, token):
    return GrantVerifier(issuer="synthetic-issuer", public_keys={"key-1": public(key)}).verify(token)


class Fence:
    def __init__(self, permitted=True):
        self.permitted = permitted
        self.seen = []
    async def permits(self, subject):
        self.seen.append(subject)
        if self.permitted == "error":
            raise RuntimeError("private backend details")
        return self.permitted


class SelectiveFence:
    def __init__(self, blocked):
        self.blocked = blocked
        self.seen = []
    async def permits(self, subject):
        self.seen.append(subject)
        return subject not in self.blocked


def run(coro):
    return asyncio.run(coro)


def test_real_signed_collection_bound_to_grant_and_fence(keys):
    key, _ = keys
    grant = verify(key, sign(key, claims()))
    fence = Fence()
    identity = run(authorize_collection(grant, course_id="backend", fence=fence))
    assert identity.subject_ref == USER and identity.producer_namespace == f"browser:{USER}" and fence.seen == [USER]
    with pytest.raises(AuthorizationError):
        run(authorize_collection(grant, course_id="other", fence=fence))
    with pytest.raises(AuthorizationError):
        run(authorize_collection(grant, course_id="backend", fence=Fence(False)))
    with pytest.raises(FenceUnavailable, match="privacy fence unavailable") as error:
        run(authorize_collection(grant, course_id="backend", fence=Fence("error")))
    assert "private" not in str(error.value)
    with pytest.raises(AuthorizationError):
        run(authorize_collection(replace(grant, expires_at=0), course_id="backend", fence=fence))


@pytest.mark.parametrize("change", [
    {"iss": "other"}, {"aud": "analytics-control"}, {"aud": ["analytics"]},
    {"exp": 1}, {"nbf": 4102444800, "exp": 4102444900}, {"iat": 4102444800, "nbf": 4102444800, "exp": 4102444810},
    {"role": "admin"}, {"role": "leader"}, {"scope": "report:read"},
    {"exp": 4102444800}, {"sub": "not-a-uuid"}, {"iat": "1234"}, {"global_read": 1},
])
def test_signed_invalid_claims_are_denied(keys, change):
    key, _ = keys
    with pytest.raises(AuthenticationError, match="invalid credential"):
        verify(key, sign(key, dict(claims(), **change)))


def test_explicit_expiry_and_token_signature_protection(keys):
    key, other = keys
    token = sign(key, claims())
    assert verify(key, token).sub == USER
    with pytest.raises(AuthenticationError):
        verify(key, sign(other, claims()))
    header, body, signature = token.split(".")
    modified = dict(claims(), sub=str(OTHER))
    changed = base64.urlsafe_b64encode(json.dumps(modified).encode()).rstrip(b"=").decode()
    with pytest.raises(AuthenticationError):
        verify(key, f"{header}.{changed}.{signature}")
    with pytest.raises(AuthenticationError):
        verify(key, sign(key, claims(), kid="unknown"))
    expired = claims(); expired.update(iat=int(time.time()) - 70, nbf=int(time.time()) - 70, exp=int(time.time()) - 1)
    with pytest.raises(AuthenticationError):
        verify(key, sign(key, expired))
    for field in ("iss", "aud", "sub", "iat", "nbf", "exp"):
        incomplete = claims()
        del incomplete[field]
        with pytest.raises(AuthenticationError):
            verify(key, sign(key, incomplete))


def compact_signed_raw(key, header, payload):
    def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    signing_input = (b64(header) + "." + b64(payload)).encode()
    return signing_input.decode() + "." + b64(key.sign(signing_input))


def test_malformed_header_duplicate_json_algorithm_and_limits(keys):
    key, _ = keys
    valid_payload = json.dumps(claims()).encode()
    headers = [b'{"typ":"JWT","alg":"EdDSA","kid":"key-1","kid":"key-1"}',
               b'{"typ":"JWT","alg":"none","kid":"key-1"}',
               b'{"typ":"JWT","alg":"EdDSA","kid":"key-1","jku":"https://invalid"}',
               b'{"typ":"JWT","alg":"EdDSA","kid":"' + b'x' * 1100 + b'"}']
    for header in headers:
        with pytest.raises(AuthenticationError):
            verify(key, compact_signed_raw(key, header, valid_payload))
    header = b'{"typ":"JWT","alg":"EdDSA","kid":"key-1"}'
    duplicate = b'{"sub":"forged",' + valid_payload[1:]
    with pytest.raises(AuthenticationError):
        verify(key, compact_signed_raw(key, header, duplicate))
    for token in ("", "a.b.c", "a" * (MAX_TOKEN_BYTES + 1), "é.a.b", "a=.b.c"):
        with pytest.raises(AuthenticationError):
            verify(key, token)


def test_key_rotation_and_separate_control_trust(keys):
    old, new = keys
    old_token, new_token = sign(old, claims(), "old"), sign(new, claims(), "new")
    overlap = GrantVerifier(issuer="synthetic-issuer", public_keys={"old": public(old), "new": public(new)})
    assert overlap.verify(old_token) and overlap.verify(new_token)
    rotated = GrantVerifier(issuer="synthetic-issuer", public_keys={"new": public(new)})
    with pytest.raises(AuthenticationError): rotated.verify(old_token)
    assert rotated.verify(new_token)
    with pytest.raises(ValueError):
        build_verifiers(grant_issuer="synthetic-issuer", grant_keys={"g": public(old)}, producer_issuer="synthetic-control", producer_keys={"p": public(old)}, allowed_producers={"synthetic-producer"})
    g, p = build_verifiers(grant_issuer="synthetic-issuer", grant_keys={"old": public(old)}, producer_issuer="synthetic-control", producer_keys={"new": public(new)}, allowed_producers={"synthetic-producer"})
    now = int(time.time())
    producer = {"grant_version": 1, "iss": "synthetic-control", "aud": "analytics-control", "sub": "synthetic-producer", "iat": now - 1, "nbf": now - 1, "exp": now + 30, "scope": "control:write"}
    control_token = sign(new, producer, "new")
    assert p.verify(control_token).scope == "control:write"
    with pytest.raises(AuthenticationError): g.verify(control_token)
    with pytest.raises(AuthenticationError): p.verify(old_token)
    with pytest.raises(AuthenticationError): p.verify(sign(new, dict(producer, scope="collect"), "new"))
    with pytest.raises(AuthenticationError): p.verify(sign(new, dict(producer, sub="other-producer"), "new"))


def test_reports_three_roles_and_empty_roster(keys):
    key, _ = keys
    user = verify(key, sign(key, claims(scope="report:read")))
    leader_claims = dict(claims(scope="report:read"), role="leader", sub=str(LEADER), subject_refs=[str(USER)])
    leader = verify(key, sign(key, leader_claims))
    empty_leader = verify(key, sign(key, dict(leader_claims, subject_refs=[])))
    admin_claims = dict(claims(scope="report:read"), role="admin", sub=str(OTHER), global_read=True, course_ids=[])
    admin = verify(key, sign(key, admin_claims))
    for grant, course, subject in ((user, "backend", USER), (leader, "backend", USER), (admin, "other-course", USER)):
        run(authorize_report(grant, course_id=course, subject_ref=subject, fence=Fence()))
    for grant, course, subject in ((user, "backend", OTHER), (user, "other-course", USER), (leader, "other-course", USER), (leader, "backend", OTHER), (empty_leader, "backend", USER)):
        with pytest.raises(AuthorizationError):
            run(authorize_report(grant, course_id=course, subject_ref=subject, fence=Fence()))
    with pytest.raises(AuthorizationError):
        run(authorize_collection(leader, course_id="backend", fence=Fence()))
    with pytest.raises(AuthorizationError):
        run(authorize_report(admin, course_id="backend", subject_ref=USER, fence=Fence(False)))
    for grant in (leader, admin):
        fence = SelectiveFence({grant.sub})
        with pytest.raises(AuthorizationError):
            run(authorize_report(grant, course_id="backend", subject_ref=USER, fence=fence))
        assert fence.seen == [grant.sub]
    fence = SelectiveFence({USER})
    with pytest.raises(AuthorizationError):
        run(authorize_report(leader, course_id="backend", subject_ref=USER, fence=fence))
    assert fence.seen == [LEADER, USER]
    fence = SelectiveFence(set())
    run(authorize_report(user, course_id="backend", subject_ref=USER, fence=fence))
    assert fence.seen == [USER]
    fence = SelectiveFence(set())
    with pytest.raises(AuthorizationError):
        run(authorize_report(leader, course_id="backend", subject_ref=OTHER, fence=fence))
    assert fence.seen == []


def test_real_http_dependency_only_in_test_app(keys):
    key, _ = keys
    verifier = GrantVerifier(issuer="synthetic-issuer", public_keys={"key-1": public(key)})
    app = FastAPI()
    def authenticate(request: Request):
        try:
            return verifier.verify(extract_bearer(request.scope["headers"]))
        except AuthenticationError:
            raise HTTPException(status_code=401, detail="unauthorized") from None
    @app.get("/test-only")
    async def protected(grant=Depends(authenticate)):
        try:
            identity = await authorize_collection(grant, course_id="backend", fence=Fence())
            return {"subject_ref": str(identity.subject_ref)}
        except AuthorizationError:
            raise HTTPException(status_code=403, detail="forbidden") from None
    @app.get("/test-only-report")
    async def report(grant=Depends(authenticate)):
        try:
            await authorize_report(grant, course_id="backend", subject_ref=USER, fence=Fence())
            return {"status": "allowed"}
        except AuthorizationError:
            raise HTTPException(status_code=403, detail="forbidden") from None
    with TestClient(app) as client:
        assert client.get("/test-only").status_code == 401
        assert client.get("/test-only", headers={"Authorization": "Bearer " + sign(key, claims())}).json() == {"subject_ref": str(USER)}
        token = sign(key, claims(scope="report:read"))
        assert client.get("/test-only", headers={"Authorization": "Bearer " + token}).status_code == 403
        assert client.get("/test-only-report", headers={"Authorization": "Bearer " + token}).json() == {"status": "allowed"}
        assert client.get("/test-only-report", headers={"Authorization": "Bearer " + sign(key, claims())}).status_code == 403
        assert client.get("/test-only", headers=[("Authorization", "Bearer " + token), ("authorization", "Bearer " + token)]).status_code == 401


@pytest.mark.parametrize("headers", [[], [(b"authorization", b"Bearer")], [(b"authorization", b"Bearer  token")],
    [(b"authorization", b"Basic token")], [(b"authorization", b"Bearer token\r\n")],
    [(b"authorization", b"Bearer " + b"x" * (MAX_TOKEN_BYTES + 1))],
    [(b"authorization", b"Bearer a"), (b"Authorization", b"Bearer b")]])
def test_bearer_header_rejects_ambiguous_or_invalid_input(headers):
    with pytest.raises(AuthenticationError):
        extract_bearer(headers)


def test_bearer_header_accepts_case_insensitive_scheme():
    assert extract_bearer([(b"authorization", b"bEaReR abc.def.ghi")]) == "abc.def.ghi"


def test_synthetic_issuer_cli_local_only_and_explicit_token_output():
    root = Path(__file__).resolve().parents[2]
    local = root / ".local"
    local.mkdir(exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1")
    command = [sys.executable, "-B", str(root / "tools" / "synthetic_issuer.py")]
    with tempfile.TemporaryDirectory(prefix="auth-cli-test-", dir=local) as directory:
        directory = Path(directory)
        private, pub, claim_file = directory / "private.key", directory / "public.pem", directory / "claims.json"
        create = subprocess.run(command + ["keygen", "--private-key", str(private), "--public-key", str(pub)], env=env, capture_output=True, text=True)
        assert create.returncode == 0 and create.stdout.strip() == "synthetic key pair created"
        assert "PRIVATE KEY" not in create.stdout + create.stderr
        second = subprocess.run(command + ["keygen", "--private-key", str(private), "--public-key", str(pub)], env=env, capture_output=True, text=True)
        assert second.returncode != 0 and "PRIVATE KEY" not in second.stdout + second.stderr
        claim_file.write_text(json.dumps(claims()), encoding="utf-8")
        arguments = ["issue", "--private-key", str(private), "--kid", "key-1", "--claims-file", str(claim_file), "--kind", "grant"]
        denied = subprocess.run(command + arguments, env=env, capture_output=True, text=True)
        assert denied.returncode != 0 and not denied.stdout
        issued = subprocess.run(command + arguments + ["--emit-token"], env=env, capture_output=True, text=True)
        assert issued.returncode == 0 and not issued.stderr
        assert GrantVerifier(issuer="synthetic-issuer", public_keys={"key-1": pub.read_bytes()}).verify(issued.stdout.strip()).sub == USER
        outside = subprocess.run(command + ["keygen", "--private-key", str(root / "outside.key"), "--public-key", str(pub)], env=env, capture_output=True, text=True)
        assert outside.returncode != 0 and not outside.stdout


def test_lifespan_loads_role_specific_public_keys_and_cleans_up(keys):
    grant_key, producer_key = keys
    env = {"ANALYTICS_COLLECTOR_DATABASE_URL": "postgresql://analytics_user@localhost:57392/analytics_test",
           "ANALYTICS_COLLECTOR_GRANT_ISSUER": "synthetic-issuer",
           "ANALYTICS_COLLECTOR_GRANT_KEYS_JSON": json.dumps({"key-1": public(grant_key).decode()}),
           "ANALYTICS_COLLECTOR_PRODUCER_ISSUER": "synthetic-control",
           "ANALYTICS_COLLECTOR_PRODUCER_KEYS_JSON": json.dumps({"control-1": public(producer_key).decode()}),
           "ANALYTICS_COLLECTOR_PRODUCER_ALLOWLIST_JSON": json.dumps(["synthetic-producer"])}
    settings = load_settings("collector", env)
    assert "BEGIN PUBLIC KEY" not in repr(settings)
    app = create_app("collector", settings)
    with TestClient(app) as client:
        assert client.get("/live").status_code == 200 and client.get("/ready").status_code == 503
        assert app.state.grant_verifier.verify(sign(grant_key, claims())).sub == USER
        now = int(time.time())
        producer = {"grant_version": 1, "iss": "synthetic-control", "aud": "analytics-control", "sub": "synthetic-producer", "iat": now, "nbf": now, "exp": now + 30, "scope": "control:write"}
        assert app.state.producer_verifier.verify(sign(producer_key, producer, "control-1")).sub == "synthetic-producer"
        assert client.post("/api/analytics/events/batch").status_code == 503
    assert not hasattr(app.state, "grant_verifier") and not hasattr(app.state, "producer_verifier")
    unconfigured = create_app("query", Settings(database_url=env["ANALYTICS_COLLECTOR_DATABASE_URL"]))
    with TestClient(unconfigured) as client:
        assert unconfigured.state.grant_verifier is None and client.get("/ready").status_code == 503


def test_invalid_runtime_configuration_fails_closed_and_is_sanitized(keys):
    key, _ = keys
    url = "postgresql://analytics_user:private-password@localhost:57392/analytics_test"
    good_grant = {"grant_issuer": "synthetic-issuer", "grant_keys_json": json.dumps({"key-1": public(key).decode()})}
    same_key_control = {"producer_issuer": "synthetic-control", "producer_keys_json": json.dumps({"other": public(key).decode()}), "producer_allowlist_json": '["synthetic-producer"]'}
    cases = [({"grant_issuer": "synthetic-issuer"}, "collector"),
             ({"grant_issuer": "synthetic-issuer", "grant_keys_json": '{"key-1":"secret-marker","key-1":"x"}'}, "collector"),
             ({"grant_issuer": "synthetic-issuer", "grant_keys_json": "x" * (64 * 1024 + 1)}, "collector"),
             ({**good_grant, **same_key_control}, "collector"),
             ({**same_key_control}, "query"), (good_grant, "processor")]
    for fields, role in cases:
        settings = Settings(database_url=url, **fields)
        with pytest.raises(ValueError, match="^invalid authentication configuration$") as error:
            load_verifiers(settings, role=role)
        assert "private-password" not in str(error.value) and "secret-marker" not in str(error.value)
    bad = create_app("collector", Settings(database_url=url, grant_issuer="synthetic-issuer"))
    with pytest.raises(ValueError, match="^invalid authentication configuration$"):
        with TestClient(bad):
            pass
    assert not hasattr(bad.state, "grant_verifier")
