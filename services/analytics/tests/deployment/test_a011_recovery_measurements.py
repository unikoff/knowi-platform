"""Offline checks for the explicit synthetic legacy fixture and time labels."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backup_postgres"))
import a010_core_fixture as core
from test_a011_source_down import observation_gaps


def test_core_fixture_has_null_visit_calendar_and_real_progress():
    events = core._events(uuid4())
    assert [item["kind"] for item in events] == ["legacy_activity_day", "legacy_activity_day", "lesson_progress"]
    assert events[0]["payload"]["visited_at"] is None
    assert events[0]["payload"]["activity_date"] == core.DAY_NULL.isoformat()
    assert events[1]["payload"]["visited_at"] == core.VISITED_AT
    assert events[2]["payload"]["completed_at"] == core.COMPLETED_AT


def test_received_gap_and_snapshot_age_are_distinct_and_require_order():
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    snapshot_age, received_gap = observation_gaps(snapshot_started=now, snapshot_finished=now + timedelta(seconds=2),
        loss_confirmed=now + timedelta(seconds=9), restored_received=now - timedelta(seconds=1),
        source_received=now + timedelta(seconds=5))
    assert snapshot_age == 7 and received_gap == 6
    with pytest.raises(ValueError):
        observation_gaps(snapshot_started=now, snapshot_finished=now + timedelta(seconds=2), loss_confirmed=now,
                         restored_received=now, source_received=now + timedelta(seconds=1))
    with pytest.raises(ValueError):
        observation_gaps(snapshot_started=now, snapshot_finished=now, loss_confirmed=now,
                         restored_received=now + timedelta(seconds=1), source_received=now)
