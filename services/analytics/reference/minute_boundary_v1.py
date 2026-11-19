"""Reference boundary credit from complete, canonical measured winner intervals.

Inputs cover the declared range and already resolve overlaps across all courses.
The caller must supply complete runs when a range edge could truncate a run;
this pure helper neither loads facts nor persists or publishes its result.
"""
from collections import defaultdict

WINDOW_MS = 900_000
MINUTE_MS = 60_000


def allocate_boundary_credit(winners, *, range_start_ms, range_end_ms):
    """Return measured and additional credit by window and winning context.

    The first strictly interior window boundary of each contiguous measured run
    receives a rounded minute on each side. Credit fills only the missing portion
    of that side's minute and is limited by the window's remaining shared budget.
    Boundary credits compete in chronological boundary order. Input must be sorted,
    non-overlapping and fully contained in the aligned half-open supported range.
    """
    if (type(range_start_ms) is not int or type(range_end_ms) is not int
            or range_start_ms % WINDOW_MS or range_end_ms % WINDOW_MS
            or range_start_ms >= range_end_ms):
        raise ValueError("range must consist of aligned 15-minute windows")
    measured = defaultdict(int)
    runs = []
    previous_end = None
    for row in winners:
        if not isinstance(row, dict) or set(row) != {"start_ms", "end_ms", "course_id", "lesson_id", "section"}:
            raise ValueError("canonical winner interval required")
        start, end = row["start_ms"], row["end_ms"]
        if (type(start) is not int or type(end) is not int or not range_start_ms <= start < end <= range_end_ms
                or previous_end is not None and start < previous_end
                or not all(isinstance(row[k], str) and row[k] for k in ("course_id", "lesson_id", "section"))):
            raise ValueError("winner intervals must be valid, ordered and non-overlapping")
        context = (row["course_id"], row["lesson_id"], row["section"])
        cursor = start
        while cursor < end:
            window = cursor // WINDOW_MS * WINDOW_MS
            stop = min(end, window + WINDOW_MS)
            measured[(window, *context)] += stop - cursor
            cursor = stop
        if previous_end != start:
            runs.append([])
        runs[-1].append((start, end, context))
        previous_end = end

    window_measured = defaultdict(int)
    for (window, *_), amount in measured.items():
        window_measured[window] += amount
    credits = defaultdict(int)
    window_credit = defaultdict(int)
    for run in runs:
        start, end = run[0][0], run[-1][1]
        boundary = (start // WINDOW_MS + 1) * WINDOW_MS
        if boundary >= end:
            continue
        # A contiguous run covers every instant in [start,end), even when the
        # winning context changes at the boundary.
        sides = ((boundary - WINDOW_MS, max(start, boundary - MINUTE_MS), boundary, boundary - 1),
                 (boundary, boundary, min(end, boundary + MINUTE_MS), boundary))
        for window, left, right, adjacent in sides:
            context = next(item[2] for item in run if item[0] <= adjacent < item[1])
            missing = MINUTE_MS - (right - left)
            amount = min(missing, WINDOW_MS - window_measured[window] - window_credit[window])
            if amount:
                credits[(window, *context)] += amount
                window_credit[window] += amount

    def rows(values):
        return [{"window_start_ms": w, "course_id": c, "lesson_id": l, "section": s, "active_ms": amount}
                for (w, c, l, s), amount in sorted(values.items())]
    return {"measured": rows(measured), "boundary_credit": rows(credits)}
