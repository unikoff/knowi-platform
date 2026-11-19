import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError
from pymentor_analytics.contracts.catalog_v1 import Snapshot, Delta, ReferenceConsumer, map_legacy_track

FIXTURE = Path(__file__).parents[2] / "contracts/catalog/snapshot.fixture.json"

def load(data):
    # JSON arrays validate as tuples on the wire under strict validation.
    return Snapshot.model_validate_json(json.dumps(data, ensure_ascii=False))

def delta(data):
    return Delta.model_validate_json(json.dumps(data, ensure_ascii=False))

def test_products_and_mapping():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    c = ReferenceConsumer()
    c.apply(load(raw))
    copy = deepcopy(raw); copy["catalog"]["course_id"] = "backend_copy"
    c.apply(load(copy))
    track = raw["catalog"]["tracks"][0]["track_id"]
    assert c.current["backend_copy"].catalog.tracks[0].track_id == track
    assert c.classify("backend", "v1", "lesson-1", "task-1", "r1") == "valid"
    assert c.classify("backend_copy", "v1", "lesson-1", "task-1", "r1") == "valid"
    assert map_legacy_track(track, {track: ("backend",)}) == "backend"
    with pytest.raises(ValueError): map_legacy_track(track, {})
    with pytest.raises(ValueError): map_legacy_track(track, {track: ("backend", "backend_copy")})

def test_history_pending_order_duplicates_and_failure():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    c = ReferenceConsumer(); first = load(raw)
    assert c.apply(first) == "applied"
    assert c.apply(first) == "duplicate"
    new = deepcopy(raw["catalog"])
    lesson = new["lessons"][0]; lesson.update(title="Новое имя", order=2, aliases=["old-lesson"])
    lesson["tasks"][0].update(task_revision="r2", aliases=["old-task"], title="Новое задание", order=2)
    d = {"schema_version": 1, "kind": "analytics_catalog_delta", "sequence": 2, "base_version": "v1", "catalog_version": "v2", "catalog": new}
    skipped = deepcopy(d); skipped.update(sequence=3, base_version="v2", catalog_version="v3")
    with pytest.raises(ValueError): c.apply(delta(skipped))
    assert c.current["backend"].catalog_version == "v1" # Core absent/retry later
    assert c.classify("backend", "v2", "lesson-1") == "pending/unknown_catalog"
    assert c.apply(delta(d)) == "applied"
    assert c.apply(delta(d)) == "duplicate"
    assert c.classify("backend", "v1", "lesson-1", "task-1", "r1") == "valid"
    assert c.classify("backend", "v2", "old-lesson", "old-task", "r2") == "valid"
    conflict = deepcopy(d); conflict["catalog"]["lessons"][0]["title"] = "Conflict"
    with pytest.raises(ValueError): c.apply(delta(conflict))
    deleted = deepcopy(d); deleted.update(sequence=3, base_version="v2", catalog_version="v3")
    deleted["catalog"]["lessons"][0]["tasks"][0]["tombstone"] = True
    assert c.apply(delta(deleted)) == "applied"
    assert c.classify("backend", "v3", "lesson-1", "task-1", "r2") == "invalid_identity"
    assert c.classify("backend", "v2", "lesson-1", "task-1", "r2") == "valid"
    dropped = deepcopy(deleted); dropped.update(sequence=4, base_version="v3", catalog_version="v4")
    dropped["catalog"]["lessons"][0]["tasks"] = []
    with pytest.raises(ValueError): c.apply(delta(dropped))
    assert c.current["backend"].catalog_version == "v3"

def test_reject_ambiguous_and_sensitive_fields():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    raw["catalog"]["lessons"][0]["aliases"] = ["lesson-1"]
    with pytest.raises(ValidationError): load(raw)
    raw["catalog"]["lessons"][0]["aliases"] = []
    raw["catalog"]["lessons"][0]["private_tests"] = "secret"
    with pytest.raises(ValidationError): load(raw)


def test_reference_consumer_resolves_legacy_unicode_lesson_and_task_aliases():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    lesson = raw["catalog"]["lessons"][0]
    lesson_alias = "Основы Python и мышление программиста 01.2-lesson-02"
    task_alias = "Основы Python и мышление программиста 01.2-task-01"
    lesson["aliases"] = [lesson_alias]
    lesson["tasks"][0]["aliases"] = [task_alias]
    consumer = ReferenceConsumer()
    assert consumer.apply(load(raw)) == "applied"
    assert consumer.classify("backend", "v1", lesson_alias, task_alias, "r1") == "valid"
