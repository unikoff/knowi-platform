import os
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://synthetic@127.0.0.1:1/isolated")

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient

from pymentor_core.adapters.analytics_http.legacy_read import LegacyReadClient, LegacyReadUnavailable
from pymentor_core.modules.analytics_delivery import legacy_bridge

CALENDAR = {"days": ["2026-11-15"], "visits": [{"activity_date": "2026-11-15", "visited_at": None}]}
TODAY = date(2026, 11, 17)
DASHBOARD = {"active_students_7d": 1, "inactive_students_7d": 1,
    "daily_active_students": [{"date": (TODAY - timedelta(days=29 - i)).isoformat(), "active_students": int(i == 29)} for i in range(30)]}
ADMIN = {"platform_activity_today": {"date": TODAY.isoformat(), "unique_users": 2, "coverage_since": None, "partial_day": True},
         "platform_course_activity_today": {"date": TODAY.isoformat(), "unique_users": None, "coverage_since": None, "partial_day": False},
         "legacy_dashboard_active_students_7d": 1, "dashboard_activity": DASHBOARD}
STAMP = "2026-11-17T00:00:00+00:00"
REF = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ROSTER = [{"subject_ref": REF, "revision": 2, "student": True}]


def complete(data, kind):
    common = {"coverage": "complete", "data": data, "source_hwm": None, "freshness": "unknown"}
    if kind == "admin":
        return {**common, "coverage_subjects": [{"subject_ref": REF, "coverage_through_revision": 2,
                  "source_snapshot_at": STAMP, "student": True}]}
    return {**common, "coverage_through_revision": 2, "source_snapshot_at": STAMP}


class Stream(httpx.AsyncByteStream):
    def __init__(self, raw): self.raw = raw
    async def __aiter__(self): yield self.raw


def test_query_client_exact_path_bearer_and_schema():
    import json
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, stream=Stream(json.dumps({"coverage": "unknown", "data": CALENDAR}).encode()))
    client = LegacyReadClient("http://127.0.0.1:1", transport=httpx.MockTransport(handler))
    result = asyncio.run(client.read("calendar", "synthetic-grant", year=2026, month=11))
    assert result == {"coverage": "unknown", "data": CALENDAR}
    assert seen[0].headers["Authorization"] == "Bearer synthetic-grant"
    assert seen[0].url.path == "/internal/analytics/v1/legacy/calendar"
    assert dict(seen[0].url.params) == {"year": "2026", "month": "11"}
    bad = LegacyReadClient("http://127.0.0.1:1", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=Stream(b'{"coverage":"unknown","data":{"days":[]}}'))))
    with pytest.raises(LegacyReadUnavailable):
        asyncio.run(bad.read("calendar", "synthetic", year=2026, month=11))
    admin = LegacyReadClient("http://127.0.0.1:1", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=Stream(json.dumps({"coverage": "unknown", "data": ADMIN}).encode()))))
    assert asyncio.run(admin.read("admin", "synthetic"))["data"] == ADMIN


def test_admin_completion_client_checks_days_counts_and_coverage():
    import json
    at = "2026-11-17T12:00:00+00:00"
    body = {"daily_lessons_completed": [{"date": (TODAY-timedelta(days=29-i)).isoformat(), "lessons_completed": int(i == 29)} for i in range(30)],
            "lessons_completed_7d": 1, "weekly_by_subject": {REF: 1},
            "coverage_subjects": [{"subject_ref": REF, "coverage_through_revision": 2, "source_snapshot_at": STAMP}]}
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, stream=Stream(json.dumps(body).encode()))
    client = LegacyReadClient("http://127.0.0.1:1", transport=httpx.MockTransport(handler))
    assert asyncio.run(client.read("admin_completions", "grant", at=at)) == body
    assert requests[0].url.path.endswith("/admin/completions") and requests[0].url.params["at"] == at
    body["weekly_by_subject"][REF] = 2
    with pytest.raises(LegacyReadUnavailable):
        asyncio.run(client.read("admin_completions", "grant", at=at))


def test_admin_completion_bridge_rechecks_source_and_fails_closed(monkeypatch):
    monkeypatch.setenv("PYMENTOR_ANALYTICS_ADMIN_COMPLETIONS_ENABLED", "1")
    claims = {"subject": REF, "role": "admin", "scope": "report:read", "courses": [], "subjects": [], "global_read": True}
    captures = [(claims, ROSTER, [(REF, 7, "student")])] * 2
    async def sql(request, operation): return captures.pop(0)
    monkeypatch.setattr(legacy_bridge, "run_request_sql", sql)
    class Signer:
        def grant(self, **kwargs): return "synthetic"
    class Client:
        async def read(self, kind, *args, **kwargs):
            if kind == "admin": return complete(ADMIN, "admin")
            return {"daily_lessons_completed": [{"date": "2026-11-17", "lessons_completed": 1}],
                    "lessons_completed_7d": 1, "weekly_by_subject": {REF: 1},
                    "coverage_subjects": [{"subject_ref": REF, "coverage_through_revision": 2, "source_snapshot_at": STAMP}]}
    state = SimpleNamespace(analytics_legacy_read_mode="analytics", analytics_grant_signer=Signer(), analytics_legacy_client=Client())
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    result = asyncio.run(legacy_bridge.apply(request, Response(), object(), "admin", {"total_students": 1}))
    assert result["most_active_students"] == [{"id": 7, "username": "student", "lessons_completed": 1}]
    captures.extend([(claims, ROSTER, [(REF, 7, "student")]), (claims, ROSTER, [(REF, 7, "renamed")])])
    with pytest.raises(HTTPException) as error:
        asyncio.run(legacy_bridge.apply(request, Response(), object(), "admin", {}))
    assert error.value.status_code == 503


def test_shadow_unknown_returns_core_and_analytics_unknown_fails_closed(monkeypatch):
    class Signer:
        def grant(self, **claims): return "synthetic"
    class Client:
        coverage = "unknown"
        async def read(self, *args, **kwargs): return complete(CALENDAR, "calendar") if self.coverage == "complete" else {"coverage": self.coverage, "data": CALENDAR}
    async def sql(request, operation):
        claims = {"subject": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "role": "user", "scope": "report:read",
                "courses": ["backend"], "subjects": [], "global_read": False}
        return (claims, 2) if request.app.state.analytics_legacy_read_mode == "analytics" else claims
    monkeypatch.setattr(legacy_bridge, "run_request_sql", sql)
    state = SimpleNamespace(analytics_legacy_read_mode="shadow", analytics_legacy_shadow={},
                            analytics_grant_signer=Signer(), analytics_legacy_client=Client())
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    response = Response()
    assert asyncio.run(legacy_bridge.apply(request, response, object(), "calendar", CALENDAR, year=2026, month=11)) == CALENDAR
    assert response.headers["X-Analytics-Source"] == "core" and response.headers["X-Analytics-Coverage"] == "unknown"
    assert state.analytics_legacy_shadow == {"observed_match": 1, "coverage_unknown": 1}
    state.analytics_legacy_read_mode = "analytics"
    with pytest.raises(HTTPException) as error:
        asyncio.run(legacy_bridge.apply(request, Response(), object(), "calendar", CALENDAR, year=2026, month=11))
    assert error.value.status_code == 503 and error.value.headers["X-Analytics-Coverage"] == "unavailable"
    state.analytics_legacy_client.coverage = "complete"
    assert asyncio.run(legacy_bridge.apply(request, Response(), object(), "calendar", {}, year=2026, month=11)) == CALENDAR


def test_admin_partial_projection_never_switches_to_analytics(monkeypatch):
    async def sql(request, operation):
        return ({"subject": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "role": "admin", "scope": "report:read",
                "courses": [], "subjects": [], "global_read": True}, ROSTER)
    class Signer:
        def grant(self, **claims): return "synthetic"
    class Client:
        async def read(self, *args, **kwargs):
            return {"coverage": "complete", "data": {"legacy_dashboard_active_students_7d": 2}}
    monkeypatch.setattr(legacy_bridge, "run_request_sql", sql)
    state = SimpleNamespace(analytics_legacy_read_mode="analytics", analytics_legacy_shadow={},
                            analytics_grant_signer=Signer(), analytics_legacy_client=Client())
    with pytest.raises(HTTPException) as error:
        asyncio.run(legacy_bridge.apply(SimpleNamespace(app=SimpleNamespace(state=state)), Response(), object(), "admin", {"active_students_7d": 2}))
    assert error.value.status_code == 503


def test_admin_shadow_compares_full_fields_and_complete_can_merge(monkeypatch):
    async def sql(request, operation):
        claims = {"subject": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "role": "admin", "scope": "report:read",
                "courses": [], "subjects": [], "global_read": True}
        return (claims, ROSTER) if request.app.state.analytics_legacy_read_mode == "analytics" else claims
    class Signer:
        def grant(self, **claims): return "synthetic"
    class Client:
        coverage = "unknown"
        async def read(self, *args, **kwargs): return complete(ADMIN, "admin") if self.coverage == "complete" else {"coverage": self.coverage, "data": ADMIN}
    monkeypatch.setattr(legacy_bridge, "run_request_sql", sql)
    state = SimpleNamespace(analytics_legacy_read_mode="shadow", analytics_legacy_shadow={},
                            analytics_grant_signer=Signer(), analytics_legacy_client=Client())
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    baseline = {"activity_today": {**ADMIN["platform_activity_today"], "date": TODAY}, "dashboard": {**DASHBOARD, "total_students": 2}}
    assert asyncio.run(legacy_bridge.apply(request, Response(), object(), "admin", baseline)) == baseline
    assert state.analytics_legacy_shadow == {"observed_match": 1, "coverage_unknown": 1}
    state.analytics_legacy_read_mode = "analytics"
    with pytest.raises(HTTPException):
        asyncio.run(legacy_bridge.apply(request, Response(), object(), "admin", baseline))
    state.analytics_legacy_client.coverage = "complete"
    unloaded = {"activity_today": {**ADMIN["platform_activity_today"], "unique_users": None},
                "dashboard": {"active_students_7d": None, "inactive_students_7d": None,
                              "daily_active_students": [], "total_students": 2}}
    response = Response()
    merged = asyncio.run(legacy_bridge.apply(request, response, object(), "admin", unloaded))
    assert merged == {**baseline, "activity_today": ADMIN["platform_activity_today"]}
    assert response.headers["X-Analytics-Source"] == "mixed"


def test_shadow_query_failure_preserves_core_response(monkeypatch):
    class Signer:
        def grant(self, **claims): return "synthetic"
    class Client:
        async def read(self, *args, **kwargs): raise LegacyReadUnavailable()
    async def sql(request, operation):
        return {"subject": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "role": "user", "scope": "report:read",
                "courses": ["backend"], "subjects": [], "global_read": False}
    monkeypatch.setattr(legacy_bridge, "run_request_sql", sql)
    state = SimpleNamespace(analytics_legacy_read_mode="shadow", analytics_legacy_shadow={},
                            analytics_grant_signer=Signer(), analytics_legacy_client=Client())
    response = Response()
    actual = asyncio.run(legacy_bridge.apply(SimpleNamespace(app=SimpleNamespace(state=state)), response, object(),
                                              "calendar", CALENDAR, year=2026, month=11))
    assert actual == CALENDAR and response.headers["X-Analytics-Coverage"] == "unavailable"
    assert state.analytics_legacy_shadow == {"unavailable": 1}


def test_user_calendar_route_applies_shadow_and_fail_closed_modes(monkeypatch):
    from pymentor_core.modules.identity.api import router as identity_router
    principal = SimpleNamespace(user_id=1)
    monkeypatch.setattr(identity_router, "_local_async_identity", lambda request: object())
    async def current(request): return principal
    reads = []
    async def source(request, identity, operation):
        reads.append(True)
        return CALENDAR
    async def grant_sql(request, operation):
        claims = {"subject": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "role": "user", "scope": "report:read",
                "courses": ["backend"], "subjects": [], "global_read": False}
        return (claims, 2) if request.app.state.analytics_legacy_read_mode == "analytics" else claims
    class Signer:
        def grant(self, **claims): return "synthetic"
    class Client:
        coverage = "unknown"
        async def read(self, *args, **kwargs): return complete(CALENDAR, "calendar") if self.coverage == "complete" else {"coverage": self.coverage, "data": CALENDAR}
    monkeypatch.setattr(identity_router, "async_current_principal", current)
    monkeypatch.setattr(identity_router, "_run_identity_sql", source)
    monkeypatch.setattr(legacy_bridge, "run_request_sql", grant_sql)
    app = FastAPI()
    app.state.analytics_legacy_read_mode = "shadow"
    app.state.analytics_legacy_shadow = {}
    app.state.analytics_grant_signer = Signer()
    app.state.analytics_legacy_client = Client()
    app.include_router(identity_router.user_router, prefix="/api/user")
    with TestClient(app) as client:
        response = client.get("/api/user/activity?year=2026&month=11")
        assert response.status_code == 200 and response.json() == CALENDAR
        assert response.headers["X-Analytics-Coverage"] == "unknown"
        app.state.analytics_legacy_read_mode = "analytics"
        reads.clear()
        unavailable = client.get("/api/user/activity?year=2026&month=11")
        assert unavailable.status_code == 503 and unavailable.headers["X-Analytics-Coverage"] == "unavailable"
        assert reads == []  # Analytics mode does not query Core's old calendar.
        app.state.analytics_legacy_client.coverage = "complete"
        result = client.get("/api/user/activity?year=2026&month=11")
        assert result.status_code == 200 and result.json() == CALENDAR
        assert result.headers["X-Analytics-Source"] == "analytics" and reads == []


def test_admin_route_reuses_authenticated_principal_in_shadow(monkeypatch):
    from pymentor_core.modules.administration.api import router as admin_router
    principal = object()
    calls = []
    async def admin_sql(request, operation, *, with_principal=False):
        calls.append(with_principal)
        return (DASHBOARD.copy(), principal) if with_principal else DASHBOARD.copy()
    async def grant_sql(request, operation):
        return {"subject": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "role": "admin", "scope": "report:read",
                "courses": [], "subjects": [], "global_read": True}
    class Signer:
        def grant(self, **claims): return "synthetic"
    class Client:
        async def read(self, *args, **kwargs): return {"coverage": "unknown", "data": ADMIN}
    monkeypatch.setattr(admin_router, "_admin_sql", admin_sql)
    monkeypatch.setattr(legacy_bridge, "run_request_sql", grant_sql)
    app = FastAPI()
    app.state.analytics_legacy_read_mode = "shadow"
    app.state.analytics_legacy_shadow = {}
    app.state.analytics_grant_signer = Signer()
    app.state.analytics_legacy_client = Client()
    app.include_router(admin_router.admin_router, prefix="/api/admin")
    with TestClient(app) as client:
        response = client.get("/api/admin/overview")
        assert response.status_code == 200 and response.json() == DASHBOARD
        assert response.headers["X-Analytics-Coverage"] == "unknown"
        assert calls == [True]


def test_verified_read_rechecks_source_revision_and_rejects_race(monkeypatch):
    claims = {"subject": REF, "role": "user", "scope": "report:read", "courses": ["backend"],
              "subjects": [], "global_read": False}
    calls = iter([(claims, 2), (claims, 3)])
    async def sql(request, operation): return next(calls)
    class Signer:
        def grant(self, **claims): return "synthetic"
    class Client:
        async def read(self, *args, **kwargs): return complete(CALENDAR, "calendar")
    monkeypatch.setattr(legacy_bridge, "run_request_sql", sql)
    state = SimpleNamespace(analytics_legacy_read_mode="analytics", analytics_legacy_shadow={},
                            analytics_grant_signer=Signer(), analytics_legacy_client=Client())
    with pytest.raises(HTTPException) as error:
        asyncio.run(legacy_bridge.apply(SimpleNamespace(app=SimpleNamespace(state=state)), Response(), object(),
                                        "calendar", {}, year=2026, month=11))
    assert error.value.status_code == 503
    assert error.value.headers["X-Analytics-Coverage"] == "unavailable"


def test_complete_client_requires_watermark_and_admin_vector():
    import json
    def client(value):
        return LegacyReadClient("http://127.0.0.1:1", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=Stream(json.dumps(value).encode()))))
    assert asyncio.run(client(complete(CALENDAR, "calendar")).read("calendar", "synthetic", year=2026, month=11))["coverage_through_revision"] == 2
    assert asyncio.run(client(complete(ADMIN, "admin")).read("admin", "synthetic"))["coverage_subjects"][0]["student"] is True
    bad = complete(CALENDAR, "calendar")
    bad["source_hwm"] = 2
    with pytest.raises(LegacyReadUnavailable):
        asyncio.run(client(bad).read("calendar", "synthetic", year=2026, month=11))


@pytest.mark.parametrize("coverage", ["unknown", "complete"])
def test_browser_metadata_pair_is_optional_and_strict(coverage):
    import json
    def read(value):
        client = LegacyReadClient("http://127.0.0.1:1", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=Stream(json.dumps(value).encode()))))
        return asyncio.run(client.read("calendar", "synthetic", year=2026, month=11))
    envelope = complete(CALENDAR, "calendar") if coverage == "complete" else {"coverage": "unknown", "data": CALENDAR}
    assert read(envelope) == envelope
    pair = {"browser_quality": "unknown", "browser_as_of": STAMP}
    assert read({**envelope, **pair}) == {**envelope, **pair}
    for bad in ({"browser_quality": "unknown"}, {"browser_as_of": STAMP},
                {"browser_quality": "complete", "browser_as_of": STAMP},
                {"browser_quality": "unknown", "browser_as_of": "2026-11-17T00:00:00"},
                {"browser_quality": "unknown", "browser_as_of": "2026-11-17T00:00:00+03:00"},
                {"browser_quality": "unknown", "browser_as_of": "invalid"}):
        with pytest.raises(LegacyReadUnavailable):
            read({**envelope, **bad})


@pytest.mark.parametrize("kind", ["calendar", "admin"])
def test_complete_browser_metadata_sets_public_headers(monkeypatch, kind):
    claims = {"subject": REF, "role": "admin" if kind == "admin" else "user", "scope": "report:read",
              "courses": [] if kind == "admin" else ["backend"], "subjects": [], "global_read": kind == "admin"}
    async def sql(request, operation): return claims, ROSTER if kind == "admin" else 2
    class Signer:
        def grant(self, **claims): return "synthetic"
    class Client:
        async def read(self, *args, **kwargs):
            return {**complete(ADMIN if kind == "admin" else CALENDAR, kind),
                    "browser_quality": "unknown", "browser_as_of": STAMP}
    monkeypatch.setattr(legacy_bridge, "run_request_sql", sql)
    state = SimpleNamespace(analytics_legacy_read_mode="analytics", analytics_legacy_shadow={},
                            analytics_grant_signer=Signer(), analytics_legacy_client=Client())
    response = Response()
    baseline = {"activity_today": {}, "dashboard": {}} if kind == "admin" else {}
    result = asyncio.run(legacy_bridge.apply(SimpleNamespace(app=SimpleNamespace(state=state)), response, object(),
                                             kind, baseline, year=2026, month=11))
    assert result == ({**baseline, "activity_today": ADMIN["platform_activity_today"], "dashboard": DASHBOARD}
                      if kind == "admin" else CALENDAR)
    assert response.headers["X-Analytics-Source"] == "mixed"
    assert response.headers["X-Analytics-Browser-Quality"] == "unknown"
    assert response.headers["X-Analytics-Browser-As-Of"] == STAMP
    assert response.headers["X-Analytics-Coverage"] == "complete"


@pytest.mark.parametrize("kind", ["calendar", "summary", "admin"])
@pytest.mark.parametrize("required,has_browser", [(False, False), (True, False), (True, True)])
def test_browser_presence_requirement_gates_legacy_reads(monkeypatch, kind, required, has_browser):
    claims = {"subject": REF, "role": "admin" if kind == "admin" else "user", "scope": "report:read",
              "courses": [], "subjects": [], "global_read": kind == "admin"}
    async def sql(request, operation): return claims, ROSTER if kind == "admin" else 2
    class Signer:
        def grant(self, **claims): return "synthetic"
    class Client:
        async def read(self, *args, **kwargs):
            result = complete(ADMIN if kind == "admin" else CALENDAR, kind)
            if has_browser:
                result.update(browser_quality="unknown", browser_as_of=STAMP)
            return result
    monkeypatch.setattr(legacy_bridge, "run_request_sql", sql)
    state = SimpleNamespace(analytics_legacy_read_mode="analytics", analytics_browser_presence_required=required,
                            analytics_grant_signer=Signer(), analytics_legacy_client=Client())
    request, response = SimpleNamespace(app=SimpleNamespace(state=state)), Response()
    baseline = {"activity_today": {}, "dashboard": {}} if kind == "admin" else {}
    async def read():
        return await legacy_bridge.apply(request, response, object(), kind, baseline, year=2026, month=11)
    if required and not has_browser:
        with pytest.raises(HTTPException) as error:
            asyncio.run(read())
        assert error.value.status_code == 503
        assert error.value.headers["X-Analytics-Coverage"] == "unavailable"
    else:
        asyncio.run(read())
        assert response.headers["X-Analytics-Coverage"] == "complete"
        if has_browser:
            assert response.headers["X-Analytics-Browser-Quality"] == "unknown"


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_browser_presence_requirement_rejects_core_baseline_modes(monkeypatch, mode):
    from pymentor_core.app.factory import create_app
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("KNOWI_REDIS_PREFIX", "analytics-smoke")
    monkeypatch.setenv("KNOWI_LOGIN_RECOVERY_KEY", "a" * 64)
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_READ_MODE", mode)
    monkeypatch.setenv("PYMENTOR_ANALYTICS_BROWSER_PRESENCE_REQUIRED", "1")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "mirror")
    with pytest.raises(ValueError, match="browser presence requires Analytics legacy reads"):
        create_app()


def test_default_core_only_does_not_require_browser_presence(monkeypatch):
    from pymentor_core.app.factory import create_app
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("KNOWI_REDIS_PREFIX", "analytics-smoke")
    monkeypatch.setenv("KNOWI_LOGIN_RECOVERY_KEY", "a" * 64)
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_READ_MODE", "off")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "mirror")
    monkeypatch.delenv("PYMENTOR_ANALYTICS_BROWSER_PRESENCE_REQUIRED", raising=False)
    assert create_app().state.analytics_browser_presence_required is False

def test_course_completion_bridge_checks_roster_and_rechecks(monkeypatch):
    claims = {"subject": REF, "role": "admin", "scope": "report:read", "courses": [], "subjects": [], "global_read": True}
    actor = {"id": 9, "revision": 1}
    capture = (claims, ROSTER, [(REF, 7, "student")], actor)
    captures = [capture, capture]
    async def sql(request, operation): return captures.pop(0)
    monkeypatch.setattr(legacy_bridge, "run_request_sql", sql)
    class Signer:
        def grant(self, **kwargs):
            assert kwargs["course_completion_roster_v1"]["month"] == 11
            return "synthetic"
    class Client:
        async def read(self, kind, *args, **kwargs):
            assert kind == "course_completions" and kwargs["course_id"] == "backend"
            return {"daily_lessons_completed": [], "lessons_completed_7d": 1,
                    "weekly_by_subject": {REF: 1}, "completed_lessons_month": 3,
                    "coverage_subjects": [{"subject_ref": REF, "coverage_through_revision": 2, "source_snapshot_at": STAMP}]}
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(analytics_grant_signer=Signer(), analytics_legacy_client=Client())))
    result = asyncio.run(legacy_bridge.apply_course_completions(request, Response(), object(), {"dashboard": {}}, "backend", year=2026, month=11))
    assert result["completed_lessons"] == 3
    assert result["dashboard"]["most_active_students"] == [{"id": 7, "username": "student", "lessons_completed": 1}]
    captures.extend([capture, (claims, ROSTER, [(REF, 7, "changed")], actor)])
    with pytest.raises(HTTPException) as error:
        asyncio.run(legacy_bridge.apply_course_completions(request, Response(), object(), {"dashboard": {}}, "backend", year=2026, month=11))
    assert error.value.status_code == 503

def test_course_completion_client_validates_month_and_all_member_coverage():
    import json
    at = "2026-11-17T12:00:00+00:00"
    other = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    body = {"daily_lessons_completed": [{"date": (TODAY-timedelta(days=29-i)).isoformat(), "lessons_completed": 0} for i in range(30)],
            "lessons_completed_7d": 0, "weekly_by_subject": {REF: 0}, "completed_lessons_month": 2,
            "coverage_subjects": [{"subject_ref": ref, "coverage_through_revision": 2, "source_snapshot_at": STAMP} for ref in (REF, other)]}
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, stream=Stream(json.dumps(body).encode()))
    client = LegacyReadClient("http://127.0.0.1:1", transport=httpx.MockTransport(handler))
    assert asyncio.run(client.read("course_completions", "grant", at=at, course_id="backend", year=2026, month=11)) == body
    assert dict(seen[0].url.params) == {"at": at, "course_id": "backend", "year": "2026", "month": "11"}
    body["completed_lessons_month"] = None
    with pytest.raises(LegacyReadUnavailable):
        asyncio.run(client.read("course_completions", "grant", at=at, course_id="backend", year=2026, month=11))
