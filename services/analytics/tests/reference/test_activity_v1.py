import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from pymentor_analytics.contracts.v2 import AcceptedEvent, BrowserEvent, EmptyPayload
from reference.activity_v1 import calculate

UTC = timezone.utc
BASE = datetime(2026, 1, 1, tzinfo=UTC)
SUBJECT = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OTHER = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
FIXTURES = json.loads((Path(__file__).resolve().parents[2] / "contracts/fixtures/activity-golden-v1.json").read_text(encoding="utf-8"))


def event(start, end, *, number=1, course="backend", section="theory", subject=SUBJECT,
          action=None, received=None, namespace="browser", opened=False):
    delta = end - start
    duration_us = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    body = BrowserEvent(schema_version=2, event_id=UUID(int=number),
        type="course_opened" if opened else "activity_segment", course_id=course,
        catalog_version="v1", lesson_id=None if opened else "same-lesson",
        section=None if opened else section, session_id=UUID(int=number // 1000 + 1),
        started_at=start, ended_at=end, last_action_at=action or start,
        visible=True, focused=True, active_ms=duration_us // 1000, payload=EmptyPayload())
    return AcceptedEvent(producer_namespace=namespace, subject_ref=subject, source="browser",
                         event=body, received_at=received or end)


def expand(runs):
    result = []
    for run_index, (start, end, course, section, action) in enumerate(runs):
        # Runs longer than idle window describe new synthetic actions at each
        # 15-second observation, not continuous absence of input.
        for index, offset in enumerate(range(start, end, 15)):
            segment_end = min(offset + 15, end)
            last_action = action if end - start <= 300 else offset
            result.append(event(BASE + timedelta(seconds=offset), BASE + timedelta(seconds=segment_end),
                number=1 + run_index * 10000 + index, course=course, section=section,
                action=BASE + timedelta(seconds=last_action), namespace=f"device-{run_index}"))
    return result


def report(records, *, start=BASE, end=BASE + timedelta(hours=1), as_of=BASE + timedelta(days=8), **kwargs):
    return calculate(records, start=start, end=end, as_of=as_of, input_complete=True, **kwargs)


def test_idle_and_resume():
    f = FIXTURES["idle_resume"]
    records = expand(f["runs"])
    before = report(records, end=BASE + timedelta(seconds=480))
    after = report(records)
    assert before["total_ms"] == f["expected"]["before_resume_ms"] == 300_000
    assert after["total_ms"] == f["expected"]["after_resume_ms"] == 315_000
    assert after["visitors"] == 1
    with pytest.raises(ValidationError):
        event(BASE + timedelta(seconds=300), BASE + timedelta(seconds=315), action=BASE)


def test_hide_blur_sleep_are_gaps_then_resume_now():
    f = FIXTURES["visibility_focus_sleep"]
    actual = report(expand(f["runs"]))
    assert actual["total_ms"] == f["expected"]["total_ms"] == 60_000
    assert actual["visitors"] == 1


def test_courses_and_theory_practice():
    f = FIXTURES["courses"]
    records = expand(f["runs"])
    course = report(records, course_id="backend")
    platform = report(records)
    assert (course["theory_ms"], course["practice_ms"], course["total_ms"]) == (480_000, 720_000, 1_200_000)
    assert platform["total_ms"] == 1_800_000
    assert platform["visitors"] == course["visitors"] == 1
    assert f["expected"]["platform_total_ms"] == platform["total_ms"]
    assert report(records, course_id="backend_copy")["total_ms"] == 600_000


def test_overlaps_global_before_course_filter_and_lexical_tie():
    f = FIXTURES["overlap"]
    records = expand(f["runs"])
    assert report(records)["total_ms"] == 3_600_000
    assert report(records, course_id="backend")["total_ms"] == 0
    assert report(records, course_id="backend_copy")["total_ms"] == 3_600_000
    assert report(records, course_id="backend")["visitors"] == 1
    assert report(records)["visitors"] == 1
    assert f["expected"]["platform_total_ms"] == 3_600_000
    # Later-started context takes precedence over the larger UUID.
    later = event(BASE + timedelta(seconds=5), BASE + timedelta(seconds=10), number=1, course="backend_copy")
    earlier = event(BASE, BASE + timedelta(seconds=15), number=999, course="backend")
    assert report([later, earlier], course_id="backend")["total_ms"] == 10_000
    assert report([later, earlier], course_id="backend_copy")["total_ms"] == 5_000


def test_latest_segment_start_can_change_attribution_at_heartbeat():
    # A starts at 0, B at 5; A's next observation starts at 15 and wins.
    a1 = event(BASE, BASE + timedelta(seconds=15), number=1)
    a2 = event(BASE + timedelta(seconds=15), BASE + timedelta(seconds=30), number=2)
    b = event(BASE + timedelta(seconds=5), BASE + timedelta(seconds=25), number=10001, course="backend_copy")
    assert report([a2, b, a1], course_id="backend")["total_ms"] == 20_000
    assert report([a2, b, a1], course_id="backend_copy")["total_ms"] == 10_000


def test_duplicate_conflict_out_of_order_and_lateness():
    first = event(BASE, BASE + timedelta(seconds=15), number=1)
    duplicate = first.model_copy(update={"received_at": first.received_at + timedelta(seconds=1)})
    conflicting = event(BASE, BASE + timedelta(seconds=15), number=1, section="practice", received=BASE + timedelta(seconds=17))
    second = event(BASE + timedelta(seconds=15), BASE + timedelta(seconds=30), number=2)
    result = report([conflicting, second, duplicate, first])
    assert result["total_ms"] == 30_000 and result["practice_ms"] == 0
    assert result["admission"] == {"accepted": 2, "duplicate": 1, "conflict": 1, "late": 0, "future_skew": 0, "deleted": 0}
    assert result["quality"] == "partial"
    late = event(BASE, BASE + timedelta(seconds=15), number=3, received=BASE + timedelta(days=7, seconds=16))
    edge = event(BASE + timedelta(seconds=15), BASE + timedelta(seconds=30), number=4, received=BASE + timedelta(days=7, seconds=30))
    result = report([late, edge])
    assert result["total_ms"] == 15_000 and result["admission"]["late"] == 1
    assert result["quality"] == "partial"
    snapshot = report([second], as_of=BASE + timedelta(seconds=20))
    assert snapshot["total_ms"] == 0 and snapshot["quality"] == "partial"
    future = event(BASE, BASE + timedelta(seconds=15), received=BASE - timedelta(seconds=106))
    assert report([future])["admission"]["future_skew"] == 1
    future_edge = event(BASE, BASE + timedelta(seconds=15), received=BASE - timedelta(seconds=105))
    assert report([future_edge])["total_ms"] == 15_000
    arrived_later = event(BASE, BASE + timedelta(seconds=15), number=5, received=BASE + timedelta(hours=2))
    arrived_first = event(BASE + timedelta(seconds=15), BASE + timedelta(seconds=30), number=6)
    assert report([arrived_later, arrived_first])["total_ms"] == 30_000
    assert report([arrived_later, arrived_first], as_of=BASE + timedelta(hours=1))["total_ms"] == 15_000


def test_midnight_moscow_and_dst_new_york():
    moscow = event(datetime(2026, 1, 1, 20, 59, 30, tzinfo=UTC), datetime(2026, 1, 1, 21, 0, 30, tzinfo=UTC))
    result = report([moscow], start=datetime(2026, 1, 1, 20, tzinfo=UTC), end=datetime(2026, 1, 1, 22, tzinfo=UTC))
    assert {day: row["total_ms"] for day, row in result["daily"].items()} == {"2026-01-01": 30_000, "2026-01-02": 30_000}
    # DST starts on March 8, but local midnight occurs at 05:00 UTC on that date.
    spring = event(datetime(2026, 3, 8, 4, 59, 30, tzinfo=UTC), datetime(2026, 3, 8, 5, 0, 30, tzinfo=UTC))
    result = report([spring], start=datetime(2026, 3, 8, 4, tzinfo=UTC), end=datetime(2026, 3, 9, 5, tzinfo=UTC), as_of=datetime(2026, 3, 17, tzinfo=UTC), timezone_name="America/New_York")
    assert {day: row["total_ms"] for day, row in result["daily"].items()} == {"2026-03-07": 30_000, "2026-03-08": 30_000}
    next_midnight = event(datetime(2026, 3, 9, 3, 59, 30, tzinfo=UTC), datetime(2026, 3, 9, 4, 0, 30, tzinfo=UTC))
    result = report([next_midnight], start=datetime(2026, 3, 8, 4, tzinfo=UTC), end=datetime(2026, 3, 9, 5, tzinfo=UTC), as_of=datetime(2026, 3, 17, tzinfo=UTC), timezone_name="America/New_York")
    assert {day: row["total_ms"] for day, row in result["daily"].items()} == {"2026-03-08": 30_000, "2026-03-09": 30_000}
    # The fall clock repeats 01:00, but these are 60 real elapsed seconds.
    fall = event(datetime(2026, 11, 1, 5, 59, 30, tzinfo=UTC), datetime(2026, 11, 1, 6, 0, 30, tzinfo=UTC))
    result = report([fall], start=datetime(2026, 11, 1, 4, tzinfo=UTC), end=datetime(2026, 11, 2, 5, tzinfo=UTC), as_of=datetime(2026, 11, 10, tzinfo=UTC), timezone_name="America/New_York")
    assert {day: row["total_ms"] for day, row in result["daily"].items()} == {"2026-11-01": 60_000}


def test_empty_zero_subject_delete_and_quality():
    assert report([])["visitors"] == 0 and report([])["total_ms"] == 0
    opened = event(BASE, BASE, opened=True)
    result = report([opened])
    assert (result["visitors"], result["total_ms"], result["quality"]) == (1, 0, "complete")
    assert report([opened], deleted_subjects={SUBJECT})["visitors"] == 0
    assert report([opened], deleted_subjects={SUBJECT})["admission"]["deleted"] == 1
    assert report([opened], subject_ref=OTHER)["visitors"] == 0
    segment = event(BASE, BASE + timedelta(seconds=15), number=2)
    other = event(BASE, BASE + timedelta(seconds=15), number=3, subject=OTHER)
    assert report([segment, other])["visitors"] == 2
    after_delete = report([segment, other], deleted_subjects={SUBJECT})
    assert (after_delete["visitors"], after_delete["total_ms"]) == (1, 15_000)
    assert calculate([opened], start=BASE, end=BASE + timedelta(hours=1), as_of=BASE + timedelta(days=8))["quality"] == "partial"
    end = BASE + timedelta(hours=1)
    assert report([opened], end=end, as_of=end + timedelta(days=7))["quality"] == "partial"
    assert report([opened], end=end, as_of=end + timedelta(days=7, seconds=60))["quality"] == "complete"


def test_submillisecond_start_quantized_and_exact_clipping():
    start = BASE + timedelta(microseconds=999)
    one = event(start, start + timedelta(milliseconds=1001))
    result = report([one], start=BASE + timedelta(milliseconds=1), end=BASE + timedelta(milliseconds=1000))
    assert result["total_ms"] == 999
    with pytest.raises(ValueError, match="millisecond"):
        report([one], start=start)
    with pytest.raises(ValidationError):
        event(BASE, BASE + timedelta(microseconds=1001))
