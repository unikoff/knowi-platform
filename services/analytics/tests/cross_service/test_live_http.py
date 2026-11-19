"""Live HTTP checks for an independently provisioned, sentinel-guarded fixture.

These tests do not start services, install schemas, relay Core outbox, or
delete resources. All such operations belong to the fixture owner.
"""
from datetime import datetime, timedelta, timezone
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import jwt
import pytest

from .guards import load, verify_databases

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def target():
    if os.environ.get("ANALYTICS_CROSS_SERVICE_RUN") != "1":
        pytest.skip("explicit synthetic cross-service run required")
    try:
        config = load(os.environ["ANALYTICS_CROSS_SERVICE_FIXTURE"], ROOT)
        verify_databases(config)
    except Exception:
        pytest.fail("synthetic database guard failed", pytrace=False)
    return config


def login(client, config, role):
    user = config["users"][role]
    response = client.post("/api/user/login", json={"email": user["email"], "password": user["password"]},
                           headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 200


def grant(client, purpose, payload, expected=200):
    response = client.post("/api/analytics/grants/" + purpose, json=payload)
    assert response.status_code == expected
    return response.json() if expected == 200 else None


def claims(grant_body):
    # This is only a shape inspection. A later request to Analytics verifies
    # the signature and audience; decoding alone proves neither.
    return jwt.decode(grant_body["grant"], options={"verify_signature": False, "verify_exp": False})


def producer_token():
    now = int(time.time())
    return jwt.encode({"grant_version": 1, "iss": "control-issuer", "aud": "analytics-control", "sub": "synthetic",
                       "iat": now, "nbf": now, "exp": now+60, "scope": "control:write"},
                      Path(os.environ["PYMENTOR_ANALYTICS_PRODUCER_KEY_FILE"]).read_bytes(), algorithm="EdDSA", headers={"kid": "p"})


def test_grants_roster_and_numeric_mapping_over_real_http(target):
    users = target["users"]
    with httpx.Client(base_url=target["core_url"], timeout=4, trust_env=False) as user, \
         httpx.Client(base_url=target["core_url"], timeout=4, trust_env=False) as leader, \
         httpx.Client(base_url=target["core_url"], timeout=4, trust_env=False) as admin, \
         httpx.Client(base_url=target["query_url"], timeout=4, trust_env=False) as query:
        for client, role in ((user, "student"), (leader, "leader"), (admin, "admin")):
            login(client, target, role)
        own = grant(user, "report", {"user_id": users["student"]["id"]})
        assert own["target_subject_ref"] == users["student"]["subject_ref"]
        user_claims = claims(own)
        assert user_claims["role"] == "user" and user_claims["subject_refs"] == [] and not user_claims["global_read"]
        grant(user, "report", {"user_id": users["other"]["id"]}, 403)
        roster = grant(leader, "report", {"course_id": target["course_id"]})
        leader_claims = claims(roster)
        assert leader_claims["role"] == "leader" and set(leader_claims["subject_refs"]) == set(target["roster"])
        grant(leader, "report", {"course_id": target["course_id"], "user_id": users["other"]["id"]}, 404)
        selected = grant(leader, "report", {"course_id": target["course_id"], "user_id": users["student"]["id"]})
        assert selected["target_subject_ref"] == users["student"]["subject_ref"]
        global_grant = grant(admin, "report", {"user_id": users["other"]["id"]})
        assert global_grant["target_subject_ref"] == users["other"]["subject_ref"]
        assert claims(global_grant)["role"] == "admin" and claims(global_grant)["global_read"] is True
        now = datetime.now(timezone.utc).replace(microsecond=0)
        params = {"from_time": (now-timedelta(hours=1)).isoformat(), "to_time": now.isoformat(), "course_id": target["course_id"]}
        for body in (own, roster, global_grant):
            response = query.get("/api/analytics/reports/activity", params=params, headers={"Authorization": "Bearer " + body["grant"]})
            assert response.status_code == 200
        denied = query.get("/api/analytics/reports/activity", params={**params, "subject_ref": users["other"]["subject_ref"]},
                           headers={"Authorization": "Bearer " + roster["grant"]})
        assert denied.status_code == 403


def test_legacy_calendar_summary_and_admin_parity_over_http(target):
    with httpx.Client(base_url=target["core_url"], timeout=4, trust_env=False) as student, \
         httpx.Client(base_url=target["core_url"], timeout=4, trust_env=False) as admin, \
         httpx.Client(base_url=target["query_url"], timeout=4, trust_env=False) as query:
        login(student, target, "student")
        login(admin, target, "admin")
        own = grant(student, "report", {})
        global_grant = grant(admin, "report", {})
        bearer = {"Authorization": "Bearer " + own["grant"]}
        admin_bearer = {"Authorization": "Bearer " + global_grant["grant"]}
        for suffix, params in (("calendar", {"year": 2026, "month": 11}), ("summary", {})):
            core_path = "/api/user/activity" + ("" if suffix == "calendar" else "/summary")
            core_response = student.get(core_path, params=params)
            received = query.get("/internal/analytics/v1/legacy/" + suffix, params=params, headers=bearer)
            assert core_response.status_code == 200 and received.status_code == 200
            assert received.json()["coverage"] == "unknown"
            expected = core_response.json()
            if suffix == "calendar" and os.environ.get("PYMENTOR_ANALYTICS_HISTORY_DATES_ONLY") == "1":
                imported_timestamp_dates = {"2026-11-16"}
                if os.environ.get("CROSS_SERVICE_LEARNER_COURSE_REVIEW") == "1":
                    imported_timestamp_dates.add(datetime.now(timezone(timedelta(hours=3))).date().isoformat())
                for visit in expected["visits"]:
                    if visit["activity_date"] in imported_timestamp_dates:
                        assert visit["visited_at"] is not None
                        visit["visited_at"] = None
            assert received.json()["data"] == expected
        platform = admin.get("/api/admin-platform/overview")
        course = admin.get("/api/admin-platform/overview", params={"course_id": "backend"})
        legacy = admin.get("/api/admin/overview")
        received = query.get("/internal/analytics/v1/legacy/admin/activity", headers=admin_bearer)
        assert platform.status_code == course.status_code == legacy.status_code == received.status_code == 200
        result = received.json()
        assert result["coverage"] == "unknown"
        assert result["data"]["platform_activity_today"] == platform.json()["activity_today"]
        assert result["data"]["platform_course_activity_today"] == course.json()["activity_today"]
        assert result["data"]["legacy_dashboard_active_students_7d"] == legacy.json()["active_students_7d"]
        assert result["data"]["dashboard_activity"] == {key: platform.json()["dashboard"][key]
            for key in ("active_students_7d", "inactive_students_7d", "daily_active_students")}
        assert query.get("/internal/analytics/v1/legacy/admin/activity", headers=bearer).status_code == 403


def test_receipt_survives_completed_job_compaction(target):
    import json
    import psycopg
    verify_databases(target)
    subject = target["users"]["student"]["subject_ref"]
    with psycopg.connect(target["databases"]["core"]["dsn"], connect_timeout=3) as conn:
        row = conn.execute("SELECT event_id,payload,fingerprint FROM analytics_transfer_outbox WHERE subject_ref=%s AND kind='legacy_activity_day' AND status='delivered' ORDER BY event_id LIMIT 1", (subject,)).fetchone()
    assert row is not None
    event_id, payload, fingerprint = row
    with psycopg.connect(target["databases"]["analytics"]["dsn"], connect_timeout=3) as conn:
        result = conn.execute("DELETE FROM analytics.processing_jobs WHERE producer_namespace='core' AND event_id=%s AND subject_ref=%s AND consumer='core.transfer.projection' AND status='completed' RETURNING event_id", (event_id, subject)).fetchall()
        assert len(result) == 1
    headers = {"Authorization": "Bearer " + producer_token()}
    with httpx.Client(base_url=target["collector_url"], timeout=4, trust_env=False) as collector:
        receipt = collector.get(f"/internal/core-transfer/v1/receipts/{event_id}", params={"fingerprint": fingerprint}, headers=headers)
        assert receipt.status_code == 200 and receipt.json() == {"status": "accepted"}
        repeat = collector.post("/internal/core-transfer/v1/events", headers=headers,
                                json={"event": json.loads(payload), "fingerprint": fingerprint})
        assert repeat.status_code == 200 and repeat.json() == {"status": "accepted"}
    with psycopg.connect(target["databases"]["analytics"]["dsn"], connect_timeout=3) as conn:
        assert conn.execute("SELECT count(*) FROM analytics.activity_facts WHERE producer_namespace='core' AND event_id=%s", (event_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM analytics.processing_jobs WHERE producer_namespace='core' AND event_id=%s", (event_id,)).fetchone()[0] == 0


def test_same_entity_revision_with_changed_payload_conflicts(target):
    import importlib.util
    import json
    import psycopg
    from datetime import date
    from uuid import UUID
    verify_databases(target)
    subject = target["users"]["student"]["subject_ref"]
    with psycopg.connect(target["databases"]["core"]["dsn"], connect_timeout=3) as conn:
        row = conn.execute("SELECT payload FROM analytics_transfer_outbox WHERE subject_ref=%s AND kind='legacy_activity_day' AND revision>0 AND status='delivered'", (subject,)).fetchone()
    assert row is not None
    original = json.loads(row[0])
    spec = importlib.util.spec_from_file_location("cross_service_core_wire", ROOT.parent / "core-api/pymentor_core/modules/analytics_delivery/contracts.py")
    wire = importlib.util.module_from_spec(spec); spec.loader.exec_module(wire)
    changed_time = datetime.fromisoformat(original["payload"]["visited_at"]) + timedelta(seconds=1)
    changed = wire.activity(UUID(subject), date.fromisoformat(original["payload"]["activity_date"]), changed_time, revision=original["revision"])
    with httpx.Client(base_url=target["collector_url"], timeout=4, trust_env=False) as collector:
        reply = collector.post("/internal/core-transfer/v1/events", headers={"Authorization": "Bearer " + producer_token()},
                               json={"event": changed, "fingerprint": wire.fingerprint(changed)})
        assert reply.status_code == 200 and reply.json() == {"status": "conflict"}
    with psycopg.connect(target["databases"]["analytics"]["dsn"], connect_timeout=3) as conn:
        assert conn.execute("SELECT count(*) FROM analytics.activity_facts WHERE producer_namespace='core' AND event_id=%s", (changed["event_id"],)).fetchone()[0] == 0


def test_collector_time_union_and_report_progress(target):
    with httpx.Client(base_url=target["core_url"], timeout=4, trust_env=False) as core, \
         httpx.Client(base_url=target["collector_url"], timeout=4, trust_env=False) as collector, \
         httpx.Client(base_url=target["query_url"], timeout=4, trust_env=False) as query:
        login(core, target, "student")
        collect = grant(core, "collect", {"course_id": target["course_id"]})
        report = grant(core, "report", {"course_id": target["course_id"]})
        start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=3)
        session = str(uuid4())
        events = []
        for offset, section in ((0, "theory"), (30, "practice")):
            begin = start + timedelta(seconds=offset)
            events.append({"schema_version": 2, "event_id": str(uuid4()), "type": "activity_segment",
                           "course_id": target["course_id"], "catalog_version": target["catalog_version"], "lesson_id": "lesson-1",
                           "section": section, "session_id": session, "started_at": begin.isoformat(),
                           "ended_at": (begin+timedelta(seconds=60)).isoformat(), "last_action_at": begin.isoformat(),
                           "visible": True, "focused": True, "active_ms": 60000, "payload": {}})
        headers = {"Authorization": "Bearer " + collect["grant"]}
        response = collector.post("/api/analytics/events/batch", headers=headers, json={"events": events})
        assert response.status_code == 200
        assert [item["status"] for item in response.json()["items"]] == ["accepted", "accepted"]
        duplicate = collector.post("/api/analytics/events/batch", headers=headers, json={"events": events})
        assert duplicate.status_code == 200 and [i["status"] for i in duplicate.json()["items"]] == ["duplicate", "duplicate"]
        params = {"from_time": (start-timedelta(seconds=1)).isoformat(),
                  "to_time": (start+timedelta(seconds=100)).isoformat(), "course_id": target["course_id"]}
        until = time.monotonic() + (20 if os.environ.get("CROSS_SERVICE_A010") == "1" else 0)
        while True:
            response = query.get("/api/analytics/reports/activity", headers={"Authorization": "Bearer " + report["grant"]}, params=params)
            if response.status_code != 503 or time.monotonic() >= until:
                break
            time.sleep(.1)
        assert response.status_code == 200
        result = response.json()
        assert result["totals"] == {"unique_visitors": 1, "active_ms": 90000, "theory_ms": 30000, "practice_ms": 60000}
        # Without proven scoped coverage the report must not infer a completion
        # count from duration; Core progress is checked separately in legacy parity.
        assert result["trusted_progress"] in (None, {"state": "unavailable", "completed_lessons": None})


def test_core_delete_does_not_report_completion_before_privacy_purge(target):
    # This is a single-shot mutation. A timeout has unknown outcome and MUST be
    # reconciled externally; never automatically replay this test on timeout.
    import json
    import psycopg
    from psycopg import sql
    verify_databases(target)
    user = target["users"]["other"]
    with psycopg.connect(target["databases"]["core"]["dsn"], connect_timeout=3) as conn:
        row = conn.execute("SELECT payload,fingerprint FROM analytics_transfer_outbox WHERE subject_ref=%s AND kind='legacy_activity_day' AND status='delivered'", (user["subject_ref"],)).fetchone()
        assert row is not None
        old_event, old_fingerprint = json.loads(row[0]), row[1]
    with httpx.Client(base_url=target["core_url"], timeout=4, trust_env=False) as admin, \
         httpx.Client(base_url=target["core_url"], timeout=4, trust_env=False) as other, \
         httpx.Client(base_url=target["collector_url"], timeout=4, trust_env=False) as browser_collector:
        login(admin, target, "admin")
        login(other, target, "other")
        collect = grant(other, "collect", {"course_id": "backend"})
        moment = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        browser_event = {"schema_version": 2, "event_id": str(uuid4()), "type": "course_opened", "course_id": "backend",
                         "catalog_version": target["catalog_version"], "lesson_id": None, "section": None, "session_id": str(uuid4()),
                         "started_at": moment, "ended_at": moment, "last_action_at": moment, "visible": True,
                         "focused": True, "active_ms": 0, "payload": {}}
        browser_headers = {"Authorization": "Bearer " + collect["grant"]}
        before = browser_collector.post("/api/analytics/events/batch", headers=browser_headers, json={"events": [browser_event]})
        assert before.status_code == 200 and before.json()["items"][0]["status"] == "accepted"
        path, headers = f"/api/admin/users/{user['id']}", {"X-Account-Ref": user["subject_ref"]}
        response = admin.delete(path, headers=headers)
        # Until the independent journal, applied prefix and purge complete,
        # returning 200/204 is a blocking failure, not deletion acceptance.
        assert response.status_code == 202 and response.json() == {"status": "pending"} and "Retry-After" in response.headers
        with psycopg.connect(target["databases"]["core"]["dsn"], connect_timeout=3) as conn:
            assert conn.execute("SELECT count(*) FROM users WHERE id=%s", (user["id"],)).fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM user_activity_days WHERE user_id=%s", (user["id"],)).fetchone()[0] == 0
            rows = conn.execute("SELECT kind,status FROM analytics_transfer_outbox WHERE subject_ref=%s", (user["subject_ref"],)).fetchall()
            assert rows == [("subject_deleted", "pending")]
        repeat = admin.delete(path, headers=headers)
        assert repeat.status_code == 202 and repeat.json() == {"status": "pending"}
        assert other.get("/api/user/me").status_code == 401
        cmd = [os.environ["CROSS_SERVICE_CORE_PYTHON"], "-B", os.environ["CROSS_SERVICE_TRANSFER_SCRIPT"],
               "relay-once", "--expected-db", target["databases"]["core"]["name"]]
        until = time.monotonic() + 30
        while time.monotonic() < until:
            child = subprocess.run(cmd, cwd=os.environ["CROSS_SERVICE_CORE_ROOT"], env=os.environ.copy(),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            assert child.returncode == 0
            with psycopg.connect(target["databases"]["core"]["dsn"], connect_timeout=3) as conn:
                deletion = conn.execute("SELECT event_id,revision,status FROM analytics_transfer_outbox WHERE subject_ref=%s AND kind='subject_deleted'", (user["subject_ref"],)).fetchone()
            assert deletion is not None
            if deletion[2] == "delivered":
                break
            time.sleep(.25)
        else:
            pytest.fail("deletion did not reach verified delivery before deadline", pytrace=False)
        with closing(sqlite3.connect("file:" + Path(os.environ["CROSS_SERVICE_JOURNAL"]).as_posix() + "?mode=ro", uri=True, timeout=3)) as journal:
            entry = journal.execute("SELECT seq,producer,control_json FROM journal WHERE control_id=?", (deletion[0],)).fetchone()
        assert entry is not None and entry[1] == "synthetic"
        control = json.loads(entry[2])
        assert control["subject_ref"] == user["subject_ref"] and control["epoch"] == deletion[1]
        with psycopg.connect(target["databases"]["analytics"]["dsn"], connect_timeout=3) as conn:
            assert conn.execute("SELECT applied_through FROM analytics.privacy_state WHERE singleton=true").fetchone()[0] >= entry[0]
            assert conn.execute("SELECT deleted,epoch FROM analytics.subject_fences WHERE subject_ref=%s", (user["subject_ref"],)).fetchone() == (True, deletion[1])
            for table in ("activity_facts", "processing_jobs", "processing_receipts", "processing_results", "processing_leases",
                          "processing_replays", "admission_subject_windows", "core_transfer_entities", "core_transfer_projection"):
                count = conn.execute(sql.SQL("SELECT count(*) FROM analytics.{} WHERE subject_ref=%s").format(sql.Identifier(table)), (user["subject_ref"],)).fetchone()[0]
                assert count == 0
        producer = producer_token()
        with httpx.Client(base_url=target["collector_url"], timeout=4, trust_env=False) as collector:
            late = collector.post("/internal/core-transfer/v1/events", headers={"Authorization": "Bearer " + producer},
                                  json={"event": old_event, "fingerprint": old_fingerprint})
            assert late.status_code == 403
        replay = browser_collector.post("/api/analytics/events/batch", headers=browser_headers, json={"events": [browser_event]})
        assert replay.status_code == 403
        assert admin.delete(path, headers=headers).status_code == 204
        replacement_ref = str(uuid4())
        with psycopg.connect(target["databases"]["core"]["dsn"], connect_timeout=3) as conn:
            conn.execute("INSERT INTO users(id,username,email,password_hash,verified,is_admin) VALUES (%s,'replacement','replacement@example.test','synthetic',true,false)", (user["id"],))
            conn.execute("INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (%s,%s)", (user["id"], replacement_ref))
        assert admin.delete(path, headers=headers).status_code == 204
        with psycopg.connect(target["databases"]["core"]["dsn"], connect_timeout=3) as conn:
            assert conn.execute("SELECT lifecycle_id FROM account_lifecycles WHERE user_id=%s", (user["id"],)).fetchone() == (replacement_ref,)
        assert other.get("/api/user/me").status_code == 401
