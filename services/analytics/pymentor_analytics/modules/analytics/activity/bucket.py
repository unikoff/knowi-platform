"""Pure activity-v1 normalization of one subject's 15-minute bucket.

Input is an already-authorized, admitted, deduplicated and complete *local
snapshot* of that subject's candidate facts across every course. This function
neither establishes source coverage nor persists/fences anything. The worker must
supply crossing segments, check its subject epoch and atomically publish all five
collections. A cap overflow raises before any output is returned.
"""
from __future__ import annotations
from collections import defaultdict
import json
from datetime import datetime, timedelta
from heapq import heappop, heappush
from uuid import UUID

from pymentor_analytics.contracts.v2 import AcceptedEvent
from pymentor_analytics.modules.analytics.activity.calculator import _ms, _us

BUCKET_MS = 900_000
MAX_BUCKET_FACTS = 10_000
MAX_BUCKET_INPUT_BYTES = 8 * 1024 * 1024
MAX_BUCKET_OUTPUT_BYTES = 8 * 1024 * 1024

class BucketOverflow(ValueError):
    """Bucket cannot be safely normalized without a different bounded strategy."""


def normalize_subject_bucket(records, *, subject_ref: UUID, bucket_start: datetime):
    """Return stable lists of winners, all-course presence and compact rollups.

    Event timestamps are floored to milliseconds before clipping. For a tie
    between competing intervals, original microsecond starts remain authoritative;
    ties then use canonical event UUID and namespace, both descending.
    """
    if not isinstance(subject_ref, UUID) or not isinstance(bucket_start, datetime):
        raise ValueError("subject UUID and bucket datetime required")
    if bucket_start.utcoffset() != timedelta(0) or _us(bucket_start) % 1000 or _ms(bucket_start) % BUCKET_MS:
        raise ValueError("bucket start must be a UTC 15-minute boundary")
    lower = _ms(bucket_start)
    upper = lower + BUCKET_MS
    seen = set()
    segments = []
    intervals = []
    points = set()
    labels = set()
    size = 0
    for index, original in enumerate(records):
        if index >= MAX_BUCKET_FACTS:
            raise BucketOverflow("bucket fact limit exceeded")
        if not isinstance(original, AcceptedEvent):
            raise ValueError("accepted event required")
        raw = original.model_dump_json()
        size += len(raw.encode("utf-8"))
        if size > MAX_BUCKET_INPUT_BYTES:
            raise BucketOverflow("bucket input byte limit exceeded")
        record = AcceptedEvent.model_validate_json(raw)
        if record.subject_ref != subject_ref:
            raise ValueError("bucket contains another subject")
        event = record.event
        identity = (record.producer_namespace, event.event_id)
        if identity in seen:
            raise ValueError("bucket facts must already be deduplicated")
        seen.add(identity)
        left, right = _ms(event.started_at), _ms(event.ended_at)
        if event.type == "course_opened":
            if lower <= left < upper:
                points.add((left, event.course_id))
                labels.add((event.course_id, None))
            continue
        if left >= upper or right <= lower:
            continue
        start, end = max(lower, left), min(upper, right)
        if start >= end:
            continue
        item = {"start_ms": start, "end_ms": end, "course_id": event.course_id,
                "lesson_id": event.lesson_id, "section": event.section}
        intervals.append(item)
        labels.add((event.course_id, None))
        labels.add((event.course_id, event.lesson_id))
        segments.append((record, start, end))
    changes = defaultdict(list)
    for index, (_, start, end) in enumerate(segments):
        changes[start].append((True, index))
        changes[end].append((False, index))
    ranked = sorted(range(len(segments)), key=lambda i: (
        _us(segments[i][0].event.started_at), str(segments[i][0].event.event_id), segments[i][0].producer_namespace))
    ranks = {index: rank for rank, index in enumerate(ranked)}
    active, heap, winners = set(), [], []
    boundaries = sorted(changes)
    for offset, left in enumerate(boundaries[:-1]):
        for add, index in changes[left]:
            if add:
                active.add(index)
                heappush(heap, (-ranks[index], index))
            else:
                active.discard(index)
        while heap and heap[0][1] not in active:
            heappop(heap)
        if not heap:
            continue
        right = boundaries[offset + 1]
        event = segments[heap[0][1]][0].event
        context = (event.course_id, event.lesson_id, event.section)
        if winners and winners[-1]["end_ms"] == left and (winners[-1]["course_id"], winners[-1]["lesson_id"], winners[-1]["section"]) == context:
            winners[-1]["end_ms"] = right
        else:
            winners.append({"start_ms": left, "end_ms": right, "course_id": context[0],
                            "lesson_id": context[1], "section": context[2]})
    sums = defaultdict(int)
    for item in winners:
        sums[(item["course_id"], item["lesson_id"], item["section"])] += item["end_ms"] - item["start_ms"]
    result = {
        "winner_intervals": winners,
        "presence_intervals": sorted(intervals, key=lambda x: (x["start_ms"], x["end_ms"], x["course_id"], x["lesson_id"], x["section"])),
        "presence_points": [{"at_ms": stamp, "course_id": course} for stamp, course in sorted(points)],
        "contributions": [{"course_id": course, "lesson_id": lesson, "section": section, "active_ms": amount}
                          for (course, lesson, section), amount in sorted(sums.items())],
        "presence_labels": [{"course_id": course, "lesson_id": lesson}
                            for course, lesson in sorted(labels, key=lambda x: (x[0], x[1] is not None, x[1] or ""))],
    }
    output_size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if output_size > MAX_BUCKET_OUTPUT_BYTES:
        raise BucketOverflow("bucket output byte limit exceeded")
    return result
