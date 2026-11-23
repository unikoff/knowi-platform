"""Load course materials and expose the shared lesson and task catalog."""

from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from pymentor_core.modules.learning.progress_policy import is_countable_lesson
from pymentor_core.modules.practice.public_specs import public_task
from ._context import COURSE_ROOT, get_authoring

FOUNDATIONS_12_LEGACY_NUMBERS = get_authoring("chapter_01.numbering").LEGACY_NUMBERS
DEEPER_LEGACY_INDICES = get_authoring("chapter_02.numbering").LEGACY_INDICES
DATABASE_LEGACY_INDICES = get_authoring("chapter_04.numbering").LEGACY_INDICES
from .practice_catalog import get_code_tasks
from .practice_catalog import get_manual_practice
from .practice_catalog import SELF_CHECK_LESSONS
from .practice_catalog import _tasks_for_lesson
from .identity import LEGACY_COURSE_LAYOUTS
from .identity import LESSON_SEQUENCE
from .identity import LESSON_SOURCE_ALIASES
from .identity import MIGRATED_TRACKS_BY_ID
from .identity import MODULE_LABELS
from .identity import NEW_PYTHON_12_MODULE_LABELS
from .identity import NEW_PYTHON_12_TRACK_NAME
from .identity import NEW_PYTHON_MODULE_LABELS
from .identity import NEW_PYTHON_TRACK_NAME
from .identity import PLANNER_API_BLOCK_FOLDERS
from .identity import PLANNER_API_DISPLAY_NUMBERS
from .identity import PLANNER_API_IDENTITY_FILENAMES
from .identity import PLANNER_API_LEGACY_INDICES
from .identity import PLANNER_API_MODULE_LABELS
from .identity import PLANNER_API_TRACK_NAME
from .identity import PYTHON_DEEPER_MODULE_LABELS
from .identity import PYTHON_DEEPER_TRACK_NAME
from .identity import RICH_TRACK_MODULE_LABELS
from .identity import _legacy_lesson_id
from .identity import _lesson_identity_source
from .identity import _stable_lesson_id
from .identity import build_task_revision
from .theory_helpers import CODE_FENCE_RE
from .theory_helpers import HEADING_RE
from .theory_helpers import PYTHON_FENCE_RE
from .theory_helpers import STARTER_FENCE_RE
from .theory_helpers import TASK_MARKERS
from .theory_helpers import TASK_TITLE_RE
from .theory_helpers import TESTS_FENCE_RE
from .theory_helpers import YOUTUBE_MARKER_RE
from .theory_helpers import YOUTUBE_URL_RE
from .theory_helpers import _clean_markdown
from .theory_helpers import _compute_fence_mask
from .theory_helpers import _extract_intro_theory
from .theory_helpers import (
    _extract_python_starter_from_block,
)
from .theory_helpers import (
    _extract_starter_from_block,
)
from .theory_helpers import _extract_task_blocks
from .theory_helpers import _extract_task_prompt
from .theory_helpers import (
    _extract_tests_from_block,
)
from .theory_helpers import _extract_title
from .theory_helpers import _extract_youtube_video
from .theory_helpers import _normalise_heading
from .theory_helpers import _read_text
from .theory_helpers import _split_paragraphs
from .theory_helpers import _starter_code
from .theory_helpers import (
    _strip_generated_task_dump,
)
from .theory_helpers import _strip_service_fences


IGNORED_FILES = {"README.md", "Задания.md"}


def _module_label(path: Path, track_dir: Path, track_id: str | None = None) -> str:
    track_id = track_id or track_dir.name
    rich_module_labels = RICH_TRACK_MODULE_LABELS.get(track_id)
    if rich_module_labels is not None and path.name in rich_module_labels:
        return rich_module_labels[path.name]
    migrated = MIGRATED_TRACKS_BY_ID.get(track_id)
    if migrated is not None:
        return (
            migrated[1].get("module_labels", {}).get(path.parent.name)
            or migrated[1]["source_dirs"].get(path.parent.name)
            or path.parent.name
        )
    if path.parent == track_dir:
        return MODULE_LABELS.get(path.name, "00 Обзор")
    return MODULE_LABELS.get(path.parent.name, path.parent.name)


def _lesson_number_parts(filename: str) -> tuple[int, ...] | None:
    match = re.match(r"^(\d+(?:\.\d+)*)", filename)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _sort_key(
    path: Path, track_id: str | None = None
) -> tuple[int, bool, tuple[int, ...], str]:
    relative = path.relative_to(COURSE_ROOT).as_posix()
    overview_order = {"План обучения.md": -2}
    if path.name in overview_order:
        return (overview_order[path.name], False, (), relative)
    display_number = PLANNER_API_DISPLAY_NUMBERS.get(relative)
    number_parts = (
        tuple(int(part) for part in display_number.split("."))
        if track_id == PLANNER_API_TRACK_NAME and display_number is not None
        else _lesson_number_parts(path.name)
    )
    if track_id in RICH_TRACK_MODULE_LABELS:
        if number_parts is None:
            return (999, False, (), relative)
        return (number_parts[0], len(number_parts) > 1, number_parts[1:], relative)
    try:
        return (LESSON_SEQUENCE.index(relative), False, (), relative)
    except ValueError:
        if number_parts is not None:
            return (
                len(LESSON_SEQUENCE) + number_parts[0],
                len(number_parts) > 1,
                number_parts[1:],
                relative,
            )
        return (len(LESSON_SEQUENCE) + 999, False, (), relative)


def _collect_markdown_files(track_dir: Path, track_id: str | None = None) -> list[Path]:
    files = [
        path
        for path in track_dir.rglob("*.md")
        if path.name not in IGNORED_FILES and not path.name.startswith("Ответы")
    ]
    resolved_track_id = track_id or track_dir.name
    rich_filenames = RICH_TRACK_MODULE_LABELS.get(resolved_track_id)
    if rich_filenames is not None:
        files = [path for path in files if path.name in rich_filenames]
    else:
        migrated = MIGRATED_TRACKS_BY_ID.get(resolved_track_id)
        if migrated is not None:
            allowed_blocks = set(migrated[1]["source_dirs"])
            files = [path for path in files if path.parent.name in allowed_blocks]
    return sorted(files, key=lambda path: _sort_key(path, resolved_track_id))


def _lesson_access(path: Path, track_dir: Path, track_id: str | None = None) -> str:
    """Возвращает уровень доступа по новой воронке обучения.

    Карта обучения остаётся открытой в каждом курсе. Бесплатный старт есть
    у первой главы: обзор и первый блок. После
    регистрации доступен второй блок; остальные занятия требуют Pro-подписку.
    """
    if path.name == "План обучения.md":
        return "free"

    current_track = track_id or track_dir.name
    if current_track not in {NEW_PYTHON_TRACK_NAME, NEW_PYTHON_12_TRACK_NAME}:
        return "subscription"

    lesson_match = re.match(r"^(\d+)", path.name)
    lesson_number = int(lesson_match.group(1)) if lesson_match else None
    if current_track == NEW_PYTHON_12_TRACK_NAME:
        if lesson_number is not None and 1 <= lesson_number <= 5:
            return "free"
        if lesson_number is not None and 6 <= lesson_number <= 10:
            return "registered"
        return "subscription"
    if lesson_number is not None and 1 <= lesson_number <= 5:
        return "free"
    if lesson_number is not None and 6 <= lesson_number <= 10:
        return "registered"
    return "subscription"


def _lesson_status(index: int, access: str) -> str:
    if index == 0:
        return "current"
    if access == "subscription":
        return "locked"
    return "done"


def _estimate_duration(text: str, task_count: int) -> str:
    word_count = len(re.findall(r"\w+", text, flags=re.UNICODE))
    minutes = max(12, min(95, round(word_count / 55) + task_count * 5))
    if minutes >= 60:
        hours, rest = divmod(minutes, 60)
        return f"{hours} ч {rest} мин" if rest else f"{hours} ч"
    return f"{minutes} мин"


def _build_lesson(
    path: Path,
    index: int,
    total: int,
    track_id: str,
    track_dir: Path,
    legacy_index: int | None = None,
) -> dict[str, Any]:
    text = _strip_generated_task_dump(_read_text(path))
    relative = path.relative_to(COURSE_ROOT).as_posix()
    identity_source = _lesson_identity_source(relative)
    lesson_id = _stable_lesson_id(relative)
    legacy_lesson_id = (
        _legacy_lesson_id(track_id, legacy_index)
        if legacy_index is not None
        else lesson_id
    )
    title = (
        "Карта обучения"
        if path.name == "План обучения.md"
        else _extract_title(path, text)
    )
    access = _lesson_access(path, track_dir, track_id)
    number_parts = _lesson_number_parts(path.name)
    if track_id == PLANNER_API_TRACK_NAME and path.name != "План обучения.md":
        display_number = PLANNER_API_DISPLAY_NUMBERS.get(relative)
        if display_number is None and number_parts is not None:
            display_number = ".".join(str(part) for part in number_parts)
        if display_number is not None:
            title = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", title).strip()
            title = f"{display_number}. {title}"
    numbered_rich_course = track_id == LEGACY_COURSE_LAYOUTS["chapter_04"][
        "track_id"
    ] and path.parent.name in {"block_13", "block_14", "block_15", "block_16"}
    if (
        (
            track_id
            in {
                NEW_PYTHON_TRACK_NAME,
                NEW_PYTHON_12_TRACK_NAME,
                PYTHON_DEEPER_TRACK_NAME,
            }
            or numbered_rich_course
        )
        and number_parts
        and not re.match(r"^\d+(?:\.\d+)*\.", title)
    ):
        display_number = ".".join(str(part) for part in number_parts)
        title = f"{display_number}. {title}"
    chapter_four_rework = relative.startswith(("chapter_04/block_15/", "chapter_04/block_16/"))
    under_revision = chapter_four_rework and lesson_id != "lesson-64c9d597a164bbdb4f145d5a"
    tasks = _tasks_for_lesson(
        identity_source,
        text,
        lesson_id,
        legacy_lesson_id,
        track_id,
        path.name,
        source_file=relative,
    )
    if under_revision:
        tasks = []
    for task in tasks:
        task["revision"] = build_task_revision(task)
    mixed_practice_lessons = {
        "chapter_01_archive/block_01/04 - Переменные и базовые типы.md",
        "chapter_01_archive/block_01/05 - Числа, операции и преобразование типов.md",
        "chapter_01_archive/block_02/06 - Строки и форматирование.md",
        "chapter_01_archive/block_02/07 - Boolean, сравнения и логика.md",
        "chapter_01_archive/block_02/09 - Цикл for и последовательности.md",
        "chapter_01_archive/block_02/09.1 - Накопление, счётчик и итог после цикла.md",
        "chapter_01_archive/block_03/11 - Списки, кортежи и множества.md",
        "chapter_01_archive/block_03/13 - Функции, параметры и return.md",
        "chapter_01_archive/block_03/15 - Ошибки, traceback и отладка.md",
        "chapter_02/block_05/23 - args, kwargs и распаковка.md",
        "chapter_02/block_05/24.1 - Вложенная функция и замыкание.md",
        "chapter_02/block_05/25.1 - Синтаксис @ и границы применения.md",
    }
    manual_practice = (
        get_manual_practice(track_id, path.name, source_file=relative)
        if not under_revision and (not tasks or relative in mixed_practice_lessons)
        else []
    )
    self_check = not under_revision and (
        (path.parent.name != "block_00" and identity_source in SELF_CHECK_LESSONS)
        or bool(manual_practice)
    )
    video = None if under_revision else _extract_youtube_video(text, title)
    tsx_theory_only = chapter_four_rework or relative.startswith("chapter_05/block_17/")

    return {
        "id": lesson_id,
        "legacy_id": legacy_lesson_id,
        "track": track_id,
        "module": _module_label(path, track_dir, track_id),
        "title": title,
        "duration": _estimate_duration(text, len(tasks)),
        "status": _lesson_status(index, access),
        "access": access,
        # These blocks keep the internal authoring plans out of learner payloads.
        "theory": [] if tsx_theory_only else _extract_intro_theory(text),
        "theory_markdown": "" if tsx_theory_only else _strip_service_fences(text),
        "self_check": self_check,
        "manual_practice": manual_practice,
        "video": video,
        "tasks": tasks,
        "source_file": relative,
        "progress_policy": {
            "retain_countable": under_revision,
            "overview": bool(re.fullmatch(r"chapter_\d+/block_00/[^/]+", relative)),
            "revision_tracked": relative.startswith(("foundations/block_03/", "chapter_01/")),
            "completion": "cards" if relative.startswith("chapter_01/") else "lesson",
            "optional_task_ids": ["lesson-b504d05e6763add4db882bfe-task-05"],
            "legacy_task_completions": (
                {"lesson-fa661256326bd13784d1c88d-task-01": "fc0a533cfbb824248e6d6f6031e019045c4a77c07030a920a0e226a6953c5675"}
                if lesson_id == "lesson-fa661256326bd13784d1c88d" else {}
            ),
        },
        "private_manual_answers": tsx_theory_only
        or relative.startswith("chapter_02/block_05/")
        or lesson_id in {"lesson-fa661256326bd13784d1c88d", "lesson-c4878f92ca23a93b777d9fa8"},
    }


def _load_track_meta(track_dir: Path) -> dict[str, Any]:
    meta_path = track_dir / "track.json"
    meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            meta = json.loads(_read_text(meta_path))
        except json.JSONDecodeError:
            meta = {}
    return {
        "id": meta.get("id", track_dir.name),
        "title": meta.get("title", track_dir.name),
        "tagline": meta.get("tagline", ""),
        "order": meta.get("order", 999),
    }


def _load_tracks() -> list[dict[str, Any]]:
    if not COURSE_ROOT.exists():
        return []

    tracks: list[dict[str, Any]] = []
    for track_dir in sorted(COURSE_ROOT.iterdir()):
        if (
            not track_dir.is_dir()
            or track_dir.name == "__pycache__"
            or track_dir.name.endswith("_archive")
        ):
            continue
        track = _load_track_meta(track_dir)
        files = _collect_markdown_files(track_dir, track["id"])
        if not files:
            continue

        total = len(files)
        lessons = []
        legacy_index = 0
        for index, path in enumerate(files):
            number_parts = _lesson_number_parts(path.name)
            is_supplemental = number_parts is not None and len(number_parts) > 1
            current_legacy_index = None if is_supplemental else legacy_index
            if not is_supplemental:
                legacy_index += 1
            if track["id"] == NEW_PYTHON_12_TRACK_NAME and number_parts:
                # The old positional aliases include the overview at index zero.
                display_number = ".".join(str(part) for part in number_parts)
                current_legacy_index = FOUNDATIONS_12_LEGACY_NUMBERS.get(display_number)
            if track["id"] == PYTHON_DEEPER_TRACK_NAME and number_parts:
                display_number = ".".join(str(part) for part in number_parts)
                current_legacy_index = DEEPER_LEGACY_INDICES.get(display_number)
            if (
                track["id"] == LEGACY_COURSE_LAYOUTS["chapter_04"]["track_id"]
                and number_parts
            ):
                display_number = ".".join(str(part) for part in number_parts)
                current_legacy_index = DATABASE_LEGACY_INDICES.get(display_number)
            if track["id"] == PLANNER_API_TRACK_NAME:
                source = path.relative_to(COURSE_ROOT).as_posix()
                current_legacy_index = PLANNER_API_LEGACY_INDICES.get(source)
            if track["id"] == LEGACY_COURSE_LAYOUTS["chapter_05"]["track_id"]:
                # The new chapter has different lessons; positional aliases would
                # associate them with unrelated lessons from the retired chapter.
                current_legacy_index = None
            lessons.append(
                _build_lesson(
                    path,
                    index=index,
                    total=total,
                    track_id=track["id"],
                    track_dir=track_dir,
                    legacy_index=current_legacy_index,
                )
            )
        track["lessons"] = lessons
        tracks.append(track)

    tracks.sort(key=lambda t: (t["order"], t["title"]))
    return tracks


TRACKS: list[dict[str, Any]] = _load_tracks()


LESSONS: list[dict[str, Any]] = [
    lesson for track in TRACKS for lesson in track["lessons"]
]


def _build_content_revision() -> str:
    """Отпечаток учебного контента для проверки фактически запущенного релиза."""
    payload = [
        {
            "id": lesson["id"],
            "source_file": lesson["source_file"],
            "self_check": lesson["self_check"],
            "manual_practice": lesson["manual_practice"],
            "tasks": [
                {
                    key: value
                    for key, value in task.items()
                    if key not in {"tests", "legacy_ids"}
                }
                for task in lesson["tasks"]
            ],
        }
        for lesson in LESSONS
    ]
    serialized = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


CONTENT_REVISION = _build_content_revision()


def get_content_revision() -> str:
    return CONTENT_REVISION


def _is_countable(lesson: dict[str, Any]) -> bool:
    """Сохраняем учёт временно недоступных занятий и прежний прогресс."""
    return is_countable_lesson(lesson)


def get_tracks_summary() -> list[dict[str, Any]]:
    """Список треков без уроков — для переключателя на фронте."""
    return [
        {
            "id": track["id"],
            "title": track["title"],
            "tagline": track["tagline"],
            "lessons_total": len(track["lessons"]),
            "lessons_countable": sum(
                1 for lesson in track["lessons"] if _is_countable(lesson)
            ),
            "countable_lesson_ids": [
                lesson["id"] for lesson in track["lessons"] if _is_countable(lesson)
            ],
        }
        for track in TRACKS
    ]


def find_track(track_id: str) -> dict[str, Any] | None:
    for track in TRACKS:
        if track["id"] == track_id:
            return track
    return None


def default_track_id() -> str | None:
    return TRACKS[0]["id"] if TRACKS else None


def get_public_lessons(track_id: str) -> list[dict[str, Any]]:
    """Уроки трека для фронта.

    Не отдаём: theory_markdown (тяжёлый, есть отдельный endpoint) и tests
    (ожидаемые ответы — студент не должен видеть их в network-вкладке).
    """
    track = find_track(track_id)
    if track is None:
        return []

    lessons: list[dict[str, Any]] = []
    for lesson in track["lessons"]:
        item = {
            key: value
            for key, value in lesson.items()
            if key not in {"theory_markdown", "legacy_id", "progress_policy", "private_manual_answers"}
        }
        if lesson.get("private_manual_answers"):
            item["manual_practice"] = [
                {
                    **exercise,
                    **(
                        {
                            "tasks": [
                                {
                                    key: value
                                    for key, value in task.items()
                                    if key != "answer"
                                }
                                for task in exercise.get("tasks", [])
                            ]
                        }
                        if "tasks" in exercise
                        else {}
                    ),
                }
                for exercise in lesson.get("manual_practice", [])
            ]
        item["tasks"] = [public_task(task) for task in lesson["tasks"]]
        lessons.append(item)
    return lessons


def find_lesson(lesson_id: str) -> dict[str, Any] | None:
    for lesson in LESSONS:
        if lesson["id"] == lesson_id or lesson.get("legacy_id") == lesson_id:
            return lesson
    return None


def find_task(task_id: str) -> dict[str, Any] | None:
    for lesson in LESSONS:
        for task in lesson["tasks"]:
            if task["id"] == task_id or task_id in task.get("legacy_ids", []):
                return task
    return None


def find_lesson_by_task(task_id: str) -> dict[str, Any] | None:
    for lesson in LESSONS:
        for task in lesson["tasks"]:
            if task["id"] == task_id or task_id in task.get("legacy_ids", []):
                return lesson
    return None


def get_legacy_lesson_id_map() -> dict[str, str]:
    """Соответствие позиционных ID их постоянным заменам для миграции прогресса."""
    return {
        lesson["legacy_id"]: lesson["id"]
        for lesson in LESSONS
        if lesson["legacy_id"] != lesson["id"]
    }
