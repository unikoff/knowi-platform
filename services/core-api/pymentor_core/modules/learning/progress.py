"""Public lesson progress policy and storage facade."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymentor_core.modules.learning.progress_policy import OPTIONAL_TASK_IDS
from pymentor_core.modules.learning.progress_policy import (
    chapter_one_completion_revision,
)
from pymentor_core.modules.learning.progress_policy import (
    chapter_one_completion_task_id,
)
from pymentor_core.modules.learning.progress_policy import is_task_required
from pymentor_core.modules.learning.progress_policy import required_lesson_tasks
from pymentor_core.shared.bindings import get_bindings

if TYPE_CHECKING:
    from datetime import datetime
    from pymentor_core.shared.ports import UnitOfWork
    from sqlalchemy.orm import Session


def get_completed_lesson_ids(user_id: int, db: Session, *, course_id: str = "backend") -> set[str]:
    return get_bindings().progress.get_completed_lesson_ids(user_id, db, course_id=course_id)


def get_completion_facts(
    user_id: int, db: Session, *, course_id: str
) -> tuple[dict[str, datetime], dict[str, tuple[str, datetime]]]:
    return get_bindings().progress.get_completion_facts(user_id, db, course_id=course_id)


def mark_lesson_completed(
    user_id: int, lesson_id: str, db: Session | None, *, uow: UnitOfWork | None = None, course_id: str = "backend",
    expected_account_ref: str | None = None,
) -> None:
    kwargs = {"uow": uow, "course_id": course_id}
    if expected_account_ref is not None:
        kwargs["expected_account_ref"] = expected_account_ref
    return get_bindings().progress.mark_lesson_completed(user_id, lesson_id, db, **kwargs)


def get_completed_task_revisions(
    user_id: int, task_ids: list[str], db: Session, *, course_id: str = "backend"
) -> dict[str, str]:
    return get_bindings().progress.get_completed_task_revisions(user_id, task_ids, db, course_id=course_id)


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
    kwargs = {"uow": uow, "course_id": course_id}
    if expected_account_ref is not None:
        kwargs["expected_account_ref"] = expected_account_ref
    return get_bindings().progress.mark_task_completed(user_id, task_id, task_revision, db, **kwargs)
