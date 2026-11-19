"""Bounded all-course rollup of already normalized bucket contributions."""
from collections import defaultdict
import json
import re

from pymentor_analytics.modules.analytics.activity.projection_ports import ProjectionLimit

MAX_ITEMS = 1_000_000
MAX_BYTES = 64 * 1024 * 1024
MAX_BUCKETS = 96
MAX_BIGINT = 2**63 - 1
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


def _identifier(value):
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def normalize_day(records):
    return _normalize(records, bucket_duration_limit=900_000)


def _normalize(records, *, bucket_duration_limit):
    totals = defaultdict(int)
    labels = set()
    count = items = size = 0
    for record in records:
        count += 1
        if count > MAX_BUCKETS:
            raise ProjectionLimit("day bucket limit exceeded")
        if not isinstance(record, dict) or set(record) != {"contributions", "presence_labels", "compact_item_count", "compact_json_bytes"}:
            raise ValueError("invalid day input")
        contributions, presence = record["contributions"], record["presence_labels"]
        n, b = record["compact_item_count"], record["compact_json_bytes"]
        if (not isinstance(contributions, list) or not isinstance(presence, list)
                or type(n) is not int or n != len(contributions) + len(presence)
                or type(b) is not int or b < 0):
            raise ValueError("invalid bucket summary")
        items += n
        size += b
        if items > MAX_ITEMS or size > MAX_BYTES:
            raise ProjectionLimit("day source limit exceeded")
        seen = set()
        bucket_total = 0
        for item in contributions:
            if (not isinstance(item, dict) or set(item) != {"course_id", "lesson_id", "section", "active_ms"}
                    or not _identifier(item["course_id"]) or not _identifier(item["lesson_id"])
                    or item["section"] not in {"theory", "practice"}
                    or type(item["active_ms"]) is not int or not 0 < item["active_ms"] <= MAX_BIGINT):
                raise ValueError("invalid day contribution")
            key = (item["course_id"], item["lesson_id"], item["section"])
            if key in seen:
                raise ValueError("duplicate bucket contribution")
            seen.add(key)
            bucket_total += item["active_ms"]
            if bucket_total > bucket_duration_limit:
                raise ProjectionLimit("bucket duration limit exceeded")
            totals[key] += item["active_ms"]
            if totals[key] > MAX_BIGINT:
                raise ProjectionLimit("day numeric limit exceeded")
        seen_labels = set()
        for item in presence:
            if (not isinstance(item, dict) or set(item) != {"course_id", "lesson_id"}
                    or not _identifier(item["course_id"]) or (item["lesson_id"] is not None and not _identifier(item["lesson_id"]))):
                raise ValueError("invalid day presence")
            key = (item["course_id"], item["lesson_id"])
            if key in seen_labels:
                raise ValueError("duplicate bucket presence")
            seen_labels.add(key)
            labels.add(key)
        if any((course, None) not in seen_labels or (course, lesson) not in seen_labels
               for course, lesson, _section in seen):
            raise ValueError("contribution without bucket presence")
    if sum(totals.values()) > 86_400_000:
        raise ProjectionLimit("day duration limit exceeded")
    result = {"contributions": [{"course_id": c, "lesson_id": l, "section": s, "active_ms": v}
                                 for (c, l, s), v in sorted(totals.items())],
              "presence_labels": [{"course_id": c, "lesson_id": l}
                                  for c, l in sorted(labels, key=lambda x: (x[0], x[1] is not None, x[1] or ""))],
              "source_bucket_count": count, "source_compact_items": items, "source_compact_bytes": size}
    if len(result["contributions"]) + len(result["presence_labels"]) > MAX_ITEMS:
        raise ProjectionLimit("day output item limit exceeded")
    if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_BYTES:
        raise ProjectionLimit("day output byte limit exceeded")
    return result
