import random

import pytest

from reference.minute_boundary_v1 import allocate_boundary_credit
from reference.minute_window_v1 import WINDOW_MS, window_boundary_credit


def row(start, end, course="python"):
    return {"start_ms": start, "end_ms": end, "course_id": course,
            "lesson_id": "lesson", "section": "theory"}


def snapshots(rows, x=2 * WINDOW_MS):
    result = {}
    for start in (x - 2 * WINDOW_MS, x - WINDOW_MS, x, x + WINDOW_MS):
        clipped = [dict(r, start_ms=max(r["start_ms"], start), end_ms=min(r["end_ms"], start + WINDOW_MS))
                   for r in rows if r["start_ms"] < start + WINDOW_MS and r["end_ms"] > start]
        result[start] = {"state": "ready", "winners": clipped} if clipped else {"state": "absent"}
    return result


def oracle(rows, x=2 * WINDOW_MS):
    result = allocate_boundary_credit(rows, range_start_ms=x - 2 * WINDOW_MS, range_end_ms=x + 2 * WINDOW_MS)
    return [{k: v for k, v in item.items() if k != "window_start_ms"}
            for item in result["boundary_credit"] if item["window_start_ms"] == x]


def test_unknown_and_proven_absence_are_distinct():
    x = 2 * WINDOW_MS
    source = snapshots([])
    assert window_boundary_credit(x, source) == {"status": "ready", "boundary_credit": []}
    source[x - WINDOW_MS] = {"state": "unknown"}
    assert window_boundary_credit(x, source) == {"status": "unavailable", "boundary_credit": None}
    with pytest.raises(ValueError):
        window_boundary_credit(x, {x: {"state": "absent"}})


def test_first_crossing_at_window_start_and_budget_order():
    x = 2 * WINDOW_MS
    rows = [row(x - 10_000, x + 10_000),
            row(x + 20_000, x + WINDOW_MS - 10_000, "sql"),
            row(x + WINDOW_MS - 10_000, x + WINDOW_MS + 10_000, "java")]
    result = window_boundary_credit(x, snapshots(rows))
    assert result["boundary_credit"] == oracle(rows)
    assert sum(item["active_ms"] for item in result["boundary_credit"]) == 10_000
    assert result["boundary_credit"][0]["course_id"] == "python"


def test_prior_crossing_suppresses_later_crossing_and_half_open_edges():
    x = 2 * WINDOW_MS
    for rows in ([row(x - WINDOW_MS - 5, x + 10)],
                 [row(x - 10, x), row(x + 1, x + 10)],
                 [row(x, x + WINDOW_MS)]):
        assert window_boundary_credit(x, snapshots(rows))["boundary_credit"] == oracle(rows) == []


def test_random_runs_match_independent_complete_range_oracle():
    rng = random.Random(13743)
    x = 2 * WINDOW_MS
    for _ in range(300):
        rows = []
        cursor = rng.randrange(0, 100_000)
        while cursor < 4 * WINDOW_MS:
            end = min(4 * WINDOW_MS, cursor + rng.randrange(1, 1_000_000))
            rows.append(row(cursor, end, rng.choice(("python", "sql"))))
            cursor = end + rng.choice((0, 1, rng.randrange(1, 100_000)))
        result = window_boundary_credit(x, snapshots(rows))
        assert result["boundary_credit"] == oracle(rows)


def test_rejects_overlapping_or_out_of_bucket_snapshot():
    x = 2 * WINDOW_MS
    source = snapshots([])
    source[x] = {"state": "ready", "winners": [row(x, x + 20), row(x + 19, x + 30)]}
    with pytest.raises(ValueError):
        window_boundary_credit(x, source)
