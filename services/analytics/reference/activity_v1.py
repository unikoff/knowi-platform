"""Metrics v1 reference oracle over already authenticated synthetic accepted events.

This deliberately favors clarity over scale. It is not a collector or a worker.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pymentor_analytics.contracts.v2 import AcceptedEvent, MAX_INTERVAL_MS, canonical_hash

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
LATENESS = timedelta(days=7)
FUTURE_SKEW = timedelta(seconds=120)


def _microseconds(value: datetime) -> int:
    if value.utcoffset() is None:
        raise ValueError("timezone required")
    delta = value.astimezone(UTC) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _ms(value: datetime) -> int:
    return _microseconds(value) // 1000


def _boundary(value: datetime) -> int:
    if _microseconds(value) % 1000:
        raise ValueError("report boundaries must be aligned to milliseconds")
    return _ms(value)


def _date(ms: int, zone):
    return (EPOCH + timedelta(milliseconds=ms)).astimezone(zone).date()


def _pieces(start: int, end: int, zone):
    """Split a UTC millisecond interval at local calendar midnight."""
    while start < end:
        day = _date(start, zone)
        midnight = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
        boundary = _ms(midnight)
        if boundary <= start:
            raise ValueError("timezone has an unsupported skipped or ambiguous midnight")
        stop = min(end, boundary)
        yield day.isoformat(), stop - start
        start = stop


def calculate(records: list[AcceptedEvent], *, start: datetime, end: datetime,
              as_of: datetime, course_id: str | None = None,
              subject_ref=None, deleted_subjects=frozenset(),
              timezone_name: str = "Europe/Moscow", input_complete: bool = False):
    """Compute a snapshot; no authentication, persistence, ACL or runtime effects.

    Duplicate identity is (namespace, UUID). Earlier receipt wins, then hash
    lexically for a simultaneous conflicting receipt. Rejected late/skewed
    candidates do not reserve an identity. Deleted subjects are fenced before
    admission. Every context competes globally before report filters apply.
    """
    lo, hi, cutoff = _boundary(start), _boundary(end), _boundary(as_of)
    if hi <= lo:
        raise ValueError("empty or reversed report interval")
    # Core's legacy day uses a fixed +03 offset, including historical dates.
    zone = (timezone(timedelta(hours=3)) if timezone_name == "Europe/Moscow"
            else UTC if timezone_name == "UTC" else ZoneInfo(timezone_name))
    unique = {}
    counts = {"accepted": 0, "duplicate": 0, "conflict": 0,
              "late": 0, "future_skew": 0, "deleted": 0}
    # Validate the object again: model_copy(update=...) can bypass validation.
    checked = [AcceptedEvent.model_validate_json(record.model_dump_json()) for record in records]
    checked.sort(key=lambda r: (_microseconds(r.received_at), canonical_hash(r)))
    for record in checked:
        if _microseconds(record.received_at) > _microseconds(as_of):
            continue
        if record.subject_ref in deleted_subjects:
            counts["deleted"] += 1
            continue
        event = record.event
        if event.ended_at > record.received_at + FUTURE_SKEW:
            counts["future_skew"] += 1
            continue
        if record.received_at > event.ended_at + LATENESS:
            counts["late"] += 1
            continue
        key = (record.producer_namespace, event.event_id)
        digest = canonical_hash(record)
        if key in unique:
            counts["duplicate" if unique[key][0] == digest else "conflict"] += 1
            continue
        unique[key] = (digest, record)
        counts["accepted"] += 1

    accepted = [item[1] for item in unique.values()]
    # A visitor is based on accepted observations, not winning attribution.
    visitors = set()
    effective_hi = min(hi, cutoff)
    for record in accepted:
        event = record.event
        if (subject_ref is not None and record.subject_ref != subject_ref) or (course_id is not None and event.course_id != course_id):
            continue
        if event.type == "course_opened":
            in_window = lo <= _ms(event.started_at) < effective_hi
        else:
            in_window = _ms(event.started_at) < effective_hi and _ms(event.ended_at) > lo
        if in_window:
            visitors.add(record.subject_ref)

    by_subject = defaultdict(list)
    for record in accepted:
        if record.event.type == "activity_segment":
            by_subject[record.subject_ref].append(record)
    totals = {"theory_ms": 0, "practice_ms": 0, "total_ms": 0}
    daily = defaultdict(lambda: {"theory_ms": 0, "practice_ms": 0, "total_ms": 0})
    for subject, segments in by_subject.items():
        if subject_ref is not None and subject != subject_ref:
            continue
        points = sorted({point for r in segments for point in (_ms(r.event.started_at), _ms(r.event.ended_at))} | {lo, effective_hi})
        for left, right in zip(points, points[1:]):
            left, right = max(left, lo), min(right, effective_hi)
            if left >= right:
                continue
            candidates = [r for r in segments if _ms(r.event.started_at) <= left and _ms(r.event.ended_at) >= right]
            if not candidates:
                continue
            winner = max(candidates, key=lambda r: (_microseconds(r.event.started_at), str(r.event.event_id), r.producer_namespace))
            event = winner.event
            if course_id is not None and event.course_id != course_id:
                continue
            key = event.section + "_ms"
            amount = right - left
            totals[key] += amount
            totals["total_ms"] += amount
            for day, duration in _pieces(left, right, zone):
                daily[day][key] += duration
                daily[day]["total_ms"] += duration
    # A segment can start just before report end and end up to 60 seconds later.
    coverage_deadline = end + timedelta(milliseconds=MAX_INTERVAL_MS) + LATENESS
    complete = input_complete and _microseconds(as_of) >= _microseconds(coverage_deadline) and not (counts["late"] or counts["future_skew"] or counts["conflict"])
    return {"metric_version": "activity-v1" if timezone_name == "Europe/Moscow" else "activity-v1-tz-experimental", "as_of": as_of.astimezone(UTC).isoformat(),
            "timezone": timezone_name, "quality": "complete" if complete else "partial",
            "visitors": len(visitors), **totals, "daily": dict(sorted(daily.items())), "admission": counts}
