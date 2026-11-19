import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from hashlib import sha256
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI

from pymentor_analytics.adapters.postgres.core_snapshot_proof import PostgresSnapshotProof
from pymentor_analytics.adapters.postgres.core_transfer import PostgresCoreTransfer
from pymentor_analytics.adapters.auth.verifier import GrantVerifier, ProducerVerifier
from pymentor_analytics.contracts.core_snapshot_proof_v1 import DOMAIN, active_line, proof_fingerprint, validate_proof
from pymentor_analytics.contracts.core_transfer_v1 import entity_key, fingerprint
from pymentor_analytics.modules.collector.ports import IntakeUnavailable
from pymentor_analytics.modules.analytics.core_transfer.api.router import create_router
from pymentor_analytics.modules.analytics.core_transfer.api.legacy_router import router as legacy_router
from pymentor_analytics.modules.analytics.core_transfer.legacy_service import LegacyService


@pytest.fixture
def producer(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "core-api"))
    from pymentor_core.modules.analytics_delivery import contracts, snapshot_contract
    return contracts, snapshot_contract


class Result:
    def __init__(self, value): self.value = value
    def one_or_none(self): return self.value
    def scalar_one_or_none(self): return self.value
    def scalar_one(self): return self.value
    def all(self): return self.value


class ProofDatabase:
    def __init__(self, proof, records):
        self.subject = UUID(proof["subject_ref"])
        self.events = records
        self.proof = SimpleNamespace(producer_namespace="core", proof_id=UUID(proof["proof_id"]),
            cut_revision=proof["cut_revision"], entity_count=proof["entity_count"], entities_digest=proof["entities_digest"],
            source_created_at=datetime.fromisoformat(proof["created_at"]), canonical_hash=proof_fingerprint(proof), proof=proof)
        self.status, self.last_failure = "pending", None

    @asynccontextmanager
    async def transaction(self): yield self

    async def execute(self, statement, args=None):
        sql = str(statement)
        args = args or {}
        if "min(p.source_created_at) AS oldest" in sql:
            return Result(SimpleNamespace(subject_ref=self.subject, oldest=self.proof.source_created_at) if self.status == "pending" else None)
        if "pg_try_advisory_xact_lock" in sql: return Result(True)
        if "SELECT epoch,deleted FROM analytics.subject_fences" in sql: return Result(None)
        if "SELECT * FROM analytics.core_snapshot_proofs" in sql: return Result(self.proof)
        if "SELECT DISTINCT ON (e.entity_key COLLATE" in sql: return Result(self.events)
        if "SELECT cut_revision FROM analytics.core_snapshot_proofs" in sql: return Result(None)
        if "SET attempts=attempts+1" in sql:
            self.last_failure = args["failure"]
            return Result(None)
        if "SET status='verified',subject_epoch" in sql:
            self.status = "verified"
            return Result(None)
        return Result(None)


def row(event, *, applied=True, projected_revision=None):
    return SimpleNamespace(entity_key=entity_key(event), revision=event["revision"], event_id=UUID(event["event_id"]),
        canonical_hash=fingerprint(event), fact_hash=fingerprint(event), source_kind="core_transfer_v1",
        accepted_event=event, projected_revision=event["revision"] if projected_revision is None else projected_revision, applied=applied)


def test_real_core_and_analytics_canonical_proof_compatibility(producer):
    contract, snapshot = producer
    subject = uuid4()
    events = [contract.activity(subject, date(2026, 11, 16), None, revision=0),
              contract.progress(subject, "курс", "lesson", datetime(2026, 11, 16, tzinfo=timezone.utc), revision=3)]
    proof = snapshot.make_proof(subject, 3, events, datetime(2026, 11, 17, tzinfo=timezone.utc))
    assert validate_proof(proof, snapshot.proof_fingerprint(proof)) == proof
    assert proof_fingerprint(proof) == snapshot.proof_fingerprint(proof)
    digest = sha256(DOMAIN)
    for event in sorted(events, key=entity_key): digest.update(active_line(event))
    assert digest.hexdigest() == proof["entities_digest"]
    bad = dict(proof, entity_count=3)
    with pytest.raises(ValueError): validate_proof(bad, proof_fingerprint(bad))


def test_verifier_waits_for_all_facts_and_durable_projection_receipts(producer):
    contract, snapshot = producer
    subject = uuid4()
    first = contract.activity(subject, date(2026, 11, 16), None, revision=0)
    second = contract.progress(subject, "backend", "lesson", datetime(2026, 11, 16, tzinfo=timezone.utc), revision=3)
    proof = snapshot.make_proof(subject, 3, [first, second], datetime(2026, 11, 17, tzinfo=timezone.utc))
    async def scenario():
        db = ProofDatabase(proof, [])
        worker = PostgresSnapshotProof(db)
        assert await worker.project_one(deadline=time.monotonic() + 2)
        assert db.status == "pending" and db.last_failure == "snapshot_pending"
        db.events = [row(first)]
        assert await worker.project_one(deadline=time.monotonic() + 2)
        assert db.status == "pending" and db.last_failure == "snapshot_pending"
        db.events = sorted([row(first), row(second, applied=False)], key=lambda item: item.entity_key)
        assert await worker.project_one(deadline=time.monotonic() + 2)
        assert db.status == "pending" and db.last_failure == "projection_pending"
        db.events = sorted([row(first), row(second)], key=lambda item: item.entity_key)
        assert await worker.project_one(deadline=time.monotonic() + 2)
        assert db.status == "verified"
    asyncio.run(scenario())


def test_coverage_rejects_newer_projection_and_mismatched_cut():
    subject = uuid4()
    class CoverageSession:
        def __init__(self): self.newer, self.cut, self.epoch = False, 3, 0
        async def execute(self, statement, args):
            assert "core_transfer_entities" not in str(statement)
            return Result([SimpleNamespace(subject_ref=subject, required_cut=args["cuts"][0], cut_revision=self.cut,
                subject_epoch=0, current_epoch=self.epoch, newer_projection=self.newer, source_created_at=datetime.now(timezone.utc))])
    async def scenario():
        session = CoverageSession()
        reader = PostgresCoreTransfer(object())
        coverage = await reader._coverage(session, "core", [subject], {subject: 3})
        assert coverage[0]["coverage_through_revision"] == 3
        session.newer = True
        with pytest.raises(IntakeUnavailable): await reader._coverage(session, "core", [subject], {subject: 3})
        session.newer = False
        with pytest.raises(IntakeUnavailable): await reader._coverage(session, "core", [subject], {subject: 2})
        session.epoch = 1
        with pytest.raises(IntakeUnavailable): await reader._coverage(session, "core", [subject], {subject: 3})
        session.epoch, session.cut = 0, None
        with pytest.raises(IntakeUnavailable): await reader._coverage(session, "core", [subject], {subject: 3})
    asyncio.run(scenario())


def test_core_http_proof_transport_against_analytics_router(producer):
    """Real Core transport, P1 verifier and ASGI router; storage remains in memory."""
    contract, snapshot = producer
    from pymentor_core.adapters.analytics_http.transport import TransferHttp
    from pymentor_core.modules.analytics_delivery.relay import Receipt
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    verifier = ProducerVerifier(issuer="producer-issuer", public_keys={"p": public}, allowed_producers={"core"})
    class Ready:
        async def is_ready(self): return True
    class Store:
        def __init__(self): self.saved, self.coverage = {}, "pending"
        async def accept(self, namespace, proof, digest, *, deadline):
            validate_proof(proof, digest)
            assert namespace == "core-v1"
            self.saved[proof["proof_id"]] = digest
            return "accepted"
        async def receipt(self, namespace, proof_id, digest, *, deadline):
            known = self.saved.get(str(proof_id))
            return ({"status": "absent", "coverage": None} if known is None else
                    {"status": "conflict", "coverage": None} if known != digest else
                    {"status": "accepted", "coverage": self.coverage})
    store = Store()
    app = FastAPI()
    app.include_router(create_router(service=object(), verifier=verifier, namespace_by_producer={"core": "core-v1"},
                                     readiness=Ready(), proof_service=store))
    now = int(time.time())
    token = jwt.encode({"grant_version": 1, "iss": "producer-issuer", "aud": "analytics-control", "sub": "core",
        "iat": now - 1, "nbf": now - 1, "exp": now + 30, "scope": "control:write"}, key,
        algorithm="EdDSA", headers={"typ": "JWT", "kid": "p"})
    subject = uuid4()
    event = contract.activity(subject, date(2026, 11, 16), None, revision=0)
    proof = snapshot.make_proof(subject, 0, [event], datetime(2026, 11, 17, tzinfo=timezone.utc))
    async def scenario():
        transport = TransferHttp("http://127.0.0.1:12345", lambda: token, transport=httpx.ASGITransport(app=app))
        digest = snapshot.proof_fingerprint(proof)
        assert await transport.lookup_proof(proof["proof_id"], digest, 2) == (Receipt.ABSENT, None)
        assert await transport.send_proof(proof, 2) is Receipt.ACCEPTED
        assert await transport.lookup_proof(proof["proof_id"], digest, 2) == (Receipt.ACCEPTED, "pending")
        store.coverage = "verified"
        assert await transport.lookup_proof(proof["proof_id"], digest, 2) == (Receipt.ACCEPTED, "verified")
        assert await transport.lookup_proof(proof["proof_id"], "0" * 64, 2) == (Receipt.CONFLICT, None)
    asyncio.run(scenario())


def test_core_legacy_read_client_accepts_complete_analytics_responses(producer):
    """Actual Core read decoder + G1 auth + Query service; PostgreSQL is stubbed."""
    pytest.importorskip("bcrypt", reason="run this cross-service contract test with the Core test environment")
    from pymentor_core.adapters.analytics_http.legacy_read import LegacyReadClient
    subject, admin = uuid4(), uuid4()
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    class Store:
        database = SimpleNamespace(transaction_guard=True)
        async def read_subject(self, namespace, ref, today, *, deadline, require_coverage=False, visit_through=None):
            assert namespace == "core" and ref == subject and require_coverage
            return ([{"activity_date": today.isoformat(), "visited_at": None}], [],
                    {"subject_ref": str(subject), "coverage_through_revision": 3, "source_snapshot_at": "2026-11-17T00:00:00+00:00"})
        async def read_admin(self, namespace, subjects, today, *, deadline, actor=None, required_revisions=None):
            assert actor == admin and required_revisions == {subject: 3, admin: 2}
            return ([{"subject_ref": str(subject), "activity_date": today.isoformat()}],
                    [{"subject_ref": str(subject), "coverage_through_revision": 3, "source_snapshot_at": "2026-11-17T00:00:00+00:00"},
                     {"subject_ref": str(admin), "coverage_through_revision": 2, "source_snapshot_at": "2026-11-17T00:00:00+00:00"}])
    class Ready:
        async def is_ready(self): return True
    app = FastAPI()
    app.include_router(legacy_router)
    app.state.trust_manifest_validated = True
    app.state.grant_verifier = GrantVerifier(issuer="grant-issuer", public_keys={"g": public})
    app.state.core_transfer_probe = Ready()
    app.state.settings = SimpleNamespace(db_timeout_seconds=3)
    app.state.legacy_service = LegacyService(Store(), "core", None, require_coverage=True)
    now = int(time.time())
    def token(role, sub, roster=None):
        claims = {"grant_version": 1, "iss": "grant-issuer", "aud": "analytics", "sub": str(sub),
                  "iat": now-1, "nbf": now-1, "exp": now+30, "role": role, "scope": "report:read",
                  "course_ids": ["backend"] if role in {"user", "leader"} else [], "subject_refs": [], "global_read": role == "admin"}
        if roster is not None: claims["legacy_admin_roster_v1"] = roster
        return jwt.encode(claims, key, algorithm="EdDSA", headers={"typ": "JWT", "kid": "g"})
    async def scenario():
        client = LegacyReadClient("http://127.0.0.1:12345", transport=httpx.ASGITransport(app=app))
        self_result = await client.read("summary", token("user", subject), deadline_seconds=2)
        assert self_result["coverage"] == "complete" and self_result["coverage_through_revision"] == 3
        for role in ("leader", "admin"):
            assert (await client.read("summary", token(role, subject), deadline_seconds=2))["coverage"] == "complete"
        roster = {"version": 1, "members": [{"subject_ref": str(subject), "revision": 3, "student": True},
                                            {"subject_ref": str(admin), "revision": 2, "student": False}]}
        admin_result = await client.read("admin", token("admin", admin, roster), deadline_seconds=2)
        assert admin_result["coverage_subjects"][0]["subject_ref"] == str(subject)
        assert admin_result["data"]["dashboard_activity"]["active_students_7d"] == 1
    asyncio.run(scenario())
