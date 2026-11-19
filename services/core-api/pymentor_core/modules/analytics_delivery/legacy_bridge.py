"""Opt-in comparison/read gate for existing Core public statistics routes."""
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
import hashlib
import json
import os
from datetime import datetime, timezone

from pymentor_core.modules.analytics_delivery.errors import LegacyReadUnavailable
from pymentor_core.app.async_request import run_request_sql
from pymentor_core.modules.analytics_delivery.grants import authorize_grant
from pymentor_core.modules.analytics_delivery.source_watermark import capture, capture_admin_labels, capture_profile


def admin_completions_enabled(state):
    return (getattr(state, "analytics_legacy_read_mode", "off") == "analytics"
            and os.environ.get("PYMENTOR_ANALYTICS_ADMIN_COMPLETIONS_ENABLED") == "1")


def _counter(state, key):
    counters = state.analytics_legacy_shadow
    counters[key] = counters.get(key, 0) + 1


def _require_browser_presence(state, result):
    # LegacyReadClient validates the pair's values and rejects partial pairs.
    if getattr(state, "analytics_browser_presence_required", False) and not {
        "browser_quality", "browser_as_of"
    }.issubset(result):
        raise LegacyReadUnavailable()


async def read_profile(request, response, principal, user_id, year, month, *, expected_ref=None):
    """Read only a Core-authorized lifecycle and reconcile revision after HTTP."""
    try:
        before = await run_request_sql(request, lambda db: capture_profile(db, principal, user_id))
        claims, reference, revision = before
        if expected_ref is not None and reference != expected_ref:
            raise LegacyReadUnavailable()
        grant = request.app.state.analytics_grant_signer.grant(**claims)
        result = await request.app.state.analytics_legacy_client.read("profile", grant, year=year, month=month,
                                                                      subject_ref=reference)
        if result["coverage"] != "complete" or result["coverage_through_revision"] != revision:
            raise LegacyReadUnavailable()
        _require_browser_presence(request.app.state, result)
        after = await run_request_sql(request, lambda db: capture_profile(db, principal, user_id))
        if before != after:
            raise LegacyReadUnavailable()
        response.headers["X-Analytics-Source"] = "mixed"
        response.headers["X-Analytics-Coverage"] = "complete"
        response.headers["X-Analytics-Coverage-Through-Revision"] = str(revision)
        response.headers["X-Analytics-Source-Snapshot-At"] = result["source_snapshot_at"]
        response.headers["X-Analytics-Source-HWM"] = "unknown"
        response.headers["X-Analytics-Freshness"] = "unknown"
        if "browser_quality" in result:
            response.headers["X-Analytics-Browser-Quality"] = result["browser_quality"]
            response.headers["X-Analytics-Browser-As-Of"] = result["browser_as_of"]
        return result["data"], reference
    except Exception:
        raise HTTPException(status_code=503, detail="Analytics statistics unavailable",
                            headers={"X-Analytics-Coverage": "unavailable"}) from None


async def apply(request, response, principal, kind, baseline, *, year=None, month=None, course_id=None):
    mode = getattr(request.app.state, "analytics_legacy_read_mode", "off")
    if mode == "off":
        return baseline
    coverage = "unavailable"
    try:
        completions_enabled = kind == "admin" and admin_completions_enabled(request.app.state)
        at = datetime.now(timezone.utc).isoformat() if completions_enabled else None
        labels = None
        if mode == "analytics":
            def before_capture(db):
                claims, source = capture(db, principal, kind)
                return claims, source, capture_admin_labels(db) if completions_enabled else None
            if completions_enabled:
                claims, source, labels = await run_request_sql(request, before_capture)
            else:
                claims, source = await run_request_sql(request, lambda db: capture(db, principal, kind))
            if kind == "admin":
                claims = {**claims, "legacy_admin_roster_v1": {"version": 1, "members": source}}
        else:
            claims = await run_request_sql(request, lambda db: authorize_grant(db, principal,
                purpose="report" if kind == "admin" else "self_report", course_id=None, target_ref=None))
        if claims["role"] not in ({"admin"} if kind == "admin" else {"user", "leader", "admin"}):
            raise LegacyReadUnavailable()
        grant = request.app.state.analytics_grant_signer.grant(**claims)
        result = await request.app.state.analytics_legacy_client.read(kind, grant, year=year, month=month)
        completions = (await request.app.state.analytics_legacy_client.read("admin_completions", grant, at=at)
                       if completions_enabled else None)
        coverage, data = result["coverage"], result["data"]
        if mode == "analytics":
            if coverage != "complete":
                raise LegacyReadUnavailable()
            _require_browser_presence(request.app.state, result)
            if kind == "admin":
                vector = result["coverage_subjects"]
                observed = sorted(({"subject_ref": item["subject_ref"], "revision": item["coverage_through_revision"],
                                    "student": item["student"]} for item in vector), key=lambda row: row["subject_ref"])
                if observed != source:
                    raise LegacyReadUnavailable()
                if completions_enabled:
                    students = {row["subject_ref"]: row["revision"] for row in source if row["student"]}
                    coverage_rows = completions["coverage_subjects"]
                    if ({row["subject_ref"]: row["coverage_through_revision"] for row in coverage_rows} != students
                            or set(completions["weekly_by_subject"]) != set(students)
                            or {row[0] for row in labels} != set(students)):
                        raise LegacyReadUnavailable()
            elif result["coverage_through_revision"] != source:
                raise LegacyReadUnavailable()
            if completions_enabled:
                after_claims, after, after_labels = await run_request_sql(request, before_capture)
            else:
                after_claims, after = await run_request_sql(request, lambda db: capture(db, principal, kind))
                after_labels = None
            original_claims = {key: value for key, value in claims.items() if key != "legacy_admin_roster_v1"}
            if after_claims != original_claims or after != source or after_labels != labels:
                raise LegacyReadUnavailable()
            has_browser = "browser_quality" in result
            response.headers["X-Analytics-Source"] = "mixed" if kind == "admin" or has_browser else "analytics"
            if has_browser:
                response.headers["X-Analytics-Browser-Quality"] = result["browser_quality"]
                response.headers["X-Analytics-Browser-As-Of"] = result["browser_as_of"]
            response.headers["X-Analytics-Coverage"] = coverage
            response.headers["X-Analytics-Source-HWM"] = "unknown"
            response.headers["X-Analytics-Freshness"] = "unknown"
            if kind == "admin":
                response.headers["X-Analytics-Coverage-Vector-SHA256"] = hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                if vector:
                    response.headers["X-Analytics-Source-Snapshot-At"] = min(item["source_snapshot_at"] for item in vector)
                dashboard = data["dashboard_activity"]
                if completions_enabled:
                    weekly = completions["weekly_by_subject"]
                    ranked = sorted((row for row in labels if weekly[row[0]] > 0), key=lambda row: -weekly[row[0]])[:6]
                    dashboard = {**dashboard, "daily_lessons_completed": completions["daily_lessons_completed"],
                                 "lessons_completed_7d": completions["lessons_completed_7d"],
                                 "most_active_students": [{"id": uid, "username": name, "lessons_completed": weekly[ref]}
                                                          for ref, uid, name in ranked]}
                    timestamps = [row["source_snapshot_at"] for row in vector + completions["coverage_subjects"]]
                    if timestamps:
                        response.headers["X-Analytics-Source-Snapshot-At"] = min(timestamps)
                if "activity_today" in baseline:
                    return {**baseline, "activity_today": data["platform_activity_today"],
                            "dashboard": {**baseline["dashboard"], **dashboard}}
                return {**baseline, **dashboard}
            response.headers["X-Analytics-Coverage-Through-Revision"] = str(source)
            response.headers["X-Analytics-Source-Snapshot-At"] = result["source_snapshot_at"]
            return data
        if kind == "admin":
            dashboard = data["dashboard_activity"]
            public_baseline = jsonable_encoder(baseline)
            source_dashboard = public_baseline["dashboard"] if "activity_today" in public_baseline else public_baseline
            comparison = all(dashboard[key] == source_dashboard[key] for key in
                             ("active_students_7d", "inactive_students_7d", "daily_active_students"))
            if course_id is None and "activity_today" in public_baseline:
                comparison = comparison and data["platform_activity_today"] == public_baseline["activity_today"]
        else:
            comparison = data == jsonable_encoder(baseline)
        if mode == "shadow":
            _counter(request.app.state, "observed_match" if comparison else "observed_mismatch")
            _counter(request.app.state, "coverage_" + coverage)
            response.headers["X-Analytics-Source"] = "core"
            response.headers["X-Analytics-Coverage"] = coverage
            return baseline
        raise LegacyReadUnavailable()
    except Exception:
        if mode == "shadow":
            _counter(request.app.state, "unavailable")
            response.headers["X-Analytics-Source"] = "core"
            response.headers["X-Analytics-Coverage"] = "unavailable"
            return baseline
        raise HTTPException(status_code=503, detail="Analytics statistics unavailable",
                            headers={"X-Analytics-Source": "analytics", "X-Analytics-Coverage": "unavailable"}) from None


async def apply_course_completions(request, response, principal, baseline, course, *, year=None, month=None):
    from pymentor_core.modules.analytics_delivery.source_watermark import capture_course
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(timezone.utc)
        roster_now = now.astimezone(ZoneInfo("Europe/Moscow"))
        roster_year, roster_month = (year, month) if year is not None else (roster_now.year, roster_now.month)
        before = await run_request_sql(request, lambda db: capture_course(db, principal, course, roster_year, roster_month))
        claims, members, labels, _actor = before
        grant = request.app.state.analytics_grant_signer.grant(**claims, course_completion_roster_v1={
            "version": 1, "course_id": course, "members": members, "year": year, "month": month})
        at = now.isoformat()
        result = await request.app.state.analytics_legacy_client.read("course_completions", grant,
            course_id=course, at=at, year=year, month=month)
        expected = {row["subject_ref"]: row["revision"] for row in members}
        students = {row["subject_ref"] for row in members if row["student"]}
        if ({row["subject_ref"]: row["coverage_through_revision"] for row in result["coverage_subjects"]} != expected
                or set(result["weekly_by_subject"]) != students or {row[0] for row in labels} != students):
            raise LegacyReadUnavailable()
        after = await run_request_sql(request, lambda db: capture_course(db, principal, course, roster_year, roster_month))
        if before != after:
            raise LegacyReadUnavailable()
        weekly = result["weekly_by_subject"]
        ranked = sorted((row for row in labels if weekly[row[0]] > 0), key=lambda row: -weekly[row[0]])[:6]
        dashboard = {**baseline["dashboard"], "daily_lessons_completed": result["daily_lessons_completed"],
                     "lessons_completed_7d": result["lessons_completed_7d"],
                     "most_active_students": [{"id": uid, "username": name, "lessons_completed": weekly[ref]}
                                              for ref, uid, name in ranked]}
        response.headers["X-Analytics-Source"] = "mixed"
        response.headers["X-Analytics-Coverage"] = "complete"
        response.headers["X-Analytics-Coverage-Vector-SHA256"] = hashlib.sha256(json.dumps(members, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if result["coverage_subjects"]:
            response.headers["X-Analytics-Source-Snapshot-At"] = min(row["source_snapshot_at"] for row in result["coverage_subjects"])
        result_baseline = {**baseline, "dashboard": dashboard}
        if year is not None:
            result_baseline["completed_lessons"] = result["completed_lessons_month"]
        return result_baseline
    except Exception:
        raise HTTPException(status_code=503, detail="Analytics statistics unavailable",
                            headers={"X-Analytics-Source": "analytics", "X-Analytics-Coverage": "unavailable"}) from None
