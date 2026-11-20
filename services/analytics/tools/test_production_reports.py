"""Assertions for an isolated cross-service course-report rehearsal.

Call capture() after a known, fully projected browser fixture, before and after
changing Core's read mode. This module does not create data, change modes or
claim to validate production ingress. The caller owns the fixture lifecycle.
"""
from datetime import date, datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


def _loopback(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port != 18151
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("course report acceptance requires the disposable Core listener")


def capture(fixture, *, expected_day, expected_visitors, expected_study_ms, transport=None):
    """Read exact expected positive values through authenticated Core HTTP.

    The supplied event day must be a complete prior Moscow day; this avoids
    comparing moving current-day totals. Cohort expectations are exact pairs:
    (with subscription, without subscription), and study entries are either
    None or {'active_ms': int, 'learners': int}. No result may be unavailable.
    Unknown coverage remains unknown and is not promoted to complete.
    """
    _loopback(fixture["core_url"])
    selected = date.fromisoformat(expected_day)
    from zoneinfo import ZoneInfo
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Moscow")).date()
    if not 1 <= (today - selected).days <= 29:
        raise ValueError("expected day must be one of the previous 29 Moscow dates")
    if not any(value is not None and value > 0 for value in expected_visitors):
        raise ValueError("a nonempty visitor expectation is required")
    if not any(value is not None and value.get("active_ms", 0) > 0 for value in expected_study_ms):
        raise ValueError("a positive measured study-time expectation is required")
    user = fixture["users"]["leader"]
    course = fixture["course_id"]
    with httpx.Client(base_url=fixture["core_url"], timeout=10, trust_env=False, transport=transport) as client:
        login = client.post("/api/user/login", json={"email": user["email"], "password": user["password"]},
                            headers={"Idempotency-Key": str(uuid4())})
        if login.status_code != 200:
            raise AssertionError("fixture leader login failed")
        me = client.get("/api/user/me")
        if me.status_code != 200:
            raise AssertionError("fixture leader identity unavailable")
        identity = me.json()["user"]
        headers = {"X-Actor-Ref": identity["account_ref"], "X-Auth-Revision": str(identity["auth_revision"])}
        overview = client.get(f"/api/admin/courses/{course}/overview", params={"year": today.year, "month": today.month})
        study = client.get(f"/api/admin/courses/{course}/study-time", headers=headers)
        if overview.status_code != 200 or study.status_code != 200:
            raise AssertionError("course report HTTP request failed")
        if study.headers.get("cache-control") != "no-store":
            raise AssertionError("study report is cacheable")
        visits = overview.json()["course_trends"]["visits"]
        measured = study.json()
        rows = []
        for report, expected in ((visits, expected_visitors), (measured, expected_study_ms)):
            if report.get("status") != "available" or report.get("quality") != "unknown" or len(report.get("daily", [])) != 30:
                raise AssertionError("report is unavailable or has an unexpected coverage contract")
            matches = [row for row in report["daily"] if row.get("date") == expected_day]
            if len(matches) != 1:
                raise AssertionError("fixture date missing or duplicated")
            row = matches[0]
            actual = (row["with_subscription"], row["without_subscription"])
            if actual != tuple(expected):
                raise AssertionError("fixture cohort values differ from the independent expectation")
            rows.append(row)
        return tuple(rows)


def assert_same_cutover_and_rollback(before, after_cutover, after_rollback):
    """Compare stable fixture-day rows, not the rolling 30-day window."""
    if before != after_cutover or before != after_rollback:
        raise AssertionError("course reports changed across read switch or rollback")
