"""Real B2/Query and PostgreSQL a010 handoff checks for the owned fixture."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import psycopg

from .chain_checks import _grant_token, login
from . import resources as r

ROOT = Path(__file__).resolve().parents[2]


def _event(fixture, kind, start, end, *, section=None):
    return {"schema_version": 2, "event_id": str(uuid4()), "type": kind, "course_id": "backend",
            "catalog_version": fixture["catalog_version"], "lesson_id": "lesson-1" if section else None,
            "section": section, "session_id": str(uuid4()), "started_at": start.isoformat(), "ended_at": end.isoformat(),
            "last_action_at": start.isoformat(), "visible": True, "focused": True,
            "active_ms": int((end-start).total_seconds()*1000), "payload": {}}


def wait_ready(analytics, *, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with psycopg.connect(analytics["dsn"], connect_timeout=3) as conn:
            row = conn.execute("SELECT phase FROM analytics.activity_projection_backfill WHERE singleton=true").fetchone()
            pending = conn.execute("SELECT count(*) FROM analytics.activity_projection_buckets WHERE state<>'ready'").fetchone()[0]
        if row == ("complete",) and pending == 0:
            return
        time.sleep(.1)
    raise TimeoutError("activity projection did not drain")


def verify_projection_roles(target):
    name = target["name"]
    with psycopg.connect(target["dsn"], connect_timeout=3) as conn:
        grants = conn.execute("""SELECT
          has_table_privilege(%s,'analytics.activity_projection_buckets','INSERT'),
          has_table_privilege(%s,'analytics.activity_projection_buckets','DELETE'),
          has_column_privilege(%s,'analytics.activity_projection_buckets','subject_ref','SELECT'),
          has_column_privilege(%s,'analytics.activity_projection_buckets','winner_intervals','SELECT'),
          has_function_privilege(%s,'analytics.backfill_activity_projection_fact(text,uuid)','EXECUTE'),
          has_function_privilege(%s,'analytics.begin_activity_projection_backfill(uuid,uuid)','EXECUTE'),
          has_function_privilege(%s,'analytics.lock_activity_projection_run(uuid)','EXECUTE')""",
          (name+"_processor", name+"_privacy", name+"_transfer_status", name+"_transfer_status",
           name+"_processor", name+"_processor", name+"_query")).fetchone()
    assert grants == (False, True, True, False, True, False, True)


def browser_bridge(fixture, core_db, analytics, *, user=None):
    user = user or fixture["browser_users"]["student"]
    with psycopg.connect(core_db["dsn"], connect_timeout=3) as conn:
        assert conn.execute("SELECT count(*) FROM user_activity_days WHERE user_id=%s", (user["id"],)).fetchone()[0] == 0
    now = datetime.now(timezone.utc).replace(microsecond=0)
    base = now - timedelta(minutes=3)
    opened = _event(fixture, "course_opened", now-timedelta(seconds=1), now-timedelta(seconds=1))
    theory = _event(fixture, "activity_segment", base+timedelta(seconds=10), base+timedelta(seconds=50), section="theory")
    practice = _event(fixture, "activity_segment", base+timedelta(seconds=30), base+timedelta(seconds=60), section="practice")
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as core, \
         httpx.Client(base_url=fixture["collector_url"], timeout=5, trust_env=False) as collector, \
         httpx.Client(base_url=fixture["query_url"], timeout=5, trust_env=False) as query:
        login(core, user)
        collect = core.post("/api/analytics/grants/collect", json={"course_id": "backend"})
        report = core.post("/api/analytics/grants/report", json={"course_id": "backend"})
        assert collect.status_code == report.status_code == 200
        batch = collector.post("/api/analytics/events/batch", headers={"Authorization": "Bearer " + collect.json()["grant"]},
                               json={"events": [opened, theory, practice]})
        assert batch.status_code == 200 and [x["status"] for x in batch.json()["items"]] == ["accepted"]*3
        wait_ready(analytics)
        auth = {"Authorization": "Bearer " + report.json()["grant"]}
        params = {"from_time": (base-timedelta(seconds=1)).isoformat(), "to_time": (now+timedelta(seconds=1)).isoformat(), "course_id": "backend"}
        summary = query.get("/api/analytics/reports/activity", params=params, headers=auth)
        detail = query.get("/api/analytics/reports/activity/details", params=params, headers=auth)
        assert summary.status_code == detail.status_code == 200
        total = {"unique_visitors": 1, "active_ms": 50000, "theory_ms": 20000, "practice_ms": 30000}
        assert summary.json()["totals"] == total and summary.json()["quality"] == "unknown"
        rows = detail.json()["rows"]
        assert {(row["level"], row["lesson_id"], row["active_ms"]) for row in rows} == {("course", None, 50000), ("lesson", "lesson-1", 50000)}
        edges = {**params, "from_time": (base+timedelta(seconds=15, milliseconds=123)).isoformat(),
                 "to_time": (base+timedelta(seconds=45, milliseconds=987)).isoformat(), "lesson_id": "lesson-1"}
        clipped = query.get("/api/analytics/reports/activity", params=edges, headers=auth)
        assert clipped.status_code == 200 and clipped.json()["totals"] == {"unique_visitors": 1, "active_ms": 30864, "theory_ms": 14877, "practice_ms": 15987}
        local_day = now.astimezone(ZoneInfo("Europe/Moscow")).date()
        guarded = {"X-Synthetic-No-Legacy-SQL": "1"}
        calendar = core.get("/api/user/activity", params={"year": local_day.year, "month": local_day.month}, headers=guarded)
        streak = core.get("/api/user/activity/summary", headers=guarded)
        assert calendar.status_code == streak.status_code == 200
        assert calendar.headers.get("x-analytics-source") == "mixed" and calendar.headers.get("x-analytics-browser-quality") == "unknown"
        assert calendar.headers.get("x-analytics-coverage") == "complete"
        assert {"activity_date": local_day.isoformat(), "visited_at": None} in calendar.json()["visits"]
        assert streak.json()["visited_today"] is True and streak.json()["current_streak"] >= 1
    with psycopg.connect(core_db["dsn"], connect_timeout=3) as conn:
        assert conn.execute("SELECT count(*) FROM user_activity_days WHERE user_id=%s", (user["id"],)).fetchone()[0] == 0
    return opened


def claim_handoff(fixture, analytics, urls, folder, run_id, authority_json):
    subject = uuid4()
    bucket = (int(time.time()) // 900 - 1) * 900
    start = datetime.fromtimestamp(bucket+30, timezone.utc)
    first = _event(fixture, "activity_segment", start, start+timedelta(seconds=20), section="theory")
    late = _event(fixture, "activity_segment", start+timedelta(seconds=5), start+timedelta(seconds=25), section="practice")
    token = _grant_token(folder, subject)
    def send(event):
        with httpx.Client(base_url=fixture["collector_url"], timeout=4, trust_env=False) as client:
            result = client.post("/api/analytics/events/batch", headers={"Authorization": "Bearer " + token}, json={"events": [event]})
            assert result.status_code == 200 and result.json()["items"][0]["status"] == "accepted"
    env = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC"}}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "tests"), CROSS_SERVICE_PRIVATE_DIR=str(folder),
               CROSS_SERVICE_ACTOR_DSN=urls["processor"], CROSS_SERVICE_ACTOR_SUBJECT=str(subject),
               CROSS_SERVICE_ACTOR_RUN_ID=str(run_id), CROSS_SERVICE_ACTOR_AUTHORITY=authority_json)
    def actor(phase):
        r.run_child([sys.executable, "-B", "-m", "cross_service.projection_actor"], cwd=ROOT,
                    env=dict(env, CROSS_SERVICE_ACTOR_PHASE=phase), timeout=15)
    send(first)
    actor("old-claim")
    old = json.loads((folder / "worker-old-claim.json").read_text(encoding="utf-8"))["claim"]
    send(late)
    with psycopg.connect(analytics["dsn"], connect_timeout=3) as conn:
        state = conn.execute("SELECT state,generation FROM analytics.activity_projection_buckets WHERE subject_ref=%s", (subject,)).fetchone()
    assert state == ("dirty", old["generation"] + 1)
    with httpx.Client(base_url=fixture["query_url"], timeout=4, trust_env=False) as query:
        report = query.get("/api/analytics/reports/activity",
            params={"from_time": start.isoformat(), "to_time": (start+timedelta(seconds=30)).isoformat(), "course_id": "backend"},
            headers={"Authorization": "Bearer " + _grant_token(folder, subject, scope="report:read")})
        assert report.status_code == 503
    actor("new-commit")
    actor("old-stale")
    with psycopg.connect(analytics["dsn"], connect_timeout=3) as conn:
        row = conn.execute("SELECT state,generation,projection_generation FROM analytics.activity_projection_buckets WHERE subject_ref=%s", (subject,)).fetchone()
    assert row == ("ready", 2, 2)
