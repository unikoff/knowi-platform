"""SQL persistence of lesson and task progress."""

from datetime import datetime
from datetime import timezone
from sqlalchemy import literal
from sqlalchemy import select
from sqlalchemy import union_all
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pymentor_core.shared.ports import UnitOfWork
from pymentor_core.adapters.sql import models


def get_completion_facts(
    user_id: int, db: Session, *, course_id: str
) -> tuple[dict[str, datetime], dict[str, tuple[str, datetime]]]:
    """Read lesson and task receipts together so mixed lessons use one snapshot."""
    lesson = models.UserLessonProgress
    task = models.UserTaskProgress
    rows = db.execute(union_all(
        select(lesson.lesson_id, literal(None, type_=task.task_revision.type), lesson.completed_at)
        .where(lesson.user_id == user_id, lesson.course_id == course_id),
        select(task.task_id, task.task_revision, task.completed_at)
        .where(task.user_id == user_id, task.course_id == course_id),
    ))
    lesson_facts = {}
    task_facts = {}
    for item_id, revision, completed_at in rows:
        if revision is None:
            lesson_facts[item_id] = completed_at
        else:
            task_facts[item_id] = (revision, completed_at)
    return lesson_facts, task_facts


def get_completed_lesson_ids(user_id: int, db: Session, *, course_id: str = "backend") -> set[str]:
    rows = (
        db.query(models.UserLessonProgress.lesson_id)
        .filter(models.UserLessonProgress.user_id == user_id, models.UserLessonProgress.course_id == course_id)
        .all()
    )
    return {row.lesson_id for row in rows}


def mark_lesson_completed(
    user_id: int,
    lesson_id: str,
    db: Session | None,
    *,
    uow: UnitOfWork | None = None,
    course_id: str = "backend",
    expected_account_ref: str | None = None,
) -> None:
    from pymentor_core.adapters.sql.analytics_delivery_ops import integration
    if integration.enabled():
        return integration.complete_lesson(integration.session_for(db, uow), user_id, lesson_id, course_id, expected_account_ref)
    if expected_account_ref is not None:
        integration.guard_lifecycle(integration.session_for(db, uow), user_id, expected_account_ref)
    if uow is not None:
        repository = uow.repository(models.UserLessonProgress)
        repository.add(models.UserLessonProgress(user_id=user_id, lesson_id=lesson_id, course_id=course_id))
        try:
            uow.commit()
        except IntegrityError:
            uow.rollback()
            if repository.find_one(user_id=user_id, lesson_id=lesson_id, course_id=course_id) is None:
                raise
        return

    if db is None:
        raise ValueError("db is required when no unit of work is supplied")
    progress = models.UserLessonProgress(user_id=user_id, lesson_id=lesson_id, course_id=course_id)
    db.add(progress)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.query(models.UserLessonProgress).filter_by(user_id=user_id, lesson_id=lesson_id, course_id=course_id).first() is None:
            raise


def get_completed_task_revisions(
    user_id: int, task_ids: list[str], db: Session, *, course_id: str = "backend"
) -> dict[str, str]:
    if not task_ids:
        return {}

    rows = (
        db.query(models.UserTaskProgress.task_id, models.UserTaskProgress.task_revision)
        .filter(
            models.UserTaskProgress.user_id == user_id,
            models.UserTaskProgress.course_id == course_id,
            models.UserTaskProgress.task_id.in_(task_ids),
        )
        .all()
    )
    return {row.task_id: row.task_revision for row in rows}


def mark_task_completed(
    user_id: int,
    task_id: str,
    task_revision: str,
    db: Session | None,
    *,
    uow: UnitOfWork | None = None,
    course_id: str = "backend",
    expected_account_ref: str | None = None,
) -> None:
    from pymentor_core.adapters.sql.analytics_delivery_ops import integration
    if integration.task_enabled():
        return integration.complete_task(integration.session_for(db, uow), user_id, task_id, task_revision,
                                         course_id, expected_account_ref)
    capture_enabled = integration.enabled() or expected_account_ref is not None
    if capture_enabled:
        integration.guard_lifecycle(integration.session_for(db, uow), user_id, expected_account_ref)
    if uow is not None:
        repository = uow.repository(models.UserTaskProgress)
        progress = repository.find_one(user_id=user_id, task_id=task_id, course_id=course_id)
        if progress is not None:
            if progress.task_revision == task_revision:
                return
            progress.task_revision = task_revision
            progress.completed_at = datetime.now(timezone.utc)
        else:
            repository.add(
                models.UserTaskProgress(
                    user_id=user_id,
                    task_id=task_id,
                    course_id=course_id,
                    task_revision=task_revision,
                )
            )

        try:
            uow.commit()
        except IntegrityError:
            uow.rollback()
            if capture_enabled:
                integration.guard_lifecycle(integration.session_for(db, uow), user_id, expected_account_ref)
            progress = repository.find_one(user_id=user_id, task_id=task_id, course_id=course_id)
            if progress is None:
                raise
            if progress.task_revision != task_revision:
                progress.task_revision = task_revision
                progress.completed_at = datetime.now(timezone.utc)
                uow.commit()
        return

    if db is None:
        raise ValueError("db is required when no unit of work is supplied")
    progress = (
        db.query(models.UserTaskProgress)
        .filter(
            models.UserTaskProgress.user_id == user_id,
            models.UserTaskProgress.course_id == course_id,
            models.UserTaskProgress.task_id == task_id,
        )
        .first()
    )
    if progress is not None:
        if progress.task_revision == task_revision:
            return
        progress.task_revision = task_revision
        progress.completed_at = datetime.now(timezone.utc)
    else:
        db.add(
            models.UserTaskProgress(
                user_id=user_id,
                task_id=task_id,
                course_id=course_id,
                task_revision=task_revision,
            )
        )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if capture_enabled:
            integration.guard_lifecycle(db, user_id, expected_account_ref)
        progress = (
            db.query(models.UserTaskProgress)
            .filter(
                models.UserTaskProgress.user_id == user_id,
                models.UserTaskProgress.course_id == course_id,
                models.UserTaskProgress.task_id == task_id,
            )
            .first()
        )
        if progress is None:
            raise
        if progress.task_revision != task_revision:
            progress.task_revision = task_revision
            progress.completed_at = datetime.now(timezone.utc)
            db.commit()
