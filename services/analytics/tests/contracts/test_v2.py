import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from pymentor_analytics.contracts import v2

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"
def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
def wire(value):
    return json.dumps(value, separators=(",", ":")).encode()


def test_browser_fixtures_and_partial_admission():
    batch = load("browser-batch.json")
    items = v2.parse_browser_items(wire(batch))
    events = [v2.parse_browser_item(item) for item in items]
    assert {e.course_id for e in events} == {"backend", "backend-copy"}
    assert events[0].lesson_id is None and events[0].active_ms == 0
    bad = dict(batch["events"][1], schema_version=3)
    mixed = v2.parse_browser_items(wire({"events": [batch["events"][0], bad]}))
    assert v2.parse_browser_item(mixed[0])
    with pytest.raises(ValidationError):
        v2.parse_browser_item(mixed[1])


@pytest.mark.parametrize("change", [
    {"subject_ref": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"}, {"source": "core"},
    {"type": "progress_fact"}, {"payload": {"code": "secret"}},
    {"focused": False}, {"visible": False}, {"active_ms": 60001},
    {"last_action_at": "2026-11-16T09:54:59Z"},
    {"ended_at": "2026-11-16T10:01:01Z"},
])
def test_browser_rejects_forgery_pii_and_invalid_interval(change):
    event = dict(load("browser-batch.json")["events"][1], **change)
    with pytest.raises(ValidationError):
        v2.parse_browser_item(wire(event))


def test_actual_byte_limits_and_duplicate_keys():
    event = load("browser-batch.json")["events"][0]
    oversized_item = b'{' + b' ' * v2.MAX_EVENT_BYTES + wire(event)[1:]
    items = v2.parse_browser_items(b'{"events":[' + oversized_item + b']}')
    with pytest.raises(ValueError, match="event too large"):
        v2.parse_browser_item(items[0])
    with pytest.raises(ValueError, match="batch too large"):
        v2.parse_browser_items(b' ' * (v2.MAX_BATCH_BYTES + 1))
    with pytest.raises(ValueError, match="too many"):
        v2.parse_browser_items(wire({"events": [event] * 101}))
    with pytest.raises(ValueError, match="duplicate JSON key"):
        v2.parse_browser_item(v2.parse_browser_items(b'{"events":[{"x":1,"x":2}]}')[0])


def test_grant_policy_scenarios_are_not_crypto_verification():
    f = load("scenarios.json")
    header = v2.JwtHeader.model_validate_json(wire(f["header"]))
    user = v2.GrantClaims.model_validate_json(wire(f["user_collect"]))
    leader = v2.GrantClaims.model_validate_json(wire(f["leader_report"]))
    now = datetime.fromisoformat(f["now"].replace("Z", "+00:00"))
    for claims in (user, leader):
        v2.validate_fixture_claims(header, claims, now=now, issuer=f["issuer"], known_kids=set(f["known_kids"]))
    v2.authorize_fixture(user, scope="collect", course_id="backend", subject_ref=user.sub)
    v2.authorize_fixture(leader, scope="report:read", course_id="backend", subject_ref=user.sub)
    for claims, scope, course, subject in [(leader, "collect", "backend", user.sub), (user, "collect", "backend", leader.sub), (leader, "report:read", "backend-copy", user.sub), (leader, "report:read", "backend", leader.sub), (user, "report:read", "backend", user.sub)]:
        with pytest.raises(ValueError):
            v2.authorize_fixture(claims, scope=scope, course_id=course, subject_ref=subject)
    empty = leader.model_copy(update={"subject_refs": []})
    with pytest.raises(ValueError):
        v2.authorize_fixture(empty, scope="report:read", course_id="backend", subject_ref=user.sub)
    with pytest.raises(ValueError):
        v2.validate_fixture_claims(header, user, now=now + timedelta(minutes=6), issuer=f["issuer"], known_kids=set(f["known_kids"]))
    with pytest.raises(ValueError):
        v2.validate_fixture_claims(header, user, now=now, issuer=f["issuer"], known_kids={"other"})
    with pytest.raises(ValidationError):
        v2.GrantClaims.model_validate_json(wire(dict(f["user_collect"], role="leader")))
    with pytest.raises(ValidationError):
        v2.JwtHeader.model_validate_json(wire(dict(f["header"], alg="none")))


def test_hash_identity_and_ack_examples():
    event = v2.parse_browser_item(wire(load("browser-batch.json")["events"][0]))
    at = datetime(2026, 11, 16, tzinfo=timezone.utc)
    record = v2.AcceptedEvent(producer_namespace="browser-1", subject_ref=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"), source="browser", event=event, received_at=at)
    original = v2.canonical_hash(record)
    assert original == v2.canonical_hash(record.model_copy(update={"received_at": at + timedelta(seconds=2)}))
    assert original != v2.canonical_hash(record.model_copy(update={"producer_namespace": "browser-2"}))
    assert original != v2.canonical_hash(record.model_copy(update={"subject_ref": UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")}))
    assert original != v2.canonical_hash(record.model_copy(update={"event": event.model_copy(update={"course_id": "backend-copy"})}))
    f = load("scenarios.json")
    ack = v2.BatchAck.model_validate_json(wire(f["acks"]))
    assert [i.status for i in ack.items] == ["accepted", "duplicate", "conflict", "pending", "rejected"]
    assert v2.ItemAck(index=5, event_id=None, status="rejected", code="invalid")
    v2.ErrorResponse.model_validate_json(wire(f["error"]))


def test_admin_reports_user_self_and_empty_leader_roster():
    f = load("scenarios.json")
    admin = v2.GrantClaims.model_validate_json(wire(dict(
        f["leader_report"], role="admin", course_ids=[], subject_refs=[], global_read=True,
    )))
    pupil = UUID(f["user_collect"]["sub"])
    for course in ("backend", "backend_copy"):
        v2.authorize_fixture(admin, scope="report:read", course_id=course, subject_ref=pupil)
    with pytest.raises(ValueError, match="scope"):
        v2.authorize_fixture(admin, scope="collect", course_id="backend", subject_ref=pupil)
    own_report = v2.GrantClaims.model_validate_json(wire(dict(f["user_collect"], scope="report:read")))
    v2.authorize_fixture(own_report, scope="report:read", course_id="backend", subject_ref=pupil)
    with pytest.raises(ValueError, match="subject"):
        v2.authorize_fixture(own_report, scope="report:read", course_id="backend", subject_ref=admin.sub)
    empty = v2.GrantClaims.model_validate_json(wire(dict(f["leader_report"], subject_refs=[])))
    with pytest.raises(ValueError, match="subject"):
        v2.authorize_fixture(empty, scope="report:read", course_id="backend", subject_ref=pupil)


def test_interval_uses_exact_integer_milliseconds():
    event = dict(load("browser-batch.json")["events"][1], ended_at="2026-11-16T10:00:01.001Z", active_ms=1001)
    assert v2.parse_browser_item(wire(event)).active_ms == 1001
    with pytest.raises(ValidationError):
        v2.parse_browser_item(wire(dict(event, ended_at="2026-11-16T10:00:01.001001Z")))


def test_control_and_server_timestamps_require_timezone():
    controls = load("core-control.json")
    for item in controls:
        if "occurred_at" in item:
            with pytest.raises(ValidationError):
                v2.core_control_adapter.validate_json(wire(dict(item, occurred_at="2026-11-16T10:00:00")))


def test_controls_and_v1_mapping():
    controls = load("core-control.json")
    assert {v2.core_control_adapter.validate_json(wire(item)).kind for item in controls} == {"catalog_snapshot", "progress_fact", "deletion_fence"}
    with pytest.raises(ValidationError):
        v2.core_control_adapter.validate_json(wire(dict(controls[1], task_revision=None)))
    with pytest.raises(ValueError, match="mapping"):
        v2.map_v1_track("legacy track", {})
    assert v2.map_v1_track("legacy track", {"legacy track": "backend"}) == "backend"
    with pytest.raises(ValidationError):
        v2.parse_browser_item(wire(controls[1]))


def test_reports_and_bounded_query():
    f = load("scenarios.json")
    for name in ("report_zero", "report_no_data", "report_stale", "report_unavailable"):
        v2.ReportResponse.model_validate_json(wire(f[name]))
    request = {"course_id": "backend", "subject_ref": None, "from_time": "2026-01-01T00:00:00Z", "to_time": "2027-01-02T00:00:00Z", "timezone": "UTC", "limit": 100}
    v2.ReportRequest.model_validate_json(wire(request))
    for change in ({"to_time": "2027-01-03T00:00:00Z"}, {"limit": 101}, {"timezone": "No/Such_Zone"}):
        with pytest.raises(ValidationError):
            v2.ReportRequest.model_validate_json(wire(dict(request, **change)))
