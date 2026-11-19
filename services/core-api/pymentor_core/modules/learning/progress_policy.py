"""Pure lesson progress policy and stable completion markers."""

import hashlib
import json
import re


# Compatibility export; optional identities belong to each course catalog.
OPTIONAL_TASK_IDS = frozenset()


def lesson_progress_policy(lesson: dict) -> dict:
    return lesson.get("progress_policy", {})


def uses_persisted_task_progress(lesson: dict) -> bool:
    return lesson_progress_policy(lesson).get("revision_tracked", True) is True


def uses_card_completion(lesson: dict | None) -> bool:
    return isinstance(lesson, dict) and lesson_progress_policy(lesson).get("completion", "cards") == "cards"


def is_overview_lesson(lesson: dict) -> bool:
    """Only the course catalog can designate an automatically complete overview."""
    return lesson_progress_policy(lesson).get("overview") is True


def is_countable_lesson(lesson: dict) -> bool:
    return is_overview_lesson(lesson) or bool(
        lesson.get("tasks") or lesson.get("self_check")
    )


def is_task_required(task: dict) -> bool:
    """Tasks are required by default; only explicit metadata or this stable mapping can opt out."""
    return task.get("required") is not False


def required_lesson_tasks(lesson: dict) -> list[dict]:
    optional = lesson_progress_policy(lesson).get("optional_task_ids", [])
    return [task for task in lesson.get("tasks", []) if is_task_required(task) and task.get("id") not in optional]


def chapter_one_completion_task_id(lesson_id: str) -> str:
    """Stable internal progress key for a chapter 01 manual self-check."""
    return f"__pymentor_lesson_completion__:{lesson_id}"


def chapter_one_completion_revision(lesson: dict) -> str:
    """Tie a saved self-check to its current authored content without a schema change."""
    authored_check = {
        "self_check": lesson.get("self_check"),
        "manual_practice": lesson.get("manual_practice", []),
    }
    serialized = json.dumps(
        authored_check,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def needs_chapter_one_self_check_progress(lesson: dict) -> bool:
    return (
        uses_card_completion(lesson)
        and bool(lesson.get("self_check"))
        and (bool(lesson.get("manual_practice")) or not lesson.get("tasks"))
    )


def lesson_completion_requirements(lesson: dict):
    """Yield alternative proofs: a lesson receipt flag and the required task revisions."""
    tasks = lesson.get("tasks", [])
    if uses_card_completion(lesson):
        # Pre-card completions remain valid alongside the revision-pinned proofs.
        yield True, ()
        for task_id, revision in lesson_progress_policy(lesson).get("legacy_task_completions", {}).items():
            yield False, ((task_id, revision),)
        required = tuple((task["id"], task.get("revision")) for task in required_lesson_tasks(lesson))
        if lesson.get("manual_practice") or not tasks:
            if needs_chapter_one_self_check_progress(lesson):
                marker = (chapter_one_completion_task_id(lesson["id"]), chapter_one_completion_revision(lesson))
                yield False, (*required, marker)
        elif required:
            yield False, required
    elif not uses_persisted_task_progress(lesson) or not tasks:
        yield True, ()
    else:
        yield bool(lesson.get("manual_practice")), tuple((task["id"], task.get("revision")) for task in tasks)
