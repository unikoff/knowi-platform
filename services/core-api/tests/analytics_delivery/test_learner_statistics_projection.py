import os
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://synthetic@127.0.0.1:1/isolated")

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pymentor_core.adapters.analytics_http.learner_read import LearnerAnalyticsClient
from pymentor_core.adapters.sql.learner_statistics import read_completions
from pymentor_core.adapters.sql.models import UserLessonProgress
from pymentor_core.modules.identity.api import learner_analytics
from pymentor_core.modules.identity.contracts import InvalidSession


class Body(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data

    async def __aiter__(self):
        yield self.data


def reply(status, value=None):
    return httpx.Response(status, stream=Body(json.dumps(value).encode()))


def test_progress_reads_only_the_selected_account_course_and_interval():
    engine = create_engine("sqlite:///:memory:")
    UserLessonProgress.__table__.create(engine)
    start = datetime(2026, 11, 14, tzinfo=timezone.utc)
    end = datetime(2026, 11, 21, tzinfo=timezone.utc)
    with Session(engine) as db:
        db.add_all([UserLessonProgress(user_id=uid, course_id=course, lesson_id=lesson, completed_at=stamp)
                    for uid, course, lesson, stamp in [
                        (1, "backend", "one", start), (1, "other", "two", start),
                        (2, "backend", "three", start),
                        (1, "backend", "old", datetime(2026, 11, 1, tzinfo=timezone.utc))]])
        db.commit()
        rows = read_completions(db, SimpleNamespace(user_id=1), "backend", start, end)
        assert len(rows) == 1 and rows[0][:2] == ("backend", "one")
    engine.dispose()


def test_statistics_projection_and_verified_history(monkeypatch):
    actor = SimpleNamespace(user_id=1, account_ref="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    claims = {"subject": actor.account_ref, "role": "user", "scope": "report:read",
              "courses": ["backend", "other"], "subjects": [], "global_read": False}
    paths = []
    history_revision = 4
    projection_status = 200
    mode = "ok"
    revoked = False
    changed = False

    class Identity:
        async def authenticate(self, token):
            assert token == "synthetic-session"
            if revoked:
                raise InvalidSession()
            return actor

    async def run_sql(request, operation):
        return operation(object())

    def authorize(db, principal, *, purpose, course_id, target_ref):
        assert principal is actor and purpose == "self_report" and target_ref is None
        assert course_id in (None, "backend")
        courses = [course_id] if course_id else (["backend"] if changed else claims["courses"])
        return {**claims, "courses": courses}

    def completions(db, principal, course, start, end):
        assert principal is actor and course == "backend"
        return ((course, "lesson-1", end.isoformat()),)

    def query(request):
        nonlocal revoked, changed
        paths.append(request.url.path)
        assert request.headers["authorization"] == "Bearer synthetic-grant"
        args = parse_qs(request.url.query.decode())
        if request.url.path.endswith("/activity"):
            assert "course_id" not in args and "subject_ref" not in args
            if projection_status != 200:
                return reply(projection_status)
            end = datetime.fromisoformat(args["to_time"][0])
            today = end.astimezone(learner_analytics.MOSCOW_TZ).date().isoformat()
            values = {"unique_visitors": 1, "active_ms": 90000, "theory_ms": 60000, "practice_ms": 30000}
            if mode == "empty":
                values = {"unique_visitors": 0, "active_ms": 0, "theory_ms": 0, "practice_ms": 0}
            if mode == "revoke":
                revoked = True
            if mode == "authority_change":
                changed = True
            daily = [] if mode == "empty" else [{"date": today, **values}]
            if mode == "invalid":
                daily[0]["active_ms"] = 1
            return reply(200, {"schema_version": 1, "status": "ok", "quality": "unknown",
                "as_of": end.isoformat(), "lag_seconds": None, "timezone": "Europe/Moscow",
                "metric_version": "activity-v1", "course_id": None, "from_time": args["from_time"][0],
                "to_time": args["to_time"][0], "totals": values,
                "daily": daily, "trusted_progress": None})
        assert request.url.path.endswith("/legacy/history")
        return reply(200, {"coverage": {"subject_ref": actor.account_ref,
            "coverage_through_revision": history_revision, "source_snapshot_at": "2026-11-20T00:00:00+00:00"},
            "days": [{"date": args["start"][0], "theory_ms": 3600000, "practice_ms": 3600000,
                      "provenance": "core_legacy_conditional"}]})

    monkeypatch.setattr(learner_analytics, "run_request_sql", run_sql)
    monkeypatch.setattr(learner_analytics, "authorize_grant", authorize)
    monkeypatch.setattr(learner_analytics, "read_completions", completions)
    monkeypatch.setattr(learner_analytics, "capture", lambda db, principal, kind: (claims, 4))
    app = FastAPI()
    app.include_router(learner_analytics.router, prefix="/api/user")
    app.state.identity_sessions = Identity()
    app.state.analytics_grant_signer = SimpleNamespace(grant=lambda **kwargs: "synthetic-grant")
    app.state.analytics_learner_client = LearnerAnalyticsClient("http://127.0.0.1:18051", transport=httpx.MockTransport(query))
    with TestClient(app) as client:
        client.cookies.set("session", "synthetic-session")
        path = "/api/user/analytics/statistics?days=7&course_id=backend"
        result = client.get(path)
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "no-store"
        body = result.json()
        assert body["scope"] == {"course_id": "backend", "activity": "platform"}
        assert body["coverage"] == {"visits": "partial", "time": "partial", "progress": "core"}
        assert body["summary"]["active_seconds"] == 90 and body["summary"]["completed_lessons_period"] == 1
        assert body["summary"]["active_days_period"] is None
        assert body["days"][0]["visited"] is True and body["days"][0]["active_seconds"] is None
        assert body["days"][1]["visited"] is None
        assert body["days"][-1]["active_seconds"] == 90
        assert paths == ["/api/analytics/reports/activity", "/internal/analytics/v1/legacy/history"]
        history_revision = 5
        assert client.get(path).json()["days"][0]["visited"] is None
        mode = "empty"
        empty = client.get(path).json()
        assert empty["coverage"]["time"] == "unavailable"
        assert empty["summary"]["active_seconds"] is None and all(day["visited"] is None for day in empty["days"])
        mode = "invalid"
        assert client.get(path).status_code == 503
        mode = "authority_change"
        assert client.get(path).status_code == 503
        changed = False
        mode = "revoke"
        assert client.get(path).status_code == 401
        revoked = False
        mode = "ok"
        assert len(client.get(path.replace("days=7", "days=30")).json()["days"]) == 30
        projection_status = 503
        failed = client.get(path)
        assert failed.status_code == 503 and failed.headers["cache-control"] == "no-store"
