import asyncio
import json
import time
from uuid import uuid4

import pytest

from pymentor_analytics.adapters.catalog.fixture import FixtureCatalog
from pymentor_analytics.app.config import load_settings
from pymentor_analytics.modules.collector.ports import IntakeUnavailable


def snapshot(course="backend", version="catalog-1"):
    return {"schema_version": 2, "kind": "catalog_snapshot", "control_id": str(uuid4()),
            "course_id": course, "catalog_version": version, "track_id": "синтетический",
            "lessons": [{"lesson_id": "lesson-1"}]}


def test_explicit_fixture_keeps_product_course_and_version_boundaries():
    async def run():
        catalog = FixtureCatalog(json.dumps([snapshot(), snapshot("backend_copy", "catalog-2")]))
        async def known(course="backend", version="catalog-1", lesson="lesson-1"):
            return await catalog.known(course_id=course, catalog_version=version, lesson_id=lesson, deadline=time.monotonic()+1)
        assert await known() and await known(lesson=None)
        assert await known("backend_copy", "catalog-2")
        assert not await known("backend_copy", "catalog-1")
        assert not await known(lesson="unknown")
        assert not await known("unknown")
        with pytest.raises(IntakeUnavailable):
            await catalog.known(course_id="backend", catalog_version="catalog-1", lesson_id=None, deadline=time.monotonic()-1)
    asyncio.run(run())


def test_unknown_or_ambiguous_catalog_configuration_is_not_guessed():
    first = snapshot()
    for payload in ([first, first], [{**first, "lessons": first["lessons"]*2}], [{**first, "email": "private"}], []):
        with pytest.raises(ValueError, match="invalid synthetic catalog"):
            FixtureCatalog(json.dumps(payload))
    with pytest.raises(ValueError):
        FixtureCatalog('[{"course_id":"a","course_id":"b"}]')
    with pytest.raises(ValueError):
        FixtureCatalog(" "*(128*1024+1))


def test_catalog_is_one_explicit_shared_process_input():
    raw = json.dumps([snapshot()])
    env = {"ANALYTICS_COLLECTOR_DATABASE_URL": "postgresql://synthetic@127.0.0.1:57392/analytics_test", "ANALYTICS_FIXTURE_CATALOG_JSON": raw}
    assert load_settings("collector", env).fixture_catalog_json.get_secret_value() == raw
    env["ANALYTICS_COLLECTOR_FIXTURE_CATALOG_JSON"] = raw
    with pytest.raises(ValueError): load_settings("collector", env)
