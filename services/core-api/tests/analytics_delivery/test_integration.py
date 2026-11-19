import os
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://synthetic@127.0.0.1:1/isolated")

from datetime import date, datetime, timezone
from uuid import UUID

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from pymentor_core.adapters.sql import models, activity, progress
from pymentor_core.adapters.sql.analytics_delivery import activity_state, metadata, outbox, write_gate
from pymentor_core.adapters.sql.unit_of_work import SqlAlchemyUnitOfWork
from pymentor_core.adapters.sql.analytics_delivery_ops import integration
from pymentor_core.adapters.sql.analytics_delivery_ops import export
from pymentor_core.adapters.sql.analytics_delivery_ops import cutover
from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_sql import metadata as proof_metadata

REF = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

@pytest.fixture
def session(synthetic_engine, monkeypatch):
    monkeypatch.setenv("PYMENTOR_ANALYTICS_DELIVERY_ENABLED", "1")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED", "1")
    engine = synthetic_engine
    models.User.__table__.create(engine)
    models.UserActivityDay.__table__.create(engine)
    models.UserLessonProgress.__table__.create(engine)
    models.UserTaskProgress.__table__.create(engine)
    metadata.create_all(engine)
    proof_metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE account_lifecycles (user_id INTEGER PRIMARY KEY, lifecycle_id VARCHAR(36) NOT NULL UNIQUE)"))
        connection.execute(text("INSERT INTO users (id, username, email, password_hash, is_admin, verified) VALUES (1, 'synthetic', 'synthetic@example.test', 'hash', false, false)"))
        connection.execute(text("INSERT INTO account_lifecycles VALUES (1, :ref)"), {"ref": REF})
        connection.execute(write_gate.insert().values(singleton=1, mode="mirror"))
    with Session(engine) as db:
        yield db
    engine.dispose()


def test_writers_capture_once_and_share_uow_session(session):
    uow = SqlAlchemyUnitOfWork(session)
    day = activity.record_activity_day(1, date(2026, 11, 17), None, uow=uow, expected_account_ref=REF)
    activity.record_activity_day(1, date(2026, 11, 17), None, uow=uow, expected_account_ref=REF)
    progress.mark_lesson_completed(1, "lesson", None, uow=uow, course_id="backend", expected_account_ref=REF)
    progress.mark_lesson_completed(1, "lesson", None, uow=uow, course_id="backend", expected_account_ref=REF)
    rows = session.execute(select(outbox.c.payload).order_by(outbox.c.revision)).scalars().all()
    import json
    events = list(map(json.loads, rows))
    assert [e["kind"] for e in events] == ["legacy_activity_day", "lesson_progress"]
    assert [e["revision"] for e in events] == [1, 2]
    assert events[0]["payload"]["activity_date"] == day.activity_date.isoformat()
    assert events[0]["subject_ref"] == events[1]["subject_ref"]
    assert str(UUID(events[0]["subject_ref"])) == events[0]["subject_ref"]
    assert session.query(models.UserLessonProgress).count() == 1


def test_injected_clock_is_preserved(session):
    timestamp = datetime(2026, 11, 17, 11, tzinfo=timezone.utc)
    class Clock:
        def now(self): return timestamp
    activity.record_activity_day(1, date(2026, 11, 17), session, clock=Clock(), expected_account_ref=REF)
    import json
    event = json.loads(session.execute(select(outbox.c.payload)).scalar_one())
    assert event["payload"]["visited_at"] == timestamp.isoformat()


def test_null_legacy_timestamp_is_filled_and_captured(session):
    session.add(models.UserActivityDay(user_id=1, activity_date=date(2026, 11, 16), visited_at=None))
    session.commit()
    timestamp = datetime(2026, 11, 17, 11, tzinfo=timezone.utc)
    activity.record_activity_day(1, date(2026, 11, 16), session, visited_at=timestamp, expected_account_ref=REF)
    import json
    event = json.loads(session.execute(select(outbox.c.payload)).scalar_one())
    assert event["payload"]["visited_at"] == timestamp.isoformat() and event["revision"] == 1


def test_capture_failure_rolls_back_source_lifecycle_revision(session, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(integration, "enqueue", fail)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        activity.record_activity_day(1, date(2026, 11, 17), session, expected_account_ref=REF)
    assert session.query(models.UserActivityDay).count() == 0
    assert session.execute(text("SELECT lifecycle_id FROM account_lifecycles")).scalar_one() == REF
    assert session.execute(text("SELECT count(*) FROM analytics_transfer_revisions")).scalar_one() == 0
    assert session.execute(select(outbox)).all() == []


def test_lesson_capture_preserves_completion_fact_and_rolls_back_on_enqueue_failure(session, monkeypatch):
    import json

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic enqueue failure")

    with monkeypatch.context() as patch:
        patch.setattr(integration, "enqueue", fail)
        with pytest.raises(RuntimeError, match="synthetic enqueue failure"):
            progress.mark_lesson_completed(1, "lesson", session, course_id="course-a", expected_account_ref=REF)
    assert session.query(models.UserLessonProgress).count() == 0
    assert session.execute(text("SELECT count(*) FROM analytics_transfer_revisions")).scalar_one() == 0
    assert session.execute(select(outbox)).all() == []

    progress.mark_lesson_completed(1, "lesson", session, course_id="course-a", expected_account_ref=REF)
    row = session.query(models.UserLessonProgress).one()
    event = json.loads(session.execute(select(outbox.c.payload)).scalar_one())
    assert event["subject_ref"] == REF
    assert event["payload"] == {"course_id": "course-a", "lesson_id": "lesson",
                                "completed_at": row.completed_at.replace(tzinfo=timezone.utc).isoformat()}
    progress.mark_lesson_completed(1, "lesson", session, course_id="course-a", expected_account_ref=REF)
    assert session.query(models.UserLessonProgress).count() == 1
    assert session.execute(select(outbox.c.payload)).scalars().all() == [json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)]


def test_task_capture_is_opt_in_atomic_and_idempotent(session, monkeypatch):
    import json
    monkeypatch.setenv("PYMENTOR_ANALYTICS_TASK_PROGRESS_ENABLED", "1")
    with monkeypatch.context() as patch:
        patch.setattr(integration, "enqueue", lambda *args: (_ for _ in ()).throw(RuntimeError("enqueue failure")))
        with pytest.raises(RuntimeError, match="enqueue failure"):
            progress.mark_task_completed(1, "task", "v1", session, course_id="course", expected_account_ref=REF)
    assert session.query(models.UserTaskProgress).count() == 0
    assert session.execute(select(outbox)).all() == []
    progress.mark_task_completed(1, "task", "v1", session, course_id="course", expected_account_ref=REF)
    progress.mark_task_completed(1, "task", "v1", session, course_id="course", expected_account_ref=REF)
    row = session.query(models.UserTaskProgress).one()
    event = json.loads(session.execute(select(outbox.c.payload)).scalar_one())
    assert event["subject_ref"] == REF and event["kind"] == "task_progress"
    assert event["payload"] == {"course_id": "course", "task_id": "task", "task_revision": "v1",
                                "completed_at": row.completed_at.replace(tzinfo=timezone.utc).isoformat()}
    progress.mark_task_completed(1, "task", "v2", session, course_id="course", expected_account_ref=REF)
    assert [json.loads(raw)["revision"] for raw in session.execute(select(outbox.c.payload).order_by(outbox.c.revision)).scalars()] == [1, 2]


def test_deletion_staging_follows_source_transaction(session):
    activity.record_activity_day(1, date(2026, 11, 17), session, expected_account_ref=REF)
    assert session.execute(select(outbox.c.kind)).scalar_one() == "legacy_activity_day"
    integration.stage_deletion(session, 1)
    session.query(models.UserActivityDay).filter_by(user_id=1).delete()
    session.delete(session.get(models.User, 1))
    session.rollback()
    assert session.get(models.User, 1) is not None
    assert session.execute(select(outbox.c.kind)).scalar_one() == "legacy_activity_day"
    integration.stage_deletion(session, 1)
    session.query(models.UserActivityDay).filter_by(user_id=1).delete()
    session.delete(session.get(models.User, 1))
    session.commit()
    import json
    event = json.loads(session.execute(select(outbox.c.payload)).scalar_one())
    assert event["kind"] == "subject_deleted" and event["revision"] == 2


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("PYMENTOR_ANALYTICS_DELIVERY_ENABLED", raising=False)
    assert not integration.enabled()


def test_default_off_uses_existing_writer_without_outbox(session, monkeypatch):
    monkeypatch.delenv("PYMENTOR_ANALYTICS_DELIVERY_ENABLED", raising=False)
    activity.record_activity_day(1, date(2026, 11, 17), session)
    progress.mark_lesson_completed(1, "lesson", session, course_id="backend")
    assert session.query(models.UserActivityDay).count() == 1
    assert session.query(models.UserLessonProgress).count() == 1
    assert session.execute(select(outbox)).all() == []


def test_bounded_export_checkpoints_same_transaction(session):
    activity.record_activity_day(1, date(2026, 11, 17), session, expected_account_ref=REF)
    assert export.initialize(session, "activity") == 1
    session.commit()
    result = export.export_page(session, "activity", limit=1)
    assert result == {"count": 1, "cursor": 1, "high_watermark": 1, "scan_exhausted": False}
    session.rollback()
    result = export.export_page(session, "activity", limit=2)
    assert result == {"count": 1, "cursor": 1, "high_watermark": 1, "scan_exhausted": True}
    session.commit()
    result = export.export_page(session, "activity", limit=2)
    assert result["count"] == 0 and result["cursor"] == 1


def test_stale_inflight_cannot_write_reused_account_or_provision_identity(session):
    replacement = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    session.execute(text("UPDATE account_lifecycles SET lifecycle_id=:ref WHERE user_id=1"), {"ref": replacement})
    session.commit()
    for operation in (
        lambda: activity.record_activity_day(1, date(2026, 11, 17), session, expected_account_ref=REF),
        lambda: progress.mark_lesson_completed(1, "lesson", session, expected_account_ref=REF),
        lambda: progress.mark_task_completed(1, "task", "rev", session, expected_account_ref=REF),
    ):
        with pytest.raises(HTTPException) as error:
            operation()
        assert error.value.status_code == 401
        session.rollback()
    assert session.query(models.UserActivityDay).count() == 0
    assert session.query(models.UserLessonProgress).count() == 0
    assert session.query(models.UserTaskProgress).count() == 0
    assert session.execute(select(outbox)).all() == []
    assert session.execute(text("SELECT lifecycle_id FROM account_lifecycles WHERE user_id=1")).scalar_one() == replacement
    session.execute(text("DELETE FROM account_lifecycles WHERE user_id=1"))
    session.commit()
    with pytest.raises(HTTPException):
        progress.mark_lesson_completed(1, "lesson", session, expected_account_ref=REF)
    session.rollback()
    assert session.execute(text("SELECT count(*) FROM account_lifecycles")).scalar_one() == 0


def test_long_running_practice_keeps_original_lifecycle(session, monkeypatch):
    from types import SimpleNamespace
    from pymentor_core.modules.learning.api import service
    monkeypatch.setattr(service, "_get_available_task", lambda task_id, user: {"id": "task", "revision": "rev"})
    monkeypatch.setattr(service, "find_lesson_by_task", lambda task_id: {"id": "lesson", "tasks": [{"id": "task"}]})
    monkeypatch.setattr(service, "_uses_persisted_task_progress", lambda lesson: True)
    monkeypatch.setattr(service, "_lesson_tasks_are_complete", lambda *args: False)
    monkeypatch.setattr(service, "current_course_id", lambda: "backend")
    monkeypatch.setattr(service.progress, "mark_task_completed", progress.mark_task_completed)
    context = service._prepare_task_for_principal("task", "rev", SimpleNamespace(id=1, account_ref=REF))
    assert context["account_ref"] == REF
    session.execute(text("UPDATE account_lifecycles SET lifecycle_id=:ref WHERE user_id=1"),
                    {"ref": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"})
    session.commit()
    with pytest.raises(HTTPException) as error:
        service._write_task_progress(context, session)
    assert error.value.status_code == 401
    session.rollback()
    assert session.query(models.UserTaskProgress).count() == 0 and session.execute(select(outbox)).all() == []


def test_activity_route_passes_authenticated_lifecycle(session, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from pymentor_core.modules.identity.api import router
    from pymentor_core.modules.identity.api.schemas import ActivityVisit
    from pymentor_core.modules.identity.activity_policy import MOSCOW_TZ
    principal = SimpleNamespace(user_id=1, account_ref=REF)
    monkeypatch.setattr(router, "_local_async_identity", lambda request: object())
    async def current(request): return principal
    async def run(request, identity, operation): return operation(session)
    monkeypatch.setattr(router, "async_current_principal", current)
    monkeypatch.setattr(router, "_run_identity_sql", run)
    monkeypatch.setattr(router.activity, "record_activity_day", activity.record_activity_day)
    clock = SimpleNamespace(now=lambda: datetime(2026, 11, 17, 11, tzinfo=MOSCOW_TZ))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        composition_root=SimpleNamespace(for_request=lambda db: SimpleNamespace(clock=clock, uow=None)))))
    response = asyncio.run(router.record_activity_visit(ActivityVisit(activity_date=date(2026, 11, 17)), request, Response()))
    assert response["activity_date"] == "2026-11-17"
    import json
    assert json.loads(session.execute(select(outbox.c.payload)).scalar_one())["subject_ref"] == REF


def prepare(session):
    cutover.begin_preparation(session, old_writers_stopped=True)
    while not cutover.prepare_page(session, limit=1)["scan_exhausted"]:
        session.commit()
    cutover.seal_preparation(session)
    session.commit()


def test_prepare_pages_preserve_interleaved_mirror_writes_and_null(session, monkeypatch):
    from sqlalchemy import event
    session.add_all([models.UserActivityDay(user_id=1, activity_date=date(2026, 11, 15), visited_at=None),
                     models.UserActivityDay(user_id=1, activity_date=date(2026, 11, 16), visited_at=None)])
    session.commit()
    cutover.begin_preparation(session, old_writers_stopped=True)
    assert cutover.prepare_page(session, limit=1)["count"] == 1
    with pytest.raises(ValueError, match="unscanned"):
        cutover.seal_preparation(session)
    session.commit()
    stamp = datetime(2026, 11, 17, 11, tzinfo=timezone.utc)
    activity.record_activity_day(1, date(2026, 11, 15), session, visited_at=stamp, expected_account_ref=REF)
    activity.record_activity_day(1, date(2026, 11, 17), session, visited_at=stamp, expected_account_ref=REF)
    assert cutover.prepare_page(session, limit=1)["count"] == 1
    cutover.seal_preparation(session)
    cutover.begin_outbox(session, writers_stopped=True, coverage_proved=True)
    session.commit()
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "outbox_only")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_READ_MODE", "analytics")
    statements = []
    def collect(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())
    event.listen(session.bind, "before_cursor_execute", collect)
    try:
        previous = activity.record_activity_day(1, date(2026, 11, 15), session, visited_at=stamp, expected_account_ref=REF)
        filled = activity.record_activity_day(1, date(2026, 11, 16), session, visited_at=stamp, expected_account_ref=REF)
        new = activity.record_activity_day(1, date(2026, 11, 18), session, visited_at=stamp, expected_account_ref=REF)
    finally:
        event.remove(session.bind, "before_cursor_execute", collect)
    assert all("user_activity_days" not in sql for sql in statements)
    assert all(item.visited_at.replace(tzinfo=timezone.utc) == stamp for item in (previous, filled, new))


def test_outbox_only_visit_api_handler_avoids_legacy_sql(session, monkeypatch):
    import asyncio
    from sqlalchemy import event
    from types import SimpleNamespace
    from pymentor_core.modules.identity.api import router
    from pymentor_core.modules.identity.api.schemas import ActivityVisit
    prepare(session)
    cutover.begin_outbox(session, writers_stopped=True, coverage_proved=True)
    session.commit()
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "outbox_only")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_READ_MODE", "analytics")
    principal = SimpleNamespace(user_id=1, account_ref=REF)
    monkeypatch.setattr(router, "_local_async_identity", lambda request: object())
    async def current(request): return principal
    async def run(request, identity, operation): return operation(session)
    monkeypatch.setattr(router, "async_current_principal", current)
    monkeypatch.setattr(router, "_run_identity_sql", run)
    monkeypatch.setattr(router.activity, "record_activity_day", activity.record_activity_day)
    clock = SimpleNamespace(now=lambda: datetime(2026, 11, 17, 11, tzinfo=timezone.utc))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        composition_root=SimpleNamespace(for_request=lambda db: SimpleNamespace(clock=clock, uow=None)))))
    statements = []
    def collect(conn, cursor, statement, parameters, context, executemany): statements.append(statement.lower())
    event.listen(session.bind, "before_cursor_execute", collect)
    try:
        result = asyncio.run(router.record_activity_visit(ActivityVisit(activity_date=date(2026, 11, 17)), request, Response()))
    finally:
        event.remove(session.bind, "before_cursor_execute", collect)
    assert result["activity_date"] == "2026-11-17"
    assert all("user_activity_days" not in sql for sql in statements)


def test_outbox_only_activity_replays_before_mirror_rollback(session, monkeypatch):
    prepare(session)
    cutover.begin_outbox(session, writers_stopped=True, coverage_proved=True)
    session.commit()
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "outbox_only")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_READ_MODE", "analytics")
    first_time = datetime(2026, 11, 17, 11, tzinfo=timezone.utc)
    later = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
    first = activity.record_activity_day(1, date(2026, 11, 17), session, visited_at=first_time, expected_account_ref=REF)
    repeated = activity.record_activity_day(1, date(2026, 11, 17), session, visited_at=later, expected_account_ref=REF)
    assert first.visited_at.replace(tzinfo=timezone.utc) == repeated.visited_at.replace(tzinfo=timezone.utc) == first_time
    assert session.query(models.UserActivityDay).count() == 0
    assert len(session.execute(select(outbox.c.event_id)).all()) == 1
    assert session.execute(select(activity_state.c.needs_core_replay)).scalar_one() is True
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "mirror")
    cutover.begin_replay(session, writers_stopped=True)
    session.commit()
    with pytest.raises(HTTPException) as blocked:
        activity.record_activity_day(1, date(2026, 11, 18), session, expected_account_ref=REF)
    assert blocked.value.status_code == 503
    with pytest.raises(ValueError):
        export.replay_core_page(session, writers_stopped=False)
    with pytest.raises(ValueError):
        cutover.finish_replay(session, writers_stopped=True)
    result = export.replay_core_page(session, writers_stopped=True)
    assert result["count"] == 1
    session.commit()
    cutover.finish_replay(session, writers_stopped=True)
    session.commit()
    restored = session.query(models.UserActivityDay).filter_by(user_id=1, activity_date=date(2026, 11, 17)).one()
    assert restored.visited_at.replace(tzinfo=timezone.utc) == first_time
    activity.record_activity_day(1, date(2026, 11, 18), session, expected_account_ref=REF)
    assert session.query(models.UserActivityDay).count() == 2


def test_outbox_only_existing_null_is_preserved_until_replay(session, monkeypatch):
    session.add(models.UserActivityDay(user_id=1, activity_date=date(2026, 11, 16), visited_at=None))
    session.commit()
    prepare(session)
    cutover.begin_outbox(session, writers_stopped=True, coverage_proved=True)
    session.commit()
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "outbox_only")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_READ_MODE", "analytics")
    timestamp = datetime(2026, 11, 17, 11, tzinfo=timezone.utc)
    activity.record_activity_day(1, date(2026, 11, 16), session, visited_at=timestamp, expected_account_ref=REF)
    old = session.query(models.UserActivityDay).filter_by(user_id=1, activity_date=date(2026, 11, 16)).one()
    assert old.visited_at is None
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "mirror")
    cutover.begin_replay(session, writers_stopped=True)
    session.commit()
    assert export.replay_core_page(session, writers_stopped=True)["count"] == 1
    session.commit()
    cutover.finish_replay(session, writers_stopped=True)
    session.commit()
    assert old.visited_at.replace(tzinfo=timezone.utc) == timestamp


def test_cutover_gate_rejects_unproven_and_mixed_writer_mode(session):
    with pytest.raises(ValueError):
        cutover.begin_outbox(session, writers_stopped=True, coverage_proved=False)
    with pytest.raises(ValueError):
        cutover.begin_outbox(session, writers_stopped=True, coverage_proved=True)
    prepare(session)
    cutover.begin_outbox(session, writers_stopped=True, coverage_proved=True)
    session.commit()
    # This process still has mirror configuration after another process changed the gate.
    with pytest.raises(HTTPException) as error:
        activity.record_activity_day(1, date(2026, 11, 17), session, expected_account_ref=REF)
    assert error.value.status_code == 503
    assert session.query(models.UserActivityDay).count() == 0 and session.execute(select(outbox)).all() == []


def test_replay_conflict_rolls_back_without_losing_source(session, monkeypatch):
    prepare(session)
    cutover.begin_outbox(session, writers_stopped=True, coverage_proved=True)
    session.commit()
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "outbox_only")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_READ_MODE", "analytics")
    when = datetime(2026, 11, 17, 11, tzinfo=timezone.utc)
    activity.record_activity_day(1, date(2026, 11, 17), session, visited_at=when, expected_account_ref=REF)
    # Model an unsafe old writer, which does not honor the new gate.
    session.add(models.UserActivityDay(user_id=1, activity_date=date(2026, 11, 17),
                                       visited_at=datetime(2026, 11, 17, 12, tzinfo=timezone.utc)))
    session.commit()
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "mirror")
    cutover.begin_replay(session, writers_stopped=True)
    session.commit()
    with pytest.raises(ValueError, match="timestamp conflict"):
        export.replay_core_page(session, writers_stopped=True)
    session.rollback()
    assert session.execute(select(activity_state.c.needs_core_replay)).scalar_one() is True
    assert session.execute(select(write_gate.c.mode)).scalar_one() == "replaying"
    assert session.query(models.UserActivityDay).one().visited_at.hour == 12


def test_replay_resumes_by_bounded_pages_after_rollback(session, monkeypatch):
    prepare(session)
    cutover.begin_outbox(session, writers_stopped=True, coverage_proved=True)
    session.commit()
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "outbox_only")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_READ_MODE", "analytics")
    for day in (16, 17):
        activity.record_activity_day(1, date(2026, 11, day), session, expected_account_ref=REF)
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "mirror")
    cutover.begin_replay(session, writers_stopped=True)
    session.commit()
    assert export.replay_core_page(session, writers_stopped=True, limit=1) == {"count": 1, "scan_exhausted": False}
    session.commit()
    with pytest.raises(ValueError):
        cutover.finish_replay(session, writers_stopped=True)
    assert export.replay_core_page(session, writers_stopped=True, limit=1)["count"] == 1
    session.rollback()  # interrupted page must not advance durable state
    assert session.query(models.UserActivityDay).count() == 1
    assert export.replay_core_page(session, writers_stopped=True, limit=1)["count"] == 1
    session.commit()
    cutover.finish_replay(session, writers_stopped=True)
    session.commit()
    assert session.query(models.UserActivityDay).count() == 2
