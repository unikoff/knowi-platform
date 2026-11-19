import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import time
from types import SimpleNamespace
from uuid import uuid4
import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from pymentor_analytics.adapters.postgres.core_transfer import CheckedDeletionStatus
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig, _DOMAIN
from pymentor_analytics.contracts.privacy_control_v1 import AuthorityProtocolError, canonical_json, deletion_hash
from pymentor_analytics.contracts.v2 import DeletionFence


class Result:
    def __init__(self, value): self.value = value
    def scalar_one_or_none(self): return self.value
    def scalar_one(self): return self.value
    def one_or_none(self): return self.value
    def one(self): return self.value


class Database:
    def __init__(self, entry):
        self.entry, self.applied, self.remaining, self.phase = entry, 1, False, "complete"
    @asynccontextmanager
    async def transaction(self): yield self
    async def execute(self, statement, args=None):
        sql = str(statement)
        if "SELECT seq FROM analytics.privacy_receipts" in sql: return Result(1)
        if "pg_attribute" in sql: return Result((True, True, True))
        if "to_regclass('analytics.core_snapshot_proofs')" in sql: return Result((False, False, False, False))
        if "to_regclass('analytics.activity_projection_buckets')" in sql: return Result((False,) * 8)
        if "to_regclass('analytics.activity_projection_day_sources')" in sql: return Result((False,) * 3)
        if "to_regclass('analytics.activity_course_buckets')" in sql: return Result((False, False))
        if "SELECT r.payload_hash" in sql:
            return Result(SimpleNamespace(payload_hash=self.entry.payload_hash, subject_ref=self.entry.control.subject_ref,
                fence_epoch=1, phase=self.phase, authority_id="test-authority", applied_through=self.applied,
                current_epoch=1, deleted=True))
        if "EXISTS(SELECT 1 FROM analytics.activity_facts" in sql: return Result(self.remaining)
        return Result(None)


def signed_client(entry):
    key = Ed25519PrivateKey.generate()
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    secret = base64.urlsafe_b64encode(b"a" * 32).rstrip(b"=").decode()
    cfg = AuthorityConfig.from_json(json.dumps({"url": "http://127.0.0.1:12345", "authority_id": "test-authority",
        "issuer": "test-issuer", "public_keys": {"key": pem}, "reader_secret": secret, "fixture_mode": True}))
    client = AuthorityClient(cfg)
    session = uuid4()
    async def signed(path, body, *, credential=None):
        request, kind = json.loads(body), path.rsplit("/", 1)[1]
        now = int(time.time())
        frame = {"version": 1, "authority_id": "test-authority", "issuer": "test-issuer",
                 "audience": "analytics-privacy-journal", "kid": "key", "kind": kind,
                 "nonce": request["nonce"], "session": str(session), "high_watermark": 1,
                 "after": request["after"], "through": request["through"],
                 "scanned_through": 1 if kind == "page" else request["after"],
                 "entries": [entry.model_dump(mode="json")] if kind == "page" else [],
                 "issued_at": now, "expires_at": now + 3}
        signature = base64.urlsafe_b64encode(key.sign(_DOMAIN + canonical_json(frame))).rstrip(b"=").decode()
        return canonical_json({"frame": frame, "signature": signature})
    client._http = signed
    return client


def test_verified_signed_page_requires_applied_prefix_and_empty_purge():
    from pymentor_analytics.contracts.privacy_control_v1 import JournalEntry
    control = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(), subject_ref=uuid4(),
                            epoch=1, occurred_at=datetime.now(timezone.utc))
    entry = JournalEntry(seq=1, producer="core", control=control, received_at=datetime.now(timezone.utc),
                         payload_hash=deletion_hash("core", control))
    async def scenario():
        db = Database(entry)
        status = CheckedDeletionStatus(db, signed_client(entry), producer_by_namespace={"server-core": "core"})
        async def check(epoch=1, subject=None):
            return await status.status("server-core", control.control_id, subject or control.subject_ref, epoch,
                                       deadline=time.monotonic() + 2)
        assert await check() == "accepted"
        db.remaining = True
        assert await check() == "pending"
        db.remaining = False
        db.applied = 0
        assert await check() == "pending"
        db.applied = 1
        db.phase = "purging"
        assert await check() == "pending"
        assert await check(epoch=2) == "conflict"
        assert await check(subject=uuid4()) == "conflict"
    asyncio.run(scenario())


def test_tampered_authority_response_never_acknowledges():
    from pymentor_analytics.contracts.privacy_control_v1 import JournalEntry
    control = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(), subject_ref=uuid4(),
                            epoch=1, occurred_at=datetime.now(timezone.utc))
    entry = JournalEntry(seq=1, producer="core", control=control, received_at=datetime.now(timezone.utc),
                         payload_hash=deletion_hash("core", control))
    async def scenario():
        client = signed_client(entry)
        sign = client._http
        async def tampered(path, body, *, credential=None):
            response = json.loads(await sign(path, body, credential=credential))
            response["frame"]["high_watermark"] = 2
            return canonical_json(response)
        client._http = tampered
        status = CheckedDeletionStatus(Database(entry), client, producer_by_namespace={"server-core": "core"})
        with pytest.raises(AuthorityProtocolError):
            await status.status("server-core", control.control_id, control.subject_ref, 1,
                                deadline=time.monotonic() + 2)
    asyncio.run(scenario())
