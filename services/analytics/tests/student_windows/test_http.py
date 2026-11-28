import copy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.factory import create_app
from pymentor_analytics.contracts.windows_v1 import WindowState, receive_snapshot, snapshot_digest
from pymentor_analytics.modules.collector.api.windows_router import PATH
from pymentor_analytics.modules.collector.ports import IntakeForbidden, IntakeUnavailable

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "contracts/fixtures/windows-v1/scenarios.json").read_text(encoding="utf-8"))


class Ready:
    async def is_ready(self):
        return True


class Closed:
    async def is_ready(self):
        return False


class Fence:
    def __init__(self, allowed=True):
        self.allowed = allowed

    async def permits(self, _):
        return self.allowed


class Catalog:
    async def known(self, *, course_id, catalog_version, lesson_id, deadline):
        course = FIXTURE["catalog"].get(course_id, {})
        return course.get("version") == catalog_version and lesson_id in course.get("lesson_ids", ())


class MemoryStore:
    """Logs calls and uses the pure state transition. PostgreSQL is tested in 605.B."""
    def __init__(self, error=None):
        self.calls = []
        self.states = {}
        self.error = error

    async def write_window(self, key, snapshot, *, received_at):
        self.calls.append((key, snapshot, received_at))
        if self.error:
            raise self.error
        transition = receive_snapshot(self.states.get(key, WindowState()), snapshot)
        self.states[key] = transition.state
        return transition


@pytest.fixture
def config():
    signer, producer = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()

    def pem(key):
        return key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()

    manifest = {"grant": {"issuer": "core-test", "keys": {"g": pem(signer)}},
                "producer": {"issuer": "test-control", "keys": {"p": pem(producer)}, "allowlist": ["test-producer"]}}
    settings = Settings(
        database_url="postgresql://analytics_user@localhost:57392/analytics_test",
        trust_manifest_json=json.dumps(manifest), windows_intake_enabled=True,
        windows_cohort=FIXTURE["student_id"], windows_t0=datetime.now(timezone.utc) - timedelta(days=1),
    )
    return signer, settings


@pytest.fixture
def sample():
    body = copy.deepcopy(FIXTURE["cases"]["602-А"]["batch"])
    moment = datetime.now(timezone(timedelta(hours=3))) - timedelta(minutes=30)
    body["windows"][0].update(activity_date=moment.date().isoformat(), slot_no=moment.hour * 4 + moment.minute // 15 + 1)
    return body


def credentials(signer, body, *, changed=None, denials=None, omit_proof=False):
    timestamp = int(time.time())
    claims = {**FIXTURE["grants"]["A"], "iat": timestamp, "nbf": timestamp, "exp": timestamp + 299,
              **(changed or {})}

    def encode(values):
        return jwt.encode(values, signer, algorithm="EdDSA", headers={"kid": "g"})

    token = encode(claims)
    raw = body if isinstance(body, bytes) else json.dumps(body, separators=(",", ":")).encode()
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    if not omit_proof:
        size = len(body["windows"]) if isinstance(body, dict) else 1
        headers["X-Analytics-Windows-Authorization"] = encode({
            "iss": "core-test", "aud": "analytics-windows-gateway", "sub": claims["sub"],
            "iat": timestamp, "nbf": timestamp, "exp": timestamp + 15, "scope": "windows:forward",
            "body_sha256": sha256(raw).hexdigest(), "grant_sha256": sha256(token.encode()).hexdigest(),
            "rejected": denials if denials is not None else [None] * size,
        })
    return raw, headers


def runtime(config, store, *, privacy=True, allowed=True):
    _, settings = config
    return create_app("collector", settings, windows_store=store, windows_readiness=Ready() if privacy else Closed(),
                      catalog=Catalog(), fence=Fence(allowed))


def post(client, config, body, **kwargs):
    raw, headers = credentials(config[0], body, **kwargs)
    return client.post(PATH, content=raw, headers=headers)


def test_compound_snapshot_round_trip_and_same_receipt(config, sample):
    store = MemoryStore()
    app = runtime(config, store)
    schema = app.openapi()
    assert schema["paths"][PATH]["post"]["operationId"] == "collectActivityWindowBatch"
    assert "WindowWireBatch" in schema["components"]["schemas"]
    with TestClient(app) as client:
        first = post(client, config, sample)
        repeat = post(client, config, sample)
    assert first.status_code == repeat.status_code == 200
    ack, duplicate = first.json()["items"][0], repeat.json()["items"][0]
    assert (ack["status"], duplicate["status"]) == ("committed", "duplicate")
    key, window, _ = store.calls[0]
    assert str(key.student_id) == FIXTURE["student_id"]
    assert ack["seq"] == sample["windows"][0]["seq"]
    assert ack["writer_id"] == sample["windows"][0]["writer_id"]
    assert ack["activity_date"] == sample["windows"][0]["activity_date"]
    assert ack["slot_no"] == sample["windows"][0]["slot_no"]
    assert ack["index"] == 0
    assert ack["payload_hash"] == duplicate["payload_hash"] == snapshot_digest(window)
    assert ack["confirmed"] == duplicate["confirmed"]
    assert ack["confirmed"]["revision"] == 1
    assert ack["confirmed"]["stats"] == FIXTURE["cases"]["602-А"]["expected"]["stats"]


@pytest.mark.parametrize("case", ["expired", "audience", "no-proof", "tampered-body"])
def test_credentials_and_core_proof_precede_write(config, sample, case):
    signer, _ = config
    changed = {}
    if case == "expired":
        changed = {"iat": int(time.time()) - 300, "nbf": int(time.time()) - 300, "exp": int(time.time()) - 1}
    elif case == "audience":
        changed = {"aud": "analytics"}
    raw, headers = credentials(signer, sample, changed=changed, omit_proof=case == "no-proof")
    if case == "tampered-body":
        raw += b" "
    store = MemoryStore()
    with TestClient(runtime(config, store)) as client:
        response = client.post(PATH, content=raw, headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert not store.calls


@pytest.mark.parametrize("field", ["subject_ref", "student_id"])
def test_subject_override_denied_as_item(config, sample, field):
    sample["windows"][0][field] = FIXTURE["other_lifecycle"]
    store = MemoryStore()
    with TestClient(runtime(config, store)) as client:
        response = post(client, config, sample)
    assert response.status_code == 200
    ack = response.json()["items"][0]
    assert (ack["status"], ack["code"], ack["confirmed"]) == ("rejected", "subject_forbidden", None)
    assert not store.calls


@pytest.mark.parametrize("authorized", [False, True])
def test_global_tool_scope(config, sample, authorized):
    sample["windows"][0].update(
        catalog_versions={}, stats=[{"tool_id": "catalog", "course_id": None, "lesson_id": None, "active_ms": 30000}],
    )
    store = MemoryStore()
    with TestClient(runtime(config, store)) as client:
        response = post(client, config, sample, changed=None if authorized else {"tool_scopes": []})
    assert response.status_code == 200
    assert response.json()["items"][0]["code"] == ("committed" if authorized else "tool_forbidden")
    assert len(store.calls) == int(authorized)


@pytest.mark.parametrize("deny_in_core", [False, True])
def test_cross_course_denies_entire_key_and_other_key_commits(config, sample, deny_in_core):
    first = sample["windows"][0]
    other = copy.deepcopy(first)
    other["slot_no"] = first["slot_no"] + (1 if first["slot_no"] < 96 else -1)
    first["catalog_versions"]["sql"] = "sql-v1"
    first["stats"][0].update(course_id="sql", lesson_id="1")
    sample["windows"].append(other)
    store = MemoryStore()
    with TestClient(runtime(config, store)) as client:
        response = post(client, config, sample,
                        changed={"course_ids": ["backend", "sql"]} if deny_in_core else None,
                        denials=["course_forbidden", None] if deny_in_core else None)
    assert response.status_code == 200
    first_ack, other_ack = response.json()["items"]
    assert (first_ack["index"], first_ack["code"], first_ack["confirmed"]) == (0, "course_forbidden", None)
    assert (other_ack["index"], other_ack["code"]) == (1, "committed")
    assert len(store.calls) == 1 and store.calls[0][0].slot_no == other["slot_no"]


@pytest.mark.parametrize("case", ["duplicate-key", "uncorrelated", "duplicate-json", "unsupported", "batch-bytes", "item-bytes"])
def test_batch_wire_boundaries(config, sample, case):
    if case == "duplicate-key":
        sample["windows"].append(sample["windows"][0])
    elif case == "uncorrelated":
        sample["windows"].append({"bad": True})
    elif case == "duplicate-json":
        sample = json.dumps(sample).replace('"protocol_version": 1', '"protocol_version": 1, "protocol_version": 1').encode()
    elif case == "unsupported":
        sample["protocol_version"] = 2
    elif case == "batch-bytes":
        sample = b" " * (64 * 1024 + 1)
    elif case == "item-bytes":
        sample = json.dumps(sample).replace('"stats":', '"stats":' + ' ' * (16 * 1024)).encode()
    store = MemoryStore()
    with TestClient(runtime(config, store)) as client:
        response = post(client, config, sample)
    if case == "item-bytes":
        assert response.status_code == 200
        assert response.json()["items"][0]["code"] == "too_large"
    else:
        expected = ("unsupported_version" if case == "unsupported" else "too_large" if case == "batch-bytes" else "invalid_request")
        assert response.status_code == (413 if case == "batch-bytes" else 400)
        assert response.json()["code"] == expected
    assert not store.calls


@pytest.mark.parametrize("case", ["closed", "deleted", "port-denied", "port-failure", "missing-store"])
def test_privacy_and_store_fail_closed(config, sample, case):
    failure = IntakeForbidden() if case == "port-denied" else IntakeUnavailable() if case == "port-failure" else None
    store = MemoryStore(error=failure)
    with TestClient(runtime(config, store if case != "missing-store" else None,
                            privacy=case != "closed", allowed=case != "deleted")) as client:
        response = post(client, config, sample)
    expected = {"closed": (503, "privacy_unavailable"), "deleted": (403, "forbidden"),
                "port-denied": (200, "privacy_forbidden"), "port-failure": (503, "unavailable"),
                "missing-store": (503, "unavailable")}
    status, code = expected[case]
    body = response.json()
    assert response.status_code == status
    assert (body["items"][0]["code"] if status == 200 else body["code"]) == code
    assert len(store.calls) == int(failure is not None)
    assert not store.states


def test_rate_limit_before_the_thirteenth_write(config, sample):
    store = MemoryStore()
    with TestClient(runtime(config, store)) as client:
        for _ in range(12):
            assert post(client, config, sample).status_code == 200
        limited = post(client, config, sample)
    assert len(store.calls) == 12
    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) > 0


def test_windows_cli_raises_the_internal_header_limit_only_when_enabled(monkeypatch):
    from types import SimpleNamespace
    from pymentor_analytics.app import cli

    calls = []
    monkeypatch.setattr(cli.sys, "argv", ["analytics", "collector"])
    monkeypatch.setattr(cli, "create_app", lambda *args: object())
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **options: calls.append(options))
    for enabled in (False, True):
        monkeypatch.setattr(cli, "load_settings", lambda role: SimpleNamespace(
            shutdown_timeout_seconds=5, real_collection=False, windows_intake_enabled=enabled,
        ))
        assert cli.main() == 0
    assert "h11_max_incomplete_event_size" not in calls[0]
    assert calls[1]["h11_max_incomplete_event_size"] == 24 * 1024


def test_all_eligible_keeps_signed_gateway_privacy_and_t0(config, sample):
    signer, base_settings = config
    subject = "605a0000-0000-4000-8000-000000000099"
    store = MemoryStore()
    enabled = Settings.model_validate({**base_settings.model_dump(),
                                       "windows_cohort": (), "windows_all_eligible": True})
    raw, headers = credentials(signer, sample, changed={"sub": subject})
    for privacy, fence, status in ((False, True, 503), (True, False, 403)):
        with TestClient(runtime((signer, enabled), store, privacy=privacy, allowed=fence)) as client:
            assert client.post(PATH, content=raw, headers=headers).status_code == status
        assert store.calls == []
    future = Settings.model_validate({**enabled.model_dump(),
                                      "windows_t0": datetime.now(timezone.utc) + timedelta(minutes=3)})
    with TestClient(runtime((signer, future), store)) as client:
        assert client.post(PATH, content=raw, headers=headers).status_code == 403
    assert store.calls == []

    with TestClient(runtime((signer, enabled), store)) as client:
        _, untrusted = credentials(signer, sample, changed={"sub": subject}, omit_proof=True)
        assert client.post(PATH, content=raw, headers=untrusted).status_code == 401
        receipt = client.post(PATH, content=raw, headers=headers)
        assert receipt.status_code == 200 and receipt.json()["items"][0]["status"] == "committed"
    assert len(store.calls) == 1 and str(store.calls[0][0].student_id) == subject

    with pytest.raises(ValueError):
        Settings.model_validate({**enabled.model_dump(), "windows_cohort": (subject,)})
    with pytest.raises(ValueError):
        Settings.model_validate({**enabled.model_dump(), "windows_t0": None})


def test_window_budget_retires_inactive_lifecycles(monkeypatch):
    from pymentor_analytics.modules.collector.api import windows_router

    now = 10
    monkeypatch.setattr(windows_router.time, "monotonic", lambda: now)
    budget = windows_router.WindowBudget(1)
    budget.admit("old")
    now += 40
    budget.admit("active")
    now += 20
    budget.admit("new")
    assert list(budget.history) == ["active", "new"]
    for _ in range(windows_router.SUBJECT_BATCHES_PER_MINUTE - 1):
        budget.admit("new")
    from pymentor_analytics.modules.collector.ports import IntakeOverloaded
    with pytest.raises(IntakeOverloaded):
        budget.admit("new")
