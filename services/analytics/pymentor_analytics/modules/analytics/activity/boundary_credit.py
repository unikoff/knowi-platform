"""Bounded boundary-credit calculation for one measured activity window.

The caller proves each supplied state under its source-generation and privacy
fences. In particular, ``absent`` means verified absence, not a missing read.
"""
from collections import defaultdict

WINDOW_MS = 900_000
MINUTE_MS = 60_000


def window_boundary_credit(window_start_ms, snapshots):
    """Return unavailable or credit rows from four canonical winner snapshots.

    ``snapshots`` maps the four window starts X-2W through X+W to mappings
    with ``state`` of ready, absent or unknown. Ready includes ``winners``;
    absent is a proved empty bucket, while unknown includes dirty/claimed or
    unproven absence. No measured duration is returned as credit.
    """
    if type(window_start_ms) is not int or window_start_ms % WINDOW_MS:
        raise ValueError("aligned window start required")
    expected = [window_start_ms + i * WINDOW_MS for i in (-2, -1, 0, 1)]
    if not isinstance(snapshots, dict) or set(snapshots) != set(expected):
        raise ValueError("four neighbor snapshots required")
    runs = []
    previous_end = None
    measured = 0
    for start in expected:
        snapshot = snapshots[start]
        if not isinstance(snapshot, dict) or snapshot.get("state") not in {"ready", "absent", "unknown"}:
            raise ValueError("invalid snapshot state")
        state = snapshot["state"]
        if state == "unknown":
            if set(snapshot) != {"state"}:
                raise ValueError("invalid unknown snapshot")
            return {"status": "unavailable", "boundary_credit": None}
        if state == "absent":
            if set(snapshot) != {"state"}:
                raise ValueError("invalid absence snapshot")
            continue
        if set(snapshot) != {"state", "winners"} or not isinstance(snapshot["winners"], list):
            raise ValueError("invalid ready snapshot")
        for row in snapshot["winners"]:
            if not isinstance(row, dict) or set(row) != {"start_ms", "end_ms", "course_id", "lesson_id", "section"}:
                raise ValueError("canonical winner required")
            left, right = row["start_ms"], row["end_ms"]
            if (type(left) is not int or type(right) is not int or not start <= left < right <= start + WINDOW_MS
                    or previous_end is not None and left < previous_end
                    or not all(isinstance(row[k], str) and row[k] for k in ("course_id", "lesson_id", "section"))):
                raise ValueError("invalid or overlapping winners")
            context = (row["course_id"], row["lesson_id"], row["section"])
            if previous_end != left:
                runs.append([])
            runs[-1].append((left, right, context))
            previous_end = right
            if start == window_start_ms:
                measured += right - left
    credits = defaultdict(int)
    remaining = WINDOW_MS - measured
    for run in runs:
        start, end = run[0][0], run[-1][1]
        boundary = (start // WINDOW_MS + 1) * WINDOW_MS
        if boundary >= end or boundary not in (window_start_ms, window_start_ms + WINDOW_MS):
            continue
        if boundary == window_start_ms:
            observed = min(end - boundary, MINUTE_MS)
            adjacent = boundary
        else:
            observed = min(boundary - start, MINUTE_MS)
            adjacent = boundary - 1
        amount = min(MINUTE_MS - observed, remaining)
        if amount:
            context = next(item[2] for item in run if item[0] <= adjacent < item[1])
            credits[context] += amount
            remaining -= amount
    return {"status": "ready", "boundary_credit": [
        {"course_id": course, "lesson_id": lesson, "section": section, "active_ms": amount}
        for (course, lesson, section), amount in sorted(credits.items())]}
