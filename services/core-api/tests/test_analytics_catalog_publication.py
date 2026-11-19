from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4
import asyncio
import importlib.util
import json
import pytest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from pymentor_core.adapters.analytics_catalog import publication


def snapshot():
    task = {"id": "task", "revision": "rev", "title": "Task", "legacy_ids": ["old-task"]}
    lesson = {"id": "lesson", "legacy_id": "old-lesson", "title": "Lesson", "tasks": [task]}
    track = {"id": "track", "title": "Track", "order": 1, "lessons": [lesson]}
    return SimpleNamespace(context=SimpleNamespace(course_id="analytics_test_course"), catalog=SimpleNamespace(TRACKS=[track]))


def test_project_preserves_history_and_marks_removed_items(monkeypatch):
    monkeypatch.setattr(publication, "current_digest", lambda _: "a" * 64)
    source = snapshot()
    first, source_digest = publication.project(source)
    assert source_digest == "a" * 64
    assert first["catalog_version"] == "sha256:" + publication.digest(first["catalog"])
    source.catalog.TRACKS[0]["lessons"][0]["tasks"] = []
    second, _ = publication.project(source, first)
    assert second["kind"] == "analytics_catalog_delta"
    assert second["sequence"] == 2 and second["base_version"] == first["catalog_version"]
    assert second["catalog"]["lessons"][0]["tasks"][0]["tombstone"] is True
    source.catalog.TRACKS[0]["lessons"] = []
    third, _ = publication.project(source, second)
    assert third["catalog"]["lessons"][0]["tombstone"] is True


def test_receipt_pending_and_confirmed_are_distinct(monkeypatch):
    tmp_path = Path("output") / ("catalog-receipt-" + uuid4().hex)
    tmp_path.mkdir()
    monkeypatch.setattr(publication, "receipt_path", lambda course: tmp_path / (course + ".json"))
    monkeypatch.setattr(publication, "current_digest", lambda _: "a" * 64)
    item, source = publication.project(snapshot())
    receipt = {"message": item, "fingerprint": publication.digest(item), "source_digest": source}
    publication.write_receipt("analytics_test_course", receipt, pending=True)
    assert publication.read_receipt("analytics_test_course", pending=True) == receipt
    assert not publication.receipt_path("analytics_test_course").exists()
    publication.write_receipt("analytics_test_course", receipt)
    publication.clear_pending("analytics_test_course")
    assert publication.read_receipt("analytics_test_course") == receipt
    publication.receipt_path("analytics_test_course").unlink()
    tmp_path.rmdir()


def test_metadata_fails_closed_and_filters_lessons(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@127.0.0.1/analytics_catalog_unused")
    from pymentor_core.modules.learning.api import analytics_catalog_router as api
    from pymentor_core.app import analytics_catalog as runtime
    source = snapshot()
    source.catalog.LESSONS = [{"id": "lesson", "access": "free"}, {"id": "locked", "access": "subscription"}]
    message = {"catalog_version": "sha256:" + "a" * 64,
        "catalog": {"tracks": [{"track_id": "track", "tombstone": False}], "lessons": [
            {"lesson_id": "lesson", "track_id": "track", "tombstone": False},
            {"lesson_id": "locked", "track_id": "track", "tombstone": False}]}}
    receipt = {"message": message, "source_digest": "a" * 64}
    async def principal(request):
        return SimpleNamespace(subscription_until=None)
    async def sql(request, operation):
        return None
    monkeypatch.setattr(api, "request_principal", principal)
    monkeypatch.setattr(api, "run_request_sql", sql)
    monkeypatch.setattr(runtime, "default_runtime", lambda: SimpleNamespace(snapshot=lambda _: source))
    monkeypatch.setattr(runtime.publication, "read_receipt", lambda _: receipt)
    monkeypatch.setattr(runtime.publication, "current_digest", lambda _: "a" * 64)
    app = FastAPI()
    app.include_router(api.analytics_catalog_router, prefix="/api")
    c = TestClient(app)
    response = c.get("/api/learning/analytics-catalog?course_id=analytics_test_course")
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"course_id": "analytics_test_course", "catalog_version": message["catalog_version"], "lesson_ids": ["lesson"]}
    assert "X-Analytics-Browser-Presence-Required" not in response.headers
    for required, mode, expected in ((False, "analytics", False), (True, "off", False),
                                      (True, "shadow", False), (True, "analytics", True)):
        app.state.analytics_browser_presence_required = required
        app.state.analytics_legacy_read_mode = mode
        result = c.get("/api/learning/analytics-catalog?course_id=analytics_test_course")
        assert result.json() == response.json()
        assert result.headers["cache-control"] == "no-store"
        assert result.headers.get("X-Analytics-Browser-Presence-Required") == ("true" if expected else None)
    invalid = c.get("/api/learning/analytics-catalog?course_id=analytics_test_course&unexpected=1")
    assert invalid.status_code == 400 and invalid.headers["cache-control"] == "no-store"
    assert "X-Analytics-Browser-Presence-Required" not in invalid.headers
    app.state.analytics_local_collection = SimpleNamespace(catalog_binding=object())
    monkeypatch.setattr(runtime, "verified_root", lambda binding: Path("owned-local-root"))
    monkeypatch.setattr(runtime.publication, "read_receipt", lambda course, **kwargs: receipt)
    parallel = c.get("/api/learning/analytics-catalog?course_id=analytics_test_course")
    assert parallel.headers["X-Analytics-Collection-Mode"] == "parallel"
    assert parallel.json() == response.json()
    monkeypatch.setattr(runtime.publication, "current_digest", lambda _: "b" * 64)
    assert c.get("/api/learning/analytics-catalog?course_id=analytics_test_course").json()["catalog_version"] is None
    async def denied(request):
        raise HTTPException(401, "unauthorized")
    monkeypatch.setattr(api, "request_principal", denied)
    response = c.get("/api/learning/analytics-catalog?course_id=analytics_test_course")
    assert response.status_code == 401 and response.headers["cache-control"] == "no-store"
    assert "X-Analytics-Browser-Presence-Required" not in response.headers


def test_lost_ack_is_reconciled_from_pending(monkeypatch):
    spec = importlib.util.spec_from_file_location("catalog_publisher", Path("services/core-api/tools/analytics_catalog/publish.py"))
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    monkeypatch.setattr(publication, "current_digest", lambda _: "a" * 64)
    monkeypatch.setattr(publisher, "current_digest", lambda _: "a" * 64)
    source = snapshot()
    item, src = publication.project(source)
    pending = {"message": item, "fingerprint": publication.digest(item), "source_digest": src}
    calls, writes = [], []
    monkeypatch.setattr(publisher, "read_receipt", lambda course, pending=False: globals_pending if pending else (_ for _ in ()).throw(FileNotFoundError()))
    globals_pending = pending
    monkeypatch.setattr(publisher, "write_receipt", lambda course, receipt, **kwargs: writes.append(receipt))
    monkeypatch.setattr(publisher, "clear_pending", lambda course: calls.append("cleared"))
    async def request(method, url, **kwargs):
        calls.append(method)
        return 200, json.dumps({"status": "accepted"}).encode()
    monkeypatch.setattr(publisher, "request", request)
    assert asyncio.run(publisher._publish_locked(source, "http://127.0.0.1:8000", lambda: "token")) == "accepted"
    assert calls == ["GET", "cleared"] and writes == [pending]


def test_simultaneous_publishers_are_excluded(monkeypatch):
    tmp_path = Path("output") / ("catalog-lock-" + uuid4().hex)
    tmp_path.mkdir()
    monkeypatch.setattr(publication, "receipt_path", lambda course: tmp_path / (course + ".json"))
    with publication.publication_lock("analytics_test_course"):
        with pytest.raises(ValueError, match="already running"):
            with publication.publication_lock("analytics_test_course"):
                pass
    (tmp_path / "analytics_test_course.lock").unlink()
    tmp_path.rmdir()


def test_fixture_guard_uses_owned_roots_and_checks_both_database_sentinels(monkeypatch):
    spec = importlib.util.spec_from_file_location("catalog_publisher_guard", Path("services/core-api/tools/analytics_catalog/publish.py"))
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    folder = Path("output") / ("catalog-guard-" + uuid4().hex)
    folder.mkdir()
    (folder / "courses").mkdir()
    folder = folder.resolve()
    monkeypatch.setattr(publisher, "fixture_root", lambda _: folder)
    names = ("admin", "leader", "student", "other")
    users = {name: {"id": i, "email": name + "@example.test", "password": "fixture", "subject_ref": str(uuid4())}
             for i, name in enumerate(names, 1)}
    browser = {name: {"id": i, "email": "browser-" + name + "@example.test", "password": "fixture", "subject_ref": str(uuid4())}
               for i, name in enumerate(names[:3], 5)}
    browser["leader"] = dict(users["leader"])
    core, analytics = "analytics_delivery_test_" + uuid4().hex, "analytics_test_" + uuid4().hex
    core_db = {"name": core, "owner": "pymentor_test", "sentinel": "a" * 64,
               "dsn": f"postgresql://test:fixture@127.0.0.1:57382/{core}"}
    analytics_db = {"name": analytics, "owner": analytics + "_migrate", "sentinel": "b" * 64,
                    "dsn": f"postgresql://test:fixture@127.0.0.1:57392/{analytics}"}
    fixture = {"core_url": "http://127.0.0.1:18151", "collector_url": "http://127.0.0.1:18152",
               "query_url": "http://127.0.0.1:18153", "privacy_url": "http://127.0.0.1:18154",
               "databases": {"core": core_db, "analytics": analytics_db}, "users": users,
               "browser_users": browser, "course_id": "backend", "catalog_version": "synthetic-v1",
               "roster": [users["student"]["subject_ref"], browser["student"]["subject_ref"]]}
    owner = {"token": "c" * 64, "state": "processes_started", "core_name": core, "core_sentinel": core_db["sentinel"],
             "analytics_resources": [{"name": analytics, "sentinel": analytics_db["sentinel"]}]}
    (folder / "owner.json").write_text(json.dumps(owner), encoding="utf-8")
    (folder / "fixture.json").write_text(json.dumps(fixture), encoding="utf-8")
    called = []
    verify = lambda value: called.append(value["databases"])
    args = (str(folder), folder / "courses", "backend", fixture["collector_url"], owner["token"])
    assert publisher.checked_fixture(*args, verify_databases=verify) == folder
    assert called == [fixture["databases"]]
    def sentinel_mismatch(_):
        raise ValueError("sentinel mismatch")
    with pytest.raises(ValueError, match="sentinel mismatch"):
        publisher.checked_fixture(*args, verify_databases=sentinel_mismatch)
    with pytest.raises(ValueError):
        publisher.checked_fixture(*args[:-1], "d" * 64, verify_databases=verify)
    with pytest.raises(ValueError):
        publisher.checked_fixture(str(folder), folder / "courses", "backend", "http://127.0.0.1:1234", owner["token"], verify_databases=verify)
    owner["analytics_resources"][0]["sentinel"] = "d" * 64
    (folder / "owner.json").write_text(json.dumps(owner), encoding="utf-8")
    with pytest.raises(ValueError):
        publisher.checked_fixture(*args, verify_databases=verify)
    assert len(called) == 1
    (folder / "owner.json").unlink()
    (folder / "fixture.json").unlink()
    (folder / "courses").rmdir()
    folder.rmdir()


def test_fixture_receipts_do_not_use_default_core_location(monkeypatch):
    from pymentor_core.adapters.analytics_catalog import fixture_guard
    folder = Path("output") / ("catalog-isolated-" + uuid4().hex)
    folder.mkdir()
    folder = folder.resolve()
    (folder / "owner.json").write_text(json.dumps({"token": "e" * 64, "state": "browser_hold"}), encoding="utf-8")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_CATALOG_FIXTURE_DIR", str(folder))
    monkeypatch.setattr(fixture_guard, "fixture_root", lambda _: folder)
    path = publication.receipt_path("backend")
    assert path.parent == folder / "analytics-catalog"
    assert "backend" not in path.name
    (folder / "owner.json").unlink()
    folder.rmdir()


def test_fixture_root_accepts_only_direct_uuid4_child(monkeypatch):
    from pymentor_core.adapters.analytics_catalog import fixture_guard
    base = (Path("output") / ("catalog-root-" + uuid4().hex)).resolve()
    fixture_parent = base / "services" / "analytics" / ".local" / "cross-service"
    owned = fixture_parent / uuid4().hex
    owned.mkdir(parents=True)
    fake_module = base / "services/core-api/pymentor_core/adapters/analytics_catalog/fixture_guard.py"
    monkeypatch.setattr(fixture_guard, "__file__", str(fake_module))
    assert fixture_guard.fixture_root(str(owned)) == owned
    with pytest.raises(ValueError):
        fixture_guard.fixture_root(str(fixture_parent / ("0" * 32)))
    with pytest.raises(ValueError):
        fixture_guard.fixture_root(str(base / uuid4().hex))
    owned.rmdir()
    for path in (fixture_parent, fixture_parent.parent, fixture_parent.parent.parent, base / "services", base):
        path.rmdir()
