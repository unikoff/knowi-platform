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


def test_full_products_preserve_every_baseline_resource_and_private_spec(runtime):
    baseline = json.loads((ROOT / "services/core-api/tests/fixtures/contracts/multicourse/MC1.2/inventory.json").read_text(encoding="utf-8"))
    current = ROOT / "services/core-api/tests/fixtures/contracts/multicourse/main-import/main-content-before.json"
    if current.is_file():
        captured = json.loads(current.read_text(encoding="utf-8"))
        baseline = {"tracks": captured["tracks"], "identity_authority": {"content_revision": captured["content_revision"]}}
    a = runtime.snapshot("backend")
    b = runtime.snapshot("backend_copy")
    assert a.catalog.TRACKS == b.catalog.TRACKS
    assert len(a.catalog.TRACKS) == 9
    assert len(a.catalog.LESSONS) == 310
    assert sum(len(lesson["tasks"]) for lesson in a.catalog.LESSONS) == 204
    assert a.catalog.get_content_revision() == baseline["identity_authority"]["content_revision"]
    expected = []
    for track in baseline["tracks"]:
        for lesson in track["lessons"]:
            expected.append((track["track_id"], lesson["lesson_id"], lesson["legacy_id"], lesson["source_file"], [(t["task_id"], t["revision"]) for t in lesson["tasks"]]))
    actual = []
    for track in a.catalog.TRACKS:
        for lesson in track["lessons"]:
            actual.append((track["id"], lesson["id"], lesson["legacy_id"], lesson["source_file"], [(t["id"], t["revision"]) for t in lesson["tasks"]]))
    assert actual == expected


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
