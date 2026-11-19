"""Hold-only report-pagination data in the owned synthetic fixture."""
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import psycopg

from .a010_checks import wait_ready
from .chain_checks import login


def page_events(fixture, now):
    """Twenty-five distinct one-second intervals, all in a past isolated window."""
    start = now.replace(microsecond=0) - timedelta(minutes=20)
    events = []
    for index in range(1, 26):
        begin = start + timedelta(seconds=2 * (index - 1))
        events.append({"schema_version": 2, "event_id": str(uuid4()), "type": "activity_segment",
                       "course_id": "backend", "catalog_version": fixture["catalog_version"],
                       "lesson_id": f"fixture-page-{index:02d}",
                       "section": "theory" if index % 2 else "practice", "session_id": str(uuid4()),
                       "started_at": begin.isoformat(), "ended_at": (begin + timedelta(seconds=1)).isoformat(),
                       "last_action_at": begin.isoformat(), "visible": True, "focused": True,
                       "active_ms": 1000, "payload": {}})
    return start, start + timedelta(seconds=50), events


def seed_and_verify(fixture, analytics):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start, end, events = page_events(fixture, now)
    # Every pre-existing subject/course must be absent in the precise test window.
    # This is a read-only guard: if the premise is false, do not mutate or retry.
    with psycopg.connect(analytics["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as conn:
        overlap = conn.execute("""SELECT count(*) FROM analytics.activity_facts
            WHERE source_kind='browser_v2' AND (
              (accepted_event->'event'->>'type'='activity_segment'
                AND (accepted_event->'event'->>'started_at')::timestamptz < %s
                AND (accepted_event->'event'->>'ended_at')::timestamptz > %s)
              OR (accepted_event->'event'->>'type'='course_opened'
                AND (accepted_event->'event'->>'started_at')::timestamptz >= %s
                AND (accepted_event->'event'->>'started_at')::timestamptz < %s))""",
            (end, start, start, end)).fetchone()[0]
    if overlap:
        raise ValueError("synthetic pagination window is not isolated")
    user = fixture["users"]["student"]
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as student, \
         httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as admin, \
         httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as leader, \
         httpx.Client(base_url=fixture["collector_url"], timeout=8, trust_env=False) as collector, \
         httpx.Client(base_url=fixture["query_url"], timeout=8, trust_env=False) as query:
        login(student, user); login(admin, fixture["users"]["admin"]); login(leader, fixture["users"]["leader"])
        collect = student.post("/api/analytics/grants/collect", json={"course_id": "backend"})
        assert collect.status_code == 200
        # One request only. A timeout is an unknown outcome; never resend blindly.
        accepted = collector.post("/api/analytics/events/batch",
                                  headers={"Authorization": "Bearer " + collect.json()["grant"]}, json={"events": events})
        assert accepted.status_code == 200 and [item["status"] for item in accepted.json()["items"]] == ["accepted"] * 25
        wait_ready(analytics)
        selected = admin.post("/api/analytics/grants/report", json={"user_id": user["id"]})
        scoped = leader.post("/api/analytics/grants/report", json={"course_id": "backend"})
        assert selected.status_code == scoped.status_code == 200
        assert selected.json()["target_subject_ref"] == user["subject_ref"]
        isolated = {"from_time": start.isoformat(), "to_time": end.isoformat()}
        expected = {"unique_visitors": 1, "active_ms": 25000, "theory_ms": 13000, "practice_ms": 12000}
        expected_lessons = {event["lesson_id"] for event in events}
        results = {}
        for label, course, grant in (("global", None, selected), ("backend", "backend", selected),
                                     ("leader_backend", "backend", scoped)):
            params = dict(isolated, **({"course_id": course} if course else {}))
            headers = {"Authorization": "Bearer " + grant.json()["grant"]}
            summary = query.get("/api/analytics/reports/activity", params=params, headers=headers)
            assert summary.status_code == 200 and summary.json()["totals"] == expected
            first = query.get("/api/analytics/reports/activity/details", params=dict(params, offset=0, limit=20), headers=headers)
            second = query.get("/api/analytics/reports/activity/details", params=dict(params, offset=20, limit=20), headers=headers)
            assert first.status_code == second.status_code == 200
            first_body, second_body = first.json(), second.json()
            assert first_body["next_offset"] == 20 and second_body["next_offset"] is None
            assert len(first_body["rows"]) == 20 and len(second_body["rows"]) == 6
            rows = first_body["rows"] + second_body["rows"]
            courses = [row for row in rows if row["level"] == "course"]
            lessons = [row for row in rows if row["level"] == "lesson"]
            assert len(courses) == 1 and courses[0]["course_id"] == "backend" and courses[0]["active_ms"] == 25000
            assert len(lessons) == 25 and {row["lesson_id"] for row in lessons} == expected_lessons
            assert all(row["course_id"] == "backend" and row["active_ms"] == 1000 for row in lessons)
            results[label] = {"totals": expected, "course_rows": 1, "lesson_rows": 25, "page_sizes": [20, 6]}
        hour = {"from_time": (now - timedelta(hours=1)).isoformat(), "to_time": now.isoformat()}
        for label, course, grant, visitors, active, theory, practice in (
                ("global", None, selected, 3, 174000, 80000, 94000),
                ("backend", "backend", selected, 3, 172000, 80000, 92000),
                ("backend_extra", "backend_extra", selected, 1, 2000, 0, 2000),
                ("leader_backend", "backend", scoped, 1, 117000, 45000, 72000)):
            response = query.get("/api/analytics/reports/activity", params=dict(hour, **({"course_id": course} if course else {})),
                                 headers={"Authorization": "Bearer " + grant.json()["grant"]})
            assert response.status_code == 200
            totals = response.json()["totals"]
            assert totals == {"unique_visitors": visitors, "active_ms": active, "theory_ms": theory, "practice_ms": practice}
            results.setdefault(label, {})["hour_totals"] = totals
        print("HOLD_PAGES " + json.dumps({"interval": isolated, "hour": hour, "results": results}, sort_keys=True), flush=True)
    print("PASS browser pagination fixture: signed user B2, independent totals and 20+6 report rows", flush=True)
