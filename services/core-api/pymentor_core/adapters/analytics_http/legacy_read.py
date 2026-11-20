"""Bounded read-only Analytics compatibility client; accepts no Core credentials."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from urllib.parse import urlencode, urlsplit

from pymentor_core.adapters.analytics_http.http import request
from pymentor_core.modules.analytics_delivery.errors import LegacyReadUnavailable

_PATHS = {"calendar": "/internal/analytics/v1/legacy/calendar",
          "summary": "/internal/analytics/v1/legacy/summary",
          "admin": "/internal/analytics/v1/legacy/admin/activity",
          "admin_completions": "/internal/analytics/v1/legacy/admin/completions",
          "course_completions": "/internal/analytics/v1/legacy/course/completions",
          "profile": "/internal/analytics/v1/legacy/admin/profile"}


def _complete_metadata(value, kind):
    def revision(v):
        return type(v) is int and 0 <= v <= (1 << 63) - 1
    def timestamp(v):
        parsed = datetime.fromisoformat(v)
        return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)
    common = {"coverage", "data", "source_hwm", "freshness"}
    if value.get("source_hwm", False) is not None or value.get("freshness") != "unknown":
        raise ValueError
    if kind != "admin":
        if set(value) != common | {"coverage_through_revision", "source_snapshot_at"} or not revision(value["coverage_through_revision"]) or not timestamp(value["source_snapshot_at"]):
            raise ValueError
    else:
        if set(value) != common | {"coverage_subjects"} or not isinstance(value["coverage_subjects"], list) or len(value["coverage_subjects"]) > 80:
            raise ValueError
        seen = set()
        for row in value["coverage_subjects"]:
            if (not isinstance(row, dict) or set(row) != {"subject_ref", "coverage_through_revision", "source_snapshot_at", "student"}
                    or str(UUID(row["subject_ref"])) != row["subject_ref"] or row["subject_ref"] in seen
                    or not revision(row["coverage_through_revision"]) or not timestamp(row["source_snapshot_at"])
                    or type(row["student"]) is not bool):
                raise ValueError
            seen.add(row["subject_ref"])


def _admin_data(data):
    expected = {"platform_activity_today", "platform_course_activity_today", "legacy_dashboard_active_students_7d", "dashboard_activity"}
    if set(data) != expected:
        raise ValueError
    platform, course, dashboard = (data["platform_activity_today"], data["platform_course_activity_today"], data["dashboard_activity"])
    if not all(isinstance(x, dict) for x in (platform, course, dashboard)):
        raise ValueError
    shape = {"date", "unique_users", "coverage_since", "partial_day"}
    if set(platform) != shape or set(course) != shape or set(dashboard) != {"active_students_7d", "inactive_students_7d", "daily_active_students"}:
        raise ValueError
    today = date.fromisoformat(platform["date"])
    if (today.isoformat() != platform["date"] or course["date"] != platform["date"]
            or type(platform["unique_users"]) is not int or platform["unique_users"] < 0
            or platform["coverage_since"] is not None or platform["partial_day"] is not True
            or course["unique_users"] is not None or course["coverage_since"] is not None or course["partial_day"] is not False):
        raise ValueError
    for key in ("active_students_7d", "inactive_students_7d"):
        if type(dashboard[key]) is not int or dashboard[key] < 0:
            raise ValueError
    if type(data["legacy_dashboard_active_students_7d"]) is not int or data["legacy_dashboard_active_students_7d"] != dashboard["active_students_7d"]:
        raise ValueError
    days = dashboard["daily_active_students"]
    if not isinstance(days, list) or len(days) != 30:
        raise ValueError
    for index, item in enumerate(days):
        expected_date = (today - timedelta(days=29 - index)).isoformat()
        if (not isinstance(item, dict) or set(item) != {"date", "active_students"} or item["date"] != expected_date
                or type(item["active_students"]) is not int or item["active_students"] < 0):
            raise ValueError
    return data


def _profile_data(data, year, month):
    from pymentor_core.modules.identity.api.schemas import ActivitySummaryResponse
    if set(data) != {"activity_days_count", "activity_days", "total_activity_days", "last_activity_date", "activity_summary"}:
        raise ValueError
    days = data["activity_days"]
    if (not isinstance(days, list) or len(days) > 31 or type(data["activity_days_count"]) is not int
            or data["activity_days_count"] != len(days) or type(data["total_activity_days"]) is not int
            or data["total_activity_days"] < len(days)):
        raise ValueError
    for day in days:
        parsed = date.fromisoformat(day)
        if parsed.isoformat() != day or parsed.year != year or parsed.month != month:
            raise ValueError
    if days != sorted(set(days)):
        raise ValueError
    if data["last_activity_date"] is not None and date.fromisoformat(data["last_activity_date"]).isoformat() != data["last_activity_date"]:
        raise ValueError
    data["activity_summary"] = ActivitySummaryResponse.model_validate(data["activity_summary"], strict=True).model_dump()
    return data


def _admin_completions(value, at, *, course=False):
    expected = {"daily_lessons_completed", "lessons_completed_7d", "weekly_by_subject", "coverage_subjects"}
    if course:
        expected.add("completed_lessons_month")
        monthly = value.get("completed_lessons_month")
        if monthly is not None and (type(monthly) is not int or monthly < 0):
            raise ValueError
    if set(value) != expected:
        raise ValueError
    from zoneinfo import ZoneInfo
    today = datetime.fromisoformat(at).astimezone(ZoneInfo("Europe/Moscow")).date()
    days, weekly, vector = value["daily_lessons_completed"], value["weekly_by_subject"], value["coverage_subjects"]
    if not isinstance(days, list) or len(days) != 30 or not isinstance(weekly, dict) or not isinstance(vector, list) or len(vector) > 80:
        raise ValueError
    for index, item in enumerate(days):
        if (not isinstance(item, dict) or set(item) != {"date", "lessons_completed"}
                or item["date"] != (today - timedelta(days=29-index)).isoformat()
                or type(item["lessons_completed"]) is not int or item["lessons_completed"] < 0):
            raise ValueError
    if type(value["lessons_completed_7d"]) is not int or value["lessons_completed_7d"] < 0:
        raise ValueError
    if any(str(UUID(ref)) != ref or type(count) is not int or count < 0 for ref, count in weekly.items()):
        raise ValueError
    if sum(weekly.values()) != value["lessons_completed_7d"]:
        raise ValueError
    seen = set()
    for row in vector:
        if not isinstance(row, dict) or set(row) != {"subject_ref", "coverage_through_revision", "source_snapshot_at"}:
            raise ValueError
        ref, revision, stamp = row["subject_ref"], row["coverage_through_revision"], row["source_snapshot_at"]
        parsed = datetime.fromisoformat(stamp)
        if (str(UUID(ref)) != ref or ref in seen or type(revision) is not int or not 0 <= revision <= (1 << 63)-1
                or parsed.utcoffset() != timedelta(0) or parsed.isoformat() != stamp):
            raise ValueError
        seen.add(ref)
    if (not course and seen != set(weekly)) or (course and not set(weekly).issubset(seen)):
        raise ValueError
    return value


class LegacyReadClient:
    def __init__(self, endpoint: str, *, transport=None, verify=True):
        url = urlsplit(endpoint)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.query or url.fragment or (url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"})):
            raise ValueError("invalid Analytics query endpoint")
        self._endpoint = endpoint.rstrip("/")
        self._transport = transport
        self._verify = verify

    async def read(self, kind: str, grant: str, *, year=None, month=None, subject_ref=None, at=None, course_id=None, deadline_seconds=1):
        if kind not in _PATHS or not isinstance(grant, str) or not grant or len(grant) > 16384:
            raise LegacyReadUnavailable()
        query = ""
        if kind in {"calendar", "profile"}:
            if type(year) is not int or type(month) is not int or not 2000 <= year <= 2100 or not 1 <= month <= 12:
                raise LegacyReadUnavailable()
            params = {"year": year, "month": month}
            if kind == "profile":
                try:
                    if not isinstance(subject_ref, str) or str(UUID(subject_ref)) != subject_ref:
                        raise ValueError
                except (ValueError, TypeError, AttributeError):
                    raise LegacyReadUnavailable() from None
                params["subject_ref"] = subject_ref
            query = "?" + urlencode(params)
        if kind in {"admin_completions", "course_completions"}:
            try:
                parsed = datetime.fromisoformat(at)
                if parsed.utcoffset() != timedelta(0) or parsed.isoformat() != at:
                    raise ValueError
            except (ValueError, TypeError):
                raise LegacyReadUnavailable() from None
            params = {"at": at}
            if kind == "course_completions":
                if not isinstance(course_id, str) or not course_id or len(course_id) > 100:
                    raise LegacyReadUnavailable()
                params["course_id"] = course_id
                if year is not None or month is not None:
                    if type(year) is not int or type(month) is not int or not 2000 <= year <= 2100 or not 1 <= month <= 12:
                        raise LegacyReadUnavailable()
                    params.update(year=year, month=month)
            query = "?" + urlencode(params)
        result = await request("GET", self._endpoint + _PATHS[kind] + query,
            headers={"Authorization": "Bearer " + grant, "Accept": "application/json"}, body=None,
            deadline_seconds=deadline_seconds, max_bytes=65536, transport=self._transport, verify=self._verify)
        if result is None or result[0] != 200:
            raise LegacyReadUnavailable()
        try:
            def unique(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise ValueError
                    values[key] = value
                return values
            value = json.loads(result[1], object_pairs_hook=unique)
            if kind in {"admin_completions", "course_completions"}:
                result = _admin_completions(value, at, course=kind == "course_completions")
                if kind == "course_completions" and ((year is None) != (result["completed_lessons_month"] is None)):
                    raise ValueError
                return result
            if not isinstance(value, dict) or value.get("coverage") not in {"unknown", "complete"} or not isinstance(value.get("data"), dict):
                raise ValueError
            browser_fields = {"browser_quality", "browser_as_of"}
            present = set(value) & browser_fields
            if present:
                if present != browser_fields or value["browser_quality"] != "unknown" or not isinstance(value["browser_as_of"], str):
                    raise ValueError
                stamp = datetime.fromisoformat(value["browser_as_of"])
                if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0) or stamp.isoformat() != value["browser_as_of"]:
                    raise ValueError
            core_envelope = {key: item for key, item in value.items() if key not in browser_fields}
            if value["coverage"] == "unknown":
                if set(core_envelope) != {"coverage", "data"}:
                    raise ValueError
            else:
                _complete_metadata(core_envelope, kind)
            if kind == "admin":
                value["data"] = _admin_data(value["data"])
            elif kind == "profile":
                value["data"] = _profile_data(value["data"], year, month)
            else:
                from pymentor_core.modules.identity.api.schemas import ActivityCalendarResponse, ActivitySummaryResponse
                model = ActivityCalendarResponse if kind == "calendar" else ActivitySummaryResponse
                value["data"] = model.model_validate(value["data"], strict=True).model_dump()
            return value
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise LegacyReadUnavailable() from None
