"""Verify independent browser/HTTP/psql evidence from the disposable CORP-607 target."""
from collections import defaultdict
from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path

import pytest


EXPECTED = Path(__file__).with_name("fixtures") / "corp_607_expected.json"
FIRST = "first"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def test_independent_window_manifest():
    fixture = read_json(EXPECTED)
    computed = defaultdict(lambda: defaultdict(int))
    for interval in fixture["intervals"]:
        cursor = datetime.fromisoformat(interval["start"])
        end = datetime.fromisoformat(interval["end"])
        assert cursor < end and cursor.utcoffset() == end.utcoffset() == timedelta(hours=3)
        while cursor < end:
            slot_no = (cursor.hour * 60 + cursor.minute) // 15 + 1
            border = cursor.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=slot_no * 15)
            segment_end = min(border, end)
            active_ms = (segment_end - cursor) // timedelta(milliseconds=1)
            computed[interval["subject"], cursor.date().isoformat(), slot_no][interval["context"]] += active_ms
            cursor = segment_end
    expected = {(row["subject"], row["date"], row["slot_no"]): row["active_ms"] for row in fixture["expected_windows"]}
    assert len(expected) == len(fixture["expected_windows"]) == fixture["expected_counts"]["manifest_keys"] == 7
    assert dict(computed) == expected
    assert {name for name, _, _ in computed} == {"first", "multi", "lost"}
    assert expected["first", "2026-11-27", 41] == {"theory": 60000}
    assert fixture["live_first"]["stats"] == [{
        "tool_id": "theory", "course_id": "backend",
        "lesson_id": "lesson-64c9d597a164bbdb4f145d5a", "active_ms": 60000,
    }]
    assert len({row["subject"] for row in fixture["expected_windows"] if row["present_before_repair"]}) == 2
    unknowns = [row for row in fixture["expected_windows"] if row["comparison"] == "unknown"]
    assert len(unknowns) == 1
    assert unknowns[0]["active_ms"] == {"theory": 40000}
    assert unknowns[0]["v2"] == unknowns[0]["v3"] == "no durable ack"
    assert unknowns[0]["present_before_repair"] is False


def assert_report(result, expected, activity_date):
    assert result["status"] == 200
    body = result["body"]
    assert body["schema_version"] == expected["schema_version"]
    assert body["source"] == expected["source"]
    assert body["scope"] == expected["scope"]
    assert {key: body["summary"][key] for key in expected["expected_after_v2_only"]} == expected["expected_after_v2_only"]
    assert body["period"]["days"] == 7
    assert body["period"]["timezone"] == "Europe/Moscow"
    row, = [entry for entry in body["days"] if entry["date"] == activity_date]
    assert (row["active_seconds"], row["theory_seconds"], row["practice_seconds"]) == (60, 60, None)
    return {key: value for key, value in body.items() if key not in expected["equality_excludes"]}


def assert_first_window_evidence(evidence_dir):
    fixture = read_json(EXPECTED)
    capture = read_json(evidence_dir / "first-window.json")
    assert capture["repair_enabled"] is False
    assert capture["readiness"] == {"core": 200, "collector": 200, "query": 200, "privacy": 200}
    assert capture["versions"]["analytics_schema"] == "a015"
    assert capture["subject_ref"] == fixture["subjects"][FIRST]
    activity_date = date.fromisoformat(capture["activity_date"])
    assert capture["browser"]["url"] == "http://127.0.0.1:52707/backend"
    assert capture["browser"]["start_clock"] == f"{activity_date}T10:00:00+03:00"
    assert capture["browser"]["end_clock"] == f"{activity_date}T10:01:00+03:00"
    assert capture["browser"]["focused_at_start"] is True
    assert capture["browser"]["focused_at_end"] is False

    raw_file = evidence_dir / "first-window-wire.json"
    raw = raw_file.read_bytes()
    initial, = json.loads(raw)["windows"]
    actual_stats = fixture["live_first"]["stats"]
    assert initial["stats"] == actual_stats
    assert initial["activity_date"] == str(activity_date) and initial["slot_no"] == 41
    assert capture["request_body_sha256"] == hashlib.sha256(raw).hexdigest() == capture["replayed_body_sha256"]

    committed, = capture["commit"]["body"]["items"]
    duplicate, = capture["duplicate"]["body"]["items"]
    assert capture["commit"]["status"] == capture["duplicate"]["status"] == 200
    assert committed["status"] == "committed"
    assert duplicate["status"] == "duplicate"
    for ack in (committed, duplicate):
        for key in ("writer_id", "writer_generation", "server_epoch", "seq", "activity_date", "slot_no"):
            assert ack[key] == initial[key]
        assert ack["index"] == 0
        assert ack["confirmed"]["stats"] == actual_stats
    assert duplicate["confirmed"] == committed["confirmed"]
    assert duplicate["payload_hash"] == committed["payload_hash"]

    before_row, = capture["postgres_before_replay"]
    after_row, = capture["postgres_after_replay"]
    assert before_row == after_row == {
        "student_id": fixture["subjects"][FIRST],
        "activity_date": str(activity_date),
        "slot_no": 41,
        "stats": actual_stats,
        "revision": committed["confirmed"]["revision"],
    }
    assert capture["postgres_subject_count"] == capture["postgres_window_count"] == 1
    assert capture["outcome"] == "PASS"
    events = [event for event in capture["events"] if event["lesson_id"] == actual_stats[0]["lesson_id"]]
    assert len(events) == 1 and events[0]["active_ms"] == 60000
    assert any(ack["status"] == 200 and ack["body"]["items"][0]["status"] == "accepted" for ack in capture["v2_acks"])
    expected_report = fixture["first_report"]
    baseline = assert_report(capture["baseline_report"], expected_report, str(activity_date))
    assert assert_report(capture["shadow_report"], expected_report, str(activity_date)) == baseline
    assert assert_report(capture["duplicate_report"], expected_report, str(activity_date)) == baseline


def test_shadow_e2e():
    value = os.environ.get("CORP607_EVIDENCE_DIR")
    if value is None:
        pytest.skip("Set CORP607_EVIDENCE_DIR to the actual browser/HTTP/psql artifact directory.")
    assert_first_window_evidence(Path(value))

