"""Exercise the study-time route with real signed requests to a stub Query."""
import asyncio
from datetime import datetime, timedelta
from hashlib import sha256
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://synthetic@127.0.0.1:1/isolated")

from pymentor_core.adapters.analytics_http.course_visitors import CourseStudyTimeClient
from pymentor_core.app.web import prevent_api_caching
from pymentor_core.modules.administration.api import role_admin_router
from pymentor_core.modules.analytics_delivery import course_visitors as bridge
from pymentor_core.modules.analytics_delivery.signing import Signer
from pymentor_core.modules.identity.activity_policy import MOSCOW_TZ


@pytest.mark.parametrize("scenario, status, expected_calls", [
    ("success", 200, ["discover", "aggregate"]),
    ("old_query", 200, ["discover"]),
    ("invalid_stats", 200, ["discover", "aggregate"]),
    ("membership_changed", 200, ["discover", "aggregate"]),
    ("authority_changed", 403, ["discover", "aggregate"]),
    ("other_course", 403, []),
    ("stale_revision", 403, []),
    ("stale_actor", 401, []),
    ("no_integration", 200, []),
])
def test_course_study_time_http_scope_and_observations(monkeypatch, scenario, status, expected_calls):
    actor = SimpleNamespace(account_ref=str(uuid4()), user_id=1)
    subjects = [str(uuid4()) for _ in range(3)]
    private_key = Ed25519PrivateKey.generate()
    signed_requests = []

    async def request_principal(_request):
        return actor

    def authorize(_db, principal, course):
        assert principal == actor
        if course != "backend":
            raise HTTPException(403, "Course access denied")
        revision = 5 if scenario == "authority_changed" and signed_requests == ["discover", "aggregate"] else 4
        return {"course_id": course, "is_admin": False, "revision": revision}

    def digests(nonce):
        return [sha256((nonce + subject).encode()).hexdigest() for subject in subjects]

    class MembershipDB:
        def execute(self, statement, params):
            members = digests(params["nonce"])
            assert params["candidates"] == sorted(members)
            assert "u.is_admin OR EXISTS" in str(statement)
            assert "l.account_ref = a.lifecycle_id AND l.revoked_at IS NULL" in str(statement)
            changed = scenario == "membership_changed" and signed_requests == ["discover", "aggregate"]
            return SimpleNamespace(all=lambda: [(members[0], None if changed else True),
                                                 (members[1], False), (members[2], None)])

    async def run_sql(_request, operation):
        return operation(MembershipDB())

    def private_query(request):
        assert request.url.path == "/internal/analytics/v1/course/study-time"
        token = request.headers["authorization"].split(" ")[1]
        claims = jwt.decode(token, private_key.public_key(), algorithms=["EdDSA"],
                            audience="analytics-course-study-time", issuer="core")
        assert claims["sub"] == actor.account_ref and claims["course_id"] == "backend"
        assert claims["body_sha256"] == sha256(request.content).hexdigest()
        with pytest.raises(jwt.InvalidAudienceError):
            jwt.decode(token, private_key.public_key(), algorithms=["EdDSA"], audience="analytics-course-visitors")
        assert all(subject.encode() not in request.content for subject in subjects)
        payload = json.loads(request.content)
        signed_requests.append(payload["operation"])
        members = digests(payload["nonce"])
        if payload["operation"] == "discover":
            if scenario == "old_query":
                return httpx.Response(404, stream=httpx.ByteStream(b"{}"))
            assert set(payload) == {"operation", "at", "nonce"}
            data = {"course_id": "backend", "subject_digests": sorted(members)}
        else:
            assert payload["active_subscriptions"] == [members[0]]
            assert payload["inactive_subscriptions"] == [members[1]]
            assert payload["excluded_subjects"] == [members[2]]
            today = datetime.fromisoformat(payload["at"]).astimezone(MOSCOW_TZ).date()
            daily = [{"date": (today - timedelta(days=29 - i)).isoformat(),
                      "with_subscription": None, "without_subscription": None} for i in range(30)]
            daily[-1].update(with_subscription={"active_ms": 92_000, "learners": 1},
                             without_subscription={"active_ms": 42_000, "learners": 1})
            if scenario == "invalid_stats":
                daily[-1]["without_subscription"]["learners"] = 2
            data = {"course_id": "backend", "quality": "unknown", "daily": daily}
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(data).encode()))

    monkeypatch.setattr(role_admin_router, "request_principal", request_principal)
    monkeypatch.setattr(bridge, "run_request_sql", run_sql)
    monkeypatch.setattr(bridge, "role_admin_reads", lambda: SimpleNamespace(authorize=authorize))
    app = FastAPI()
    app.middleware("http")(prevent_api_caching)
    app.include_router(role_admin_router.router, prefix="/api")
    if scenario != "no_integration":
        private = private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption())
        app.state.analytics_grant_signer = Signer(issuer="core", kid="test", private_pem=private)
        app.state.analytics_course_study_time_client = CourseStudyTimeClient(
            "http://localhost", transport=httpx.MockTransport(private_query),
        )
    course = "other" if scenario == "other_course" else "backend"
    headers = {
        "X-Actor-Ref": str(uuid4()) if scenario == "stale_actor" else actor.account_ref,
        "X-Auth-Revision": "3" if scenario == "stale_revision" else "4",
    }

    async def get():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(f"/api/admin/courses/{course}/study-time", headers=headers)

    response = asyncio.run(get())
    assert response.status_code == status and signed_requests == expected_calls
    assert response.headers["Cache-Control"] == "no-store"
    data = response.json()
    if scenario == "success":
        assert data["status"] == "available" and data["quality"] == "unknown" and len(data["daily"]) == 30
        assert data["daily"][0]["with_subscription"] is None
        assert data["daily"][0]["without_subscription"] is None
        assert data["daily"][-1]["with_subscription"] == {"active_ms": 92_000, "learners": 1}
        assert data["daily"][-1]["without_subscription"] == {"active_ms": 42_000, "learners": 1}
    elif status == 200:
        assert data == {"status": "unavailable", "quality": "unknown", "daily": []}
    else:
        assert data["code"] in {"unauthenticated", "forbidden"} and "daily" not in data
