from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from pymentor_core.adapters.course_local import runtime
from pymentor_core.adapters.course_local.packages import CourseNotFoundError
from pymentor_core.adapters.sql import models, role_admin_commands
from pymentor_core.app import course_context
from pymentor_core.app.wiring import configure_bindings
from pymentor_core.modules.identity.contracts import InvalidSession
from pymentor_core.modules.identity.principal import Principal
from pymentor_core.modules.learning import progress
from pymentor_core.modules.learning.api import service
from pymentor_core.modules.learning.api.router import learning_router


URL = "/api/learning/progress/completed-count"


def lesson(lesson_id, *, task_ids=(), self_check=False, mixed=False, **policy):
    return {
        "id": lesson_id,
        "title": lesson_id,
        "status": "current",
        "tasks": [{"id": task_id, "revision": "current"} for task_id in task_ids],
        "manual_practice": [{"id": "manual"}] if mixed else [],
        "self_check": self_check,
        "progress_policy": policy,
    }


@pytest.fixture
def client(synthetic_engine, monkeypatch):
    configure_bindings()
    engine = synthetic_engine
    for model in (
        models.User, models.UserLessonProgress, models.UserTaskProgress,
        models.CourseLeadership, models.RoleAdminRevision,
    ):
        model.__table__.create(engine)
    sessions = sessionmaker(bind=engine)

    accounts = {}
    with sessions.begin() as db:
        db.execute(text("CREATE TABLE account_lifecycles (user_id INTEGER PRIMARY KEY, lifecycle_id VARCHAR)"))
        for user_id, name in enumerate(("student", "other", "admin"), start=1):
            user = models.User(id=user_id, username=name, email=f"{name}@example.test",
                               password="example-test", is_admin=name == "admin")
            db.add(user)
            db.execute(
                text("INSERT INTO account_lifecycles VALUES (:id, :ref)"),
                {"id": user_id, "ref": f"lifecycle-{name}"},
            )
            accounts[name] = Principal.from_user(user, account_ref=f"lifecycle-{name}")
        accounts["stale"] = Principal.from_user(
            accounts["student"], account_ref="removed-lifecycle"
        )

        def saved(lesson_id, at, *, user_id=1, course_id="backend"):
            db.add(models.UserLessonProgress(
                lesson_id=lesson_id, user_id=user_id, course_id=course_id, completed_at=datetime.fromisoformat(at)
            ))

        def task(task_id, at, revision="current"):
            db.add(models.UserTaskProgress(
                task_id=task_id, user_id=1, task_revision=revision, completed_at=datetime.fromisoformat(at)
            ))

        saved("before", "2026-11-12T20:59:59")
        saved("at-start", "2026-11-12T21:00:00")
        saved("at-end", "2026-11-19T21:00:00")
        saved("last-second", "2026-11-19T20:59:59")
        saved("overview", "2026-11-13T12:00:00")
        saved("removed-from-catalog", "2026-11-13T12:00:00")
        saved("not-countable", "2026-11-13T12:00:00")
        saved("only-other-student", "2026-11-13T12:00:00", user_id=2)
        saved("only-other-course", "2026-11-13T12:00:00", course_id="other-course")
        saved("mixed", "2026-11-12T20:00:00")
        task("mixed-task", "2026-11-17T12:00:00")
        saved("mixed-outside", "2026-11-20T12:00:00")
        task("late-mixed-task", "2026-11-17T12:00:00")
        task("task-current", "2026-11-18T12:00:00")
        task("task-stale", "2026-11-18T12:00:00", "prior")
        task("task-before-self-check", "2026-11-12T20:00:00")
        task(
            progress.chapter_one_completion_task_id("card-with-self-check"),
            "2026-11-19T12:00:00",
            progress.chapter_one_completion_revision(
                lesson("card-with-self-check", task_ids=("task-before-self-check",), self_check=True, mixed=True)
            ),
        )

    markers = [
        lesson(item, self_check=True) for item in
        ("before", "at-start", "at-end", "last-second", "only-other-student", "only-other-course")
    ]
    catalog = markers + [
        lesson("overview", overview=True),
        lesson("not-countable"),
        lesson("mixed", task_ids=("mixed-task",), mixed=True, completion="lesson"),
        lesson("mixed-outside", task_ids=("late-mixed-task",), mixed=True, completion="lesson"),
        lesson("current", task_ids=("task-current",)),
        lesson("stale", task_ids=("task-stale",)),
        lesson("card-with-self-check", task_ids=("task-before-self-check",), self_check=True, mixed=True),
    ]

    def find_track(_):
        return {"lessons": catalog}

    def require_enabled(course_id):
        if course_id not in {"backend", "other-course"}:
            raise CourseNotFoundError(course_id)

    registry = SimpleNamespace(
        entries={key: SimpleNamespace(course_id=key, enabled=True) for key in ("backend", "other-course")},
        require_enabled=require_enabled,
    )
    for module in (runtime, course_context):
        monkeypatch.setattr(module, "default_runtime", lambda: SimpleNamespace(registry=registry))
    monkeypatch.setattr(service, "find_track", find_track)
    monkeypatch.setattr(service, "default_track_id", lambda: "track")
    monkeypatch.setattr(service, "get_content_revision", lambda: "fixture-revision")
    monkeypatch.setattr(service, "get_tracks_summary", lambda: [
        {"id": "track", "title": "Track", "lessons_total": len(catalog), "lessons_countable": len(catalog) - 1,
         "countable_lesson_ids": [item["id"] for item in catalog if item["id"] != "not-countable"]}
    ])

    # The policy itself and progress queries run against the seeded SQL state.
    # Isolate the authorization fixture from lifecycle locking in this scenario.
    def read_users(db, ids):
        return {row["id"]: row for row in db.execute(
            select(models.User.id, models.User.is_admin).where(models.User.id.in_(ids))
        ).mappings()}

    monkeypatch.setattr(role_admin_commands, "_lock_users", read_users)

    @asynccontextmanager
    async def unit_of_work():
        with sessions() as db:
            async def run_sync(operation):
                return operation(db)

            yield SimpleNamespace(session=SimpleNamespace(run_sync=run_sync))

    async def authenticate(token):
        if token not in accounts:
            raise InvalidSession()
        return accounts[token]

    app = FastAPI()
    app.include_router(learning_router, prefix="/api/learning")
    app.state.identity_sessions = SimpleNamespace(
        authenticate=authenticate, database=SimpleNamespace(unit_of_work=unit_of_work)
    )
    with TestClient(app) as http:
        yield http
    engine.dispose()


def read_count(client, *, token="student", course="backend", start="2026-11-13", end="2026-11-19"):
    return client.get(
        URL, params={"course_id": course, "from": start, "to": end},
        headers={"Cookie": f"session={token}"} if token else {},
    )


def test_course_and_learner_scope_with_moscow_boundaries_and_completion_evidence(client):
    response = read_count(client)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {
        "course_id": "backend", "from": "2026-11-13", "to": "2026-11-19", "completed_lessons": 5,
    }
    assert read_count(client, token="other").json()["completed_lessons"] == 1
    assert read_count(client, course="other-course").json()["completed_lessons"] == 1
    assert read_count(client, start="2026-10-01", end="2026-10-31").json()["completed_lessons"] == 0


def test_scope_authentication_and_validated_period(client):
    for token in ("", "invalid", "stale"):
        assert read_count(client, token=token).status_code == 401
    assert read_count(client, token="admin").status_code == 403
    assert read_count(client, course="unknown").status_code == 404
    assert read_count(client, end="2026-11-12").status_code == 422
    assert read_count(client, end="2026-12-14").status_code == 422


def test_catalog_completion_statistics_exclude_automatically_completed_overview(client):
    response = client.get("/api/learning/catalog?course_id=backend", headers={"Cookie": "session=student"})
    assert response.status_code == 200, response.text
    lessons = {item["id"]: item for item in response.json()["courses"]["track"]}

    assert lessons["overview"]["completed"] is True
    assert lessons["overview"]["completed_on"] is None
    assert lessons["overview"]["counts_toward_statistics"] is False
    assert lessons["not-countable"]["counts_toward_statistics"] is False
    assert lessons["at-start"]["completed"] is True
    assert lessons["at-start"]["completed_on"] == "2026-11-13"
    assert lessons["mixed"]["completed_on"] == "2026-11-17"
    assert lessons["current"]["completed_on"] == "2026-11-18"
    assert lessons["stale"]["completed_on"] is None
    assert lessons["only-other-student"]["completed_on"] is None
    assert lessons["only-other-course"]["completed_on"] is None
    assert lessons["at-start"]["counts_toward_statistics"] is True
    assert read_count(client, start="2026-11-13T00:00:00").status_code == 422
    assert client.get(f"{URL}?course_id=backend&from=2026-11-13&to=2026-11-19&user_id=2",
                      headers={"Cookie": "session=student"}).status_code == 422
