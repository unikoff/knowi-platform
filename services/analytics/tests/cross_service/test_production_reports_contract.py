from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from tools.test_production_reports import capture, assert_same_cutover_and_rollback


def test_report_assertion_rejects_unavailable_and_changed_rows():
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Moscow")).date()
    day = (today - timedelta(days=1)).isoformat()
    visitors = {"date": day, "with_subscription": None, "without_subscription": 1}
    time = {"date": day, "with_subscription": None, "without_subscription": {"active_ms": 15000, "learners": 1}}
    status = "available"
    fixture = {"core_url": "http://127.0.0.1:18151", "course_id": "backend",
               "users": {"leader": {"email": "leader@example.test", "password": "synthetic"}}}

    def handler(request):
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={})
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"user": {"account_ref": "fixture", "auth_revision": 2}})
        if request.url.path.endswith("/overview"):
            return httpx.Response(200, json={"course_trends": {"visits": {"status": status, "quality": "unknown", "daily": [visitors] * 30}}})
        assert request.headers["X-Actor-Ref"] == "fixture" and request.headers["X-Auth-Revision"] == "2"
        return httpx.Response(200, headers={"cache-control": "no-store"}, json={"status": "available", "quality": "unknown", "daily": [time] * 30})

    # Use a realistic series so that a duplicate date is itself rejected.
    def valid_handler(request):
        response = handler(request)
        if request.url.path.endswith(("/overview", "/study-time")):
            body = response.json()
            report = body["course_trends"]["visits"] if request.url.path.endswith("/overview") else body
            template = visitors if request.url.path.endswith("/overview") else time
            report["daily"] = [dict(template, date=(today - timedelta(days=i)).isoformat()) for i in range(29, -1, -1)]
            return httpx.Response(200, json=body, headers={"cache-control": "no-store"})
        return response

    args = {"expected_day": day, "expected_visitors": (None, 1),
            "expected_study_ms": (None, {"active_ms": 15000, "learners": 1}), "transport": httpx.MockTransport(valid_handler)}
    before = capture(fixture, **args)
    assert_same_cutover_and_rollback(before, before, before)
    with pytest.raises(AssertionError):
        assert_same_cutover_and_rollback(before, before, ())
    status = "unavailable"
    with pytest.raises(AssertionError):
        capture(fixture, **args)
    with pytest.raises(ValueError):
        capture({**fixture, "core_url": "https://example.org"}, **args)
