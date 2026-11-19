import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import sqlite3
from uuid import uuid4, uuid5
import pytest

from pymentor_analytics.adapters.core_privacy.client import SourceConfig, CoreDeletionClient, deletion_control, RESPONSE_DOMAIN
from pymentor_analytics.adapters.core_privacy.guard import CoreDeletionGuard
from pymentor_analytics.contracts.privacy_control_v1 import deletion_hash, canonical_json
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable


def config():
    return SourceConfig.from_json(json.dumps({"url": "http://127.0.0.1:18041/api/internal/analytics/deletions", "instance_id": str(uuid4()),
        "secret": base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode()}))


def formatted(value): return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def body(c, nonce, *, when=None, events=None):
    payload = {"version": 1, "instance_id": str(c.instance_id), "nonce": str(nonce),
               "as_of": formatted(when or datetime.now(timezone.utc)), "events": events or []}
    payload["mac"] = hmac.new(c.secret, RESPONSE_DOMAIN + canonical_json(payload), hashlib.sha256).hexdigest()
    return canonical_json(payload)


def test_control_identity_and_hash_stable_across_retry():
    instance, subject = uuid4(), uuid4()
    timestamp = datetime(2026, 11, 17, tzinfo=timezone.utc)
    one = deletion_control(instance, subject, timestamp)
    two = deletion_control(instance, subject, timestamp)
    assert one.control_id == uuid5(instance, "analytics-local-delete:" + str(subject))
    assert one.schema_version == 2 and one.kind == "deletion_fence" and one.epoch == 1
    assert one.occurred_at == timestamp and deletion_hash("core-local", one) == deletion_hash("core-local", two)


def test_signed_snapshot_fail_closed_for_replay_tamper_staleness_and_duplicates():
    c, nonce = config(), uuid4()
    client = CoreDeletionClient(c)
    subject = uuid4()
    now = datetime.now(timezone.utc)
    event = {"subject_ref": str(subject), "deleted_at": formatted(now - timedelta(seconds=1))}
    raw = body(c, nonce, events=[event])
    assert client.validate(raw, nonce)[0].subject_ref == subject
    invalid = [body(c, nonce, when=now - timedelta(seconds=4)), body(c, nonce, events=[event, event]),
               raw.replace(str(subject).encode(), str(uuid4()).encode()), b"{}", b"x" * (256*1024+1)]
    for payload in invalid:
        with pytest.raises(PrivacyUnavailable): client.validate(payload, nonce)
    with pytest.raises(PrivacyUnavailable): client.validate(raw, uuid4())


def test_guard_queries_all_controls_once_and_never_fails_open():
    class Source:
        async def snapshot(self): return (deletion_control(uuid4(), uuid4(), datetime.now(timezone.utc)),)
    class Result:
        def scalar_one(self): return status.good
    class Session:
        async def execute(self, sql, params):
            status.calls += 1
            status.sql, status.payload = str(sql), json.loads(params["items"])
            return Result()
    class Status:
        good = True
        calls = 0
        sessions = None
        def __init__(self): self.sessions = self
        @asynccontextmanager
        async def begin(self): yield Session()
    status = Status()
    async def run():
        guard = CoreDeletionGuard(Source(), status, "core-local")
        await guard.check(None)
        assert status.calls == 1 and len(status.payload) == 1
        assert "jsonb_to_recordset" in status.sql and "privacy_receipts" in status.sql and "subject_fences" in status.sql
        status.good = False
        with pytest.raises(PrivacyUnavailable): await guard.check(None)
        class Broken:
            async def snapshot(self): raise OSError("offline")
        with pytest.raises(PrivacyUnavailable): await CoreDeletionGuard(Broken(), status, "core-local").check(None)
    asyncio.run(run())


def test_guard_sql_allows_monotonic_fence_only_with_exact_complete_receipt():
    """Execute the production rejection predicate with real SQL NULL semantics."""
    class Source:
        async def snapshot(self): return (deletion_control(uuid4(), uuid4(), datetime.now(timezone.utc)),)
    class Result:
        def scalar_one(self): return True
    class Status:
        def __init__(self): self.sessions = self
        @asynccontextmanager
        async def begin(self): yield self
        async def execute(self, sql, params):
            self.sql = str(sql)
            return Result()
    status = Status()
    asyncio.run(CoreDeletionGuard(Source(), status, "core-local").check(None))
    predicate = status.sql.split("WHERE r.control_id IS NULL", 1)[1].split(")", 1)[0]
    predicate = "r.control_id IS NULL" + predicate
    db = sqlite3.connect(":memory:")
    query = ("WITH e(control,subject,epoch,hash) AS (VALUES(?,?,?,?)), "
             "r(control_id,subject_ref,fence_epoch,payload_hash,phase,seq) AS (VALUES(?,?,?,?,?,?)), "
             "f(deleted,epoch) AS (VALUES(?,?)), s(applied_through) AS (VALUES(?)) "
             "SELECT (" + predicate + ") FROM e LEFT JOIN r ON r.control_id=e.control CROSS JOIN f CROSS JOIN s")
    expected = ("control", "subject", 1, "hash")
    def rejected(receipt=("control", "subject", 1, "hash", "complete", 2), fence=(1, 1), applied=2):
        return db.execute(query, expected + receipt + fence + (applied,)).fetchone()[0]
    assert rejected() == 0
    assert rejected(("control", "subject", 5, "hash", "complete", 2), (1, 5)) == 0
    assert rejected(("control", "subject", 1, "hash", "complete", 2), (1, 5)) == 0
    for receipt, fence, applied in [
        ((None, None, None, None, None, None), (None, None), None),
        (("other", "subject", 1, "hash", "complete", 2), (1, 1), 2),
        (("control", "other", 1, "hash", "complete", 2), (1, 1), 2),
        (("control", "subject", None, "hash", "complete", 2), (1, 1), 2),
        (("control", "subject", 0, "hash", "complete", 2), (1, 1), 2),
        (("control", "subject", 5, "hash", "complete", 2), (1, 4), 2),
        (("control", "subject", 1, "wrong", "complete", 2), (1, 1), 2),
        (("control", "subject", 1, "hash", "purging", 2), (1, 1), 2),
        (("control", "subject", 1, "hash", "complete", 2), (1, None), 2),
        (("control", "subject", 1, "hash", "complete", 2), (0, 1), 2),
        (("control", "subject", 1, "hash", "complete", 2), (1, 1), None),
        (("control", "subject", 1, "hash", "complete", 2), (1, 1), 1),
    ]:
        assert rejected(receipt, fence, applied) == 1


def test_http_request_binds_nonce_and_instance_to_hex_mac(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    import re
    from pymentor_analytics.adapters.core_privacy.client import REQUEST_DOMAIN
    c = config()
    class Transport:
        request = None
        def write(self, data): self.request = data.decode("ascii")
        async def drain(self): pass
        def close(self): pass
        async def wait_closed(self): pass
    transport = Transport()
    class Reader:
        async def readuntil(self, marker):
            target = transport.request.split(" ")[1]
            params = parse_qs(urlsplit(target).query)
            nonce = params["nonce"][0]
            request = {"version": 1, "instance_id": str(c.instance_id), "nonce": nonce}
            expected = hmac.new(c.secret, REQUEST_DOMAIN + canonical_json(request), hashlib.sha256).hexdigest()
            assert f"X-Analytics-Request-MAC: {expected}\r\n" in transport.request
            assert params["instance_id"] == [str(c.instance_id)]
            self.payload = body(c, __import__("uuid").UUID(nonce))
            return f"HTTP/1.1 200 OK\r\nContent-Length: {len(self.payload)}\r\n\r\n".encode()
        async def readexactly(self, n): return self.payload
    async def open_connection(*args, **kwargs): return Reader(), transport
    monkeypatch.setattr(asyncio, "open_connection", open_connection)
    assert asyncio.run(CoreDeletionClient(c).snapshot()) == ()


def test_relay_unknown_append_retries_same_control_and_cache_avoids_busy_duplicates():
    from pymentor_analytics.adapters.core_privacy.relay import CoreDeletionRelay
    c = config()
    control = deletion_control(c.instance_id, uuid4(), datetime.now(timezone.utc))
    class Source:
        async def snapshot(self): return (control,)
    class Authority:
        calls = []
        async def append(self, value, **kwargs):
            self.calls.append((value.control_id, deletion_hash(kwargs["producer"], value)))
            if len(self.calls) == 1: raise TimeoutError()
    class Settings:
        producer = "core-local"
        command_key = object()
        def token(self): return "short-lived"
    async def run():
        authority = Authority()
        relay = CoreDeletionRelay(Source(), authority, Settings())
        with pytest.raises(TimeoutError): await relay.tick()
        await relay.tick()
        await relay.tick()
        assert len(authority.calls) == 2 and authority.calls[0] == authority.calls[1]
    asyncio.run(run())


def test_relay_uses_distinct_keys_and_short_lived_verified_producer_jwt():
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from pymentor_analytics.adapters.core_privacy.relay import RelayConfig
    signer, command = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    def pem(key):
        return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption()).decode()
    fields = {"producer": "core-local", "producer_issuer": "issuer", "producer_kid": "kid",
              "producer_private_key_pem": pem(signer), "command_private_key_pem": pem(command)}
    cfg = RelayConfig.from_json(json.dumps(fields))
    claims = jwt.decode(cfg.token(), signer.public_key(), algorithms=["EdDSA"], issuer="issuer", audience="analytics-control")
    assert claims["sub"] == "core-local" and claims["scope"] == "control:write" and claims["exp"] - claims["iat"] == 60
    fields["command_private_key_pem"] = fields["producer_private_key_pem"]
    with pytest.raises(ValueError): RelayConfig.from_json(json.dumps(fields))
