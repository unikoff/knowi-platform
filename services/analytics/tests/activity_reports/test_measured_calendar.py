import asyncio
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport
from pymentor_analytics.contracts.auth_v1 import AuthorizationError, VerifiedGrant
from pymentor_analytics.modules.analytics.activity.measured_calendar import read_measured_calendar
from pymentor_analytics.modules.analytics.api.measured_calendar_router import router


class Store:
    day_projection_enabled = True

    def __init__(self, responses):
        self.responses = iter(responses)
        self.queries = []

    async def read_activity(self, grant, query, **kwargs):
        self.queries.append((query, kwargs))
        return next(self.responses)


def snapshot(*days):
    return {"as_of": datetime(2026, 11, 17, tzinfo=timezone.utc),
            "daily": [{"date": date.fromisoformat(day), "active_ms": ms} for day, ms in days]}


def test_positive_time_only_and_streak_across_month_boundary():
    store = Store([snapshot(("2026-11-01", 3), ("2026-11-02", 0)),
                   snapshot(("2026-10-31", 2), ("2026-11-01", 1))])
    result = asyncio.run(read_measured_calendar(store, None, 2026, 11,
                         now=datetime(2026, 11, 1, 12, tzinfo=timezone.utc)))
    assert result["days"] == [date(2026, 11, 1)]
    assert result["current_streak"] == 2 and result["streak_is_lower_bound"]
    assert result["coverage"] == "unknown"
    assert all(q.course_id is None and q.subject_ref is None and not kw
               for q, kw in store.queries)


def test_no_observation_is_unknown_not_zero():
    store = Store([snapshot(), snapshot()])
    result = asyncio.run(read_measured_calendar(store, None, 2026, 11,
                         now=datetime(2026, 11, 17, 12, tzinfo=timezone.utc)))
    assert result["days"] == [] and result["current_streak"] is None
    assert result["coverage"] == "unknown" and not result["streak_is_lower_bound"]


def test_route_self_only_and_day_flag():
    app = FastAPI()
    app.include_router(router)
    grant = SimpleNamespace(scope="report:read", role="user", global_read=False,
                            subject_refs=(), course_ids=("course",), sub=uuid4())
    app.state.trust_manifest_validated = True
    app.state.grant_verifier = SimpleNamespace(verify=lambda token: grant)
    app.state.probe = SimpleNamespace(is_ready=lambda: asyncio.sleep(0, result=True))
    app.state.settings = SimpleNamespace(db_timeout_seconds=1)
    app.state.activity_reports = Store([snapshot(), snapshot()])
    client = TestClient(app)
    headers = {"Authorization": "Bearer token"}
    assert client.get("/api/analytics/reports/measured-calendar?year=2026&month=11", headers=headers).status_code == 200
    assert client.get("/api/analytics/reports/measured-calendar?year=2026&month=11&subject_ref=x", headers=headers).status_code == 400
    grant.role = "admin"
    assert client.get("/api/analytics/reports/measured-calendar?year=2026&month=11", headers=headers).status_code == 403
    grant.role = "user"
    app.state.activity_reports.day_projection_enabled = False
    assert client.get("/api/analytics/reports/measured-calendar?year=2026&month=11", headers=headers).status_code == 503


def test_scoped_calendar_rejects_ungranted_courses_and_keeps_exact_scope():
    app = FastAPI()
    app.include_router(router)
    grant = SimpleNamespace(scope="report:read", role="user", global_read=False,
                            subject_refs=(), course_ids=("backend", "platform-tools"), sub=uuid4())
    app.state.trust_manifest_validated = True
    app.state.grant_verifier = SimpleNamespace(verify=lambda token: grant)
    app.state.probe = SimpleNamespace(is_ready=lambda: asyncio.sleep(0, result=True))
    app.state.settings = SimpleNamespace(db_timeout_seconds=1)
    app.state.activity_reports = store = Store([snapshot(("2026-11-01", 3), ("2026-11-02", 0)), snapshot()])
    path = "/api/analytics/reports/measured-calendar?year=2026&month=11"
    headers = {"Authorization": "Bearer token"}
    with TestClient(app) as client:
        assert client.get(path + "&course_id=other", headers=headers).status_code == 403
        assert client.get(path + "&course_id=platform-tools", headers=headers).status_code == 403
        assert client.get(path + "&course_id=backend&course_id=backend", headers=headers).status_code == 400
        assert client.get(path + "&course_id=backend&subject_ref=other", headers=headers).status_code == 400
        assert store.queries == []
        response = client.get(path + "&course_id=backend", headers=headers)
        assert response.status_code == 200
        assert response.json()["course_id"] == "backend" and response.json()["days"] == ["2026-11-01"]
        assert len(store.queries) == 2
        assert all(q.course_id == "backend" and q.subject_ref is None and not kw
                   for q, kw in store.queries)


def test_unqualified_read_keeps_signed_courses_and_self_subject(monkeypatch):
    from pymentor_analytics.adapters.postgres import activity_day_projection
    from pymentor_analytics.modules.analytics.activity.measured_calendar import _query

    async def schema(_session):
        return True
    monkeypatch.setattr(activity_day_projection, "has_activity_day_projection_schema", schema)
    actor = uuid4()
    args_seen = []

    class Result:
        def scalars(self): return self
        def all(self): return [actor]
        def scalar_one(self): return True
        def mappings(self): return self
        def one(self):
            return {"error": None, "as_of": datetime.now(timezone.utc),
                    "totals": {"unique_visitors": 0, "active_ms": 0, "theory_ms": 0, "practice_ms": 0},
                    "daily": []}

    class Session:
        async def execute(self, statement, args):
            args_seen.append(args)
            return Result()

    class Database:
        async def transaction_guard(self, session): pass
        @asynccontextmanager
        async def transaction(self): yield Session()

    report = PostgresProjectionActivityReport(Database(), None, expected_run_id=uuid4(), day_projection_enabled=True)
    grant = VerifiedGrant(actor, "user", "report:read", ("one",), (), False, int(time.time()) + 60)
    query = _query(date(2026, 11, 1), date(2026, 11, 2))
    asyncio.run(report.read_activity(grant, query))
    snapshot_args = next(args for args in args_seen if "all_courses" in args)
    assert snapshot_args["all_courses"] is False and snapshot_args["courses"] == ["one"]
    assert snapshot_args["all_subjects"] is False and snapshot_args["scope_subjects"] == [actor]
    other = query.model_copy(update={"subject_ref": uuid4()})
    with pytest.raises(AuthorizationError):
        asyncio.run(report.read_activity(grant, other))
