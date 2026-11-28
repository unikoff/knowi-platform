"""Read-only independent history oracle. It does not call the publishing sweep."""
from collections import Counter
from datetime import datetime, time, timedelta, timezone
import json
from zoneinfo import ZoneInfo

from sqlalchemy import text

from pymentor_analytics.contracts.v2 import AcceptedEvent
from pymentor_analytics.contracts.windows_v1 import WindowStat, canonical_stats
from pymentor_analytics.modules.analytics.activity.bucket import MAX_BUCKET_FACTS, MAX_BUCKET_INPUT_BYTES, BucketOverflow

MSK = ZoneInfo("Europe/Moscow")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
SOURCE = text("""SELECT accepted_event FROM analytics.activity_facts
    WHERE subject_ref=:subject AND source_kind IN ('browser_v2','browser_tool_v1')
      AND accepted_event#>>'{event,type}' IN ('activity_segment','tool_segment')
      AND started_at>=:first AND started_at<:last
    ORDER BY started_at,producer_namespace,event_id
    LIMIT :limit""")


def _millisecond(value):
    return (value - EPOCH) // timedelta(milliseconds=1)


def slot_interval(key):
    midnight = datetime.combine(key.activity_date, time.min, MSK)
    start = (midnight + timedelta(minutes=(key.slot_no - 1) * 15)).astimezone(timezone.utc)
    return start, start + timedelta(minutes=15)


def _context(event):
    if event.type == "activity_segment":
        return event.section, event.course_id, event.lesson_id
    if event.type == "tool_segment":
        return event.payload.tool_id, None if event.course_id == "platform-tools" else event.course_id, None
    return None


def independent_stats(records, key):
    """Visit lower-priority intervals only where no higher-priority interval assigned time."""
    start, stop = slot_interval(key)
    first, last = _millisecond(start), _millisecond(stop)
    intervals = []
    identities = set()
    endpoints = {first, last}
    for record in records:
        event = record.event
        if record.subject_ref != key.student_id:
            raise ValueError("oracle received a fact for another lifecycle")
        identity = (record.producer_namespace, event.event_id)
        if identity in identities:
            raise ValueError("oracle received a duplicate accepted identity")
        identities.add(identity)
        context = _context(event)
        left, right = max(first, _millisecond(event.started_at)), min(last, _millisecond(event.ended_at))
        if context is not None and left < right:
            rank = event.started_at.astimezone(timezone.utc), event.event_id.int, record.producer_namespace
            intervals.append((rank, left, right, context))
            endpoints.update((left, right))

    boundaries = sorted(endpoints)
    offsets = {value: index for index, value in enumerate(boundaries)}
    unassigned = list(range(len(boundaries)))

    def next_unassigned(index):
        while unassigned[index] != index:
            unassigned[index] = unassigned[unassigned[index]]
            index = unassigned[index]
        return index

    measures = Counter()
    for _, left, right, context in sorted(intervals, key=lambda item: item[0], reverse=True):
        current = next_unassigned(offsets[left])
        stop_index = offsets[right]
        while current < stop_index:
            measures[context] += boundaries[current + 1] - boundaries[current]
            unassigned[current] = next_unassigned(current + 1)
            current = unassigned[current]
    return canonical_stats(tuple(WindowStat(tool_id=tool, course_id=course, lesson_id=lesson, active_ms=ms)
                                 for (tool, course, lesson), ms in measures.items()))


async def read_independent_stats(session, key):
    start, stop = slot_interval(key)
    rows = (await session.execute(SOURCE, {"subject": key.student_id, "first": start - timedelta(minutes=1),
                                          "last": stop, "limit": MAX_BUCKET_FACTS + 1})).scalars().all()
    encoded = [json.dumps(row, separators=(",", ":")) for row in rows]
    if len(encoded) > MAX_BUCKET_FACTS or sum(len(row.encode()) for row in encoded) > MAX_BUCKET_INPUT_BYTES:
        raise BucketOverflow("oracle source exceeds the historical bucket bound")
    return independent_stats((AcceptedEvent.model_validate_json(row) for row in encoded), key)
