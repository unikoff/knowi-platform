"""Run CORP-607.B negative cases through live owned Core and Analytics listeners."""
import argparse
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import uuid

import httpx
import jwt

import shadow_607_target as target


ORIGIN = "http://127.0.0.1:52707"
ACTIVITY_DATE = "2026-11-27"
GRANT = "/api/analytics/windows/grant"
BATCH = "/api/analytics/windows/batch"
TRACK = "FastAPI, SQLite и SQLAlchemy - StudyHub Database API"


def safe_output(output, value):
    with output.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def login(client, learner):
    response = client.post("/api/user/login", json={
        "email": learner["email"], "password": learner["password"],
    }, headers={"Origin": ORIGIN, "Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 200
    result = client.get("/api/user/me")
    assert result.status_code == 200 and result.json()["user"]["account_ref"] == learner["subject_ref"]


def grant(client, writer, learner):
    response = client.post(GRANT, json={"protocol_version": 1, "writer_id": writer, "writer_generation": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["writer_id"] == writer and body["subject_ref"] == learner["subject_ref"]
    return body


def catalog(client, course):
    catalog_response = client.get("/api/learning/analytics-catalog", params={"course_id": course})
    lessons_response = client.get("/api/learning/lessons", params={"course_id": course, "track": TRACK})
    assert catalog_response.status_code == lessons_response.status_code == 200
    published = catalog_response.json()
    lessons = lessons_response.json()["lessons"]
    lesson, = [item for item in lessons if item["title"].startswith("71. ")]
    assert lesson["is_available"] and lesson["id"] in published["lesson_ids"]
    return published["catalog_version"], lesson["id"]


def snapshot(client, writer, slot, courses, *, activity_date=ACTIVITY_DATE):
    lessons = {course: catalog(client, course) for course in courses}
    return {"protocol_version": 1, "windows": [{
        "activity_date": activity_date, "slot_no": slot, "writer_id": writer, "writer_generation": 1,
        "server_epoch": 1, "seq": 1, "client_final": False,
        "catalog_versions": {course: version for course, (version, _) in lessons.items()},
        "stats": [
            {"tool_id": "theory", "course_id": course, "lesson_id": lesson, "active_ms": 60000}
            for course, (_, lesson) in lessons.items()
        ],
    }]}


def send(client, token, raw):
    reply = client.post(BATCH, content=raw, headers={"Authorization": "Bearer " + token,
                         "Content-Type": "application/json"})
    return {"status": reply.status_code, "body": reply.json()}


def read_state(data, pending, learner, slot, *, activity_date=ACTIVITY_DATE):
    name, owner = pending["analytics"], pending["analytics_owner"]
    with target.connect_owned(data, name) as connection:
        target.observe_identity(connection, name, owner, pending["sentinel"]["analytics"])
        result = connection.execute("""
            SELECT w.stats,w.revision,c.payload_hash,c.seq
            FROM analytics.student_activity_windows AS w
            JOIN analytics.student_activity_window_control AS c USING (student_id,activity_date,slot_no)
            WHERE w.student_id=%s AND w.activity_date=%s AND w.slot_no=%s
        """, (learner["subject_ref"], activity_date, slot)).fetchone()
        if result is None:
            return None
        return dict(zip(("stats", "revision", "payload_hash", "seq"), result, strict=True))


def wire(body):
    return json.dumps(body, separators=(",", ":")).encode()


def assert_commit(response):
    assert response["status"] == 200
    item, = response["body"]["items"]
    assert item["status"] == "committed"
    return item["confirmed"]


def scope_case(client, data, pending, learner, local, folder, activity_date):
    scope_path = local._file(folder, "corp607-scope-failures.json")
    original = json.loads(local._read(folder, scope_path.name, 4096))
    subject = learner["subject_ref"]
    assert original[subject]["course_ids"] == ["backend", "backend_copy"]

    writer = str(uuid.uuid4())
    token = grant(client, writer, learner)["grant"]
    initial = snapshot(client, writer, 42, ["backend", "backend_copy"], activity_date=activity_date)
    assert read_state(data, pending, learner, 42, activity_date=activity_date) is None
    written = send(client, token, wire(initial))
    confirmed = assert_commit(written)
    before = read_state(data, pending, learner, 42, activity_date=activity_date)
    assert before and before["revision"] == confirmed["revision"] and len(before["stats"]) == 2

    changed = deepcopy(original)
    changed[subject]["course_ids"].remove("backend_copy")
    request = deepcopy(initial)
    request["windows"][0]["seq"] = 2
    request["windows"][0]["stats"][0]["active_ms"] += 1000

    temp_name = f"scope-revoked-{uuid.uuid4().hex}.json"
    target.live._write(local, folder, temp_name, changed)
    (folder / temp_name).replace(scope_path)
    try:
        rejected = send(client, token, wire(request))
        after = read_state(data, pending, learner, 42, activity_date=activity_date)
    finally:
        temp_name = f"scope-restored-{uuid.uuid4().hex}.json"
        target.live._write(local, folder, temp_name, original)
        (folder / temp_name).replace(scope_path)

    return {"subject_ref": subject, "request": initial, "after_revocation_request": request,
            "initial_reply": written, "after_revocation": rejected, "postgres_before": before, "postgres_after": after}


def privacy_case(action, client, data, pending, learner, local, folder, activity_date):
    path = "corp607-private-privacy-retry.json"
    if action == "privacy-arm":
        writer = str(uuid.uuid4())
        body = snapshot(client, writer, 43, ["backend"], activity_date=activity_date)
        issued = grant(client, writer, learner)
        assert read_state(data, pending, learner, 43, activity_date=activity_date) is None
        target.live._write(local, folder, path, {"body": body, "grant": issued["grant"],
                                               "jwt_expires_unix": jwt.decode(issued["grant"], options={"verify_signature": False})["exp"]})
        return {"prepared": body, "subject_ref": learner["subject_ref"], "postgres_before": None}

    armed = json.loads(local._read(folder, path, 16384))
    assert armed["body"]["windows"][0]["activity_date"] == activity_date, "retry must keep the original window date"
    mask = httpx.get("http://127.0.0.1:18042/ready", timeout=3, trust_env=False).status_code
    assert mask == (503 if action == "privacy-closed" else 200)
    assert read_state(data, pending, learner, 43, activity_date=activity_date) is None
    raw = wire(armed["body"])
    expiry = datetime.fromtimestamp(armed["jwt_expires_unix"], timezone.utc)
    token = armed["grant"]
    if expiry <= datetime.now(timezone.utc):
        assert action == "privacy-restored"
        token = grant(client, armed["body"]["windows"][0]["writer_id"], learner)["grant"]
    response = send(client, token, raw)
    after = read_state(data, pending, learner, 43, activity_date=activity_date)
    return {"collector_readiness": mask, "request_sha256": sha256(raw).hexdigest(), "reply": response, "postgres_after": after}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=uuid.UUID)
    parser.add_argument("action", choices=("scope", "privacy-arm", "privacy-closed", "privacy-restored"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--activity-date", type=date.fromisoformat, default=date.fromisoformat(ACTIVITY_DATE))
    args = parser.parse_args()
    activity_date = args.activity_date.isoformat()
    local, folder, _ = target.runtime._identity(str(args.run))
    if args.output.resolve().parent != folder:
        parser.error("Output must be a new path in the owned private fixture directory")
    pending = json.loads(local._read(folder, "corp607-pending.json", 4096))
    learners = json.loads(local._read(folder, "corp607-learners.json", 16384))
    learner = learners["scope" if args.action == "scope" else "privacy"]
    data = target.bootstrap.config(target.CONNECTION)
    with httpx.Client(base_url=ORIGIN, timeout=6, trust_env=False, headers={"Origin": ORIGIN}) as client:
        login(client, learner)
        if args.action == "scope":
            evidence = scope_case(client, data, pending, learner, local, folder, activity_date)
        else:
            evidence = privacy_case(args.action, client, data, pending, learner, local, folder, activity_date)
    safe_output(args.output, {"run": str(args.run), "action": args.action, "activity_date": activity_date, **evidence})
    if args.action == "scope":
        rejected, = evidence["after_revocation"]["body"]["items"]
        assert rejected["status"] == "rejected" and rejected["code"] == "course_forbidden"
        assert evidence["postgres_after"] == evidence["postgres_before"]
    elif args.action == "privacy-closed":
        assert evidence["reply"]["status"] == 503 and evidence["reply"]["body"]["code"] == "privacy_unavailable"
        assert evidence["postgres_after"] is None
    elif args.action == "privacy-restored":
        confirmed = assert_commit(evidence["reply"])
        assert evidence["postgres_after"]["revision"] == confirmed["revision"]
    print(f"{args.action}: PASS")


if __name__ == "__main__":
    main()

