"""Course access, material delivery and solution submission workflows."""

import logging
import json
from urllib.request import Request as URLRequest, urlopen
from concurrent.futures import ThreadPoolExecutor
import os
from copy import deepcopy
from time import perf_counter
from typing import Any

from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from pymentor_core.shared.ports import UnitOfWork
from pymentor_core.modules.learning.ports import RunnerPolicy
from pymentor_core.modules.learning.api import solve_bridge
from pymentor_core.modules.identity.principal import Principal
from pymentor_core.modules.practice.save_progress import save_progress
from pymentor_core.modules.practice.public_specs import public_submit_result
from pymentor_core.modules.learning.complete_lesson import CompletionError
from pymentor_core.modules.learning.complete_lesson import CompletionPolicy
from pymentor_core.modules.learning.complete_lesson import CompletionWrites
from pymentor_core.modules.learning.complete_lesson import (
    complete_lesson as complete_lesson_use_case,
)
from pymentor_core.modules.practice.runner import run_python_code
from pymentor_core.modules.practice.runner import run_python_preview
from pymentor_core.modules.practice.runner import run_python_task
from pymentor_core.modules.practice.runner import RunnerBusy
from pymentor_core.modules.practice.runner import RunnerUnavailable
from pymentor_core.modules.practice.runner import get_runner_policy
from pymentor_core.modules.practice.runner import get_runner_executor
from pymentor_core.app.database import SessionLocal
from pymentor_core.modules.learning.course_context import current_course_id
from pymentor_core.modules.learning.catalog import default_track_id
from pymentor_core.modules.learning.catalog import find_lesson
from pymentor_core.modules.learning.catalog import find_lesson_by_task
from pymentor_core.modules.learning.catalog import find_task
from pymentor_core.modules.learning.catalog import get_content_revision
from pymentor_core.modules.learning.catalog import find_track
from pymentor_core.modules.learning.catalog import get_public_lessons
from pymentor_core.modules.learning.catalog import get_tracks_summary
from pymentor_core.modules.identity import public as accounts
from pymentor_core.modules.learning import progress
from pymentor_core.modules.learning.progress_policy import is_overview_lesson, uses_persisted_task_progress, uses_card_completion, lesson_progress_policy
from pymentor_core.modules.learning.api.access import get_optional_user
from pymentor_core.modules.learning.api.access import get_lesson_access
from pymentor_core.modules.learning.api.schemas import CodeSubmitRequest
from pymentor_core.modules.learning.api.schemas import InterpreterRunRequest


logger = logging.getLogger("pymentor.runner")
RUNNER_ENABLED_ENV = "PYMENTOR_PUBLIC_RUNNER_ENABLED"
_PRINCIPAL_UNSET = object()


def _require_runner_enabled() -> None:
    """Allow legacy execution by default; an explicit false value disables it."""
    if os.getenv(RUNNER_ENABLED_ENV, "1").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Runner execution is temporarily disabled",
            headers={"Retry-After": "60"},
        )


def _uses_persisted_task_progress(lesson: dict) -> bool:
    return uses_persisted_task_progress(lesson)


def _chapter_one_needs_self_check_progress(lesson: dict) -> bool:
    return (
        _is_chapter_one_lesson(lesson)
        and bool(lesson.get("self_check"))
        and (bool(lesson.get("manual_practice")) or not lesson.get("tasks"))
    )


def _lesson_progress_task_ids(lesson: dict) -> list[str]:
    if not _uses_persisted_task_progress(lesson):
        return []
    task_ids = [task["id"] for task in lesson.get("tasks", [])]
    task_ids.extend(lesson_progress_policy(lesson).get("legacy_task_completions", {}))
    if _chapter_one_needs_self_check_progress(lesson):
        task_ids.append(progress.chapter_one_completion_task_id(lesson["id"]))
    return task_ids


def _lesson_tasks_are_complete(lesson: dict, user_id: int, db: Session) -> bool:
    tasks = progress.required_lesson_tasks(lesson)
    if not tasks:
        return True

    completed_revisions = progress.get_completed_task_revisions(user_id=user_id, task_ids=[task['id'] for task in tasks], db=db, course_id=current_course_id())
    return all(
        completed_revisions.get(task["id"]) == task.get("revision") for task in tasks
    )


def _lesson_completion_status(
    lesson: dict, completed_ids: set[str], task_revisions: dict[str, str]
) -> bool:
    if is_overview_lesson(lesson):
        return True
    if lesson["id"] not in completed_ids:
        stored_lesson_completion = False
    else:
        stored_lesson_completion = True

    if _is_chapter_one_lesson(lesson):
        # Historical lesson-only rows predate per-card revision tracking. Keep
        # them visible; all completions created by this version use current
        # task revisions (and a private self-check key for mixed practice).
        if stored_lesson_completion:
            return True
        legacy = lesson_progress_policy(lesson).get("legacy_task_completions", {})
        if any(task_revisions.get(task_id) == revision for task_id, revision in legacy.items()):
            return True
        tasks = lesson.get("tasks", [])
        required_tasks = progress.required_lesson_tasks(lesson)
        all_tasks_complete = all(
            task_revisions.get(task["id"]) == task.get("revision")
            for task in required_tasks
        )
        if required_tasks and not all_tasks_complete:
            return False
        if lesson.get("manual_practice") or not tasks:
            if not _chapter_one_needs_self_check_progress(lesson):
                return False
            marker_id = progress.chapter_one_completion_task_id(lesson["id"])
            return task_revisions.get(
                marker_id
            ) == progress.chapter_one_completion_revision(lesson)
        return bool(required_tasks) and all_tasks_complete

    if not _uses_persisted_task_progress(lesson) or not lesson.get("tasks"):
        return stored_lesson_completion

    all_tasks_complete = all(
        task_revisions.get(task["id"]) == task.get("revision")
        for task in lesson["tasks"]
    )
    if lesson.get("manual_practice"):
        return stored_lesson_completion and all_tasks_complete
    return all_tasks_complete



class _LocalContent:
    default_track_id = staticmethod(default_track_id)
    find_lesson = staticmethod(find_lesson)
    find_track = staticmethod(find_track)
    get_content_revision = staticmethod(get_content_revision)
    get_public_lessons = staticmethod(get_public_lessons)
    get_tracks_summary = staticmethod(get_tracks_summary)


class _PreparedContent:
    """Per-request owned content copies prepared outside the event loop."""
    def __init__(self, kind, *, track=None, lesson_id=None):
        self._default = default_track_id() if kind in {"tracks", "lessons", "catalog"} else None
        self._revision = get_content_revision() if kind in {"tracks", "catalog"} else None
        self._lesson = find_lesson(lesson_id) if lesson_id is not None else None
        self._summaries = get_tracks_summary() if kind in {"tracks", "catalog", "material"} else []
        ids = [item["id"] for item in self._summaries]
        if kind == "lessons":
            ids = [track or self._default] if (track or self._default) else []
        self._tracks = {track_id: find_track(track_id) for track_id in ids}
        public_ids = []
        if kind == "lessons":
            public_ids = ids
        elif kind == "material" and self._lesson is not None:
            public_ids = [track_id for track_id, source in self._tracks.items()
                          if source and any(item["id"] == self._lesson["id"] for item in source["lessons"])][:1]
        self._public = {track_id: get_public_lessons(track_id) for track_id in public_ids}

    def default_track_id(self): return self._default
    def get_content_revision(self): return self._revision
    def find_lesson(self, lesson_id): return self._lesson
    def find_track(self, track_id): return self._tracks.get(track_id)
    def get_public_lessons(self, track_id): return self._public[track_id]
    def get_tracks_summary(self): return self._summaries


def prepare_request_content(kind, **kwargs):
    return _PreparedContent(kind, **kwargs)

def build_lessons_payload(
    track_id: str, user: Principal | None, db: Session, lesson_id: str | None = None,
    *, content=None,
) -> list[dict]:
    content = content or _LocalContent
    lessons = content.get_public_lessons(track_id)
    track = content.find_track(track_id)
    if track is None:
        raise RuntimeError("Trusted track is missing for public lessons")
    source_lessons = track["lessons"]
    source_by_id = {lesson["id"]: lesson for lesson in source_lessons}
    public_ids = [lesson["id"] for lesson in lessons]
    if (len(source_by_id) != len(source_lessons) or len(set(public_ids)) != len(public_ids)
            or set(public_ids) != set(source_by_id)):
        raise RuntimeError("Trusted and public lesson identities do not match")
    if lesson_id is not None:
        lessons = [lesson for lesson in lessons if lesson["id"] == lesson_id]
    completed_ids = progress.get_completed_lesson_ids(user.id, db, course_id=current_course_id()) if user else set()
    tracked_progress_ids = [
        task_id for lesson in lessons for task_id in _lesson_progress_task_ids(source_by_id[lesson["id"]])
    ]
    completed_task_revisions = (
        progress.get_completed_task_revisions(user_id=user.id, task_ids=tracked_progress_ids, db=db, course_id=current_course_id())
        if user
        else {}
    )
    for lesson in lessons:
        is_available, locked_reason = get_lesson_access(lesson, user)
        lesson["is_available"] = is_available
        lesson["locked_reason"] = locked_reason
        source_lesson = source_by_id[lesson["id"]]
        lesson["completed"] = _lesson_completion_status(
            source_lesson, completed_ids, completed_task_revisions
        )
        if _uses_persisted_task_progress(source_lesson):
            for task in lesson.get("tasks", []):
                task["completed"] = completed_task_revisions.get(
                    task["id"]
                ) == task.get("revision")
        if not is_available:
            # В списке оставляем только навигационные данные и признак блокировки.
            # Полная теория и практические задания выдаются только после проверки доступа.
            lesson["theory"] = []
            lesson["tasks"] = []
            lesson["manual_practice"] = []
    return lessons


def list_tracks(request: Request, db: Session, *, principal=_PRINCIPAL_UNSET, content=None):
    """Треки с прогрессом пользователя — для переключателя курса."""
    user = get_optional_user(request=request, db=db) if principal is _PRINCIPAL_UNSET else principal
    completed_ids = progress.get_completed_lesson_ids(user.id, db, course_id=current_course_id()) if user else set()

    tracks = []
    for track in (content or _LocalContent).get_tracks_summary():
        countable_ids = track.pop("countable_lesson_ids")
        source_track = (content or _LocalContent).find_track(track["id"])
        source_lessons = source_track["lessons"] if source_track else []
        tracked_progress_ids = [
            task_id
            for lesson in source_lessons
            for task_id in _lesson_progress_task_ids(lesson)
        ]
        task_revisions = (
            progress.get_completed_task_revisions(user_id=user.id, task_ids=tracked_progress_ids, db=db, course_id=current_course_id())
            if user
            else {}
        )
        track["lessons_completed"] = sum(
            1
            for lesson in source_lessons
            if lesson["id"] in countable_ids
            and _lesson_completion_status(lesson, completed_ids, task_revisions)
        )
        tracks.append(track)

    return {
        "tracks": tracks,
        "default_track": (content or _LocalContent).default_track_id(),
        "content_revision": (content or _LocalContent).get_content_revision(),
    }


def list_lessons(request: Request, track: str | None, db: Session, *, principal=_PRINCIPAL_UNSET, content=None):
    track_id = track or (content or _LocalContent).default_track_id()
    if track_id is None or (content or _LocalContent).find_track(track_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Track not found"
        )

    user = get_optional_user(request=request, db=db) if principal is _PRINCIPAL_UNSET else principal
    return {
        "track": track_id,
        "lessons": build_lessons_payload(track_id, user, db, content=content),
        "access": {
            "is_authenticated": user is not None,
            "has_active_subscription": bool(
                user and accounts.has_active_subscription(user)
            ),
        },
    }


def learning_catalog(request: Request, db: Session, *, principal=_PRINCIPAL_UNSET, content=None):
    """Complete navigation in one request, without lesson bodies or task text."""
    user = get_optional_user(request=request, db=db) if principal is _PRINCIPAL_UNSET else principal
    completed_ids = progress.get_completed_lesson_ids(user.id, db, course_id=current_course_id()) if user else set()
    summaries = (content or _LocalContent).get_tracks_summary()
    source_tracks = {track["id"]: (content or _LocalContent).find_track(track["id"]) for track in summaries}
    task_ids = [
        task_id
        for track in source_tracks.values()
        if track
        for lesson in track["lessons"]
        for task_id in _lesson_progress_task_ids(lesson)
    ]
    revisions = (
        progress.get_completed_task_revisions(user.id, task_ids, db, course_id=current_course_id())
        if user and task_ids
        else {}
    )
    courses = {}
    for track in summaries:
        countable_ids = set(track.pop("countable_lesson_ids"))
        lessons = []
        blocks_by_title: dict[str, dict[str, Any]] = {}
        source_track = source_tracks[track["id"]]
        for lesson in source_track["lessons"] if source_track else []:
            item = {
                key: lesson[key]
                for key in (
                    "id",
                    "module",
                    "title",
                    "duration",
                    "status",
                    "access",
                    "source_file",
                )
                if key in lesson
            }
            available, reason = get_lesson_access(lesson, user)
            completed = _lesson_completion_status(lesson, completed_ids, revisions)
            item.update(
                is_available=available, locked_reason=reason, completed=completed
            )
            lessons.append(item)
            block_title = str(lesson.get("module") or "Другие уроки")
            block = blocks_by_title.setdefault(
                block_title,
                {
                    "id": f"{track['id']}:{block_title}",
                    "title": block_title,
                    "completed": 0,
                    "total": 0,
                    "lesson_ids": [],
                },
            )
            block["lesson_ids"].append(lesson["id"])
            if lesson["id"] in countable_ids:
                block["total"] += 1
                if completed:
                    block["completed"] += 1
        track["lessons_completed"] = sum(
            item["completed"] for item in lessons if item["id"] in countable_ids
        )
        track["blocks"] = list(blocks_by_title.values())
        courses[track["id"]] = lessons
    return {
        "tracks": summaries,
        "courses": courses,
        "default_track": (content or _LocalContent).default_track_id(),
        "content_revision": (content or _LocalContent).get_content_revision(),
    }


def lesson_material(lesson_id: str, request: Request, db: Session, *, principal=_PRINCIPAL_UNSET, content=None):
    lesson = (content or _LocalContent).find_lesson(lesson_id)
    if lesson is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lesson not found"
        )
    user = get_optional_user(request=request, db=db) if principal is _PRINCIPAL_UNSET else principal
    available, reason = get_lesson_access(lesson, user)
    if not available:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=reason)
    # Reuse the public projection: hidden tests stay on the server.
    for track in (content or _LocalContent).get_tracks_summary():
        source_track = (content or _LocalContent).find_track(track["id"])
        if source_track and any(
            item["id"] == lesson["id"] for item in source_track["lessons"]
        ):
            material = build_lessons_payload(track["id"], user, db, lesson["id"], content=content)[0]
            return {"lesson": material}
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Lesson not found"
    )


def manual_practice_answer(
    lesson_id: str,
    exercise_index: int,
    task_index: int,
    request: Request,
    db: Session,
    *, principal=_PRINCIPAL_UNSET, content=None,
):
    """Возвращает закрытый разбор только после явного запроса и проверки доступа."""
    lesson = (content or _LocalContent).find_lesson(lesson_id)
    if lesson is None or not lesson.get("private_manual_answers", False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Разбор не найден"
        )

    user = get_optional_user(request=request, db=db) if principal is _PRINCIPAL_UNSET else principal
    available, reason = get_lesson_access(lesson, user)
    if not available:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=reason)

    practice = lesson.get("manual_practice", [])
    if not 0 <= exercise_index < len(practice):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Разбор не найден"
        )
    tasks = practice[exercise_index].get("tasks", [])
    if not 0 <= task_index < len(tasks) or not isinstance(
        tasks[task_index].get("answer"), dict
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Разбор не найден"
        )
    return {"answer": deepcopy(tasks[task_index]["answer"])}


def complete_lesson(
    lesson_id: str,
    request: Request,
    db: Session,
    *,
    uow: UnitOfWork | None = None,
    principal=_PRINCIPAL_UNSET,
    content=None,
):
    """Ручная отметка «Выполнено» — только для уроков с self_check."""
    port_kwargs = {"uow": uow} if uow is not None else {}
    if principal is _PRINCIPAL_UNSET:
        principal = accounts.get_current_principal(request=request, db=db, **port_kwargs)
    lesson = (content or _LocalContent).find_lesson(lesson_id)

    policy = CompletionPolicy(
        access=get_lesson_access,
        uses_persisted_tasks=_uses_persisted_task_progress,
        tasks_complete=lambda item, user_id: _lesson_tasks_are_complete(
            item, user_id, db
        ),
        needs_chapter_one_marker=_chapter_one_needs_self_check_progress,
        is_chapter_one=_is_chapter_one_lesson,
    )
    writes = CompletionWrites(
        mark_chapter_one_self_check=lambda user_id, item: progress.mark_task_completed(user_id=user_id, task_id=progress.chapter_one_completion_task_id(item['id']), task_revision=progress.chapter_one_completion_revision(item), db=db, **port_kwargs, course_id=current_course_id()),
        mark_lesson=lambda user_id, stable_id: progress.mark_lesson_completed(user_id=user_id, lesson_id=stable_id, db=db, **port_kwargs, course_id=current_course_id()),
    )
    try:
        return complete_lesson_use_case(
            principal=principal, lesson=lesson, policy=policy, writes=writes
        )
    except CompletionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def lesson_theory(lesson_id: str, request: Request, db: Session, *, principal=_PRINCIPAL_UNSET, content=None):
    user = get_optional_user(request=request, db=db) if principal is _PRINCIPAL_UNSET else principal

    lesson = (content or _LocalContent).find_lesson(lesson_id)
    if lesson is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lesson not found"
        )

    is_available, locked_reason = get_lesson_access(lesson, user)
    if not is_available:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=locked_reason)

    return {
        "lesson_id": lesson["id"],
        "title": lesson["title"],
        "theory_markdown": lesson.get("theory_markdown", ""),
    }


def _get_available_task(task_id: str, user: Principal | None) -> dict:
    task = find_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    lesson = find_lesson_by_task(task_id)
    if lesson is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lesson not found"
        )

    is_available, locked_reason = get_lesson_access(lesson, user)
    if not is_available:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=locked_reason)

    return task


def _require_current_task_revision(task: dict, submitted_revision: str | None) -> None:
    """Reject stale task metadata before running or grading student code."""
    current_revision = task.get("revision")
    if not isinstance(current_revision, str) or submitted_revision != current_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Условие или набор проверок задания обновились. "
                "Обновите страницу и отправьте решение ещё раз."
            ),
        )


def _prepare_task_for_principal(task_id: str, revision: str | None, user: Principal | None) -> dict:
    task = _get_available_task(task_id, user)
    _require_current_task_revision(task, revision)
    lesson = find_lesson_by_task(task_id)
    return {"user_id": user.id if user is not None else None,
            "task": deepcopy(task), "lesson": deepcopy(lesson)}


def _prepare_task(task_id: str, revision: str | None, request: Request) -> dict:
    with SessionLocal() as db:
        user = get_optional_user(request=request, db=db)
        return _prepare_task_for_principal(task_id, revision, user)


def _write_task_progress(context: dict, db, composition_root: Any | None = None) -> None:
    user_id, task, lesson = context["user_id"], context["task"], context["lesson"]
    if user_id is None or lesson is None:
        return
    port_kwargs = ({"uow": composition_root.for_request(db).uow}
                   if composition_root is not None else {})
    save_progress(
        user_id=user_id, lesson=lesson, task=task,
        uses_persisted_tasks=_uses_persisted_task_progress,
        tasks_complete=lambda item, uid: _lesson_tasks_are_complete(item, uid, db),
        is_chapter_one=_is_chapter_one_lesson,
        mark_task=lambda uid, task_id, revision: progress.mark_task_completed(user_id=uid, task_id=task_id, task_revision=revision, db=db, **port_kwargs, course_id=current_course_id()),
        mark_lesson=lambda uid, lesson_id: progress.mark_lesson_completed(user_id=uid, lesson_id=lesson_id, db=db, **port_kwargs, course_id=current_course_id()),
    )


def _save_task_progress(context: dict, composition_root: Any | None = None) -> None:
    if context["user_id"] is None or context["lesson"] is None:
        return
    start = perf_counter()
    try:
        with SessionLocal() as db:
            _write_task_progress(context, db, composition_root)
    finally:
        logger.info("runner_progress_save_finished duration_ms=%.1f", (perf_counter() - start) * 1000)


async def _prepare_request_task(task_id, revision, request):
    from pymentor_core.modules.identity.api.sessions import _local_async_identity
    identity = _local_async_identity(request)
    if identity is None:
        return await run_in_threadpool(_prepare_task, task_id, revision, request)
    from pymentor_core.app.async_request import request_principal
    principal = await request_principal(request, optional=True)
    return await run_in_threadpool(_prepare_task_for_principal, task_id, revision, principal)


async def _save_request_task(context, request, composition_root):
    from pymentor_core.modules.identity.api.sessions import _local_async_identity
    identity = _local_async_identity(request)
    if identity is None:
        if composition_root is None:
            return await run_in_threadpool(_save_task_progress, context)
        return await run_in_threadpool(_save_task_progress, context, composition_root)
    if context["user_id"] is None or context["lesson"] is None:
        return
    from pymentor_core.app.async_request import run_request_sql
    start = perf_counter()
    try:
        await run_request_sql(request, lambda db: _write_task_progress(context, db, composition_root), identity=identity)
    finally:
        logger.info("runner_progress_save_finished duration_ms=%.1f", (perf_counter() - start) * 1000)


def _remote_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Проверка временно недоступна.", headers={"Retry-After": "2"})


def _remote_url() -> str | None:
    value = os.environ.get("PYMENTOR_REMOTE_RUNNER_URL", "").strip()
    return value or None


def _remote_call(url: str, code: str) -> dict:
    try:
        body = json.dumps({"code": code}, ensure_ascii=False).encode("utf-8")
        request = URLRequest(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=35) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("response too large")
        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result
        result = json.loads(raw, object_pairs_hook=unique_pairs)
        if not isinstance(result, dict) or set(result) != {"ok", "stdout", "stderr", "error", "traceback"}:
            raise ValueError("invalid fields")
        if type(result["ok"]) is not bool or not isinstance(result["stdout"], str):
            raise ValueError("invalid result")
        if any(result[k] is not None and not isinstance(result[k], str) for k in ("stderr", "error", "traceback")):
            raise ValueError("invalid details")
        if any(len(result[k] or "") > 12000 for k in ("stdout", "stderr", "error", "traceback")):
            raise ValueError("output too large")
        if result["ok"] and (result["error"] or result["traceback"]):
            raise ValueError("inconsistent result")
        return result
    except Exception:
        raise _remote_unavailable() from None


def _normalize_remote_stdout(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return value[:-1] if value.endswith("\n") else value


def _remote_script(code: str, namespace: Any) -> str:
    # JSON-only values prevent code injection and preserve Python value types.
    if namespace is None:
        namespace = {}
    if type(namespace) is not dict or any(type(key) is not str for key in namespace):
        raise _remote_unavailable()
    if any(key in namespace for key in ("__builtins__", "__name__")):
        raise _remote_unavailable()
    remaining = [4096]
    def validate(value, depth):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 32:
            raise ValueError("namespace too complex")
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError("non-string key")
                validate(item, depth + 1)
        elif type(value) is list:
            for item in value:
                validate(item, depth + 1)
        elif type(value) not in (str, int, float, bool, type(None)):
            raise ValueError("non-JSON value")
    try:
        validate(namespace, 0)
        encoded = json.dumps(namespace, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 6000:
            raise ValueError("namespace too large")
        if not namespace:
            script = code
        else:
            script = ("import json\n"
                      "_ns = json.loads(" + repr(encoded) + ")\n"
                      "_ns['__name__'] = '__student__'\n"
                      "exec(" + repr(code) + ", _ns)\n")
        if not 1 <= len(script) <= 8000 or len(script.encode("utf-8")) > 32000:
            raise ValueError("broker code limit")
        return script
    except (ValueError, TypeError, OverflowError, RecursionError, UnicodeError):
        raise _remote_unavailable() from None


def _remote_task(url: str, function, code: str, task: dict) -> dict:
    policy: RunnerPolicy = get_runner_policy()
    if task.get("mode", "solve") == "solve":
        if function not in (run_python_task, run_python_preview):
            raise _remote_unavailable()
        return solve_bridge.run(code, task, function is run_python_preview, lambda source: _remote_call(url, source), policy, _remote_unavailable)
    tests = task.get("tests")
    if (task.get("mode") != "script" or not isinstance(tests, list) or not tests
        or any(not isinstance(t, dict) or bool(set(t) - {"name", "assert", "namespace", "expected", "float_tolerance"}) or t.get("assert") != "stdout"
               or (function is run_python_task and "expected" not in t) or not isinstance(t.get("expected", ""), str)
               for t in tests)):
        raise _remote_unavailable()
    error = None
    kind = None
    if len(code) > policy.max_code_length:
        error = f"Код слишком длинный. Лимит: {policy.max_code_length} символов."
    else:
        error = policy.blocked_reason(code) or policy.syntax_error_reason(code)
        if not error and function is run_python_task:
            error = policy.requirements_error(code, task.get("requirements"))
            kind = "requirements" if error else None
    if error:
        result = {"ok": False, "score": 0, "tests": [], "stdout": "", "error": error}
        if kind:
            result["error_kind"] = kind
        return result
    if function is run_python_preview:
        return _remote_call(url, _remote_script(code, tests[0].get("namespace")))
    scripts = [_remote_script(code, test.get("namespace")) for test in tests]
    if function is not run_python_task:
        raise _remote_unavailable()
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(_remote_call, url, script) for script in scripts]
            responses = [future.result() for future in futures]
    except Exception:
        raise _remote_unavailable() from None
    results, outputs = [], []
    for index, (test, response) in enumerate(zip(tests, responses), 1):
        expected = test.get("expected", "")
        actual = response["stdout"]
        passed = response["ok"] and _normalize_remote_stdout(expected) == _normalize_remote_stdout(actual)
        item = {"name": test.get("name") or f"test {index}", "passed": passed, "expected": expected, "actual": actual if response["ok"] else None}
        if not response["ok"]:
            item["error"] = response["error"] or "Ошибка выполнения."
        results.append(item)
        outputs.append(actual)
    count = sum(item["passed"] for item in results)
    return {"ok": count == len(results), "score": round(count / len(results) * 100), "tests": results, "stdout": "".join(outputs)[-1200:]}


async def _execute_task(function, code: str, task: dict) -> dict:
    remote_url = _remote_url()
    if remote_url:
        return await run_in_threadpool(_remote_task, remote_url, function, code, task)
    _require_runner_enabled()
    try:
        return await get_runner_executor().run(function, code=code, task=task)
    except RunnerBusy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Проверка занята. Повторите отправку через несколько секунд",
            headers={"Retry-After": "2"},
        ) from None
    except RunnerUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Проверка временно недоступна. Повторите попытку позже.",
            headers={"Retry-After": "2"},
        ) from None


_SAFE_SUBMIT_CHECK_ERROR = (
    "Не удалось проверить решение. Проверьте код и попробуйте ещё раз."
)


_SAFE_SUBMIT_REQUIREMENTS_ERROR = (
    "Решение не соответствует требованиям кода. Сверьте конструкции с условием задачи."
)
_CHAPTER_ONE_TEST_FAILURE = (
    "Одна из проверок не пройдена. Сверьте решение с условием задачи."
)


def _has_valid_lesson_source_file(lesson: dict | None) -> bool:
    source_file = lesson.get("source_file") if isinstance(lesson, dict) else None
    return isinstance(source_file, str) and bool(source_file.strip())


def _is_chapter_one_lesson(lesson: dict | None) -> bool:
    """Compatibility name for metadata-driven card completion policy."""
    return uses_card_completion(lesson)


def _project_chapter_one_submit_result(result: Any) -> dict[str, Any]:
    """Keep the submit response shape while omitting runner-controlled details."""
    result = result if isinstance(result, dict) else {}
    ok = result.get("ok") is True
    raw_score = result.get("score")
    score = raw_score if type(raw_score) is int and 0 <= raw_score <= 100 else 0

    tests = []
    raw_tests = result.get("tests")
    if isinstance(raw_tests, list):
        for index, test in enumerate(raw_tests, start=1):
            passed = isinstance(test, dict) and test.get("passed") is True
            public_test = {"name": f"Проверка {index}", "passed": passed}
            if not passed:
                public_test["error"] = _CHAPTER_ONE_TEST_FAILURE
            tests.append(public_test)

    has_failed_test = any(not test["passed"] for test in tests)
    has_runner_error = bool(result.get("error"))
    show_general_error = has_runner_error or (
        not ok and (not tests or not has_failed_test)
    )
    public_error = _SAFE_SUBMIT_CHECK_ERROR if show_general_error else None
    if not ok and result.get("error_kind") == "requirements":
        public_error = _SAFE_SUBMIT_REQUIREMENTS_ERROR

    return {
        "ok": ok,
        "score": score,
        "tests": tests,
        "stdout": "",
        "stderr": None,
        "error": public_error,
        "traceback": None,
    }


def _invalid_source_file_submit_result() -> dict[str, Any]:
    """Fail closed when resolved lesson metadata cannot scope a public submit."""
    return {
        "ok": False,
        "score": 0,
        "tests": [],
        "stdout": "",
        "stderr": None,
        "error": _SAFE_SUBMIT_CHECK_ERROR,
        "traceback": None,
    }


async def submit_task(task_id: str, payload: CodeSubmitRequest, request: Request):
    if not _remote_url():
        _require_runner_enabled()
    context = await _prepare_request_task(task_id, payload.task_revision, request)
    if not _has_valid_lesson_source_file(context.get("lesson")):
        return _invalid_source_file_submit_result()

    result = await _execute_task(run_python_task, payload.code, context["task"])
    if result.get("ok"):
        app = request.scope.get("app")
        composition_root = getattr(
            getattr(app, "state", None), "composition_root", None
        )
        await _save_request_task(context, request, composition_root)

    return _project_chapter_one_submit_result(result)


async def run_task_code(task_id: str, payload: CodeSubmitRequest, request: Request):
    """Запускает одну демонстрационную проверку без зачёта урока."""
    if not _remote_url():
        _require_runner_enabled()
    context = await _prepare_request_task(task_id, payload.task_revision, request)
    return await _execute_task(run_python_preview, payload.code, context["task"])


async def run_interpreter_code(payload: InterpreterRunRequest):
    """Запускает самостоятельный фрагмент Python без проверки и зачёта урока."""
    _require_runner_enabled()
    if _remote_url():
        raise _remote_unavailable()
    try:
        return await get_runner_executor().run(run_python_code, code=payload.code)
    except RunnerBusy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Интерпретатор занят. Повторите запуск через несколько секунд",
            headers={"Retry-After": "2"},
        ) from None
    except RunnerUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Интерпретатор временно недоступен. Повторите попытку позже.",
            headers={"Retry-After": "2"},
        ) from None
