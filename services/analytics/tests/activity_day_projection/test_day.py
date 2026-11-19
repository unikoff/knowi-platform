import asyncio
from copy import deepcopy

import pytest

from pymentor_analytics.adapters.postgres.activity_day_projection import _payload, has_activity_day_projection_schema
from pymentor_analytics.modules.analytics.activity.day import MAX_BIGINT, normalize_day
from pymentor_analytics.modules.analytics.activity.projection_ports import ProjectionLimit
from pymentor_analytics.modules.collector.ports import IntakeUnavailable


def bucket(amount=10, course='c', lesson='l', section='theory'):
    return {'contributions': [{'course_id': course, 'lesson_id': lesson, 'section': section, 'active_ms': amount}],
            'presence_labels': [{'course_id': course, 'lesson_id': None}, {'course_id': course, 'lesson_id': lesson}],
            'compact_item_count': 3, 'compact_json_bytes': 123}


def test_day_sums_globally_and_deduplicates_presence_and_preserves_source_caps():
    other = bucket(7, 'other')
    other['contributions'] = []  # course observed, but lost the global winner
    other['compact_item_count'] = 2
    value = normalize_day([bucket(), bucket(20), other])
    assert value['contributions'] == [{'course_id': 'c', 'lesson_id': 'l', 'section': 'theory', 'active_ms': 30}]
    assert value['presence_labels'] == [{'course_id': 'c', 'lesson_id': None}, {'course_id': 'c', 'lesson_id': 'l'},
                                        {'course_id': 'other', 'lesson_id': None}, {'course_id': 'other', 'lesson_id': 'l'}]
    assert (value['source_bucket_count'], value['source_compact_items'], value['source_compact_bytes']) == (3, 8, 369)
    _payload(value)


def test_empty_day_and_bounds():
    assert normalize_day([]) == {'contributions': [], 'presence_labels': [], 'source_bucket_count': 0,
                                 'source_compact_items': 0, 'source_compact_bytes': 0}
    with pytest.raises(ProjectionLimit): normalize_day([bucket()] * 97)
    with pytest.raises(ProjectionLimit): normalize_day([bucket(MAX_BIGINT), bucket(1)])
    b = bucket(); b['compact_json_bytes'] = 64 * 1024 * 1024 + 1
    with pytest.raises(ProjectionLimit): normalize_day([b])


@pytest.mark.parametrize('change', ['float', 'bool', 'duplicate', 'bad_count', 'bad_identifier', 'unknown_field'])
def test_invalid_bucket_rejected(change):
    b = bucket()
    if change == 'float': b['contributions'][0]['active_ms'] = 1.0
    if change == 'bool': b['contributions'][0]['active_ms'] = True
    if change == 'duplicate': b['presence_labels'].append(deepcopy(b['presence_labels'][0])); b['compact_item_count'] += 1
    if change == 'bad_count': b['compact_item_count'] += 1
    if change == 'bad_identifier': b['contributions'][0]['course_id'] = 'invalid space'
    if change == 'unknown_field': b['contributions'][0]['extra'] = 1
    with pytest.raises(ValueError): normalize_day([b])


def test_payload_rejects_forged_metadata_and_noncanonical_order():
    value = normalize_day([bucket()])
    value['source_compact_items'] = 0
    with pytest.raises(ValueError): _payload(value)
    value = normalize_day([bucket()]); value['presence_labels'].reverse()
    with pytest.raises(ValueError): _payload(value)


def test_schema_detection_fails_closed_on_partial():
    class Result:
        def __init__(self, row): self.row = row
        def one(self): return self.row
    class Session:
        def __init__(self, row): self.row = row
        async def execute(self, _): return Result(self.row)
    async def check():
        assert not await has_activity_day_projection_schema(Session((False, False, False)))
        assert await has_activity_day_projection_schema(Session((True, True, True)))
        with pytest.raises(IntakeUnavailable): await has_activity_day_projection_schema(Session((True, False, True)))
    asyncio.run(check())


def test_winner_requires_presence_and_duration_cannot_exceed_bucket():
    bad = bucket(); bad['presence_labels'].pop(); bad['compact_item_count'] -= 1
    with pytest.raises(ValueError): normalize_day([bad])
    with pytest.raises(ProjectionLimit): normalize_day([bucket(900_001)])
    # A legitimate day can contain more than one bucket of the same winner.
    value = normalize_day([bucket(900_000), bucket(900_000)])
    assert value['contributions'][0]['active_ms'] == 1_800_000
    _payload(value)


def test_payload_accepts_many_lessons_and_more_than_fifteen_minutes():
    # Both buckets are below 15 minutes; their merged day has 97 distinct
    # contributions and totals more than one bucket of active time.
    contributions = [{'course_id': 'c', 'lesson_id': f'lesson-{i:03}', 'section': 'theory', 'active_ms': 5000}
                     for i in range(97)]
    labels = [{'course_id': 'c', 'lesson_id': None}] + [
        {'course_id': 'c', 'lesson_id': f'lesson-{i:03}'} for i in range(97)]
    record = {'contributions': contributions, 'presence_labels': labels,
              'compact_item_count': len(contributions) + len(labels), 'compact_json_bytes': 12345}
    value = normalize_day([record, record])
    assert value['source_bucket_count'] == 2
    assert len(value['contributions']) == 97
    assert sum(item['active_ms'] for item in value['contributions']) == 970_000
    _payload(value)


def test_payload_rejects_forged_day_duration_over_24_hours():
    value = normalize_day([bucket()])
    value['contributions'][0]['active_ms'] = 86_400_001
    with pytest.raises(ProjectionLimit):
        _payload(value)
