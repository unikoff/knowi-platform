"""Exercise the actual overview HTTP route, with synthetic SQL and private Query."""
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

from pymentor_core.adapters.analytics_http.course_visitors import CourseVisitorsClient
from pymentor_core.modules.administration.api import role_admin_router as overview
from pymentor_core.modules.analytics_delivery import course_visitors as bridge
from pymentor_core.modules.analytics_delivery.signing import Signer
from pymentor_core.modules.identity.activity_policy import MOSCOW_TZ

_UNAVAILABLE = {"status": "unavailable", "quality": "unknown", "daily": []}


@pytest.mark.parametrize("scenario", ["success", "timeout", "missing_lifecycle", "revoked"])
def test_overview_serializes_visits_rechecks_scope_and_keeps_timeout_local(monkeypatch, scenario):
    actor = SimpleNamespace(account_ref=str(uuid4()), user_id=1)
    subject = str(uuid4())
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    calls, checked = [], []
    revoked = False

    def authorize(_db, principal, course):
        checked.append((principal.user_id, course))
        if revoked or course != "backend":
            raise HTTPException(403, "Course access denied")
        return {"is_admin": False, "course_id": course, "revision": 4}

    class LocalDB:
        def execute(self, statement, params):
            observed = sha256((params["nonce"] + subject).encode()).hexdigest()
            assert params["candidates"] == [observed]
            assert "subscription_until >" in str(statement)
            return SimpleNamespace(all=lambda: [] if scenario == "missing_lifecycle" else [(observed, True)])

    async def read_sql(_request, operation):
        return operation(LocalDB())

    async def baseline(_request, _operation, *, with_principal):
        assert with_principal
        stale = {"status": "unavailable", "daily": []}
        slots = {"booked": 0, "free": 0, "total": 0}
        return {
            "course_id": "backend", "period": {"year": 2026, "month": 11},
            "roster_count": 0, "booking_count": 0, "lessons_granted": 0,
            "lessons_used": 0, "completed_lessons": 0, "course_activity_days": None,
            "course_trends": {"visits": stale, "completions": stale},
            "dashboard": {
                "total_students": 0, "active_students_7d": None, "inactive_students_7d": None,
                "active_subscriptions_count": 0, "lessons_completed_7d": 0, "expiring_subscriptions_count": 0,
                "expiring_subscriptions": [], "slots_30d": slots, "slots_7d": slots, "upcoming_bookings": [],
                "most_active_students": [], "daily_active_students": None, "daily_lessons_completed": [],
            },
        }, actor

    def streamed_response(value):
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(value).encode()))

    def private_query(request):
        nonlocal revoked
        token = request.headers["authorization"].split(" ")[1]
        claims = jwt.decode(token, key.public_key(), algorithms=["EdDSA"],
                            audience="analytics-course-visitors", issuer="core")
        assert claims["sub"] == actor.account_ref and claims["course_id"] == "backend"
        assert claims["body_sha256"] == sha256(request.content).hexdigest()
        assert subject.encode() not in request.content
        payload = json.loads(request.content)
        calls.append(payload["operation"])
        digest = sha256((payload["nonce"] + subject).encode()).hexdigest()
        if payload["operation"] == "discover":
            assert set(payload) == {"operation", "at", "nonce"}
            return streamed_response({"course_id": "backend", "subject_digests": [digest]})
        assert payload["active_subscriptions"] == [digest] and payload["inactive_subscriptions"] == []
        if scenario == "timeout":
            raise httpx.ReadTimeout("synthetic timeout")
        today = datetime.fromisoformat(payload["at"]).astimezone(MOSCOW_TZ).date()
        revoked = scenario == "revoked"
        return streamed_response({"course_id": "backend", "quality": "unknown", "daily": [
            {"date": (today - timedelta(days=29 - i)).isoformat(),
             "with_subscription": 1 if i == 29 else None, "without_subscription": None} for i in range(30)
        ]})

    monkeypatch.setattr(overview, "_read", baseline)
    monkeypatch.setattr(bridge, "run_request_sql", read_sql)
    monkeypatch.setattr(bridge, "role_admin_reads", lambda: SimpleNamespace(authorize=authorize))
    app = FastAPI()
    app.include_router(overview.router, prefix="/api")
    app.state.analytics_grant_signer = Signer(issuer="core", kid="test", private_pem=private)
    app.state.analytics_course_visitors_client = CourseVisitorsClient("http://localhost",
        transport=httpx.MockTransport(private_query))

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/api/admin/courses/backend/overview?year=2026&month=11")

    response = asyncio.run(check())
    assert checked == [(1, "backend")] * 3
    assert calls == (["discover"] if scenario == "missing_lifecycle" else ["discover", "aggregate"])
    if scenario == "revoked":
        assert response.status_code == 403 and "course_trends" not in response.json()
    else:
        assert response.status_code == 200
        visits = response.json()["course_trends"]["visits"]
        if scenario == "success":
            assert visits["status"] == "available" and visits["quality"] == "unknown"
            assert len(visits["daily"]) == 30 and visits["daily"][-1]["with_subscription"] == 1
            assert visits["daily"][-1]["without_subscription"] is None
        else:
            assert visits == _UNAVAILABLE
