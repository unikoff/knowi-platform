import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://synthetic@127.0.0.1:1/isolated")

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from pymentor_core.modules.identity.api import learner_analytics


def test_statistics_fails_closed_after_session_and_role_checks(monkeypatch):
    actor = SimpleNamespace(user_id=1, account_ref="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    role = "user"
    calls = []

    class Identity:
        async def authenticate(self, token):
            assert token == "synthetic-session"
            return actor

    async def run_sql(request, operation):
        return operation(object())

    def authorize(db, principal, *, purpose, course_id, target_ref):
        calls.append((principal, purpose, course_id, target_ref))
        if role != "user":
            raise HTTPException(403, "Analytics grant denied")
        return {"role": role}

    monkeypatch.setattr(learner_analytics, "run_request_sql", run_sql)
    monkeypatch.setattr(learner_analytics, "authorize_grant", authorize)
    monkeypatch.setattr(learner_analytics, "read_completions", lambda *args: ())
    app = FastAPI()
    app.include_router(learner_analytics.router, prefix="/api/user")
    app.state.identity_sessions = Identity()
    operation = app.openapi()["paths"]["/api/user/analytics/statistics"]["get"]
    assert operation["security"] == [{"SessionCookie": []}]
    assert "503" in operation["responses"] and "200" in operation["responses"]
    with TestClient(app) as client:
        path = "/api/user/analytics/statistics?days=7&course_id=backend"
        assert client.get(path).status_code == 401
        client.cookies.set("session", "synthetic-session")
        response = client.get(path)
        assert response.status_code == 503
        assert response.json() == {"detail": "Learner Analytics unavailable"}
        assert response.headers["cache-control"] == "no-store"
        assert calls == [(actor, "self_report", "backend", None)]
        assert client.get("/api/user/analytics/statistics?days=30").status_code == 503
        for suffix in ("&subject_ref=other", "&days=30", "&course_id=other"):
            assert client.get(path + suffix).status_code == 422
        assert client.get("/api/user/analytics/statistics?days=14").status_code == 422
        role = "admin"
        assert client.get(path).status_code == 403
