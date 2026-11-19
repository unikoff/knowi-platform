"""activity-v1 over a bounded set of already admitted unique facts.

Caller supplies every overlapping course for each subject and must enforce access,
privacy, input completeness, identity dedup and admission separately.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from heapq import heappop, heappush
from pymentor_analytics.contracts.v2 import AcceptedEvent

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MOSCOW = timezone(timedelta(hours=3))

def _us(value):
    if value.utcoffset() is None:
        raise ValueError("timezone required")
    d = value.astimezone(UTC) - EPOCH
    return ((d.days * 86400 + d.seconds) * 1000000 + d.microseconds)

def _ms(value):
    return _us(value) // 1000

def _days(left, right):
    while left < right:
        current = (EPOCH + timedelta(milliseconds=left)).astimezone(MOSCOW)
        next_midnight = datetime.combine(current.date() + timedelta(days=1), datetime.min.time(), tzinfo=MOSCOW)
        stop = min(right, _ms(next_midnight))
        yield current.date(), stop - left
        left = stop

def calculate(records, *, start, end, as_of, course_id=None, subject_ref=None, allowed_courses=None, lesson_id=None, include_breakdown=False):
    """Return measured totals and day rows, with no coverage/freshness claims."""
    if any(_us(v) % 1000 for v in (start, end, as_of)) or end <= start:
        raise ValueError("invalid millisecond aligned interval")
    if course_id is not None and allowed_courses is not None:
        raise ValueError("choose course_id or allowed_courses")
    allowed = None if allowed_courses is None else frozenset(allowed_courses)
    lo, hi = _ms(start), min(_ms(end), _ms(as_of))
    visitors, daily_visitors = set(), defaultdict(set)
    breakdown_visitors = defaultdict(set)
    breakdown_time = defaultdict(lambda: {"theory_ms": 0, "practice_ms": 0})
    subjects = defaultdict(list)
    seen = set()
    for original in records:
        record = AcceptedEvent.model_validate_json(original.model_dump_json())
        key = (record.producer_namespace, record.event.event_id)
        if key in seen:
            raise ValueError("facts must already be deduplicated")
        seen.add(key)
        if _us(record.received_at) > _us(as_of) or (subject_ref is not None and record.subject_ref != subject_ref):
            continue
        event = record.event
        left, right = _ms(event.started_at), _ms(event.ended_at)
        if event.type == "activity_segment" and left < hi and right > lo:
            subjects[record.subject_ref].append((record, max(lo,left), min(hi,right)))
        if (course_id is not None and event.course_id != course_id) or (allowed is not None and event.course_id not in allowed):
            continue
        if lesson_id is not None and event.lesson_id != lesson_id:
            continue
        if event.type == "course_opened":
            if lo <= left < hi:
                visitors.add(record.subject_ref)
                if include_breakdown:
                    breakdown_visitors[(event.course_id, None)].add(record.subject_ref)
                day = (EPOCH + timedelta(milliseconds=left)).astimezone(MOSCOW).date()
                daily_visitors[day].add(record.subject_ref)
        elif left < hi and right > lo:
            visitors.add(record.subject_ref)
            if include_breakdown:
                breakdown_visitors[(event.course_id, None)].add(record.subject_ref)
                breakdown_visitors[(event.course_id, event.lesson_id)].add(record.subject_ref)
            for day, _ in _days(max(lo,left), min(hi,right)):
                daily_visitors[day].add(record.subject_ref)
    daily = defaultdict(lambda: {"theory_ms": 0, "practice_ms": 0})
    for segments in subjects.values():
        # Sweep boundaries. Heap priority is latest original start, UUID, namespace.
        changes = defaultdict(list)
        for i, (record, left, right) in enumerate(segments):
            changes[left].append((True, i)); changes[right].append((False, i))
        active, heap = set(), []
        points = sorted(changes)
        # Integer inversion gives a max-heap including namespace lexical order.
        ranked = sorted(range(len(segments)), key=lambda i: (_us(segments[i][0].event.started_at), str(segments[i][0].event.event_id), segments[i][0].producer_namespace))
        ranks = {index: rank for rank, index in enumerate(ranked)}
        for index, point in enumerate(points[:-1]):
            for add, item in changes[point]:
                if add:
                    active.add(item); heappush(heap, (-ranks[item], item))
                else:
                    active.discard(item)
            while heap and heap[0][1] not in active:
                heappop(heap)
            if not heap:
                continue
            winner = segments[heap[0][1]][0].event
            if (course_id is not None and winner.course_id != course_id) or (allowed is not None and winner.course_id not in allowed):
                continue
            if lesson_id is not None and winner.lesson_id != lesson_id:
                continue
            if include_breakdown:
                amount = points[index + 1] - point
                breakdown_time[(winner.course_id, None)][winner.section + "_ms"] += amount
                breakdown_time[(winner.course_id, winner.lesson_id)][winner.section + "_ms"] += amount
            for day, amount in _days(point, points[index + 1]):
                daily[day][winner.section + "_ms"] += amount
    rows = []
    for day in sorted(set(daily) | set(daily_visitors)):
        values = daily[day]
        rows.append({"date": day, "unique_visitors": len(daily_visitors[day]),
                     "active_ms": values["theory_ms"] + values["practice_ms"], **values})
    theory = sum(row["theory_ms"] for row in rows)
    practice = sum(row["practice_ms"] for row in rows)
    result = {"totals": {"unique_visitors": len(visitors), "active_ms": theory + practice,
                         "theory_ms": theory, "practice_ms": practice}, "daily": rows}
    if include_breakdown:
        details = []
        for course, lesson in sorted(set(breakdown_visitors) | set(breakdown_time), key=lambda k: (k[0], k[1] is not None, k[1] or "")):
            values = breakdown_time[(course, lesson)]
            details.append({"level": "course" if lesson is None else "lesson", "course_id": course,
                            "lesson_id": lesson, "unique_visitors": len(breakdown_visitors[(course, lesson)]),
                            "active_ms": values["theory_ms"] + values["practice_ms"], **values})
        result["breakdown"] = details
    return result
