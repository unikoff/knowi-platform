import os
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://synthetic@127.0.0.1:1/isolated")

from datetime import date, datetime, timezone
import json

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from pymentor_core.adapters.sql import models
from pymentor_core.adapters.sql.analytics_delivery import activity_state, enqueue, metadata, outbox, write_gate
from pymentor_core.modules.analytics_delivery.contracts import activity
from pymentor_core.adapters.sql.analytics_delivery_ops import export, integration, snapshot_export
from pymentor_core.modules.analytics_delivery.snapshot_contract import digest_entities
from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_sql import metadata as proof_metadata, proofs

REF = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def backfill_and_seal(db):
    for stream in ("activity", "progress"):
        export.initialize(db, stream)
        export.export_page(db, stream)
        export.seal_backfill(db, stream)
    db.commit()


@pytest.fixture
def db(synthetic_engine, monkeypatch):
    monkeypatch.setenv("PYMENTOR_ANALYTICS_DELIVERY_ENABLED", "1")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED", "1")
    engine = synthetic_engine
    for model in (models.User, models.UserActivityDay, models.UserLessonProgress, models.UserTaskProgress):
        model.__table__.create(engine)
    metadata.create_all(engine)
    proof_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE account_lifecycles(user_id INTEGER PRIMARY KEY, lifecycle_id VARCHAR(36) UNIQUE NOT NULL)"))
        conn.execute(text("INSERT INTO users(id,username,email,password_hash,is_admin,verified) VALUES (1,'synthetic','synthetic@example.test','hash',false,true)"))
        conn.execute(text("INSERT INTO account_lifecycles VALUES (1,:ref)"), {"ref": REF})
        conn.execute(write_gate.insert().values(singleton=1, mode="mirror"))
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_snapshot_stages_only_the_date_even_when_a_legacy_timestamp_exists(db, monkeypatch):
    monkeypatch.setenv("PYMENTOR_ANALYTICS_HISTORY_DATES_ONLY", "1")
    db.add(models.UserActivityDay(user_id=1, activity_date=date(2026, 11, 15),
                                 visited_at=datetime(2026, 11, 15, 11, tzinfo=timezone.utc)))
    db.add(models.UserLessonProgress(user_id=1, course_id="backend", lesson_id="lesson-1",
                                    completed_at=datetime(2026, 11, 16, 21, 30, tzinfo=timezone.utc)))
    db.commit()
    backfill_and_seal(db)
    first = snapshot_export.stage_subject(db, 1, REF)
    db.commit()
    events = [json.loads(raw) for raw in db.execute(select(outbox.c.payload)).scalars()]
    assert first["cut_revision"] == 0 and first["entity_count"] == 2
    assert first["entities_digest"] == digest_entities(events)
    assert next(x for x in events if x["kind"] == "legacy_activity_day")["payload"]["visited_at"] is None
    again = snapshot_export.stage_subject(db, 1, REF)
    db.commit()
    assert again == first and len(db.execute(select(proofs)).all()) == 1
    assert len(db.execute(select(outbox)).all()) == 2


def test_task_backfill_requires_seal_and_upgrades_verified_v1_proof(db, monkeypatch):
    db.add(models.UserTaskProgress(user_id=1, course_id="backend", task_id="task", task_revision="v1",
                                   completed_at=datetime(2026, 11, 16, tzinfo=timezone.utc)))
    db.commit()
    backfill_and_seal(db)
    first = snapshot_export.stage_subject(db, 1, REF)
    db.execute(proofs.update().where(proofs.c.proof_id == first["proof_id"]).values(status="verified"))
    db.commit()
    monkeypatch.setenv("PYMENTOR_ANALYTICS_TASK_PROGRESS_ENABLED", "1")
    with pytest.raises(ValueError, match="backfill is not sealed"):
        snapshot_export.stage_subject(db, 1, REF)
    db.rollback()
    export.initialize(db, "tasks")
    export.export_page(db, "tasks")
    export.seal_backfill(db, "tasks")
    db.commit()
    second = snapshot_export.stage_subject(db, 1, REF)
    db.commit()
    assert (first["schema_version"], first["entity_count"]) == (1, 0)
    assert (second["schema_version"], second["scope"], second["cut_revision"], second["entity_count"]) == (2, "subject_all_v2", 1, 1)
    assert json.loads(db.execute(select(outbox.c.payload)).scalar_one())["kind"] == "task_progress"


def test_same_cut_changed_source_is_rejected(db):
    db.add(models.UserActivityDay(user_id=1, activity_date=date(2026, 11, 15), visited_at=None))
    db.commit()
    backfill_and_seal(db)
    snapshot_export.stage_subject(db, 1, REF)
    db.commit()
    day = db.query(models.UserActivityDay).one()
    day.activity_date = date(2026, 11, 17)
    db.commit()  # models an old, uncaptured writer
    with pytest.raises(ValueError, match="source fact lacks captured backfill or live event"):
        snapshot_export.stage_subject(db, 1, REF)
    db.rollback()
    assert len(db.execute(select(proofs)).all()) == 1
    assert len(db.execute(select(outbox)).all()) == 1


def test_expired_snapshot_never_partially_commits(db):
    db.add(models.UserActivityDay(user_id=1, activity_date=date(2026, 11, 15), visited_at=None))
    db.commit()
    backfill_and_seal(db)
    ticks = iter([0.0, 11.0])
    with pytest.raises(TimeoutError):
        snapshot_export.stage_subject(db, 1, REF, deadline_seconds=10, clock=lambda: next(ticks))
    db.rollback()
    assert db.execute(select(proofs)).all() == []
    assert len(db.execute(select(outbox)).all()) == 1


def test_mismatched_live_history_rolls_back_all_missing_baselines(db):
    from uuid import UUID
    db.add(models.UserActivityDay(user_id=1, activity_date=date(2026, 11, 15), visited_at=None))
    db.add(models.UserActivityDay(user_id=1, activity_date=date(2026, 11, 16), visited_at=datetime(2026, 11, 17, tzinfo=timezone.utc)))
    db.commit()
    backfill_and_seal(db)
    changed = db.query(models.UserActivityDay).filter_by(activity_date=date(2026, 11, 16)).one()
    changed.visited_at = None
    db.commit()
    with pytest.raises(ValueError, match="matching captured revision"):
        snapshot_export.stage_subject(db, 1, REF)
    db.rollback()
    assert len(db.execute(select(outbox)).all()) == 2
    assert db.execute(select(proofs)).all() == []


def test_dirty_outbox_only_state_requires_live_fact(db, monkeypatch):
    from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_sql import preparation
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", "outbox_only")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_LEGACY_READ_MODE", "analytics")
    db.execute(write_gate.update().values(mode="outbox_only"))
    db.execute(preparation.insert().values(singleton=1, high_watermark=0, cursor=0, sealed=True))
    db.execute(activity_state.insert().values(subject_ref=REF, activity_date=date(2026, 11, 17),
        visited_at=datetime(2026, 11, 17, tzinfo=timezone.utc), needs_core_replay=True))
    db.commit()
    backfill_and_seal(db)
    with pytest.raises(ValueError, match="lacks captured live revision"):
        snapshot_export.stage_subject(db, 1, REF)
    db.rollback()
    assert db.execute(select(outbox)).all() == [] and db.execute(select(proofs)).all() == []


def test_delete_removes_proofs_even_after_proof_flag_disabled(db, monkeypatch):
    backfill_and_seal(db)
    snapshot_export.stage_subject(db, 1, REF)
    db.commit()
    monkeypatch.delenv("PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED", raising=False)
    integration.stage_deletion(db, 1)
    db.commit()
    assert db.execute(select(proofs)).all() == []
    assert db.execute(select(outbox.c.kind)).scalar_one() == "subject_deleted"


def test_backfill_barrier_requires_exhaustion_and_prevents_old_page(db, monkeypatch):
    db.add(models.UserActivityDay(user_id=1, activity_date=date(2026, 11, 15), visited_at=None))
    db.commit()
    export.initialize(db, "activity")
    with pytest.raises(ValueError, match="unscanned"):
        export.seal_backfill(db, "activity")
    export.export_page(db, "activity")
    export.seal_backfill(db, "activity")
    export.seal_backfill(db, "activity")
    db.commit()
    monkeypatch.delenv("PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED")
    with pytest.raises(ValueError, match="sealed"):
        export.export_page(db, "activity")
