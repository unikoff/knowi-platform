"""Synthetic in-process Core-to-Collector publication; no database or real courses."""
import asyncio
import importlib.util
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
from fastapi import FastAPI

def test_synthetic_publisher_to_collector_and_receipt(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("services/analytics").resolve()))
    from pymentor_core.adapters.analytics_catalog import publication
    from pymentor_analytics.modules.catalog.api.router import create_router
    from pymentor_analytics.modules.catalog.service import CatalogService
    class Store:
        row = None
        async def accept(self, namespace, message, fingerprint, *, deadline):
            self.row = (namespace, message.catalog.course_id, message.catalog_version, fingerprint)
            return "accepted"
        async def receipt(self, namespace, course, version, fingerprint, *, deadline):
            return "accepted" if self.row == (namespace, course, version, fingerprint) else "absent"
    class Verifier:
        def verify(self, token):
            assert token == "signed-producer-test"
            return SimpleNamespace(sub="test-producer", scope="control:write", expires_at=time.time() + 60)
    class Ready:
        async def is_ready(self): return True
    app = FastAPI()
    app.include_router(create_router(service=CatalogService(Store()), verifier=Verifier(),
                                    namespace_by_producer={"test-producer": "test-namespace"}, readiness=Ready()))
    spec = importlib.util.spec_from_file_location("catalog_publisher_cross", Path("services/core-api/tools/analytics_catalog/publish.py"))
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    tmp_path = Path("output") / ("catalog-cross-" + uuid4().hex)
    tmp_path.mkdir()
    monkeypatch.setattr(publication, "receipt_path", lambda course: tmp_path / (course + ".json"))
    monkeypatch.setattr(publication, "current_digest", lambda _: "b" * 64)
    monkeypatch.setattr(publisher, "current_digest", lambda _: "b" * 64)
    lesson = {"id": "lesson", "legacy_id": None, "title": "Lesson", "tasks": []}
    source = SimpleNamespace(context=SimpleNamespace(course_id="analytics_test_course"),
                             catalog=SimpleNamespace(TRACKS=[{"id": "track", "title": "Track", "order": 1, "lessons": [lesson]}]))
    transport = httpx.ASGITransport(app=app)
    assert asyncio.run(publisher.publish(source, "http://localhost", lambda: "signed-producer-test", transport=transport)) == "accepted"
    receipt = publication.read_receipt("analytics_test_course")
    assert receipt["message"]["catalog_version"] == "sha256:" + publication.digest(receipt["message"]["catalog"])
    assert receipt["source_digest"] == "b" * 64
    publication.receipt_path("analytics_test_course").unlink()
    (tmp_path / "analytics_test_course.lock").unlink()
    tmp_path.rmdir()
