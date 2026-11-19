"""Real Collector -> bucket worker -> day worker -> Query checks for a011."""
from datetime import datetime, time as day_time, timedelta, timezone
import time
from uuid import uuid4

import httpx
import psycopg

from .chain_checks import _grant_token


def wait_day_ready(target, run_id, *, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with psycopg.connect(target["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as conn:
            bucket = conn.execute("SELECT run_id,phase FROM analytics.activity_projection_backfill WHERE singleton=true").fetchone()
            day = conn.execute("SELECT run_id,phase FROM analytics.activity_projection_day_backfill WHERE singleton=true").fetchone()
            bad = conn.execute("""SELECT count(*) FROM analytics.activity_projection_day_sources s
                LEFT JOIN analytics.activity_projection_days d USING(subject_ref,local_day,metric_version)
                LEFT JOIN analytics.subject_fences f USING(subject_ref)
                WHERE s.state<>'ready' OR d.run_id IS DISTINCT FROM %s OR d.generation IS DISTINCT FROM s.generation
                   OR d.subject_epoch IS DISTINCT FROM coalesce(f.epoch,0) OR coalesce(f.deleted,false)""", (run_id,)).fetchone()[0]
            buckets = conn.execute("SELECT count(*) FROM analytics.activity_projection_buckets WHERE state<>'ready' OR projection_generation IS DISTINCT FROM generation").fetchone()[0]
        if bucket == (run_id, "complete") and day == (run_id, "complete") and bad == buckets == 0:
            return
        time.sleep(.2)
    raise TimeoutError("activity day projection did not converge")


def day_row(target, subject, local_day):
    with psycopg.connect(target["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as conn:
        return conn.execute("""SELECT s.state,s.generation,d.generation,d.run_id,d.subject_epoch,
            d.source_bucket_count FROM analytics.activity_projection_day_sources s
            LEFT JOIN analytics.activity_projection_days d USING(subject_ref,local_day,metric_version)
            WHERE s.subject_ref=%s AND s.local_day=%s AND s.metric_version='activity-v1'""", (subject, local_day)).fetchone()


def assert_no_old_bucket_or_day_claims(conn, old_run_id):
    """Do not make claims about separately restored generic processing leases."""
    assert conn.execute("SELECT count(*) FROM analytics.activity_projection_day_sources WHERE claim_run_id=%s", (old_run_id,)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM analytics.activity_projection_buckets WHERE claim_run_id=%s", (old_run_id,)).fetchone()[0] == 0


def day_event(fixture, start, offset, length, section):
    begin = start + timedelta(seconds=offset)
    return {"schema_version": 2, "event_id": str(uuid4()), "type": "activity_segment",
            "course_id": "backend", "catalog_version": fixture["catalog_version"], "lesson_id": "lesson-1",
            "section": section, "session_id": str(uuid4()), "started_at": begin.isoformat(),
            "ended_at": (begin + timedelta(seconds=length)).isoformat(), "last_action_at": begin.isoformat(),
            "visible": True, "focused": True, "active_ms": length * 1000, "payload": {}}


def assert_real_day_flow(fixture, target, folder, run_id):
    """Use a full prior Moscow day so Query must read a011's day projection."""
    moscow = timezone(timedelta(hours=3))
    local_day = datetime.now(timezone.utc).astimezone(moscow).date() - timedelta(days=1)
    lower = datetime.combine(local_day, day_time.min, moscow).astimezone(timezone.utc)
    start = lower + timedelta(hours=12)
    subject = uuid4()
    collect_token = _grant_token(folder, subject)
    params = {"from_time": lower.isoformat(), "to_time": (lower + timedelta(days=1)).isoformat(), "course_id": "backend"}
    with httpx.Client(base_url=fixture["collector_url"], timeout=5, trust_env=False) as collector, \
         httpx.Client(base_url=fixture["query_url"], timeout=5, trust_env=False) as query:
        def send_once(item):
            response = collector.post("/api/analytics/events/batch", headers={"Authorization": "Bearer " + collect_token},
                                      json={"events": [item]})
            if response.status_code != 200 or response.json()["items"][0]["status"] != "accepted":
                raise RuntimeError("day fixture ingress not confirmed; reconcile before retry")
        def report(expected):
            response = query.get("/api/analytics/reports/activity", params=params,
                                 headers={"Authorization": "Bearer " + _grant_token(folder, subject, scope="report:read")})
            assert response.status_code == 200 and response.json()["totals"] == expected
        send_once(day_event(fixture, start, 0, 10, "theory"))
        wait_day_ready(target, run_id)
        before = day_row(target, subject, local_day)
        assert before and before[0] == "ready" and before[1] == before[2] and before[3:] == (run_id, 0, 1)
        report({"unique_visitors": 1, "active_ms": 10000, "theory_ms": 10000, "practice_ms": 0})
        # A real later-arriving fact invalidates and rebuilds the same day.
        send_once(day_event(fixture, start, 20, 5, "practice"))
        wait_day_ready(target, run_id)
        after = day_row(target, subject, local_day)
        assert after and after[0] == "ready" and after[1] == after[2] and after[1] > before[1] and after[3:] == (run_id, 0, 1)
        report({"unique_visitors": 1, "active_ms": 15000, "theory_ms": 10000, "practice_ms": 5000})
    print("PASS a011 actual signed ingress, bucket/day rebuild and full-day Query after late event", flush=True)
    return subject, local_day


def assert_restored_day(query_url, target, folder, run_id, subject, local_day):
    """The surviving historical subject must be rebuilt under the new run."""
    moscow = timezone(timedelta(hours=3))
    lower = datetime.combine(local_day, day_time.min, moscow).astimezone(timezone.utc)
    row = day_row(target, subject, local_day)
    assert row and row[0] == "ready" and row[1] == row[2] and row[3:] == (run_id, 0, 1)
    with httpx.Client(base_url=query_url, timeout=5, trust_env=False) as query:
        response = query.get("/api/analytics/reports/activity",
            params={"from_time": lower.isoformat(), "to_time": (lower + timedelta(days=1)).isoformat(), "course_id": "backend"},
            headers={"Authorization": "Bearer " + _grant_token(folder, subject, scope="report:read")})
        assert response.status_code == 200 and response.json()["totals"] == {
            "unique_visitors": 1, "active_ms": 15000, "theory_ms": 10000, "practice_ms": 5000}
