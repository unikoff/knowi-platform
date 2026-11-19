"""Exercise the query's portable rollups; PostgreSQL plan/execution needs a separate test."""
import random
import sqlite3
import json
import importlib.util
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pytest

from pymentor_analytics.adapters.postgres.activity_projection_reports import _SNAPSHOT


def _body(name, following):
    return _SNAPSHOT.split(f"\n{name} AS MATERIALIZED (\n", 1)[1].split(f"\n),\n{following}", 1)[0].replace(
        "course_id=ANY(CAST(:courses AS varchar[]))", "course_id=:course"
    )


@pytest.mark.parametrize("bounds", [(100, 2000), (0, 1800), (100, 100)])
@pytest.mark.parametrize("invalid", [False, True])
@pytest.mark.parametrize("pending", [False, True])
def test_single_pass_bucket_stats_preserve_edge_limits_and_projection_state(bounds, invalid, pending):
    lo, hi = bounds
    rows = [(0, 3, 7, 10, 20, False, False), (900, 5, 11, 30, 40, invalid, False),
            (1800, 13, 17, 50, 60, False, pending)]
    body = _body("scoped_stats", "precheck").replace("bool_or(", "max(")
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE scoped(bucket_ms INTEGER, compact_item_count INTEGER, edge_item_count INTEGER, compact_json_bytes INTEGER, edge_json_bytes INTEGER, invalid BOOLEAN, pending BOOLEAN)")
        db.execute("CREATE TABLE params(lo_ms INTEGER, hi_ms INTEGER)")
        db.execute("INSERT INTO params VALUES(?,?)", (lo, hi))
        db.executemany("INSERT INTO scoped VALUES(?,?,?,?,?,?,?)", rows)
        result = db.execute(body, {"bucket_ms": 900}).fetchone()
    expected = [0, 0, 0, 0]
    for start, compact_count, edge_count, compact_bytes, edge_bytes, _, _ in rows:
        if lo < hi and start < hi and start + 900 > lo:
            if lo <= start and start + 900 <= hi:
                expected[0] += compact_count
                expected[2] += compact_bytes
            else:
                expected[1] += edge_count
                expected[3] += edge_bytes
    assert result == (len(rows), invalid, pending, *expected)


def test_single_pass_stats_match_previous_form_on_randomized_boundaries():
    rng = random.Random(8519)
    body = _body("scoped_stats", "precheck").replace("bool_or(", "max(")
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE scoped(bucket_ms INTEGER, compact_item_count INTEGER, edge_item_count INTEGER, compact_json_bytes INTEGER, edge_json_bytes INTEGER, invalid BOOLEAN, pending BOOLEAN)")
        db.execute("CREATE TABLE params(lo_ms INTEGER, hi_ms INTEGER)")
        db.execute("INSERT INTO params VALUES(0,0)")
        previous = """SELECT
          COALESCE(sum(CASE WHEN p.lo_ms<=b.bucket_ms AND b.bucket_ms+:bucket_ms<=p.hi_ms THEN b.compact_item_count ELSE 0 END),0),
          COALESCE(sum(CASE WHEN p.lo_ms>b.bucket_ms OR b.bucket_ms+:bucket_ms>p.hi_ms THEN b.edge_item_count ELSE 0 END),0),
          COALESCE(sum(CASE WHEN p.lo_ms<=b.bucket_ms AND b.bucket_ms+:bucket_ms<=p.hi_ms THEN b.compact_json_bytes ELSE 0 END),0),
          COALESCE(sum(CASE WHEN p.lo_ms>b.bucket_ms OR b.bucket_ms+:bucket_ms>p.hi_ms THEN b.edge_json_bytes ELSE 0 END),0)
          FROM scoped b CROSS JOIN params p
          WHERE p.lo_ms<p.hi_ms AND b.bucket_ms<p.hi_ms AND b.bucket_ms+:bucket_ms>p.lo_ms"""
        for _ in range(120):
            lo = rng.randrange(0, 5000)
            hi = lo + rng.randrange(0, 5000)
            rows = [(i * 900, *(rng.choice((None, 0, rng.randrange(1, 100))) for _ in range(4)),
                     rng.choice((False, False, True, None)),
                     rng.choice((False, False, True, None))) for i in range(rng.randrange(0, 12))]
            db.execute("DELETE FROM scoped")
            db.executemany("INSERT INTO scoped VALUES(?,?,?,?,?,?,?)", rows)
            db.execute("UPDATE params SET lo_ms=?,hi_ms=?", (lo, hi))
            args = {"bucket_ms": 900}
            count, invalid, pending, *counts = db.execute(body, args).fetchone()
            assert count == len(rows)
            assert bool(invalid) == any(row[-2] is True for row in rows)
            assert bool(pending) == any(row[-1] is True for row in rows)
            assert tuple(counts) == db.execute(previous, args).fetchone()


def test_grouped_full_arrays_match_original_expansion_with_edges_and_large_values():
    rng = random.Random(619)
    # Different JSON array order is deliberately retained. PostgreSQL JSONB
    # normalizes object keys; sort_keys models that equality for SQLite here.
    common = [
        {"course_id": "backend", "lesson_id": "lesson-1", "section": "theory", "active_ms": 2**62},
        {"course_id": "backend_copy", "lesson_id": "lesson-1", "section": "practice", "active_ms": 17},
    ]
    labels = [{"course_id": "backend", "lesson_id": None},
              {"course_id": "backend_copy", "lesson_id": "lesson-1"}]
    rows = []
    for i in range(250):
        contributions = common if i % 3 else list(reversed(common))
        presence = labels if i % 4 else list(reversed(labels))
        if i % 11 == 0:  # unique arrays remain valid, rather than coalescing
            contributions = [{**common[0], "active_ms": i + 1}]
            presence = labels + [{"course_id": "backend", "lesson_id": f"lesson-{i}"}]
        rows.append((str(i % 5), f"2026-10-{i % 3 + 1:02d}",
                     json.dumps(contributions, sort_keys=True), json.dumps(presence, sort_keys=True)))
    # Edge values have already been clipped and must join the same final sums.
    edge_times = [("2026-10-01", "backend", "lesson-1", "theory", 4)]
    edge_visits = [("edge-subject", "2026-10-01", "backend", None)]
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE full_valid(subject_ref TEXT,local_day TEXT,contributions TEXT,presence_labels TEXT)")
        db.executemany("INSERT INTO full_valid VALUES(?,?,?,?)", rows)
        time_groups = db.execute(_body("full_time_groups", "full_visit_groups")).fetchall()
        visit_groups = db.execute(_body("full_visit_groups", "raw_times")).fetchall()

    def expand_times(source):
        result = defaultdict(int)
        for day, raw, copies in source:
            for item in json.loads(raw):
                if item["active_ms"] > 0:
                    key = (day, item["course_id"], item["lesson_id"], item["section"])
                    result[key] += item["active_ms"] * copies
        for day, course, lesson, section, amount in edge_times:
            result[(day, course, lesson, section)] += amount
        return dict(result)

    def expand_visits(source):
        return set(edge_visits) | {(subject, day, item["course_id"], item["lesson_id"])
            for subject, day, raw in source for item in json.loads(raw)}

    assert expand_times(time_groups) == expand_times((day, raw, 1) for _, day, raw, _ in rows)
    assert expand_visits(visit_groups) == expand_visits((subject, day, raw) for subject, day, _, raw in rows)
    assert max(expand_times(time_groups).values()) > 2**63 - 1
    assert "CAST(v.active_ms AS numeric)*b.copies" in _SNAPSHOT


def test_grouped_full_and_partial_bucket_totals_match_activity_oracle():
    from pymentor_analytics.modules.analytics.activity.bucket import normalize_subject_bucket
    from pymentor_analytics.modules.analytics.activity.calculator import calculate, _ms

    spec = importlib.util.spec_from_file_location("rollup_reference", Path(__file__).parents[1] / "reference/test_activity_v1.py")
    golden = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(golden)
    start = golden.BASE
    split = start + timedelta(minutes=15)
    end = split + timedelta(minutes=5)
    records = [
        golden.event(split-timedelta(seconds=5), split+timedelta(seconds=10), number=1),
        golden.event(split-timedelta(seconds=1), split+timedelta(seconds=10), number=2,
                     course="backend_copy", section="practice"),
        golden.event(split-timedelta(seconds=5), split+timedelta(seconds=10), number=3,
                     subject=golden.OTHER),
        golden.event(split+timedelta(seconds=20), split+timedelta(seconds=20), number=4,
                     subject=golden.OTHER, opened=True),
    ]
    rows, edges = [], []
    for subject in (golden.SUBJECT, golden.OTHER):
        facts = [item for item in records if item.subject_ref == subject]
        full = normalize_subject_bucket(facts, subject_ref=subject, bucket_start=start)
        edge = normalize_subject_bucket(facts, subject_ref=subject, bucket_start=split)
        rows.append((str(subject), "day", json.dumps(full["contributions"], sort_keys=True),
                     json.dumps(full["presence_labels"], sort_keys=True)))
        edges.append((str(subject), edge))
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE full_valid(subject_ref TEXT,local_day TEXT,contributions TEXT,presence_labels TEXT)")
        db.executemany("INSERT INTO full_valid VALUES(?,?,?,?)", rows)
        time_groups = db.execute(_body("full_time_groups", "full_visit_groups")).fetchall()
        visit_groups = db.execute(_body("full_visit_groups", "raw_times")).fetchall()
    for course in (None, "backend", "backend_copy"):
        amounts = defaultdict(int)
        visitors = set()
        for _, raw, copies in time_groups:
            for item in json.loads(raw):
                if course is None or item["course_id"] == course:
                    amounts[item["section"]] += item["active_ms"] * copies
        for subject, _, raw in visit_groups:
            if any(course is None or item["course_id"] == course for item in json.loads(raw)):
                visitors.add(subject)
        for subject, edge in edges:
            for item in edge["winner_intervals"]:
                if (course is None or item["course_id"] == course) and item["start_ms"] < _ms(end) and item["end_ms"] > _ms(split):
                    amounts[item["section"]] += min(item["end_ms"], _ms(end)) - max(item["start_ms"], _ms(split))
            if any((course is None or item["course_id"] == course) and item["start_ms"] < _ms(end)
                   and item["end_ms"] > _ms(split) for item in edge["presence_intervals"]):
                visitors.add(subject)
            if any((course is None or item["course_id"] == course) and _ms(split) <= item["at_ms"] < _ms(end)
                   for item in edge["presence_points"]):
                visitors.add(subject)
        oracle = calculate(records, start=start, end=end, as_of=end+timedelta(days=1), course_id=course)
        assert oracle["totals"] == {"unique_visitors":len(visitors), "active_ms":sum(amounts.values()),
                                   "theory_ms":amounts["theory"], "practice_ms":amounts["practice"]}


@pytest.mark.parametrize("course,lesson", [(None, None), ("backend", None), ("backend_copy", "lesson-1")])
def test_early_aggregation_preserves_totals_days_and_breakdowns(course, lesson):
    rng = random.Random(71027)
    times, visits = [], []
    for _ in range(2000):
        subject = rng.randrange(12)
        day = f"2026-10-{rng.randrange(1, 5):02d}"
        selected_course = rng.choice(("backend", "backend_copy"))
        selected_lesson = rng.choice((None, "lesson-1", "lesson-2"))
        section = rng.choice(("theory", "practice"))
        amount = rng.randrange(-2, 50)
        times.append((day, selected_course, selected_lesson, section, amount))
        visits.extend([(subject, day, selected_course, selected_lesson)] * rng.randrange(1, 4))
    params = {"all_courses": course is None, "course": course, "lesson_id": lesson}
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE raw_times(local_day TEXT, course_id TEXT, lesson_id TEXT, section TEXT, amount INTEGER)")
        db.execute("CREATE TABLE raw_visits(subject_ref INTEGER, local_day TEXT, course_id TEXT, lesson_id TEXT)")
        db.executemany("INSERT INTO raw_times VALUES(?,?,?,?,?)", times)
        db.executemany("INSERT INTO raw_visits VALUES(?,?,?,?)", visits)
        grouped_times = db.execute(_body("times", "raw_visits"), params).fetchall()
        grouped_visits = db.execute(_body("visits", "total_values"), params).fetchall()

    def allowed(item_course, item_lesson):
        return (course is None or item_course == course) and (lesson is None or item_lesson == lesson)

    # Compare every downstream grouping, including total/day distinct visitors
    # and course/lesson breakdown; raw rows model the pre-aggregation query.
    for grouping in ((), (0,), (1,), (1, 2)):
        def sums(rows):
            out = defaultdict(int)
            for day, row_course, row_lesson, section, amount in rows:
                if amount > 0 and allowed(row_course, row_lesson):
                    fields = (day, row_course, row_lesson)
                    out[(tuple(fields[i] for i in grouping), section)] += amount
            return dict(out)
        assert sums(grouped_times) == sums(times)

        def counts(rows):
            out = defaultdict(set)
            for subject, day, row_course, row_lesson in rows:
                if allowed(row_course, row_lesson) and (grouping != (1, 2) or row_lesson is not None):
                    fields = (day, row_course, row_lesson)
                    out[tuple(fields[i] for i in grouping)].add(subject)
            return {key: len(value) for key, value in out.items()}
        assert counts(grouped_visits) == counts(visits)
