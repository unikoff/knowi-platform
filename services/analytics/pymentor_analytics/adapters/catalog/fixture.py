"""Explicit bounded synthetic catalog, supplied by the local fixture operator."""
import json
import time
from types import MappingProxyType

from pydantic import TypeAdapter

from pymentor_analytics.contracts.v2 import CatalogSnapshot
from pymentor_analytics.modules.collector.ports import IntakeUnavailable


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate catalog field")
        value[key] = item
    return value


class FixtureCatalog:
    def __init__(self, raw):
        try:
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > 128 * 1024:
                raise ValueError
            payload = json.loads(raw, object_pairs_hook=_unique)
            if not isinstance(payload, list) or not 1 <= len(payload) <= 100:
                raise ValueError
            snapshots = TypeAdapter(list[CatalogSnapshot]).validate_json(json.dumps(payload, allow_nan=False))
            index, control_ids, total = {}, set(), 0
            for snapshot in snapshots:
                key = (snapshot.course_id, snapshot.catalog_version)
                lessons = frozenset(item.lesson_id for item in snapshot.lessons)
                total += len(snapshot.lessons)
                if key in index or snapshot.control_id in control_ids or len(lessons) != len(snapshot.lessons) or total > 10000:
                    raise ValueError
                index[key] = lessons
                control_ids.add(snapshot.control_id)
            self._index = MappingProxyType(index)
        except Exception:
            raise ValueError("invalid synthetic catalog configuration") from None

    async def known(self, *, course_id, catalog_version, lesson_id, deadline):
        if time.monotonic() >= deadline:
            raise IntakeUnavailable("catalog deadline expired")
        lessons = self._index.get((course_id, catalog_version))
        return lessons is not None and (lesson_id is None or lesson_id in lessons)
