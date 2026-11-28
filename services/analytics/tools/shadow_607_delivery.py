"""Drive the 607.B delivery failure matrix through real authenticated Core HTTP."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import time
from uuid import UUID, uuid4

import httpx
from shadow_607_delivery_target import (
    ARMED, COMMITTED, DISCONNECTED, WORKTREE, checked_plan, read, target, wire,
)
from shadow_607_failures import catalog
from pymentor_analytics.contracts.windows_v1 import WindowSnapshot, snapshot_digest

CORE = "http://127.0.0.1:18041"
COLLECTOR = "http://127.0.0.1:18042"
QUERY = "http://127.0.0.1:18043"
BATCH = "/api/analytics/windows/batch"
GRANT = "/api/analytics/windows/grant"
OLD_BATCH = "/api/analytics/events/batch"
OLD_GRANT = "/api/analytics/grants/collect"
REPORT = "/api/user/analytics/statistics?days=7&course_id=backend"


def login(client, learner):
    response = client.post("/api/user/login", json={"email": learner["email"], "password": learner["password"]},
                           headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 200 and client.cookies
    result = client.get("/api/user/me")
    assert result.status_code == 200 and result.json()["user"]["account_ref"] == learner["subject_ref"]


def grant(client, writer, subject):
    response = client.post(GRANT, json={"protocol_version": 1, "writer_id": writer, "writer_generation": 1})
    assert response.status_code == 200
    model = response.json()
    assert model["subject_ref"] == subject and model["writer_id"] == writer and model["writer_generation"] == 1
    return model["grant"]


def snapshot(key, writer, version, lesson, *, sequence=1):
    return {"activity_date": key["activity_date"], "slot_no": key["slot_no"], "writer_id": writer,
            "writer_generation": 1, "seq": sequence, "server_epoch": 1, "client_final": False,
            "stats": [{"tool_id": "theory", "course_id": "backend", "lesson_id": lesson, "active_ms": key["active_ms"]}],
            "catalog_versions": {"backend": version}}


def batch(*windows):
    return wire({"protocol_version": 1, "windows": windows})


def submit(client, bearer, body):
    sent_at = time.monotonic()
    response = client.post(BATCH, headers={"Authorization": "Bearer " + bearer, "Content-Type": "application/json"},
                           content=body)
    return {"status": response.status_code, "reply": response.json(), "body_sha256": sha256(body).hexdigest(),
            "round_trip_ms": int((time.monotonic() - sent_at) * 1000), "retry_after": response.headers.get("Retry-After")}


def query_window(data, guards, learner, key):
    name, owner = guards["analytics"], guards["analytics_owner"]
    with target.connect_owned(data, name) as pg:
        target.observe_identity(pg, name, owner, guards["sentinel"]["analytics"])
        pg.execute("BEGIN READ ONLY")
        row = pg.execute("""SELECT w.stats,w.revision,c.payload_hash,c.seq,c.writer_id::text,c.writer_generation,
                                   c.server_epoch,c.phase,c.source_generation,c.change_generation,c.coverage_reasons
            FROM analytics.student_activity_window_control c
            LEFT JOIN analytics.student_activity_windows w USING (student_id,activity_date,slot_no)
            WHERE c.student_id=%s AND c.activity_date=%s AND c.slot_no=%s""",
            (learner["subject_ref"], date.fromisoformat(key["activity_date"]), key["slot_no"])).fetchone()
        pg.execute("ROLLBACK")
    labels = ("stats", "revision", "payload_hash", "seq", "writer_id", "writer_generation", "server_epoch",
              "phase", "source_generation", "change_generation", "coverage_reasons")
    return dict(zip(labels, row, strict=True)) if row is not None else None


def await_marker(local, folder):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if local._file(folder, DISCONNECTED, required=False).exists():
            return read(local, folder, DISCONNECTED, 4096)
        time.sleep(.04)
    raise AssertionError("Collector did not observe the Core peer disconnect")


def legacy_event(client, collector, data, guards, learner, key, version, lesson):
    credential = client.post(OLD_GRANT, json={"course_id": "backend"})
    assert credential.status_code == 200 and credential.json()["subject_ref"] == learner["subject_ref"]
    start = datetime.fromisoformat(key["start"]) + timedelta(seconds=5)
    event = {"schema_version": 2, "event_id": key["event_id"], "type": "activity_segment",
             "course_id": "backend", "catalog_version": version, "lesson_id": lesson, "section": "theory",
             "session_id": str(uuid4()), "started_at": start.isoformat(), "ended_at": (start + timedelta(minutes=1)).isoformat(),
             "last_action_at": start.isoformat(), "visible": True, "focused": True, "active_ms": 60000, "payload": {}}
    result = collector.post(OLD_BATCH, headers={"Authorization": "Bearer " + credential.json()["grant"]},
                            json={"events": [event]})
    assert result.status_code == 200 and result.json()["items"][0]["status"] == "accepted"
    name, owner = guards["analytics"], guards["analytics_owner"]
    with target.connect_owned(data, name) as pg:
        target.observe_identity(pg, name, owner, guards["sentinel"]["analytics"])
        pg.execute("BEGIN READ ONLY")
        count, = pg.execute("SELECT count(*) FROM analytics.activity_facts WHERE subject_ref=%s AND event_id=%s",
                            (learner["subject_ref"], key["event_id"])).fetchone()
        pg.execute("ROLLBACK")
    assert count == 1
    return {"event_id": key["event_id"], "item_ack": result.json()["items"][0], "committed_fact_rows": count}


def report_at(client, seconds):
    deadline = time.monotonic() + 14
    while time.monotonic() < deadline:
        observed = client.get(REPORT)
        body = observed.json()
        measured = body.get("summary") or {}
        if observed.status_code == 200 and measured.get("theory_seconds") == seconds:
            assert measured["active_seconds"] == seconds
            return {"status": observed.status_code, "summary": measured}
        time.sleep(.15)
    raise AssertionError("ordinary v2 report did not reach the expected activity")


def verify_ack(ack, written, stats, *, code, index):
    assert ack["status"] == code and ack["index"] == index
    assert written and written["stats"] == stats and written["revision"] == 1
    assert ack["writer_id"] == written["writer_id"] and ack["writer_generation"] == written["writer_generation"]
    assert ack["seq"] == written["seq"] and ack["payload_hash"] == written["payload_hash"]
    assert ack["confirmed"]["revision"] == written["revision"]
    assert ack["confirmed"]["stats"] == written["stats"]


def run(instance, evidence):
    data, local, folder, guards, details, learner, plan = checked_plan(instance)
    if any(local._file(folder, name, required=False).exists() for name in (ARMED, COMMITTED, DISCONNECTED)):
        raise ValueError("delivery attempt already started; inspect the recorded outcome before retrying")
    headers = {"Origin": CORE, "Referer": CORE + "/backend"}
    evidence.update({"version": 1, "case": "607-Б delivery", "instance_id": str(instance),
                     "source": plan["source"], "databases": details, "subject_ref": learner["subject_ref"],
                     "t0": plan["t0"], "worker_enabled": False, "expected": plan["windows"],
                     "http": {"core": CORE, "collector": COLLECTOR, "query": QUERY, "report_path": REPORT}})
    with httpx.Client(base_url=CORE, headers=headers, timeout=15, trust_env=False) as browser_a, \
            httpx.Client(base_url=CORE, headers=headers, timeout=15, trust_env=False) as browser_b, \
            httpx.Client(base_url=COLLECTOR, timeout=6, trust_env=False) as collector:
        assert browser_a.get("/health/ready").status_code == 200
        assert collector.get("/ready").status_code == 200
        assert httpx.get(QUERY + "/ready", timeout=5, trust_env=False).status_code == 200
        login(browser_a, learner)
        login(browser_b, learner)
        assert dict(browser_a.cookies) != dict(browser_b.cookies)
        evidence["two_independent_authenticated_sessions"] = True
        version, lesson = catalog(browser_a, "backend")
        evidence["catalog"] = {"course_id": "backend", "version": version, "lesson_id": lesson}
        issued_a = grant(browser_a, plan["writer_a"], learner["subject_ref"])
        issued_b = grant(browser_b, plan["writer_b"], learner["subject_ref"])
        key_a, key_b = plan["windows"]
        assert query_window(data, guards, learner, key_a) is None
        assert query_window(data, guards, learner, key_b) is None
        first = snapshot(key_a, plan["writer_a"], version, lesson)
        second = snapshot(key_b, plan["writer_a"], version, lesson)
        raw = batch(first, second)
        digest = sha256(raw).hexdigest()
        evidence["input_windows"] = [first, second]
        evidence["request_body_sha256"] = digest
        expected_hash = snapshot_digest(WindowSnapshot.model_validate_json(wire(first)))
        target.live._write(local, folder, ARMED, {"instance_id": str(instance), "body_sha256": digest,
                                                 "snapshot_sha256": expected_hash})

        failed = submit(browser_a, issued_a, raw)
        evidence["lost_reply"] = failed
        assert failed["status"] == 503 and failed["reply"]["code"] == "unavailable" and "items" not in failed["reply"]
        assert failed["round_trip_ms"] >= 8500
        committed = read(local, folder, COMMITTED, 4096)
        disconnected = await_marker(local, folder)
        assert committed == {"activity_date": key_a["activity_date"], "slot_no": key_a["slot_no"],
                             "body_sha256": digest, "snapshot_sha256": expected_hash, "revision": 1, "after_commit": True}
        assert disconnected == {"body_sha256": digest, "peer_disconnected": True}
        first_row = query_window(data, guards, learner, key_a)
        second_row = query_window(data, guards, learner, key_b)
        evidence["after_commit_before_retry"] = {"fault": committed, "socket": disconnected, "first": first_row, "second": second_row}
        assert first_row and first_row["stats"] == first["stats"] and first_row["revision"] == 1
        assert first_row["payload_hash"] == expected_hash and second_row is None

        evidence["v2_after_503"] = legacy_event(browser_a, collector, data, guards, learner, plan["old_v2"][0], version, lesson)
        evidence["ordinary_report_after_503"] = report_at(browser_a, 60)
        repeat = submit(browser_a, issued_a, raw)
        evidence["exact_retry"] = repeat
        assert repeat["body_sha256"] == digest and repeat["status"] == 200
        after_first = query_window(data, guards, learner, key_a)
        after_second = query_window(data, guards, learner, key_b)
        assert after_first == first_row
        duplicate, created = repeat["reply"]["items"]
        verify_ack(duplicate, after_first, first["stats"], code="duplicate", index=0)
        verify_ack(created, after_second, second["stats"], code="committed", index=1)
        evidence["postgres_after_retry"] = [after_first, after_second]

        contender = snapshot(dict(key_a, active_ms=450000), plan["writer_b"], version, lesson, sequence=100)
        second_writer = submit(browser_b, issued_b, batch(contender))
        evidence["competing_writer"] = second_writer
        assert second_writer["status"] == 200
        (pending,) = second_writer["reply"]["items"]
        assert pending["status"] == "pending" and pending["code"] == "ownership_conflict"
        assert pending["writer_id"] == plan["writer_b"] and pending["seq"] == 100
        after_pending = query_window(data, guards, learner, key_a)
        evidence["control_after_competing_writer"] = after_pending
        assert after_pending["phase"] == "pending_reconciliation" and "writer_conflict" in after_pending["coverage_reasons"]
        assert after_pending["change_generation"] > after_first["change_generation"]
        for field in ("stats", "revision", "writer_id", "writer_generation", "seq", "server_epoch", "payload_hash"):
            assert after_pending[field] == after_first[field]
        assert query_window(data, guards, learner, key_b) == after_second

        same = batch(first)
        repeated = []
        for _ in range(9):
            result = submit(browser_a, issued_a, same)
            assert result["status"] == 200 and result["reply"]["items"][0]["status"] == "duplicate"
            repeated.append(result["status"])
        denied = submit(browser_a, issued_a, same)
        evidence["rate_budget"] = {"admitted": 3 + len(repeated), "request_13": denied}
        assert denied["status"] == 429 and denied["reply"]["code"] == "rate_limited"
        assert int(denied["retry_after"]) > 0
        evidence["v2_after_429"] = legacy_event(browser_a, collector, data, guards, learner, plan["old_v2"][1], version, lesson)
        evidence["ordinary_report_after_429"] = report_at(browser_a, 120)
        current = [query_window(data, guards, learner, key) for key in (key_a, key_b)]
        assert current == [after_pending, after_second]
        evidence["postgres_final"] = current
        evidence["result"] = "PASS"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", type=UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    params = parser.parse_args()
    evidence = {"result": "OPEN"}
    root = (WORKTREE / "docs/analytics/evidence-598/607/delivery").resolve()
    path = params.output.resolve()
    if not path.is_relative_to(root) or path.exists():
        raise ValueError("use a new output file in the delivery evidence directory")
    with path.open("x", encoding="utf-8") as log:
        try:
            run(params.instance_id, evidence)
        except Exception as exc:
            evidence["result"] = "FAIL"
            evidence["exception_type"] = type(exc).__name__
            raise
        finally:
            json.dump(evidence, log, ensure_ascii=False, indent=2, allow_nan=False)
            log.write("\n")
    print(wire({"result": evidence["result"], "evidence": str(path)}).decode("utf-8"))


if __name__ == "__main__":
    main()
