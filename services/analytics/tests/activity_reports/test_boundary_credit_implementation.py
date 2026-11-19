"""Production bounded calculation agrees with the independent interval oracle."""
from reference.minute_boundary_v1 import allocate_boundary_credit
from pymentor_analytics.modules.analytics.activity.boundary_credit import WINDOW_MS, window_boundary_credit


def test_crossing_and_shared_window_budget_match_oracle():
    boundary = 2 * WINDOW_MS
    winners = [
        {"start_ms": boundary-20_000, "end_ms": boundary, "course_id": "a", "lesson_id": "1", "section": "theory"},
        {"start_ms": boundary, "end_ms": boundary+20_000, "course_id": "b", "lesson_id": "2", "section": "practice"},
    ]
    snapshots = {}
    for offset in (-2, -1, 0, 1):
        start = boundary + offset * WINDOW_MS
        snapshots[start] = {"state": "ready", "winners": [w for w in winners if start <= w["start_ms"] < start+WINDOW_MS]}
    result = window_boundary_credit(boundary, snapshots)
    oracle = allocate_boundary_credit(winners, range_start_ms=0, range_end_ms=4*WINDOW_MS)
    expected = [{k: v for k, v in row.items() if k != "window_start_ms"}
                for row in oracle["boundary_credit"] if row["window_start_ms"] == boundary]
    assert result == {"status": "ready", "boundary_credit": expected}
