"""Real full-course loader checks; no app factory, SQL or external transport."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sys
import uuid

import pytest

from pymentor_core.adapters.course_local.packages import CourseNotFoundError, CoursePackageError
from pymentor_core.adapters.course_local.provider import LocalCourseProvider
from pymentor_core.adapters.course_local.runtime import CourseRuntime, content_version, course_scope, current_course_id


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def runtime():
    return CourseRuntime(ROOT / "courses")


@pytest.fixture
def disposable_courses():
    parent = Path(__file__).resolve().parent
    temporary = parent / (".mc-catalog-" + uuid.uuid4().hex)
    shutil.copytree(ROOT / "courses", temporary)
    try:
        yield temporary
    finally:
        resolved = temporary.resolve(strict=True)
        assert resolved.parent == parent and resolved.name.startswith(".mc-catalog-")
        shutil.rmtree(resolved)


@pytest.mark.parametrize("course_id", ["backend", "backend_copy"])
def test_full_products_preserve_every_baseline_resource_and_private_spec(runtime, course_id):
    baseline = json.loads((ROOT / "services/core-api/tests/fixtures/contracts/multicourse/MC1.2/inventory.json").read_text(encoding="utf-8"))
    current = ROOT / "services/core-api/tests/fixtures/contracts/multicourse/main-import/main-content-before.json"
    if current.is_file():
        baseline = json.loads(current.read_text(encoding="utf-8"))
    catalog = runtime.snapshot(course_id).catalog
    assert len(catalog.TRACKS) == 9
    assert len(catalog.LESSONS) == (302 if course_id == "backend" else 310)
    assert sum(len(lesson["tasks"]) for lesson in catalog.LESSONS) == 204
    expected = [(track["track_id"], lesson["lesson_id"], lesson["legacy_id"], lesson["source_file"],
                 [(task["task_id"], task["revision"]) for task in lesson["tasks"]])
                for track in baseline["tracks"] for lesson in track["lessons"]]
    if course_id == "backend":
        # Account for explicit additions and moves while retaining the historical inventory.
        # Preserve the original track, public/legacy IDs and task revisions for each move.
        block17_sources = (
            ("lesson-2c72a2e8f15b3d99765d3a4d", "chapter_05/block_17/81 - Создаём согласованный резерв Planner.md"),
            ("lesson-79ad20d8bad8ea5e8682a7ce", "chapter_05/block_17/81.1 - Восстанавливаем рабочую копию из резерва.md"),
            ("lesson-a63726516955a18a16be6c0d", "chapter_05/block_17/82 - Создаём исходную историю на новой базе.md"),
            ("lesson-0df09001e79b8b3a9b510600", "chapter_05/block_17/83 - Подключаем заполненную копию к истории.md"),
            ("lesson-c5597af4286aa5f6de00eccd", "chapter_05/block_17/84 - Добавляем пользователя в схему Planner.md"),
            ("lesson-0576d7e8df944d238cbb79e4", "chapter_05/block_17/85 - Записываем уникальные учётные записи.md"),
        )
        retired_block15_ids = {
            "lesson-6fba6a4aadfbd60afad41707",  # 71.1
            "lesson-ae74df09d7d35d88a34f52c0",  # 73.1
            "lesson-68a97f28ebcb4f5e7776430b",  # 75.1
        }
        block15_insertions = {
            "lesson-4d0e92d5a45c9a88004f2f6e": (  # 73
                "lesson-816d61d45c842f03786c56af",
                "chapter_04/block_15/73.2 - Считаем статистику в SQL.md",
            ),
        }
        historical_by_id = {item[1]: item for item in expected}
        replacement_ids = {lesson_id for lesson_id, _ in block17_sources}
        rewritten = []
        inserted_block17 = False
        for historic in expected:
            if historic[3].startswith("chapter_05/block_17/"):
                if not inserted_block17:
                    for lesson_id, source in block17_sources:
                        original = historical_by_id[lesson_id]
                        rewritten.append((*original[:3], source, original[4]))
                    inserted_block17 = True
            elif historic[1] not in replacement_ids and historic[1] not in retired_block15_ids:
                rewritten.append(historic)
                if historic[1] in block15_insertions:
                    lesson_id, source = block15_insertions[historic[1]]
                    rewritten.append((historic[0], lesson_id, lesson_id, source, []))
        assert inserted_block17
        expected = rewritten
    actual = [(track["id"], lesson["id"], lesson["legacy_id"], lesson["source_file"],
               [(task["id"], task["revision"]) for task in lesson["tasks"]])
              for track in catalog.TRACKS for lesson in track["lessons"]]
    assert len(actual) == len(expected)
    for index, (observed, historic) in enumerate(zip(actual, expected)):
        assert observed == historic, f"{course_id} lesson identity/revision mismatch at index {index}"


def test_lesson_70_course_specific_policy_and_public_answers(runtime):
    lesson_id = "lesson-c4878f92ca23a93b777d9fa8"
    provider = LocalCourseProvider(runtime)
    for course_id, private, requirements in (("backend", True, True), ("backend_copy", False, False)):
        catalog = runtime.snapshot(course_id).catalog
        lesson = catalog.find_lesson(lesson_id)
        assert lesson["private_manual_answers"] is private
        exercise = lesson["manual_practice"][0]
        assert ("requirements" in exercise["tasks"][2]) is requirements
        assert "answer" in exercise["tasks"][2]
        public = next(item for item in provider.get_public_lessons(lesson["track"], course_id=course_id)
                      if item["id"] == lesson_id)
        assert "answer" not in public["manual_practice"][0]["tasks"][2]
        assert "private_manual_answers" not in public


def test_python_modules_and_provider_results_are_not_shared_between_courses(runtime):
    a = runtime.snapshot("backend")
    b = runtime.snapshot("backend_copy")
    assert a.catalog is not b.catalog
    assert a.context.plans != b.context.plans
    a_practice = a.context.get_authoring("chapter_01")
    b_practice = b.context.get_authoring("chapter_01")
    assert a_practice is not b_practice
    assert a_practice.CODE_TASKS is not b_practice.CODE_TASKS
    assert a_practice.CODE_TASKS == b_practice.CODE_TASKS
    provider = LocalCourseProvider(runtime)
    first = a.catalog.LESSONS[0]["id"]
    changed = provider.find_lesson(first, course_id="backend")
    changed["manual_practice"].append({"untrusted_mutation": True})
    assert provider.find_lesson(first, course_id="backend")["manual_practice"] == b.catalog.LESSONS[0]["manual_practice"]
    assert provider.find_lesson(first, course_id="backend_copy")["manual_practice"] == b.catalog.LESSONS[0]["manual_practice"]


def test_concurrent_scopes_reset_and_resolve_the_right_product(runtime):
    provider = LocalCourseProvider(runtime)

    def read(course):
        assert current_course_id() == "backend"
        with course_scope(course):
            assert current_course_id() == course
            snapshot = runtime.snapshot(current_course_id())
            result = provider.get_tracks_summary()
            assert snapshot.context.course_id == course
            assert len(result) == 9
            with pytest.raises(RuntimeError):
                with course_scope("nested"):
                    raise RuntimeError("cancelled operation")
            assert current_course_id() == course
        assert current_course_id() == "backend"
        return snapshot.context.namespace

    jobs = ["backend", "backend_copy"] * 20
    with ThreadPoolExecutor(max_workers=8) as executor:
        namespaces = list(executor.map(read, jobs))
    assert len(set(namespaces)) == 2
    assert current_course_id() == "backend"


def test_missing_and_disabled_course_never_falls_back(runtime, disposable_courses):
    with pytest.raises(CourseNotFoundError):
        runtime.snapshot("does_not_exist")
    path = disposable_courses / "registry.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    registry["courses"][1]["enabled"] = False
    path.write_text(json.dumps(registry), encoding="utf-8")
    other = CourseRuntime(disposable_courses)
    with pytest.raises(CourseNotFoundError):
        other.snapshot("backend_copy")


def test_digest_drift_and_missing_resource_fail_before_loading(disposable_courses):
    broken = disposable_courses / "backend_copy/backend/plans/chapter_01/track.json"
    broken.write_text(broken.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(CoursePackageError, match="content_version"):
        CourseRuntime(disposable_courses).snapshot("backend_copy")
    (disposable_courses / "backend/frontend/course/index.ts").unlink()
    with pytest.raises(CoursePackageError, match="missing course resource"):
        CourseRuntime(disposable_courses).snapshot("backend")


def test_changed_copy_python_and_markdown_are_isolated_and_pinned(disposable_courses):
    runtime = CourseRuntime(disposable_courses)
    original = runtime.snapshot("backend")
    # Only trusted test authoring files are edited, never the running source/data.
    package = disposable_courses / "backend_copy"
    practice = package / "backend/practice/chapter_01/__init__.py"
    practice.write_text(practice.read_text(encoding="utf-8") + '\nCOURSE_SENTINEL = "copy-only"\n', encoding="utf-8")
    lesson = package / "backend/plans" / original.catalog.LESSONS[0]["source_file"]
    lesson.write_text(lesson.read_text(encoding="utf-8") + "\nCopy-specific theory marker.\n", encoding="utf-8")
    manifest_path = package / "course.yaml"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    from pymentor_core.adapters.course_local.packages import load_course_manifest
    manifest["content_version"] = content_version(package, load_course_manifest(package))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    changed = CourseRuntime(disposable_courses).snapshot("backend_copy")
    assert changed.context.get_authoring("chapter_01").COURSE_SENTINEL == "copy-only"
    assert not hasattr(original.context.get_authoring("chapter_01"), "COURSE_SENTINEL")
    assert "Copy-specific theory marker." in changed.catalog.LESSONS[0]["theory_markdown"]
    assert "Copy-specific theory marker." not in original.catalog.LESSONS[0]["theory_markdown"]
    assert runtime.snapshot("backend") is original


def test_no_database_or_network_driver_is_needed_by_the_loader(runtime):
    # Collection of neighboring ASGI tests may already import app/database.
    # A fresh process proves the loader's dependency boundary independently.
    import os
    import subprocess
    env = os.environ.copy()
    for key in ('DATABASE_URL','REDIS_URL','PYMENTOR_MIGRATION_URL'):
        env.pop(key, None)
    root = Path(__file__).resolve().parents[3]
    env['PYTHONPATH'] = str(root/'services/core-api')
    script = '''import sys
from pathlib import Path
from pymentor_core.adapters.course_local.runtime import CourseRuntime
CourseRuntime(Path(sys.argv[1])).snapshot("backend")
assert "pymentor_core.app.factory" not in sys.modules
assert "pymentor_core.app.database" not in sys.modules
print("loader-only PASS")
'''
    checked = subprocess.run([sys.executable,'-B','-c',script,str(runtime.registry.root)],env=env,capture_output=True,text=True,timeout=30)
    assert checked.returncode == 0, checked.stderr
    assert checked.stdout.strip() == 'loader-only PASS'


def test_all_public_tasks_in_both_courses_hide_judge_tests_and_legacy_aliases(runtime):
    provider = LocalCourseProvider(runtime)
    for course in ("backend", "backend_copy"):
        seen = 0
        for track in provider.get_tracks_summary(course_id=course):
            for lesson in provider.get_public_lessons(track["id"], course_id=course):
                assert "theory_markdown" not in lesson
                for task in lesson["tasks"]:
                    assert not {"tests", "hidden_tests", "reference_code", "legacy_ids"}.intersection(task)
                    seen += 1
        assert seen == 204
