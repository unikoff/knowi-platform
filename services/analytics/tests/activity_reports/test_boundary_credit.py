from datetime import timedelta
import importlib.util
from pathlib import Path

import pytest

from reference.minute_boundary_v1 import WINDOW_MS, allocate_boundary_credit
from pymentor_analytics.modules.analytics.activity.bucket import normalize_subject_bucket
from pymentor_analytics.modules.analytics.activity.calculator import _ms


def interval(start, end, course="python", section="theory"):
    return {"start_ms": start, "end_ms": end, "course_id": course,
            "lesson_id": "lesson", "section": section}


def allocate(rows, end=3 * WINDOW_MS):
    return allocate_boundary_credit(rows, range_start_ms=0, range_end_ms=end)


def amounts(rows):
    return {(r["window_start_ms"], r["course_id"], r["section"]): r["active_ms"] for r in rows}


def test_first_crossing_only_with_context_change_at_exact_boundary():
    rows = [interval(WINDOW_MS - 10_000, WINDOW_MS),
            interval(WINDOW_MS, 2 * WINDOW_MS + 10_000, "sql", "practice")]
    result = allocate(rows)
    assert amounts(result["boundary_credit"]) == {
        (0, "python", "theory"): 50_000,
    }
    assert sum(amounts(result["measured"]).values()) == WINDOW_MS + 20_000
    assert result == allocate(rows)


def test_short_crossing_credits_both_sides_and_gap_starts_new_run():
    rows = [interval(WINDOW_MS - 10_000, WINDOW_MS),
            interval(WINDOW_MS, WINDOW_MS + 20_000, "sql", "practice"),
            interval(2 * WINDOW_MS - 15_000, 2 * WINDOW_MS + 5_000, "sql")]
    result = allocate(rows)
    assert amounts(result["boundary_credit"]) == {
        (0, "python", "theory"): 50_000,
        (WINDOW_MS, "sql", "practice"): 40_000,
        (WINDOW_MS, "sql", "theory"): 45_000,
        (2 * WINDOW_MS, "sql", "theory"): 55_000,
    }


def test_boundaries_without_crossing_and_full_window_budget():
    assert not allocate([interval(0, WINDOW_MS), interval(WINDOW_MS + 1, 2 * WINDOW_MS)])["boundary_credit"]
    rows = [interval(0, WINDOW_MS - 20_000, "other"),
            interval(WINDOW_MS - 10_000, WINDOW_MS + 10_000)]
    result = allocate(rows)
    assert amounts(result["boundary_credit"])[(0, "python", "theory")] == 10_000
    for window in (0, WINDOW_MS):
        total = sum(r["active_ms"] for group in result.values() for r in group if r["window_start_ms"] == window)
        assert total <= WINDOW_MS


def test_invalid_or_overlapping_input_is_rejected():
    with pytest.raises(ValueError):
        allocate([interval(0, 100), interval(99, 200)])
    with pytest.raises(ValueError):
        allocate_boundary_credit([], range_start_ms=1, range_end_ms=WINDOW_MS)


def test_real_overlap_winners_and_moscow_midnight_are_deterministic():
    spec = importlib.util.spec_from_file_location("credit_golden", Path(__file__).parents[1] / "reference/test_activity_v1.py")
    golden = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(golden)
    boundary = golden.BASE + timedelta(hours=21)  # midnight Europe/Moscow
    first = golden.event(boundary - timedelta(seconds=20), boundary + timedelta(seconds=20), number=1)
    second = golden.event(boundary - timedelta(seconds=10), boundary + timedelta(seconds=10), number=2,
                          course="backend_copy", section="practice")
    start = boundary - timedelta(minutes=15)
    def winners(events):
        return sum((normalize_subject_bucket(events, subject_ref=golden.SUBJECT, bucket_start=b)["winner_intervals"]
                    for b in (start, boundary)), [])
    rows = winners([first, second])
    assert rows == winners([second, first])
    result = allocate_boundary_credit(rows, range_start_ms=_ms(start), range_end_ms=_ms(boundary + timedelta(minutes=15)))
    assert sum(r["active_ms"] for r in result["measured"]) == 40_000
    assert amounts(result["boundary_credit"]) == {
        (_ms(start), "backend_copy", "practice"): 40_000,
        (_ms(boundary), "backend_copy", "practice"): 40_000,
    }
