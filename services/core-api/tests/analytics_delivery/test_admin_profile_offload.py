import asyncio
from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException, Response

from pymentor_core.adapters.analytics_http.legacy_read import LegacyReadClient, LegacyReadUnavailable
from pymentor_core.adapters.sql import administration
from pymentor_core.modules.analytics_delivery import legacy_bridge

REF = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
STAMP = "2026-11-17T00:00:00+00:00"
SUMMARY = {"current_streak": 0, "visited_today": False, "total_active_days": 1,
           "weekly_activity": [], "weekly_lessons_completed": []}
DATA = {"activity_days_count": 1, "activity_days": ["2026-11-15"], "total_activity_days": 1,
        "last_activity_date": "2026-11-15", "activity_summary": SUMMARY}
CLAIMS = {"subject": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "role": "admin", "scope": "report:read",
          "courses": [], "subjects": [], "global_read": True}


def test_profile_decoder_checks_scope_and_shape():
    import json
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield json.dumps({"coverage": "complete", "data": DATA, "coverage_through_revision": 2,
                "source_snapshot_at": STAMP, "source_hwm": None, "freshness": "unknown"}).encode()
    def handler(request):
        assert request.url.path == "/internal/analytics/v1/legacy/admin/profile"
        assert dict(request.url.params) == {"year": "2026", "month": "11", "subject_ref": REF}
        return httpx.Response(200, stream=Stream())
    client = LegacyReadClient("http://127.0.0.1:1", transport=httpx.MockTransport(handler))
    assert asyncio.run(client.read("profile", "token", year=2026, month=11, subject_ref=REF))["data"] == DATA
    with pytest.raises(LegacyReadUnavailable):
        asyncio.run(client.read("profile", "token", year=2026, month=11, subject_ref="1"))


def test_profile_bridge_checks_lifecycle_and_revision_before_and_after_http(monkeypatch):
    values = iter([(CLAIMS, REF, 2), (CLAIMS, REF, 2)])
    async def sql(request, operation): return next(values)
    class Signer:
        def grant(self, **kwargs): return "token"
    class Client:
        async def read(self, kind, token, **kwargs):
            assert kind == "profile" and kwargs["subject_ref"] == REF
            return {"coverage": "complete", "data": DATA, "coverage_through_revision": 2, "source_snapshot_at": STAMP}
    monkeypatch.setattr(legacy_bridge, "run_request_sql", sql)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(analytics_grant_signer=Signer(), analytics_legacy_client=Client())))
    response = Response()
    assert asyncio.run(legacy_bridge.read_profile(request, response, object(), 1, 2026, 11, expected_ref=REF)) == (DATA, REF)
    assert response.headers["X-Analytics-Coverage-Through-Revision"] == "2"
    values = iter([(CLAIMS, REF, 2), (CLAIMS, REF, 3)])
    with pytest.raises(HTTPException) as error:
        asyncio.run(legacy_bridge.read_profile(request, Response(), object(), 1, 2026, 11, expected_ref=REF))
    assert error.value.status_code == 503
    values = iter([(CLAIMS, REF, 2)])
    with pytest.raises(HTTPException):
        asyncio.run(legacy_bridge.read_profile(request, Response(), object(), 1, 2026, 11,
            expected_ref="cccccccc-cccc-4ccc-8ccc-cccccccccccc"))


@pytest.mark.parametrize("required,has_browser", [(False, False), (True, False), (True, True)])
def test_profile_and_quota_require_browser_presence_before_return_or_mutation(monkeypatch, required, has_browser):
    from pymentor_core.modules.administration.api import router, service
    from pymentor_core.modules.administration.api.schemas import QuotaChange
    from pymentor_core.app import async_request
    from pymentor_core.modules.identity import principal as principal_module
    actor, mutations = object(), []
    async def principal(request): return actor
    async def sql(request, operation): return CLAIMS, REF, 2
    async def content_sql(request, operation, *, with_principal=False):
        result = operation(object(), actor, None)
        return (result, actor) if with_principal else result
    class Signer:
        def grant(self, **claims): return "synthetic"
    class Client:
        async def read(self, *args, **kwargs):
            result = {"coverage": "complete", "data": DATA, "coverage_through_revision": 2, "source_snapshot_at": STAMP}
            if has_browser:
                result.update(browser_quality="unknown", browser_as_of=STAMP)
            return result
    monkeypatch.setattr(async_request, "request_principal", principal)
    monkeypatch.setattr(principal_module, "require_admin", lambda value: value)
    monkeypatch.setattr(legacy_bridge, "run_request_sql", sql)
    monkeypatch.setattr(router, "_admin_content_sql", content_sql)
    monkeypatch.setattr(service, "student_profile", lambda **kwargs: {"profile": {"account_ref": REF}})
    def mutate(**kwargs):
        mutations.append(kwargs)
        return {"profile": kwargs["analytics_activity"]}
    monkeypatch.setattr(service, "change_quota", mutate)
    state = SimpleNamespace(analytics_legacy_read_mode="analytics", analytics_browser_presence_required=required,
                            analytics_grant_signer=Signer(), analytics_legacy_client=Client())
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    async def profile(): return await router.student_profile(1, request, Response(), 2026, 11)
    async def quota(): return await router.change_quota(1, QuotaChange(year=2026, month=11, add=1), request, Response())
    if required and not has_browser:
        for action in (profile, quota):
            with pytest.raises(HTTPException) as error:
                asyncio.run(action())
            assert error.value.status_code == 503
        assert mutations == []
    else:
        assert asyncio.run(profile())["profile"]["activity_days"] == DATA["activity_days"]
        assert asyncio.run(quota())["profile"]["activity_days"] == DATA["activity_days"]
        assert len(mutations) == 1


def test_profile_adapter_skips_all_legacy_activity_sql(monkeypatch):
    class Query:
        def filter(self, *args): return self
        def order_by(self, *args): return self
        def all(self): return []
        def scalar(self): return 0
    class DB:
        def query(self, *args):
            assert "user_activity_days" not in " ".join(map(str, args)).lower()
            return Query()
    monkeypatch.setattr(administration.bookings, "month_bounds", lambda *args: (date(2026, 11, 1), date(2026, 11, 30)))
    monkeypatch.setattr(administration.bookings, "get_quota_status", lambda **kwargs: {})
    monkeypatch.setattr(administration, "_build_learning_progress", lambda *args, **kwargs: ([], 0))
    monkeypatch.setattr(administration, "_serialize_account", lambda *args, **kwargs: {})
    monkeypatch.setattr(administration.activity, "get_activity_records", lambda **kwargs: pytest.fail("legacy calendar read"))
    monkeypatch.setattr(administration.activity, "get_activity_summary", lambda **kwargs: pytest.fail("legacy summary read"))
    result = administration._build_student_profile(SimpleNamespace(id=1), 2026, 11, DB(), analytics_activity=DATA)
    assert all(result[key] == value for key, value in DATA.items())


def test_quota_never_mutates_when_analytics_read_fails(monkeypatch):
    from pymentor_core.modules.administration.api import router
    from pymentor_core.modules.administration.api.schemas import QuotaChange
    from pymentor_core.app import async_request
    from pymentor_core.modules.identity import principal as principal_module
    actor = SimpleNamespace()
    async def principal(request): return actor
    async def unavailable(*args, **kwargs): raise HTTPException(503, "unavailable")
    async def mutate(*args, **kwargs): pytest.fail("quota mutation must not start")
    monkeypatch.setattr(async_request, "request_principal", principal)
    monkeypatch.setattr(principal_module, "require_admin", lambda value: value)
    monkeypatch.setattr(legacy_bridge, "read_profile", unavailable)
    monkeypatch.setattr(router, "_admin_content_sql", mutate)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(analytics_legacy_read_mode="analytics")))
    with pytest.raises(HTTPException) as error:
        asyncio.run(router.change_quota(1, QuotaChange(year=2026, month=11, add=1), request, Response()))
    assert error.value.status_code == 503


def test_quota_rechecks_target_lifecycle_before_mutation(monkeypatch):
    from pymentor_core.modules.analytics_delivery import grants
    from pymentor_core.adapters.sql import role_admin_cutover
    from pymentor_core.shared.errors import ApplicationError
    monkeypatch.setattr(grants, "authorize_grant", lambda *args, **kwargs: {
        "role": "admin", "target_subject_ref": "cccccccc-cccc-4ccc-8ccc-cccccccccccc"})
    monkeypatch.setattr(role_admin_cutover, "require_mode", lambda *args, **kwargs: None)
    monkeypatch.setattr(administration.bookings, "add_quota", lambda **kwargs: pytest.fail("must not mutate replacement"))
    with pytest.raises(ApplicationError) as error:
        administration.change_quota(1, SimpleNamespace(year=2026, month=11, add=1), object(), object(),
                                    analytics_activity=DATA, expected_account_ref=REF)
    assert error.value.status_code == 409


def test_profile_route_off_mode_preserves_old_path(monkeypatch):
    from pymentor_core.modules.administration.api import router
    async def existing(request, operation, **kwargs): return {"profile": {"legacy": True}}
    async def unexpected(*args, **kwargs): pytest.fail("Analytics read must remain off")
    monkeypatch.setattr(router, "_admin_content_sql", existing)
    monkeypatch.setattr(legacy_bridge, "read_profile", unexpected)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(analytics_legacy_read_mode="off")))
    assert asyncio.run(router.student_profile(1, request, Response(), 2026, 11)) == {"profile": {"legacy": True}}


def test_profile_and_quota_routes_use_prevalidated_activity(monkeypatch):
    from pymentor_core.modules.administration.api import router, service
    from pymentor_core.modules.administration.api.schemas import QuotaChange
    from pymentor_core.app import async_request
    from pymentor_core.modules.identity import principal as principal_module
    actor, calls = object(), []
    async def principal(request): return actor
    async def content_sql(request, operation, *, with_principal=False):
        result = operation(object(), actor, None)
        return (result, actor) if with_principal else result
    async def profile_query(request, response, principal, user_id, year, month, **kwargs):
        calls.append("query")
        assert principal is actor and (user_id, year, month) == (1, 2026, 11)
        if kwargs:
            assert kwargs["expected_ref"] == REF
        return DATA, REF
    def baseline(**kwargs):
        calls.append("baseline")
        assert kwargs["analytics_activity"] == {}
        return {"profile": {"account_ref": REF, "quota": {}}}
    def mutation(**kwargs):
        calls.append("mutation")
        assert kwargs["analytics_activity"] == DATA and kwargs["expected_account_ref"] == REF
        return {"profile": {**DATA, "quota": {}}}
    monkeypatch.setattr(async_request, "request_principal", principal)
    monkeypatch.setattr(principal_module, "require_admin", lambda value: value)
    monkeypatch.setattr(router, "_admin_content_sql", content_sql)
    monkeypatch.setattr(legacy_bridge, "read_profile", profile_query)
    monkeypatch.setattr(service, "student_profile", baseline)
    monkeypatch.setattr(service, "change_quota", mutation)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(analytics_legacy_read_mode="analytics")))
    profile = asyncio.run(router.student_profile(1, request, Response(), 2026, 11))
    assert profile["profile"]["activity_days"] == DATA["activity_days"] and calls == ["baseline", "query"]
    calls.clear()
    response = asyncio.run(router.change_quota(1, QuotaChange(year=2026, month=11, add=1), request, Response()))
    assert response["profile"]["activity_days"] == DATA["activity_days"] and calls == ["query", "mutation"]


@pytest.mark.parametrize("required", ["0", "1"])
def test_profile_openapi_feature_on_advertises_unavailable(monkeypatch, required):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from pathlib import Path
    from pymentor_core.app.factory import create_app
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    original_read = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: private if str(self) == "synthetic-grant.pem" else original_read(self))
    values = {"REDIS_URL": "redis://127.0.0.1:1/0", "KNOWI_REDIS_PREFIX": "analytics-smoke",
              "KNOWI_LOGIN_RECOVERY_KEY": "a" * 64, "PYMENTOR_ANALYTICS_DELIVERY_ENABLED": "1",
              "PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED": "1", "PYMENTOR_ANALYTICS_GRANTS_ENABLED": "1",
              "PYMENTOR_ANALYTICS_GRANT_KEY_FILE": "synthetic-grant.pem", "PYMENTOR_ANALYTICS_GRANT_ISSUER": "fixture",
              "PYMENTOR_ANALYTICS_GRANT_KID": "fixture", "PYMENTOR_ANALYTICS_LEGACY_READ_MODE": "analytics",
              "PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE": "outbox_only", "PYMENTOR_ANALYTICS_QUERY_URL": "http://127.0.0.1:1"}
    for name, value in values.items(): monkeypatch.setenv(name, value)
    monkeypatch.setenv("PYMENTOR_ANALYTICS_BROWSER_PRESENCE_REQUIRED", required)
    app = create_app()
    assert app.state.analytics_browser_presence_required is (required == "1")
    schema = app.openapi()
    for route, method in (("/api/admin/users/{user_id}/profile", "get"), ("/api/admin/users/{user_id}/quota", "post")):
        responses = schema["paths"][route][method]["responses"]
        assert "503" in responses and "X-Analytics-Coverage" in responses["200"]["headers"]
