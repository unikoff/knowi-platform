"""Opt-in source writes; each commits source, lifecycle, revision and outbox together."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import delete, insert, inspect, select, text, update

from pymentor_core.adapters.sql import models
from pymentor_core.adapters.sql.analytics_delivery import activity_state, enqueue, next_revision, outbox, write_gate
from pymentor_core.modules.analytics_delivery.contracts import activity, deletion, progress, task_progress
from pymentor_core.modules.analytics_delivery.integration import enabled, task_enabled, write_mode


def ensure_mirror_ready(db):
    if db.execute(select(activity_state.c.subject_ref).where(activity_state.c.needs_core_replay.is_(True)).limit(1)).first():
        raise HTTPException(status_code=503, detail="Activity rollback reconciliation required")


def require_write_gate(db, expected: str) -> None:
    current = db.execute(select(write_gate.c.mode).where(write_gate.c.singleton == 1).with_for_update(read=True)).scalar_one_or_none()
    if current != expected:
        raise HTTPException(status_code=503, detail="Activity write mode unavailable")
    if expected == "outbox_only":
        from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_export import proof_enabled
        from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_sql import preparation
        if not proof_enabled() or db.execute(select(preparation.c.sealed).where(preparation.c.singleton == 1)).scalar_one_or_none() is not True:
            raise HTTPException(status_code=503, detail="Activity state preparation unavailable")


def session_for(db, uow):
    session = getattr(uow, "session", None) if uow is not None else db
    if session is None:
        raise RuntimeError("analytics capture requires a SQLAlchemy unit of work")
    return session


def lifecycle(db, user_id: int) -> UUID:
    """Acquire the same user lock as guarded deletion, provisioning atomically."""
    user = db.query(models.User.id).filter(models.User.id == user_id).with_for_update().one_or_none()
    if user is None:
        raise ValueError("account unavailable")
    db.execute(text("""INSERT INTO account_lifecycles(user_id,lifecycle_id)
        VALUES (:uid,:ref) ON CONFLICT (user_id) DO NOTHING"""),
        {"uid": user_id, "ref": str(uuid4())})
    ref = db.execute(text("SELECT lifecycle_id FROM account_lifecycles WHERE user_id=:uid"),
                     {"uid": user_id}).scalar_one()
    return UUID(str(ref))


def guard_lifecycle(db, user_id: int, expected_ref: str | None) -> UUID:
    """Lock the account and require the authenticated lifecycle, without provisioning."""
    try:
        if not expected_ref or str(UUID(expected_ref)) != expected_ref:
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(status_code=401, detail="Account lifecycle changed") from None
    user = db.query(models.User.id).filter(models.User.id == user_id).with_for_update().one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Account lifecycle changed")
    ref = db.execute(text("SELECT lifecycle_id FROM account_lifecycles WHERE user_id=:uid"),
                     {"uid": user_id}).scalar_one_or_none()
    if ref != expected_ref:
        raise HTTPException(status_code=401, detail="Account lifecycle changed")
    return UUID(expected_ref)


def record_visit(db, user_id, activity_date, visited_at, expected_ref):
    try:
        mode = write_mode()
        require_write_gate(db, mode)
        ref = guard_lifecycle(db, user_id, expected_ref)
        if mode == "outbox_only":
            return _record_outbox_only(db, user_id, ref, activity_date, visited_at)
        ensure_mirror_ready(db)
        day = db.query(models.UserActivityDay).filter_by(user_id=user_id, activity_date=activity_date).one_or_none()
        timestamp = visited_at or datetime.now(timezone.utc)
        changed = day is None or day.visited_at is None
        if day is None:
            day = models.UserActivityDay(user_id=user_id, activity_date=activity_date, visited_at=timestamp)
            db.add(day)
        elif day.visited_at is None:
            day.visited_at = timestamp
        if changed:
            revision = next_revision(db, ref)
            enqueue(db, activity(ref, activity_date, day.visited_at, revision=revision), timestamp)
            sync_prepared_day(db, ref, activity_date, day.visited_at)
        db.commit()
        db.refresh(day)
        return day
    except Exception:
        db.rollback()
        raise


def _record_outbox_only(db, user_id, ref, activity_date, visited_at):
    ref_text = str(ref)
    row = db.execute(select(activity_state).where(activity_state.c.subject_ref == ref_text,
        activity_state.c.activity_date == activity_date).with_for_update()).mappings().one_or_none()
    timestamp = visited_at or datetime.now(timezone.utc)
    if row is None:
        chosen = timestamp
        db.execute(insert(activity_state).values(subject_ref=ref_text, activity_date=activity_date,
                                                 visited_at=chosen, needs_core_replay=True))
        enqueue(db, activity(ref, activity_date, chosen, revision=next_revision(db, ref)), timestamp)
    else:
        chosen = row["visited_at"]
        if chosen is None:
            chosen = timestamp
            db.execute(update(activity_state).where(activity_state.c.subject_ref == ref_text,
                activity_state.c.activity_date == activity_date).values(visited_at=chosen, needs_core_replay=True))
            enqueue(db, activity(ref, activity_date, chosen, revision=next_revision(db, ref)), timestamp)
    db.commit()
    return SimpleNamespace(activity_date=activity_date, visited_at=chosen)


def sync_prepared_day(db, ref, day, visited_at):
    """Called under the subject lock or exclusive preparation gate."""
    ref = str(ref)
    row = db.execute(select(activity_state).where(activity_state.c.subject_ref == ref,
        activity_state.c.activity_date == day).with_for_update()).mappings().one_or_none()
    if row is None:
        db.execute(insert(activity_state).values(subject_ref=ref, activity_date=day,
            visited_at=visited_at, needs_core_replay=False))
    elif row["needs_core_replay"]:
        raise ValueError("activity state requires replay")
    else:
        db.execute(update(activity_state).where(activity_state.c.subject_ref == ref,
            activity_state.c.activity_date == day).values(visited_at=visited_at))


def complete_lesson(db, user_id, lesson_id, course_id, expected_ref):
    try:
        ref = guard_lifecycle(db, user_id, expected_ref)
        existing = db.query(models.UserLessonProgress).filter_by(
            user_id=user_id, lesson_id=lesson_id, course_id=course_id).one_or_none()
        if existing is None:
            now = datetime.now(timezone.utc)
            db.add(models.UserLessonProgress(user_id=user_id, lesson_id=lesson_id,
                                            course_id=course_id, completed_at=now))
            revision = next_revision(db, ref)
            enqueue(db, progress(ref, course_id, lesson_id, now, revision=revision), now)
        db.commit()
    except Exception:
        db.rollback()
        raise


def complete_task(db, user_id, task_id, task_revision, course_id, expected_ref):
    try:
        ref = guard_lifecycle(db, user_id, expected_ref)
        row = db.query(models.UserTaskProgress).filter_by(
            user_id=user_id, task_id=task_id, course_id=course_id).one_or_none()
        if row is None or row.task_revision != task_revision:
            now = datetime.now(timezone.utc)
            if row is None:
                db.add(models.UserTaskProgress(user_id=user_id, task_id=task_id, course_id=course_id,
                                               task_revision=task_revision, completed_at=now))
            else:
                row.task_revision, row.completed_at = task_revision, now
            enqueue(db, task_progress(ref, course_id, task_id, task_revision, now,
                                      revision=next_revision(db, ref)), now)
        db.commit()
    except Exception:
        db.rollback()
        raise


def stage_deletion(db, user_id):
    """Call while locked, before account removal, without committing here."""
    ref = lifecycle(db, user_id)
    now = datetime.now(timezone.utc)
    db.execute(delete(outbox).where(outbox.c.subject_ref == str(ref), outbox.c.kind != "subject_deleted"))
    db.execute(delete(activity_state).where(activity_state.c.subject_ref == str(ref)))
    # Once a009 has ever been installed, its queued proofs must be removed even
    # if its feature flag is later disabled. Inspect within this transaction.
    from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_sql import proofs
    if inspect(db.connection()).has_table(proofs.name):
        db.execute(delete(proofs).where(proofs.c.subject_ref == str(ref)))
    enqueue(db, deletion(ref, revision=next_revision(db, ref)), now)
