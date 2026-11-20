"""Additional real-HTTP checks for the opt-in a007/a008/a009 chain."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from datetime import date, datetime, timezone
from contextlib import closing
from uuid import uuid4

import httpx
import jwt
import psycopg

from . import resources as r

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT.parent / "core-api"


def login(client, user):
    response = client.post("/api/user/login", json={"email": user["email"], "password": user["password"]},
                           headers={"Idempotency-Key": str(uuid4())})
    if response.status_code != 200:
        raise AssertionError("synthetic login failed")


def producer_token(folder):
    now = int(time.time())
    return jwt.encode({"grant_version": 1, "iss": "control-issuer", "aud": "analytics-control", "sub": "synthetic",
                       "iat": now, "nbf": now, "exp": now + 60, "scope": "control:write"},
                      (folder / "producer.private.pem").read_bytes(), algorithm="EdDSA", headers={"kid": "p"})


def _grant_token(folder, subject, *, scope="collect"):
    now = int(time.time())
    return jwt.encode({"grant_version": 1, "iss": "grant-issuer", "aud": "analytics", "sub": str(subject),
                       "iat": now, "nbf": now, "exp": now+(300 if scope == "collect" else 60), "role": "user", "scope": scope,
                       "course_ids": ["backend"], "subject_refs": [], "global_read": False},
                      (folder / "grant.private.pem").read_bytes(), algorithm="EdDSA", headers={"kid": "g"})


def seed_historical_browser(fixture, analytics, folder):
    from datetime import timedelta
    subject = uuid4()
    start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=2)
    event = {"schema_version": 2, "event_id": str(uuid4()), "type": "activity_segment", "course_id": "backend",
             "catalog_version": fixture["catalog_version"], "lesson_id": "lesson-1", "section": "theory",
             "session_id": str(uuid4()), "started_at": start.isoformat(), "ended_at": (start+timedelta(seconds=30)).isoformat(),
             "last_action_at": start.isoformat(), "visible": True, "focused": True, "active_ms": 30000, "payload": {}}
    with httpx.Client(base_url=fixture["collector_url"], timeout=4, trust_env=False) as collector:
        response = collector.post("/api/analytics/events/batch", headers={"Authorization": "Bearer " + _grant_token(folder, subject)},
                                  json={"events": [event]})
        assert response.status_code == 200 and response.json()["items"][0]["status"] == "accepted"
    with psycopg.connect(analytics["dsn"], connect_timeout=3) as conn:
        assert conn.execute("SELECT phase FROM analytics.activity_projection_backfill").fetchone() == ("scanning",)
        assert conn.execute("SELECT count(*) FROM analytics.activity_facts WHERE subject_ref=%s AND source_kind='browser_v2'", (str(subject),)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM analytics.activity_projection_buckets WHERE subject_ref=%s AND state='dirty'", (str(subject),)).fetchone()[0] >= 1
    return subject


def assert_projection_backfill(analytics, run_id, subject):
    with psycopg.connect(analytics["dsn"], connect_timeout=3) as conn:
        assert conn.execute("SELECT run_id,phase FROM analytics.activity_projection_backfill").fetchone() == (run_id, "complete")
        rows = conn.execute("SELECT state, generation, projection_generation FROM analytics.activity_projection_buckets WHERE subject_ref=%s", (str(subject),)).fetchall()
        assert rows and all(state == "ready" and generation == projected for state, generation, projected in rows)


def publish_catalog(python, core_env, folder, owner_token, fixture, analytics_db, *, browser_pages=False):
    env = dict(core_env, CROSS_SERVICE_OWNER_TOKEN=owner_token,
               PYMENTOR_ANALYTICS_CATALOG_FIXTURE_DIR=str(folder), PYMENTOR_ANALYTICS_CATALOG_URL=fixture["collector_url"],
               PYMENTOR_ANALYTICS_PRODUCER_ISSUER="control-issuer", PYMENTOR_ANALYTICS_PRODUCER_KID="p",
               PYMENTOR_ANALYTICS_PRODUCER_ID="synthetic", PYMENTOR_ANALYTICS_PRODUCER_KEY_FILE=str(folder / "producer.private.pem"))
    r.run_child([str(python), "-B", str(CORE / "tools/analytics_catalog/publish.py"),
                 "--courses-root", str(folder / "courses"), "--course-id", "backend"], cwd=CORE, env=env, timeout=30)
    if (folder / "catalog-lost-reply.marker").read_text(encoding="ascii") != "upstream_committed_reply_dropped":
        raise AssertionError("catalog lost reply was not exercised")
    with httpx.Client(base_url=fixture["core_url"], timeout=4, trust_env=False) as student, \
         httpx.Client(base_url=fixture["core_url"], timeout=4, trust_env=False) as admin:
        login(student, fixture["users"]["student"]); login(admin, fixture["users"]["admin"])
        params = {"course_id": "backend"}
        response = student.get("/api/learning/analytics-catalog", params=params)
        assert response.status_code == 200 and response.headers.get("cache-control") == "no-store"
        data = response.json()
        expected_lessons = ["lesson-1"] + ([f"fixture-page-{index:02d}" for index in range(1, 26)] if browser_pages else [])
        assert data["course_id"] == "backend" and data["catalog_version"].startswith("sha256:") and data["lesson_ids"] == expected_lessons
        assert admin.get("/api/learning/analytics-catalog", params=params).status_code == 403
        source = folder / "courses/backend/backend/plans/chapter_01/block_01/01 - Как запускается программа.md"
        original = source.read_bytes()
        try:
            source.write_bytes(original + b"\nsynthetic digest mismatch\n")
            stale = student.get("/api/learning/analytics-catalog", params=params)
            assert stale.status_code == 200 and stale.json() == {"course_id": "backend", "catalog_version": None, "lesson_ids": []}
        finally:
            source.write_bytes(original)
        assert student.get("/api/learning/analytics-catalog", params=params).json() == data
    with psycopg.connect(analytics_db["dsn"], connect_timeout=3) as conn:
        rows = conn.execute("SELECT catalog_version FROM analytics.catalog_versions WHERE producer_namespace='core' AND course_id='backend'").fetchall()
    assert rows == [(data["catalog_version"],)]
    fixture["catalog_version"] = data["catalog_version"]
    replacement = folder / ("fixture-" + uuid4().hex + ".tmp")
    r.write_new(replacement, fixture)
    os.replace(replacement, folder / "fixture.json")


def hold_extra_catalog(python, core_env, folder, owner_token, fixture):
    """Publish the other registered course using the unchanged fixture guard."""
    env = dict(core_env, CROSS_SERVICE_OWNER_TOKEN=owner_token,
               PYMENTOR_ANALYTICS_CATALOG_FIXTURE_DIR=str(folder), PYMENTOR_ANALYTICS_CATALOG_URL=fixture["collector_url"],
               PYMENTOR_ANALYTICS_PRODUCER_ISSUER="control-issuer", PYMENTOR_ANALYTICS_PRODUCER_KID="p",
               PYMENTOR_ANALYTICS_PRODUCER_ID="synthetic", PYMENTOR_ANALYTICS_PRODUCER_KEY_FILE=str(folder / "producer.private.pem"))
    def save(value):
        replacement = folder / ("fixture-" + uuid4().hex + ".tmp")
        r.write_new(replacement, value)
        os.replace(replacement, folder / "fixture.json")
    save(dict(fixture, course_id="backend_extra"))
    try:
        r.run_child([str(python), "-B", str(CORE / "tools/analytics_catalog/publish.py"),
                     "--courses-root", str(folder / "courses"), "--course-id", "backend_extra"], cwd=CORE, env=env, timeout=30)
    finally:
        save(fixture)
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as student:
        login(student, fixture["users"]["student"])
        response = student.get("/api/learning/analytics-catalog", params={"course_id": "backend_extra"})
        if response.status_code != 200:
            raise AssertionError("extra course metadata unavailable")
        data = response.json()
        if data.get("course_id") != "backend_extra" or not (data.get("catalog_version") or "").startswith("sha256:") or data.get("lesson_ids") != ["extra-lesson-1"]:
            raise AssertionError("extra course catalog mismatch")
        return data["catalog_version"]


def hold_cross_course_baseline(fixture, analytics, extra_version):
    """Seed one subject in both courses, then independently assert its report scope."""
    from datetime import timedelta
    from .a010_checks import wait_ready
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(seconds=8)
    user = fixture["users"]["student"]
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as student, \
         httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as admin, \
         httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as leader, \
         httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as fresh, \
         httpx.Client(base_url=fixture["collector_url"], timeout=5, trust_env=False) as collector, \
         httpx.Client(base_url=fixture["query_url"], timeout=5, trust_env=False) as query:
        login(student, user); login(admin, fixture["users"]["admin"]); login(leader, fixture["users"]["leader"])
        login(fresh, fixture["browser_users"]["student"])
        for course, version, lesson, offset, section in (("backend", fixture["catalog_version"], "lesson-1", 0, "theory"),
                                                          ("backend_extra", extra_version, "extra-lesson-1", 4, "practice")):
            grant = student.post("/api/analytics/grants/collect", json={"course_id": course})
            assert grant.status_code == 200
            begin = start + timedelta(seconds=offset)
            event = {"schema_version": 2, "event_id": str(uuid4()), "type": "activity_segment", "course_id": course,
                     "catalog_version": version, "lesson_id": lesson, "section": section, "session_id": str(uuid4()),
                     "started_at": begin.isoformat(), "ended_at": (begin + timedelta(seconds=2)).isoformat(),
                     "last_action_at": begin.isoformat(), "visible": True, "focused": True, "active_ms": 2000, "payload": {}}
            opened = dict(event, event_id=str(uuid4()), type="course_opened", lesson_id=None, section=None,
                          ended_at=begin.isoformat(), active_ms=0)
            response = collector.post("/api/analytics/events/batch", headers={"Authorization": "Bearer " + grant.json()["grant"]}, json={"events": [opened, event]})
            assert response.status_code == 200 and [item["status"] for item in response.json()["items"]] == ["accepted", "accepted"]
        wait_ready(analytics)
        selected = admin.post("/api/analytics/grants/report", json={"user_id": user["id"]})
        scoped = leader.post("/api/analytics/grants/report", json={"course_id": "backend"})
        assert selected.status_code == scoped.status_code == 200
        assert selected.json()["target_subject_ref"] == user["subject_ref"]
        assert leader.post("/api/analytics/grants/report", json={"course_id": "backend_extra"}).status_code == 403
        params = {"from_time": start.isoformat(), "to_time": now.isoformat(), "subject_ref": user["subject_ref"]}
        expected = ((None, 4000, 2000, 2000), ("backend", 2000, 2000, 0), ("backend_extra", 2000, 0, 2000))
        for course, active, theory, practice in expected:
            p = dict(params, **({"course_id": course} if course else {}))
            response = query.get("/api/analytics/reports/activity", params=p,
                                 headers={"Authorization": "Bearer " + selected.json()["grant"]})
            assert response.status_code == 200 and response.json()["totals"] == {
                "unique_visitors": 1, "active_ms": active, "theory_ms": theory, "practice_ms": practice}
        denied = query.get("/api/analytics/reports/activity", params=dict(params, course_id="backend_extra"),
                           headers={"Authorization": "Bearer " + scoped.json()["grant"]})
        assert denied.status_code == 403
        fresh_grant = fresh.post("/api/analytics/grants/report", json={})
        assert fresh_grant.status_code == 200
        window = {"from_time": (now - timedelta(hours=1)).isoformat(), "to_time": now.isoformat()}
        baseline = {}
        for label, course, grant, visitors, active in (
            ("global", None, selected, 3, 149000),
            ("backend", "backend", selected, 3, 147000),
            ("backend_extra", "backend_extra", selected, 1, 2000),
            ("leader_backend", "backend", scoped, 1, 92000),
            ("fresh_user", None, fresh_grant, 0, 0),
        ):
            response = query.get("/api/analytics/reports/activity", params=dict(window, **({"course_id": course} if course else {})),
                                 headers={"Authorization": "Bearer " + grant.json()["grant"]})
            assert response.status_code == 200
            totals = response.json()["totals"]
            assert totals["unique_visitors"] == visitors and totals["active_ms"] == active
            baseline[label] = totals
        print("HOLD_BASELINE " + json.dumps({"interval": window, "totals": baseline}, sort_keys=True), flush=True)
    print("PASS hold-only cross-course selected-subject distinct baseline and leader scope", flush=True)


def proof_vector_state(required, proof):
    """Classify one Core roster entry without exposing its lifecycle reference."""
    if proof is None:
        return "missing_verified"
    cut, epoch, current_epoch, newer = proof
    if cut != required or epoch != current_epoch or newer:
        return "stale_verified"
    return "current"


def observe_hold_overview(fixture, core_db, analytics_db, *, expect_current, expected_ids=(1, 2, 3, 4, 7)):
    """Observe the real global/course routes and Core-to-Analytics proof vector."""
    with psycopg.connect(core_db["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as conn:
        roster = conn.execute("""SELECT u.id,a.lifecycle_id,COALESCE(r.revision,0)
            FROM users u JOIN account_lifecycles a ON a.user_id=u.id
            LEFT JOIN analytics_transfer_revisions r ON r.subject_ref=a.lifecycle_id ORDER BY u.id""").fetchall()
        assert [row[0] for row in roster] == list(expected_ids)
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == len(roster)
    vector = []
    with psycopg.connect(analytics_db["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as conn:
        for uid, ref, required in roster:
            proof = conn.execute("""SELECT p.cut_revision,p.subject_epoch,COALESCE(f.epoch,0),
                EXISTS(SELECT 1 FROM analytics.core_transfer_projection q WHERE q.producer_namespace='core'
                    AND q.subject_ref=p.subject_ref AND q.revision>p.cut_revision)
                FROM analytics.core_snapshot_proofs p LEFT JOIN analytics.subject_fences f ON f.subject_ref=p.subject_ref
                WHERE p.producer_namespace='core' AND p.subject_ref=%s AND p.status='verified'""", (ref,)).fetchone()
            vector.append({"actor_id": uid, "required_revision": required,
                           "verified_cut": proof[0] if proof else None, "state": proof_vector_state(required, proof)})
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as admin:
        login(admin, fixture["users"]["admin"])
        whole = admin.get("/api/admin-platform/overview")
        course = admin.get("/api/admin-platform/overview", params={"course_id": "backend"})
        print("HOLD_OVERVIEW " + json.dumps({"phase": "after_refresh" if expect_current else "before_refresh",
              "global_status": whole.status_code, "course_status": course.status_code, "proof_vector": vector}, sort_keys=True), flush=True)
        assert course.status_code == 200 and course.headers.get("x-analytics-source") == "core"
        if expect_current:
            assert all(item["state"] == "current" for item in vector)
            assert whole.status_code == 200 and whole.headers.get("x-analytics-coverage") == "complete"
            assert whole.headers.get("x-analytics-source") == "mixed" and whole.headers.get("x-analytics-coverage-vector-sha256")
        else:
            assert next(item for item in vector if item["actor_id"] == 3)["state"] != "current"
            assert all(item["state"] == "current" for item in vector if item["actor_id"] != 3)
            assert whole.status_code == 503


def cli(python, env, target, operation, *, stream=None):
    args = [str(python), "-B", str(CORE / "tools/analytics_transfer/run.py"), operation,
            "--expected-db", target["name"]]
    if stream is not None:
        args += ["--stream", stream, "--limit", "100"]
    r.run_child(args, cwd=CORE, env=env, timeout=20)


def cutover_cli(python, env, target, operation, *flags):
    args = [str(python), "-B", str(CORE / "tools/analytics_transfer/run.py"), operation,
            "--expected-db", target["name"], *flags]
    r.run_child(args, cwd=CORE, env=env, timeout=20)


def prepare_cutover(python, env, target):
    cutover_cli(python, env, target, "prepare-init", "--writers-stopped")
    cutover_cli(python, env, target, "prepare-page", "--limit", "100")
    cutover_cli(python, env, target, "prepare-seal")
    cutover_cli(python, env, target, "begin-outbox", "--writers-stopped", "--coverage-proved")
    with psycopg.connect(target["dsn"], connect_timeout=3) as conn:
        assert conn.execute("SELECT mode FROM analytics_transfer_write_gate WHERE singleton=1").fetchone() == ("outbox_only",)
        assert conn.execute("SELECT sealed FROM analytics_transfer_activity_preparation WHERE singleton=1").fetchone() == (True,)


def assert_outbox_only_visits(fixture, core_db):
    user = fixture["users"]["student"]
    with psycopg.connect(core_db["dsn"], connect_timeout=3) as conn:
        before = conn.execute("SELECT activity_date,visited_at FROM user_activity_days WHERE user_id=%s ORDER BY activity_date", (user["id"],)).fetchall()
    visited = {day.isoformat() for day, _ in before}
    replay_day = next(day for day in ("2026-11-18", "2026-11-12") if day not in visited)
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as client:
        login(client, user)
        for day in ("2026-11-13", replay_day):
            response = client.post("/api/user/activity/visit", json={"activity_date": day},
                                   headers={"X-Synthetic-No-Legacy-SQL": "1"})
            assert response.status_code == 200 and response.json()["activity_date"] == day
    with psycopg.connect(core_db["dsn"], connect_timeout=3) as conn:
        after = conn.execute("SELECT activity_date,visited_at FROM user_activity_days WHERE user_id=%s ORDER BY activity_date", (user["id"],)).fetchall()
        dirty = conn.execute("SELECT count(*) FROM analytics_transfer_activity_state WHERE subject_ref=%s AND needs_core_replay", (user["subject_ref"],)).fetchone()[0]
    assert before == after and dirty == 2
    return replay_day


def replay_mirror(python, env, target, fixture, replay_day):
    cutover_cli(python, env, target, "begin-replay", "--writers-stopped")
    for _ in range(20):
        cutover_cli(python, env, target, "replay-page", "--writers-stopped", "--limit", "100")
        with psycopg.connect(target["dsn"], connect_timeout=3) as conn:
            dirty = conn.execute("SELECT count(*) FROM analytics_transfer_activity_state WHERE needs_core_replay").fetchone()[0]
        if dirty == 0:
            break
    else:
        raise TimeoutError("bounded activity replay did not drain")
    cutover_cli(python, env, target, "finish-replay", "--writers-stopped")
    with psycopg.connect(target["dsn"], connect_timeout=3) as conn:
        assert conn.execute("SELECT mode FROM analytics_transfer_write_gate WHERE singleton=1").fetchone() == ("mirror",)
        rows = conn.execute("SELECT activity_date::text,visited_at FROM user_activity_days WHERE user_id=%s", (fixture["users"]["student"]["id"],)).fetchall()
    dates = dict(rows)
    assert dates["2026-11-11"] is None and dates["2026-11-13"] is not None and dates[replay_day] is not None


def assert_legacy_rollback_read(fixture, core_db, replay_day):
    """Verify the restarted legacy reader sees the replayed Core rows over HTTP."""
    user = fixture["users"]["student"]
    with psycopg.connect(core_db["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as conn:
        assert conn.execute("SELECT mode FROM analytics_transfer_write_gate WHERE singleton=1").fetchone() == ("mirror",)
        assert conn.execute("SELECT count(*) FROM analytics_transfer_activity_state WHERE needs_core_replay").fetchone() == (0,)
        rows = conn.execute("SELECT activity_date::text, visited_at FROM user_activity_days WHERE user_id=%s ORDER BY activity_date", (user["id"],)).fetchall()
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as client:
        login(client, user)
        params = {"year": 2026, "month": 11}
        response = client.get("/api/user/activity", params=params)
        assert response.status_code == 200 and response.headers.get("x-analytics-source") is None
        assert response.json()["days"] == [day for day, _ in rows if day.startswith("2026-11-")]
        assert replay_day in response.json()["days"]
        # Negative control proves that this response actually uses Core SQL.
        assert client.get("/api/user/activity", params=params, headers={"X-Synthetic-No-Legacy-SQL": "1"}).status_code == 500


def assert_returned_analytics_read(fixture):
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as client:
        login(client, fixture["users"]["student"])
        response = client.get("/api/user/activity", params={"year": 2026, "month": 11},
                              headers={"X-Synthetic-No-Legacy-SQL": "1"})
        assert response.status_code == 200 and response.headers.get("x-analytics-source") == "mixed"
        assert response.headers.get("x-analytics-coverage") == "complete"


def seed_browser_student_activity(fixture, core_db, analytics_db, relay, *, user=None):
    user = user or fixture["browser_users"]["student"]
    with httpx.Client(base_url=fixture["core_url"], timeout=4, trust_env=False) as client:
        login(client, user)
        response = client.post("/api/user/activity/visit", json={"activity_date": "2026-11-12"})
        assert response.status_code == 200
    relay()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with psycopg.connect(analytics_db["dsn"], connect_timeout=3) as conn:
            count = conn.execute("SELECT count(*) FROM analytics.core_transfer_projection WHERE subject_ref=%s", (user["subject_ref"],)).fetchone()[0]
        if count:
            return
        time.sleep(.1)
    raise TimeoutError("browser student source projection did not arrive")


def verify_proof_roles(target):
    name = target["name"]
    with psycopg.connect(target["dsn"], connect_timeout=3) as conn:
        result = conn.execute("""SELECT
           has_column_privilege(%s,'analytics.core_snapshot_proofs','proof','INSERT'),
           has_column_privilege(%s,'analytics.core_snapshot_proofs','status','INSERT'),
           has_column_privilege(%s,'analytics.core_snapshot_proofs','status','UPDATE'),
           has_table_privilege(%s,'analytics.core_snapshot_proofs','DELETE'),
           has_column_privilege(%s,'analytics.core_snapshot_proofs','subject_ref','SELECT'),
           has_column_privilege(%s,'analytics.core_snapshot_proofs','proof','SELECT'),
           has_function_privilege(%s,'analytics.invalidate_core_snapshot_proofs(text,uuid,bigint)','EXECUTE')""",
           (name+"_collector", name+"_collector", name+"_processor", name+"_privacy",
            name+"_transfer_status", name+"_transfer_status", name+"_collector")).fetchone()
    assert result == (True, False, True, True, True, False, False)


def bigint_roundtrip(core_db):
    # Roll back the isolated probe rows after an actual PostgreSQL roundtrip.
    ref, event_id, proof_id = str(uuid4()), str(uuid4()), str(uuid4())
    stream, value = "ci-bigint", (1 << 31) + 1
    with psycopg.connect(core_db["dsn"], connect_timeout=3) as conn:
        conn.execute("INSERT INTO analytics_transfer_revisions(subject_ref,revision) VALUES (%s,%s)", (ref, value))
        conn.execute("INSERT INTO analytics_transfer_checkpoints(stream,high_watermark,cursor) VALUES (%s,%s,%s)", (stream, value, value))
        conn.execute("INSERT INTO analytics_transfer_snapshot_barriers(stream,high_watermark,sealed_at) VALUES (%s,%s,now())", (stream, value))
        conn.execute("INSERT INTO analytics_transfer_activity_preparation(singleton,high_watermark,cursor,sealed) VALUES (1,%s,%s,false)", (value, value))
        conn.execute("""INSERT INTO analytics_transfer_outbox(event_id,subject_ref,kind,entity_key,revision,payload,fingerprint,
            created_at,status,attempts,uncertain,next_attempt_at) VALUES (%s,%s,'legacy_activity_day',%s,%s,'{}',%s,now(),'delivered',0,false,now())""",
                     (event_id, ref, "a"*64, value, "b"*64))
        conn.execute("""INSERT INTO analytics_transfer_snapshot_proofs(proof_id,subject_ref,cut_revision,payload,fingerprint,
            status,accepted,uncertain,attempts,next_attempt_at) VALUES (%s,%s,%s,'{}',%s,'verified',true,false,0,now())""",
                     (proof_id, ref, value, "b"*64))
        observed = (conn.execute("SELECT revision FROM analytics_transfer_revisions WHERE subject_ref=%s", (ref,)).fetchone()[0],
                    *conn.execute("SELECT high_watermark,cursor FROM analytics_transfer_checkpoints WHERE stream=%s", (stream,)).fetchone(),
                    conn.execute("SELECT high_watermark FROM analytics_transfer_snapshot_barriers WHERE stream=%s", (stream,)).fetchone()[0],
                    *conn.execute("SELECT high_watermark,cursor FROM analytics_transfer_activity_preparation WHERE singleton=1").fetchone(),
                    conn.execute("SELECT revision FROM analytics_transfer_outbox WHERE event_id=%s", (event_id,)).fetchone()[0],
                    conn.execute("SELECT cut_revision FROM analytics_transfer_snapshot_proofs WHERE proof_id=%s", (proof_id,)).fetchone()[0])
        assert observed == (value,) * 8
        conn.rollback()


def proof_status(collector_url, folder, proof_id, fingerprint):
    with httpx.Client(base_url=collector_url, timeout=4, trust_env=False) as client:
        response = client.get("/internal/core-transfer/v1/proofs/" + proof_id, params={"fingerprint": fingerprint},
                              headers={"Authorization": "Bearer " + producer_token(folder)})
        assert response.status_code == 200
        return response.json()


def assert_received_pending(target, analytics, fixture, folder):
    with psycopg.connect(target["dsn"], connect_timeout=3) as conn:
        row = conn.execute("SELECT proof_id,fingerprint FROM analytics_transfer_snapshot_proofs WHERE subject_ref=%s", (fixture["users"]["student"]["subject_ref"],)).fetchone()
    assert row is not None
    assert proof_status(fixture["collector_url"], folder, row[0], row[1]) == {"status": "accepted", "coverage": "pending"}
    with psycopg.connect(analytics["dsn"], connect_timeout=3) as conn:
        assert conn.execute("SELECT count(*) FROM analytics.core_snapshot_proofs WHERE status='pending'").fetchone()[0] == 6
    return row


def assert_received_verified(fixture, folder, proof, analytics):
    assert proof_status(fixture["collector_url"], folder, proof[0], proof[1]) == {"status": "accepted", "coverage": "verified"}
    subject = fixture["users"]["student"]["subject_ref"]
    with psycopg.connect(analytics["dsn"], connect_timeout=3) as conn:
        counts = conn.execute("""SELECT count(*),count(*) FILTER (WHERE NOT EXISTS (
            SELECT 1 FROM analytics.processing_receipts r WHERE r.producer_namespace=e.producer_namespace
              AND r.event_id=e.event_id AND r.subject_ref=e.subject_ref AND r.consumer='core.transfer.projection'))
            FROM analytics.core_transfer_entities e WHERE e.subject_ref=%s""", (subject,)).fetchone()
    assert counts[0] > 0 and counts[1] == 0


def assert_complete_query(fixture, core_db):
    with httpx.Client(base_url=fixture["core_url"], timeout=4, trust_env=False) as core, \
         httpx.Client(base_url=fixture["query_url"], timeout=4, trust_env=False) as query:
        user = fixture["users"]["student"]
        login(core, user)
        grant = core.post("/api/analytics/grants/report", json={})
        assert grant.status_code == 200
        result = query.get("/internal/analytics/v1/legacy/calendar", params={"year": 2026, "month": 11},
                           headers={"Authorization": "Bearer " + grant.json()["grant"]})
        assert result.status_code == 200
        body = result.json()
        with psycopg.connect(core_db["dsn"], connect_timeout=3) as conn:
            revision = conn.execute("SELECT COALESCE((SELECT revision FROM analytics_transfer_revisions WHERE subject_ref=%s),0)", (user["subject_ref"],)).fetchone()[0]
        assert body["coverage"] == "complete" and body["coverage_through_revision"] == revision
        assert body["source_hwm"] is None and body["freshness"] == "unknown"


def assert_progress_and_history(fixture, core_db, analytics_db):
    user = fixture["users"]["student"]
    subject = user["subject_ref"]
    with psycopg.connect(core_db["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as source:
        rows = source.execute("""SELECT revision,payload::jsonb->'payload'->>'task_revision',status
            FROM analytics_transfer_outbox WHERE subject_ref=%s AND kind='task_progress' ORDER BY revision""", (subject,)).fetchall()
        assert len(rows) == 2 and rows[0] == (0, "r1", "delivered") and rows[1][0] > 0 and rows[1][1:] == ("r2", "delivered")
        assert source.execute("SELECT visited_at IS NOT NULL FROM user_activity_days WHERE user_id=%s AND activity_date='2026-11-16'",
                              (user["id"],)).fetchone() == (True,)
    with psycopg.connect(analytics_db["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as target:
        assert target.execute("""SELECT p.revision, f.accepted_event->'payload'->>'task_revision'
            FROM analytics.core_transfer_projection p JOIN analytics.activity_facts f USING (producer_namespace,event_id,subject_ref)
            WHERE p.subject_ref=%s AND f.accepted_event->>'kind'='task_progress'""", (subject,)).fetchall() == [(rows[1][0], "r2")]
        assert target.execute("""SELECT f.accepted_event->'payload'->>'visited_at'
            FROM analytics.activity_facts f JOIN analytics.core_transfer_entities e USING (producer_namespace,event_id,subject_ref)
            WHERE f.subject_ref=%s AND e.revision=0 AND f.accepted_event->'payload'->>'activity_date'='2026-11-16'""", (subject,)).fetchall() == [(None,)]
        assert target.execute("""SELECT proof->>'scope' FROM analytics.core_snapshot_proofs
            WHERE subject_ref=%s AND status='verified'""", (subject,)).fetchall() == [("subject_all_v2",)]
    with httpx.Client(base_url=fixture["core_url"], timeout=4, trust_env=False) as core, \
         httpx.Client(base_url=fixture["query_url"], timeout=4, trust_env=False) as query:
        login(core, user)
        grant = core.post("/api/analytics/grants/report", json={})
        assert grant.status_code == 200
        response = query.get("/internal/analytics/v1/legacy/history",
                             params={"start": "2026-11-11", "end": "2026-11-16"},
                             headers={"Authorization": "Bearer " + grant.json()["grant"]})
        assert response.status_code == 200
        assert response.json()["days"] == [{"date": day, "theory_ms": 3600000, "practice_ms": 3600000,
                                           "provenance": "core_legacy_conditional"}
                                          for day in ("2026-11-11", "2026-11-13", "2026-11-15", "2026-11-16")]


def assert_live_sql_detector(fixture):
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as client:
        login(client, fixture["users"]["student"])
        params = {"year": 2026, "month": 11}
        assert client.get("/api/user/activity", params=params).status_code == 200
        # In the baseline mode the synthetic guard must actually interrupt the
        # old query; this negative control makes later 200s meaningful.
        response = client.get("/api/user/activity", params=params, headers={"X-Synthetic-No-Legacy-SQL": "1"})
        assert response.status_code == 500


def assert_guarded_core_reads(fixture, *, browser=False):
    headers = {"X-Synthetic-No-Legacy-SQL": "1"}
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as student, \
         httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as admin:
        login(student, fixture["users"]["student"]); login(admin, fixture["users"]["admin"])
        for path, params in (("/api/user/activity", {"year": 2026, "month": 11}), ("/api/user/activity/summary", {})):
            response = student.get(path, params=params, headers=headers)
            assert response.status_code == 200 and response.headers.get("x-analytics-source") == ("mixed" if browser else "analytics")
            assert response.headers.get("x-analytics-coverage") == "complete"
            if browser:
                assert response.headers.get("x-analytics-browser-quality") == "unknown"
        for path in ("/api/admin/overview", "/api/admin-platform/overview"):
            response = admin.get(path, headers=headers)
            assert response.status_code == 200 and response.headers.get("x-analytics-coverage") == "complete"
            assert response.headers.get("x-analytics-source") == "mixed"


def global_completion_views(fixture, *, switched):
    headers = {"X-Synthetic-No-Completion-SQL": "1"}
    fields = ("daily_lessons_completed", "lessons_completed_7d", "most_active_students")
    views = []
    with httpx.Client(base_url=fixture["core_url"], timeout=6, trust_env=False) as admin:
        login(admin, fixture["users"]["admin"])
        for path in ("/api/admin/overview", "/api/admin-platform/overview"):
            if not switched:
                assert admin.get(path, headers=headers).status_code == 500
            response = admin.get(path, headers=headers if switched else {})
            assert response.status_code == 200
            if switched:
                assert response.headers.get("x-analytics-coverage") == "complete"
            data = response.json()
            dashboard = data.get("dashboard", data)
            views.append({name: dashboard[name] for name in fields})
    return views


def assert_learner_course_reads(fixture):
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    window = {"year": today.year, "month": today.month}
    forbid_old_sql = {"X-Synthetic-No-Legacy-SQL": "1", "X-Synthetic-No-Completion-SQL": "1"}
    with httpx.Client(base_url=fixture["core_url"], timeout=10, trust_env=False) as student:
        login(student, fixture["users"]["student"])
        history = student.get("/api/user/activity/history", params={"start": "2026-11-11", "end": "2026-11-16"}, headers=forbid_old_sql)
        assert history.status_code == 200 and len(history.json()["days"]) == 4
        assert all(item["provenance"] == "core_legacy_conditional" and item["theory_ms"] == item["practice_ms"] == 3600000
                   for item in history.json()["days"])
        overlap = student.get("/api/user/activity/history", params={"start": today.isoformat(), "end": today.isoformat()},
                              headers=forbid_old_sql)
        assert overlap.status_code == 200 and [item["date"] for item in overlap.json()["days"]] == [today.isoformat()]
        measured = student.get("/api/user/activity/measured", params=window, headers=forbid_old_sql)
        assert measured.status_code == 200
        measured = measured.json()
        assert today.isoformat() in measured["days"] and "2026-11-11" not in measured["days"]
        assert measured["current_streak"] >= 1 and measured["streak_is_lower_bound"] and measured["coverage"] == "unknown"
        slots = student.get("/api/courses/backend/booking/slots", params={**window, "owner_id": fixture["users"]["leader"]["id"]})
        assert slots.status_code == 200 and slots.json()["quota"]["used"] == 1
        assert student.get("/api/admin/courses/backend/overview", params=window).status_code == 403
    course = "/api/admin/courses/backend/overview"
    with httpx.Client(base_url=fixture["core_url"], timeout=10, trust_env=False) as leader, \
         httpx.Client(base_url=fixture["core_url"], timeout=10, trust_env=False) as admin:
        login(leader, fixture["users"]["leader"])
        login(admin, fixture["users"]["admin"])
        # Course trends still use canonical Core lesson and task receipts.
        owner = leader.get(course, params=window, headers={"X-Synthetic-No-Legacy-SQL": "1"})
        privileged = admin.get("/api/admin-platform/overview", params={"course_id": "backend"}, headers=forbid_old_sql)
        assert owner.status_code == privileged.status_code == 200
        assert owner.json()["completed_lessons"] >= 1 and owner.headers.get("x-analytics-coverage") == "complete"
        assert owner.json()["dashboard"]["lessons_completed_7d"] == privileged.json()["dashboard"]["lessons_completed_7d"] >= 1
        assert leader.get("/api/admin/courses/backend_extra/overview", params=window).status_code == 403
        assert leader.get("/api/user/activity/measured", params=window).status_code == 403


PROFILE_ACTIVITY = ("activity_days_count", "activity_days", "total_activity_days", "last_activity_date", "activity_summary")


def baseline_admin_profile(fixture):
    """Capture the same target/window before the Core read-mode switch."""
    user = fixture["users"]["student"]
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as admin:
        login(admin, fixture["users"]["admin"])
        response = admin.get(f"/api/admin/users/{user['id']}/profile", params={"year": 2026, "month": 11})
        assert response.status_code == 200
        profile = response.json()["profile"]
        return {key: profile[key] for key in PROFILE_ACTIVITY}


def mixed_profile_baseline(fixture, core_db, analytics_db, legacy, *, dates_only=False):
    """Independent Core days plus accepted B2 days, before outbox-only mode."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Moscow")
    user = fixture["users"]["student"]
    with psycopg.connect(core_db["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as conn:
        visits = dict(conn.execute("SELECT activity_date,visited_at FROM user_activity_days WHERE user_id=%s", (user["id"],)).fetchall())
        imported_dates = {row[0] for row in conn.execute("""SELECT (payload::jsonb->'payload'->>'activity_date')::date
            FROM analytics_transfer_outbox WHERE subject_ref=%s AND kind='legacy_activity_day'
            GROUP BY 1 HAVING max(revision)=0""", (user["subject_ref"],))} if dates_only else set()
        completions = [row[0].astimezone(tz).date() for row in conn.execute(
            "SELECT completed_at FROM user_lesson_progress WHERE user_id=%s AND completed_at IS NOT NULL", (user["id"],)).fetchall()]
    with psycopg.connect(analytics_db["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as conn:
        intervals = conn.execute("""SELECT accepted_event->'event'->>'started_at',accepted_event->'event'->>'ended_at'
            FROM analytics.activity_facts WHERE subject_ref=%s AND source_kind='browser_v2'
            AND accepted_event->'event'->>'type'='activity_segment'""", (user["subject_ref"],)).fetchall()
    assert len(intervals) == 2  # the six-scenario B2 test for this subject
    browser_days = set()
    for start, end in intervals:
        first = datetime.fromisoformat(start).astimezone(tz).date()
        last = (datetime.fromisoformat(end) - timedelta(microseconds=1)).astimezone(tz).date()
        while first <= last:
            browser_days.add(first); first += timedelta(days=1)
    legacy_days = sorted(day.isoformat() for day in visits if (day.year, day.month) == (2026, 11))
    assert legacy["activity_days"] == legacy_days and legacy["activity_days_count"] == len(legacy_days)
    assert legacy["total_activity_days"] == len(visits)
    expected_days = set(visits) | browser_days
    selected = sorted(day.isoformat() for day in expected_days if (day.year, day.month) == (2026, 11))
    today = datetime.now(timezone.utc).astimezone(tz).date()
    past = {day for day in expected_days if day <= today}
    cursor = today if today in past else today - timedelta(days=1)
    streak = 0
    while cursor in past:
        streak += 1; cursor -= timedelta(days=1)
    week = [today - timedelta(days=6-i) for i in range(7)]
    expected_summary = {"current_streak": streak, "visited_today": today in past, "total_active_days": len(past),
        "weekly_activity": [{"activity_date": day.isoformat(), "active": day in past} for day in week],
        "weekly_lessons_completed": [{"activity_date": day.isoformat(), "lessons_completed": completions.count(day)} for day in week]}
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as admin, \
         httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as student:
        login(admin, fixture["users"]["admin"]); login(student, user)
        headers = {"X-Synthetic-No-Legacy-SQL": "1"}
        response = admin.get(f"/api/admin/users/{user['id']}/profile", params={"year": 2026, "month": 11}, headers=headers)
        assert response.status_code == 200 and response.headers.get("x-analytics-coverage") == "complete"
        profile = {key: response.json()["profile"][key] for key in PROFILE_ACTIVITY}
        assert profile == {"activity_days_count": len(selected), "activity_days": selected,
                           "total_activity_days": len(expected_days), "last_activity_date": max(expected_days).isoformat(),
                           "activity_summary": expected_summary}
        calendar = student.get("/api/user/activity", params={"year": 2026, "month": 11}, headers=headers)
        assert calendar.status_code == 200
        rows = calendar.json()["visits"]
        assert [row["activity_date"] for row in rows] == selected
        for row in rows:
            day = date.fromisoformat(row["activity_date"]); expected_time = visits.get(day)
            if day in imported_dates:
                expected_time = None
            actual_time = datetime.fromisoformat(row["visited_at"].replace("Z", "+00:00")) if row["visited_at"] else None
            assert actual_time == expected_time
        return profile


def quota_snapshot(core_db, user_id):
    with psycopg.connect(core_db["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as conn:
        return conn.execute("""SELECT course_id,year,month,granted_slots FROM booking_quotas
            WHERE user_id=%s ORDER BY year,month,course_id NULLS FIRST""", (user_id,)).fetchall()


def assert_profile_offload(fixture, core_db, expected):
    user = fixture["users"]["student"]
    headers = {"X-Synthetic-No-Legacy-SQL": "1"}
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as admin:
        login(admin, fixture["users"]["admin"])
        response = admin.get(f"/api/admin/users/{user['id']}/profile", params={"year": 2026, "month": 11}, headers=headers)
        assert response.status_code == 200 and response.headers.get("x-analytics-coverage") == "complete"
        assert {key: response.json()["profile"][key] for key in PROFILE_ACTIVITY} == expected
        before = quota_snapshot(core_db, user["id"])
        response = admin.post(f"/api/admin/users/{user['id']}/quota", json={"year": 2026, "month": 11, "add": 1}, headers=headers)
        assert response.status_code == 200 and response.headers.get("x-analytics-coverage") == "complete"
        assert {key: response.json()["profile"][key] for key in PROFILE_ACTIVITY} == expected
        after = quota_snapshot(core_db, user["id"])
        slots = lambda rows: sum(row[3] for row in rows if row[1:3] == (2026, 11) and row[0] in (None, "backend"))
        assert slots(after) == slots(before) + 1


def assert_profile_query_outage(fixture, core_db):
    """Call only while this fixture's Query is confirmed stopped."""
    user = fixture["users"]["student"]
    before = quota_snapshot(core_db, user["id"])
    headers = {"X-Synthetic-No-Legacy-SQL": "1"}
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as admin:
        login(admin, fixture["users"]["admin"])
        response = admin.get(f"/api/admin/users/{user['id']}/profile", params={"year": 2026, "month": 11}, headers=headers)
        assert response.status_code == 503
        response = admin.post(f"/api/admin/users/{user['id']}/quota", json={"year": 2026, "month": 11, "add": 1}, headers=headers)
        assert response.status_code == 503
    assert quota_snapshot(core_db, user["id"]) == before


def assert_profile_reused_id_race(fixture, core_db, folder):
    """Proxy commits a new lifecycle after buffering the signed Query response."""
    uid = fixture["users"]["other"]["id"]
    assert quota_snapshot(core_db, uid) == []
    headers = {"X-Synthetic-No-Legacy-SQL": "1"}
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as admin:
        login(admin, fixture["users"]["admin"])
        start = time.monotonic()
        response = admin.post(f"/api/admin/users/{uid}/quota", json={"year": 2026, "month": 11, "add": 1}, headers=headers)
        elapsed = time.monotonic() - start
    marker = json.loads((folder / "profile-race.json").read_text(encoding="utf-8"))
    assert response.status_code == 503 and elapsed < .9
    assert marker["old_ref"] != marker["new_ref"]
    with psycopg.connect(core_db["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as conn:
        account = conn.execute("SELECT u.username,a.lifecycle_id FROM users u JOIN account_lifecycles a ON a.user_id=u.id WHERE u.id=%s", (uid,)).fetchone()
    assert account == ("race-replacement", marker["new_ref"])
    assert quota_snapshot(core_db, uid) == []


def _self_token(folder, subject):
    now = int(time.time())
    return jwt.encode({"grant_version": 1, "iss": "grant-issuer", "aud": "analytics", "sub": str(subject),
                       "iat": now, "nbf": now, "exp": now + 60, "role": "user", "scope": "report:read",
                       "course_ids": ["backend"], "subject_refs": [], "global_read": False},
                      (folder / "grant.private.pem").read_bytes(), algorithm="EdDSA", headers={"kid": "g"})


def _post_event(client, folder, wire, event):
    response = client.post("/internal/core-transfer/v1/events", headers={"Authorization": "Bearer " + producer_token(folder)},
                           json={"event": event, "fingerprint": wire.fingerprint(event)})
    assert response.status_code == 200 and response.json() == {"status": "accepted"}


def independent_verified_proofs(fixture, analytics, folder):
    sys.path.insert(0, str(CORE))
    try:
        from pymentor_core.modules.analytics_delivery import contracts as wire, snapshot_contract as proof_wire
    finally:
        sys.path.remove(str(CORE))
    subjects = [uuid4(), uuid4()]
    proofs = []
    with httpx.Client(base_url=fixture["collector_url"], timeout=4, trust_env=False) as collector:
        for subject in subjects:
            event = wire.activity(subject, date(2026, 11, 10), None, revision=0)
            _post_event(collector, folder, wire, event)
            proof = proof_wire.make_proof(subject, 0, [event], datetime.now(timezone.utc))
            response = collector.post("/internal/core-transfer/v1/proofs", headers={"Authorization": "Bearer " + producer_token(folder)},
                                      json={"proof": proof, "fingerprint": proof_wire.proof_fingerprint(proof)})
            assert response.status_code == 200 and response.json() == {"status": "accepted"}
            proofs.append(proof)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with psycopg.connect(analytics["dsn"], connect_timeout=3) as conn:
            count = conn.execute("SELECT count(*) FROM analytics.core_snapshot_proofs WHERE subject_ref=ANY(%s::uuid[]) AND status='verified'", ([str(x) for x in subjects],)).fetchone()[0]
        if count == 2:
            return wire, subjects, proofs
        time.sleep(.1)
    raise TimeoutError("independent synthetic proofs not verified")


def assert_late_invalidation(fixture, analytics, folder, wire, subjects):
    headers = {"Authorization": "Bearer " + producer_token(folder)}
    with httpx.Client(base_url=fixture["collector_url"], timeout=4, trust_env=False) as collector:
        _post_event(collector, folder, wire, wire.activity(subjects[0], date(2026, 11, 11), None, revision=0))
        _post_event(collector, folder, wire, wire.activity(subjects[1], date(2026, 11, 11), None, revision=1))
    with psycopg.connect(analytics["dsn"], connect_timeout=3) as conn:
        statuses = {str(ref): status for ref, status in conn.execute("SELECT subject_ref,status FROM analytics.core_snapshot_proofs WHERE subject_ref=ANY(%s::uuid[])", ([str(x) for x in subjects],)).fetchall()}
    assert statuses == {str(subjects[0]): "pending", str(subjects[1]): "verified"}
    with httpx.Client(base_url=fixture["query_url"], timeout=4, trust_env=False) as query:
        response = query.get("/internal/analytics/v1/legacy/calendar", params={"year": 2026, "month": 11},
                             headers={"Authorization": "Bearer " + _self_token(folder, subjects[0])})
        assert response.status_code == 503


def assert_above_cut_closed(fixture, analytics, folder, subject):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with psycopg.connect(analytics["dsn"], connect_timeout=3) as conn:
            revision = conn.execute("SELECT max(revision) FROM analytics.core_transfer_projection WHERE subject_ref=%s", (str(subject),)).fetchone()[0]
            status = conn.execute("SELECT status FROM analytics.core_snapshot_proofs WHERE subject_ref=%s", (str(subject),)).fetchone()[0]
        if revision == 1:
            assert status == "verified"
            break
        time.sleep(.1)
    else:
        raise TimeoutError("above-cut fact was not projected")
    with httpx.Client(base_url=fixture["query_url"], timeout=4, trust_env=False) as query:
        response = query.get("/internal/analytics/v1/legacy/calendar", params={"year": 2026, "month": 11},
                             headers={"Authorization": "Bearer " + _self_token(folder, subject)})
        assert response.status_code == 503


def delete_subject_with_proof(python, relay_env, fixture, core_db, analytics_db, folder, *, browser_payload=None, user=None, day_projection=False):
    user = user or fixture["browser_users"]["student"]
    with psycopg.connect(core_db["dsn"], connect_timeout=3) as conn:
        old = conn.execute("SELECT payload,fingerprint FROM analytics_transfer_outbox WHERE subject_ref=%s AND kind='legacy_activity_day' AND status='delivered'", (user["subject_ref"],)).fetchone()
    if browser_payload is None:
        assert old is not None
    event = json.loads(old[0]) if old is not None else None
    with httpx.Client(base_url=fixture["core_url"], timeout=5, trust_env=False) as admin:
        login(admin, fixture["users"]["admin"])
        path = f"/api/admin/users/{user['id']}"
        headers = {"X-Account-Ref": user["subject_ref"]}
        first = admin.delete(path, headers=headers)
        assert first.status_code == 202 and first.json() == {"status": "pending"}
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            cli(python, relay_env, core_db, "relay-once")
            with psycopg.connect(core_db["dsn"], connect_timeout=3) as conn:
                deletion = conn.execute("SELECT event_id,revision,status FROM analytics_transfer_outbox WHERE subject_ref=%s AND kind='subject_deleted'", (user["subject_ref"],)).fetchone()
            assert deletion is not None
            if deletion[2] == "delivered":
                break
            time.sleep(.25)
        else:
            raise TimeoutError("proof subject deletion did not reach applied purge")
        with closing(sqlite3.connect("file:" + (folder / "journal.sqlite").as_posix() + "?mode=ro", uri=True, timeout=3)) as journal:
            entry = journal.execute("SELECT seq,producer,control_json FROM journal WHERE control_id=?", (deletion[0],)).fetchone()
        assert entry is not None and entry[1] == "synthetic" and json.loads(entry[2])["subject_ref"] == user["subject_ref"]
        with psycopg.connect(analytics_db["dsn"], connect_timeout=3) as conn:
            assert conn.execute("SELECT count(*) FROM analytics.core_snapshot_proofs WHERE subject_ref=%s", (user["subject_ref"],)).fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM analytics.activity_facts WHERE subject_ref=%s", (user["subject_ref"],)).fetchone()[0] == 0
            assert conn.execute("SELECT deleted,epoch FROM analytics.subject_fences WHERE subject_ref=%s", (user["subject_ref"],)).fetchone() == (True, deletion[1])
            assert conn.execute("SELECT count(*) FROM analytics.privacy_receipts WHERE control_id=%s AND phase='complete'", (deletion[0],)).fetchone()[0] == 1
            assert conn.execute("SELECT applied_through FROM analytics.privacy_state").fetchone()[0] >= entry[0]
            if browser_payload is not None:
                assert conn.execute("SELECT count(*) FROM analytics.activity_projection_buckets WHERE subject_ref=%s", (user["subject_ref"],)).fetchone()[0] == 0
            if day_projection:
                assert conn.execute("SELECT count(*) FROM analytics.activity_projection_day_sources WHERE subject_ref=%s", (user["subject_ref"],)).fetchone()[0] == 0
                assert conn.execute("SELECT count(*) FROM analytics.activity_projection_days WHERE subject_ref=%s", (user["subject_ref"],)).fetchone()[0] == 0
        with httpx.Client(base_url=fixture["collector_url"], timeout=4, trust_env=False) as collector:
            if event is not None:
                late = collector.post("/internal/core-transfer/v1/events", headers={"Authorization": "Bearer " + producer_token(folder)},
                                      json={"event": event, "fingerprint": old[1]})
                assert late.status_code == 403
            if browser_payload is not None:
                late = collector.post("/api/analytics/events/batch", headers={"Authorization": "Bearer " + _grant_token(folder, user["subject_ref"])},
                                      json={"events": [browser_payload]})
                assert late.status_code == 403
        assert admin.delete(path, headers=headers).status_code == 204
    return event, old[1] if old is not None else None, deletion[1]


def assert_deleted_after_replay(fixture, analytics_db, folder, user, evidence, browser_payload):
    event, fingerprint, epoch = evidence
    with psycopg.connect(analytics_db["dsn"], connect_timeout=3, options="-c default_transaction_read_only=on") as conn:
        assert conn.execute("SELECT deleted,epoch FROM analytics.subject_fences WHERE subject_ref=%s", (user["subject_ref"],)).fetchone() == (True, epoch)
        revision = conn.execute("SELECT version_num FROM public.alembic_version").fetchone()[0]
        tables = ("activity_facts", "core_transfer_projection", "core_snapshot_proofs", "activity_projection_buckets", "activity_projection_days")
        if revision == "a014":
            tables += ("activity_course_buckets", "activity_course_days", "activity_credit_windows", "activity_credit_days")
        for table in tables:
            from psycopg import sql
            assert conn.execute(sql.SQL("SELECT count(*) FROM analytics.{} WHERE subject_ref=%s").format(sql.Identifier(table)), (user["subject_ref"],)).fetchone() == (0,)
    with httpx.Client(base_url=fixture["collector_url"], timeout=4, trust_env=False) as collector:
        if event is not None:
            result = collector.post("/internal/core-transfer/v1/events", headers={"Authorization": "Bearer " + producer_token(folder)},
                                    json={"event": event, "fingerprint": fingerprint})
            assert result.status_code == 403
        if browser_payload is not None:
            result = collector.post("/api/analytics/events/batch", headers={"Authorization": "Bearer " + _grant_token(folder, user["subject_ref"])},
                                    json={"events": [browser_payload]})
            assert result.status_code == 403
