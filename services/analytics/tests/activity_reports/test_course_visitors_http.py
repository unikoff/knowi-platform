import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
import httpx
import jwt
import pytest

from pymentor_analytics.adapters.auth.verifier import GrantVerifier
from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport
from pymentor_analytics.contracts.auth_v1 import AuthenticationError
from pymentor_analytics.contracts.course_visitors import (
    CourseStudyTimeReport, CourseVisitorCandidates, CourseVisitorDiscovery, CourseVisitorReport, MOSCOW,
)
from pymentor_analytics.modules.analytics.api import course_visitors_router as api


def test_private_grant_cannot_be_used_across_report_scopes_or_modify_payload(monkeypatch):
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(serialization.Encoding.PEM,
                                       serialization.PublicFormat.SubjectPublicKeyInfo)
    verifier = GrantVerifier(issuer="core", public_keys={"test": pub})
    now = datetime.now(timezone.utc)
    stamp = int(now.timestamp())
    body = json.dumps({
        "at": now.isoformat(), "nonce": "a" * 32,
        "operation": "aggregate",
        "active_subscriptions": [], "inactive_subscriptions": [],
    }).encode()
    std = {"grant_version": 1, "iss": "core", "sub": str(uuid4()), "iat": stamp, "nbf": stamp, "exp": stamp + 15}
    private = jwt.encode({**std, "aud": "analytics-course-visitors", "course_id": "backend",
                          "body_sha256": sha256(body).hexdigest()}, key, algorithm="EdDSA", headers={"kid": "test"})
    browser = jwt.encode({**std, "aud": "analytics", "role": "leader", "scope": "report:read",
                          "course_ids": ["backend"], "subject_refs": [], "global_read": False},
                         key, algorithm="EdDSA", headers={"kid": "test"})
    with pytest.raises(AuthenticationError):
        verifier.verify(private)
    assert verifier.verify(browser).role == "leader"

    class Probe:
        async def is_ready(self):
            return True

    async def read(_db, grant, snapshot):
        assert grant.course_id == "backend"
        today = snapshot.at.astimezone(MOSCOW).date()
        return CourseVisitorReport(course_id=grant.course_id, daily=[
            {"date": today - timedelta(days=29 - i), "with_subscription": None, "without_subscription": None}
            for i in range(30)
        ])

    async def discover(_db, grant, snapshot):
        assert snapshot.operation == "discover"
        return CourseVisitorCandidates(course_id=grant.course_id, subject_digests=["d" * 64])

    monkeypatch.setattr(api, "read_course_visitor_report", read)
    monkeypatch.setattr(api, "discover_course_visitor_candidates", discover)
    app = FastAPI()
    app.include_router(api.router)
    app.state.trust_manifest_validated = True
    app.state.grant_verifier = verifier
    app.state.course_visitor_slots = asyncio.Semaphore(1)
    app.state.settings = SimpleNamespace(db_timeout_seconds=2)
    app.state.probe = Probe()
    app.state.database = object()
    path = "/internal/analytics/v1/course/visitors"
    assert path not in app.openapi()["paths"]

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async def post(token, payload=body, route=path):
                return await client.post(route, content=payload, headers={
                    "Authorization": "Bearer " + token, "Content-Type": "application/json"})

            valid = await post(private)
            assert valid.status_code == 200
            assert valid.headers["Cache-Control"] == "no-store"
            assert valid.json()["quality"] == "unknown"
            assert valid.json()["daily"][-1]["with_subscription"] is None
            assert (await post(browser)).status_code == 401
            assert (await post(private, body + b" ")).status_code == 403
            assert (await post(private, route=path + "?course_id=other")).status_code == 400
            assert (await post(private, b"x" * (api.MAX_BODY_BYTES + 1))).status_code == 413
            discovery = json.dumps({"operation": "discover", "at": now.isoformat(), "nonce": "a" * 32}).encode()
            discovery_grant = jwt.encode({**std, "aud": "analytics-course-visitors", "course_id": "backend",
                "body_sha256": sha256(discovery).hexdigest()}, key, algorithm="EdDSA", headers={"kid": "test"})
            assert (await post(discovery_grant, discovery)).json() == {
                "course_id": "backend", "subject_digests": ["d" * 64],
            }
            assert (await post(discovery_grant, body)).status_code == 403
            app.state.trust_manifest_validated = False
            assert (await post(private)).status_code == 503

    asyncio.run(check())


def test_course_study_time_uses_its_own_signed_capability_and_preserves_old_wire(monkeypatch):
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    verifier = GrantVerifier(issuer="core", public_keys={"test": pub})
    now = datetime.now(timezone.utc)
    stamp = int(now.timestamp())
    scope = {"at": now.isoformat(), "nonce": "a" * 32}
    digest = "b" * 64
    aggregation = {**scope, "operation": "aggregate", "active_subscriptions": [digest],
                   "inactive_subscriptions": [], "excluded_subjects": []}
    actor = str(uuid4())
    path = "/internal/analytics/v1/course/study-time"
    old_path = "/internal/analytics/v1/course/visitors"
    audience = "analytics-course-study-time"

    def signed(body, *, aud=audience, course="backend", **extra):
        claims = {"grant_version": 1, "iss": "core", "sub": actor, "iat": stamp, "nbf": stamp, "exp": stamp + 15,
                  "aud": aud, "course_id": course, "body_sha256": sha256(body).hexdigest(), **extra}
        return jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": "test"})

    async def study(_reports, grant, snapshot):
        assert grant.course_id == "backend"
        if isinstance(snapshot, CourseVisitorDiscovery):
            return CourseVisitorCandidates(course_id=grant.course_id, subject_digests=[digest])
        today = snapshot.at.astimezone(MOSCOW).date()
        rows = [{"date": today - timedelta(days=29 - i), "with_subscription": None, "without_subscription": None}
                for i in range(30)]
        rows[-1]["with_subscription"] = {"active_ms": 12_000, "learners": 2}
        return CourseStudyTimeReport(course_id=grant.course_id, daily=rows)

    monkeypatch.setattr(api, "read_course_study_time", study)
    app = FastAPI()
    app.include_router(api.router)
    app.state.trust_manifest_validated = True
    app.state.grant_verifier = verifier
    app.state.course_visitor_slots = asyncio.Semaphore(1)
    app.state.settings = SimpleNamespace(db_timeout_seconds=2)

    class Probe:
        async def is_ready(self):
            return True

    app.state.probe = Probe()
    app.state.activity_reports = PostgresProjectionActivityReport(object(), None, expected_run_id=uuid4())
    assert path not in app.openapi()["paths"] and old_path not in app.openapi()["paths"]

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async def post(values, *, grant=None, route=path):
                body = json.dumps(values).encode()
                return await client.post(route, content=body, headers={
                    "Authorization": "Bearer " + (grant or signed(body)), "Content-Type": "application/json",
                })

            body = json.dumps(aggregation).encode()
            valid = await post(aggregation)
            assert valid.status_code == 200 and valid.headers["Cache-Control"] == "no-store"
            assert valid.json()["daily"][-1]["with_subscription"] == {"active_ms": 12_000, "learners": 2}
            assert valid.json()["daily"][-2]["with_subscription"] is None
            discovery = await post({**scope, "operation": "discover"})
            assert discovery.json() == {"course_id": "backend", "subject_digests": [digest]}
            assert (await post(aggregation, route=old_path)).status_code == 401
            assert (await post(aggregation, grant=signed(body, aud="analytics-course-visitors"))).status_code == 401
            assert (await post(aggregation, grant=signed(body, aud="analytics", scope="report:read", role="leader"))).status_code == 401
            assert (await post({**aggregation, "course_id": "other"}, grant=signed(body))).status_code == 403
            assert (await post({**aggregation, "excluded_subjects": [digest]})).status_code == 400
            old_body = {key: value for key, value in aggregation.items() if key != "excluded_subjects"}
            assert (await post(old_body)).status_code == 400
            app.state.activity_reports = object()
            assert (await post(aggregation)).status_code == 503

    asyncio.run(check())
