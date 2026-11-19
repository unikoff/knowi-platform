import time
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pymentor_analytics.modules.catalog.api.router import create_router
from pymentor_analytics.modules.catalog.service import CatalogService, digest, parse_message
from pymentor_analytics.contracts.auth_v1 import AuthenticationError
from pymentor_analytics.adapters.postgres.catalog import PostgresCatalog


def message():
    catalog = {"course_id": "analytics_test_course", "tracks": [{"track_id": "track", "title": "Track", "order": 1, "tombstone": False}],
               "lessons": [{"lesson_id": "lesson", "track_id": "track", "title": "Lesson", "order": 1,
                            "aliases": [], "tombstone": False, "tasks": []}]}
    return {"schema_version": 1, "kind": "analytics_catalog_snapshot", "sequence": 1,
            "catalog_version": "sha256:" + digest(catalog), "catalog": catalog}


class Store:
    def __init__(self):
        self.items = {}

    async def accept(self, namespace, message, fingerprint, *, deadline):
        key = (namespace, message.catalog.course_id, message.catalog_version)
        old = self.items.get(key)
        if old is not None and old != fingerprint:
            return "conflict"
        self.items[key] = fingerprint
        return "accepted"

    async def receipt(self, namespace, course_id, catalog_version, fingerprint, *, deadline):
        old = self.items.get((namespace, course_id, catalog_version))
        return "absent" if old is None else "accepted" if old == fingerprint else "conflict"


class Verifier:
    def verify(self, token):
        if token != "valid":
            raise AuthenticationError()
        return SimpleNamespace(sub="producer", scope="control:write", expires_at=time.time() + 60)


class Ready:
    async def is_ready(self):
        return True


def client():
    app = FastAPI()
    app.include_router(create_router(service=CatalogService(Store()), verifier=Verifier(),
                                    namespace_by_producer={"producer": "server-ns"}, readiness=Ready()))
    return TestClient(app)


def test_publication_receipt_conflict_and_auth():
    c = client()
    item = message()
    fingerprint = digest(item)
    path = f"/internal/analytics/catalog/v1/receipts/{item['catalog']['course_id']}/{item['catalog_version']}"
    headers = {"Authorization": "Bearer valid"}
    assert c.post("/internal/analytics/catalog/v1", json={"message": item, "fingerprint": fingerprint}).status_code == 401
    assert c.get(path, params={"fingerprint": fingerprint}, headers=headers).json()["status"] == "absent"
    assert c.post("/internal/analytics/catalog/v1", json={"message": item, "fingerprint": fingerprint}, headers=headers).json()["status"] == "accepted"
    assert c.post("/internal/analytics/catalog/v1", json={"message": item, "fingerprint": fingerprint}, headers=headers).json()["status"] == "accepted"
    assert c.get(path, params={"fingerprint": fingerprint}, headers=headers).json()["status"] == "accepted"
    assert c.get(path, params={"fingerprint": "0" * 64}, headers=headers).status_code == 409
    assert c.post("/internal/analytics/catalog/v1", json={"message": item, "fingerprint": "0" * 64}, headers=headers).status_code == 400


def test_version_and_identity_are_validated():
    item = message()
    item["catalog"]["lessons"].append(item["catalog"]["lessons"][0])
    item["catalog_version"] = "sha256:" + digest(item["catalog"])
    with pytest.raises(ValueError):
        parse_message(item)
    item = message()
    item["catalog_version"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError):
        parse_message(item)


def test_unicode_legacy_aliases_publish_without_weakening_canonical_ids():
    item = message()
    lesson = item["catalog"]["lessons"][0]
    lesson["aliases"] = ["Основы Python и мышление программиста 01.2-lesson-02"]
    lesson["tasks"] = [{"task_id": "task-1", "task_revision": "r1", "title": "Task", "order": 1,
                        "tombstone": False, "aliases": ["Основы Python и мышление программиста 01.2-task-01"]}]
    item["catalog_version"] = "sha256:" + digest(item["catalog"])
    fingerprint = digest(item)
    c = client()
    result = c.post("/internal/analytics/catalog/v1", json={"message": item, "fingerprint": fingerprint},
                    headers={"Authorization": "Bearer valid"})
    assert result.status_code == 200 and result.json() == {"status": "accepted"}
    parsed = parse_message(item)
    assert parsed.catalog.lessons[0].aliases == tuple(lesson["aliases"])
    assert parsed.catalog.lessons[0].tasks[0].aliases == tuple(lesson["tasks"][0]["aliases"])
    lesson["lesson_id"] = "Недопустимый ID"
    item["catalog_version"] = "sha256:" + digest(item["catalog"])
    with pytest.raises(ValueError):
        parse_message(item)


@pytest.mark.parametrize("alias", ["", "x" * 129, "lesson\nname", "lesson\u0000name", "lesson\u0085name"])
@pytest.mark.parametrize("level", ["lesson", "task"])
def test_legacy_alias_bounds_and_control_characters_remain_rejected(alias, level):
    item = message()
    lesson = item["catalog"]["lessons"][0]
    lesson["tasks"] = [{"task_id": "task-1", "task_revision": "r1", "title": "Task", "order": 1,
                        "tombstone": False, "aliases": []}]
    target = lesson if level == "lesson" else lesson["tasks"][0]
    target["aliases"] = [alias]
    item["catalog_version"] = "sha256:" + digest(item["catalog"])
    with pytest.raises(ValueError):
        parse_message(item)


def test_unicode_legacy_aliases_remain_unambiguous():
    item = message()
    lesson = item["catalog"]["lessons"][0]
    lesson["aliases"] = ["Общее имя", "Общее имя"]
    item["catalog_version"] = "sha256:" + digest(item["catalog"])
    with pytest.raises(ValueError):
        parse_message(item)
    lesson["aliases"] = []
    lesson["tasks"] = [{"task_id": task_id, "task_revision": "r1", "title": "Task", "order": n,
                        "tombstone": False, "aliases": ["Общее задание"]}
                       for n, task_id in enumerate(("task-1", "task-2"), 1)]
    item["catalog_version"] = "sha256:" + digest(item["catalog"])
    with pytest.raises(ValueError):
        parse_message(item)
def test_adapter_history_and_admission_without_postgres():
    class Result:
        def __init__(self, value): self.value = value
        def scalar_one_or_none(self): return self.value
    class Session:
        def __init__(self): self.rows = []
        async def execute(self, stmt, args):
            sql = str(stmt)
            if "pg_advisory_xact_lock" in sql: return Result(None)
            if sql.startswith("INSERT"):
                self.rows.append({**args, "body": json.loads(args["body"])})
                return Result(None)
            rows = [r for r in self.rows if r["ns"] == args["ns"] and r["course"] == args["course"]]
            if "catalog_version=:version" in sql:
                rows = [r for r in rows if r["version"] == args["version"]]
            row = max(rows, key=lambda r: r["sequence"], default=None)
            return Result(None if row is None else row["fingerprint"] if "SELECT fingerprint" in sql else row["body"])
    class Database:
        def __init__(self): self.session = Session()
        @asynccontextmanager
        async def transaction(self): yield self.session
    async def scenario():
        store = PostgresCatalog(Database(), "server-ns")
        service = CatalogService(store)
        first = message()
        deadline = time.monotonic() + 5
        assert await service.accept("server-ns", first, digest(first), deadline=deadline) == "accepted"
        assert await service.accept("server-ns", first, digest(first), deadline=deadline) == "accepted"
        assert await store.known(course_id="analytics_test_course", catalog_version=first["catalog_version"], lesson_id="lesson", deadline=deadline)
        changed = json.loads(json.dumps(first))
        changed["kind"], changed["sequence"], changed["base_version"] = "analytics_catalog_delta", 2, first["catalog_version"]
        changed["catalog"]["lessons"][0]["tombstone"] = True
        changed["catalog_version"] = "sha256:" + digest(changed["catalog"])
        assert await service.accept("server-ns", changed, digest(changed), deadline=deadline) == "accepted"
        assert not await store.known(course_id="analytics_test_course", catalog_version=changed["catalog_version"], lesson_id="lesson", deadline=deadline)
        assert await store.known(course_id="analytics_test_course", catalog_version=first["catalog_version"], lesson_id="lesson", deadline=deadline)
        invalid = json.loads(json.dumps(changed))
        invalid["sequence"], invalid["base_version"] = 3, changed["catalog_version"]
        invalid["catalog"]["lessons"][0]["tombstone"] = False
        invalid["catalog"]["lessons"][0]["title"] = "Different"
        invalid["catalog_version"] = "sha256:" + digest(invalid["catalog"])
        assert await service.accept("server-ns", invalid, digest(invalid), deadline=deadline) == "conflict"
    asyncio.run(scenario())
