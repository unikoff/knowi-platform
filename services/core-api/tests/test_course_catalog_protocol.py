"""Course-owned catalogs with a non-Python program, without database/network I/O."""

from dataclasses import replace
import importlib
import json
from pathlib import Path
import shutil
import sys
import uuid
from types import MappingProxyType
from unittest.mock import Mock

import pytest

from pymentor_core.adapters.course_local.packages import (
    CourseNotFoundError, CoursePackageError, RESERVED_COURSE_SLUGS, load_course_manifest,
)
from pymentor_core.adapters.course_local.provider import LocalCourseProvider
from pymentor_core.adapters.course_local.runtime import CourseRuntime, content_version
from pymentor_core.modules.learning.progress_policy import (
    is_countable_lesson, is_overview_lesson, required_lesson_tasks,
    uses_card_completion, uses_persisted_task_progress,
)

ROOT = Path(__file__).resolve().parents[3]
COURSE = "isolation_fixture"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def repin(root):
    package = root / COURSE
    manifest = json.loads((package / "course.yaml").read_text(encoding="utf-8"))
    manifest["content_version"] = content_version(package, load_course_manifest(package))
    write_json(package / "course.yaml", manifest)


@pytest.fixture
def course_root():
    tests_root = Path(__file__).resolve().parent
    temporary = tests_root / (".catalog-protocol-" + uuid.uuid4().hex)
    temporary.mkdir()
    root = temporary / "courses"
    package = root / COURSE
    for relative in ("backend/plans/program", "backend/practice", "frontend"):
        (package / relative).mkdir(parents=True, exist_ok=True)
    (package / "backend/practice/__init__.py").write_text("", encoding="utf-8")
    (package / "backend/plans/program/intro.md").write_text("# A different program", encoding="utf-8")
    for entry in ("course", "personal", "landing"):
        (package / f"frontend/{entry}.ts").write_text("export {};", encoding="utf-8")
    shutil.copytree(ROOT / "courses/shared/backend", root / "shared/backend")
    manifest = {
        "schema_version": 2, "course_id": COURSE, "slug": "isolation-fixture", "title": "Design course",
        "content_version": "0" * 64,
        "layout": {"plans": "backend/plans", "practice": "backend/practice", "frontend": "frontend"},
        "frontend": {"course": "frontend/course.ts", "personal_landing": "frontend/personal.ts", "course_landing": "frontend/landing.ts"},
        "tracks": [{"track_id": "design-studio", "title": "Design studio", "order": 1, "source_dirs": ["program"]}],
    }
    write_json(package / "course.yaml", manifest)
    write_json(root / "registry.json", {"schema_version": 1, "courses": [
        {"course_id": COURSE, "enabled": True, "manifest": f"{COURSE}/course.yaml"},
    ]})
    write_json(package / "personal.settings.yaml", {"schema_version": 1, "course_id": COURSE,
        "contacts": None, "telegram": {"enabled": False, "chat_id": None, "topic_id": None}})
    (package / "backend/catalog.py").write_text('''from .catalog_api import Catalog
TRACKS = [{"id": "design-studio", "title": "Design studio", "order": 1, "tagline": "Own program", "lessons": [
    {"id": "studio-welcome", "track": "design-studio", "module": "Orientation", "title": "Welcome", "access": "free",
     "source_file": "program/intro.md", "tasks": [], "self_check": False, "manual_practice": [],
     "progress_policy": {"overview": True}},
    {"id": "studio-practice", "track": "design-studio", "module": "Workshop", "title": "Practice", "access": "free",
     "source_file": "program/intro.md", "self_check": True, "private_manual_answers": True,
     "manual_practice": [{"tasks": [{"title": "Reflect", "answer": {"code": "PRIVATE_ANSWER"}}]}],
     "tasks": [{"id": "studio-task", "revision": "a" * 64, "title": "Visible", "prompt": "Write a result",
                "tests": [{"expected": "PRIVATE_JUDGE"}], "reference_code": "PRIVATE_REFERENCE"}]}
]}]
globals().update(Catalog(TRACKS).exports())
''', encoding="utf-8")
    repin(root)
    try:
        yield root
    finally:
        resolved = temporary.resolve(strict=True)
        assert resolved.parent == tests_root and resolved.name.startswith(".catalog-protocol-")
        shutil.rmtree(resolved)


def test_arbitrary_program_uses_own_catalog_and_generic_progress(course_root):
    runtime = CourseRuntime(course_root)
    snapshot = runtime.snapshot(COURSE)
    assert Path(snapshot.catalog.__file__).resolve() == course_root / COURSE / "backend/catalog.py"
    assert snapshot.catalog.default_track_id() == "design-studio"
    welcome, practice = snapshot.catalog.LESSONS
    assert is_overview_lesson(welcome) and is_countable_lesson(welcome)
    assert not is_overview_lesson(practice)
    assert uses_card_completion(practice) and uses_persisted_task_progress(practice)
    assert [task["id"] for task in required_lesson_tasks(practice)] == ["studio-task"]
    provider = LocalCourseProvider(runtime)
    public = provider.get_public_lessons("design-studio", course_id=COURSE)
    assert "PRIVATE_" not in json.dumps(public)
    assert "progress_policy" not in public[0]
    assert provider.find_task("studio-task", course_id=COURSE)["tests"]
    assert provider.find_lesson_by_task("studio-task", course_id=COURSE)["id"] == "studio-practice"


def test_unknown_disabled_and_reserved_registry_identity_fail_closed(course_root):
    runtime = CourseRuntime(course_root)
    with pytest.raises(CourseNotFoundError):
        runtime.snapshot("unknown")
    entries = dict(runtime.registry.entries)
    entries[COURSE] = replace(entries[COURSE], enabled=False)
    runtime.registry = replace(runtime.registry, entries=MappingProxyType(entries))
    with pytest.raises(CourseNotFoundError):
        runtime.snapshot(COURSE)
    package = course_root / COURSE
    manifest = json.loads((package / "course.yaml").read_text())
    for slug in RESERVED_COURSE_SLUGS:
        manifest["slug"] = slug
        write_json(package / "course.yaml", manifest)
        with pytest.raises(CoursePackageError, match="reserved"):
            CourseRuntime(course_root)


@pytest.mark.parametrize("broken", ["TRACKS=[]\nLESSONS=[]\n", "raise RuntimeError('author failure')\n"])
def test_protocol_and_import_failures_clean_namespace_and_allow_safe_retry(course_root, broken):
    path = course_root / COURSE / "backend/catalog.py"
    original = path.read_text()
    path.write_text(broken, encoding="utf-8")
    repin(course_root)
    runtime = CourseRuntime(course_root)
    context = runtime.context(COURSE)
    with pytest.raises((CoursePackageError, RuntimeError)):
        runtime.snapshot(COURSE)
    assert COURSE not in runtime._contexts and COURSE not in runtime._snapshots
    assert not any(name == context.namespace or name.startswith(context.namespace + ".") for name in sys.modules)
    path.write_text(original, encoding="utf-8")
    repin(course_root)
    assert runtime.snapshot(COURSE).catalog.default_track_id() == "design-studio"


def test_catalog_code_shared_helpers_and_manifest_inputs_are_pinned(course_root):
    runtime = CourseRuntime(course_root)
    pinned = runtime.snapshot(COURSE)
    path = course_root / COURSE / "backend/catalog.py"
    path.write_text(path.read_text() + '\nCOURSE_SENTINEL = "new"\n', encoding="utf-8")
    with pytest.raises(CoursePackageError, match="content_version"):
        CourseRuntime(course_root).snapshot(COURSE)
    repin(course_root)
    changed = CourseRuntime(course_root).snapshot(COURSE)
    assert changed.context.namespace != pinned.context.namespace
    assert changed.catalog.COURSE_SENTINEL == "new"
    assert not hasattr(pinned.catalog, "COURSE_SENTINEL")
    shared = course_root / "shared/backend/catalog_api.py"
    shared.write_text(shared.read_text() + "\n# new helper\n", encoding="utf-8")
    with pytest.raises(CoursePackageError, match="content_version"):
        CourseRuntime(course_root).snapshot(COURSE)
    repin(course_root)
    manifest_path = course_root / COURSE / "course.yaml"
    manifest = json.loads(manifest_path.read_text())
    manifest["tracks"][0]["title"] = "Changed server input"
    write_json(manifest_path, manifest)
    with pytest.raises(CoursePackageError, match="content_version"):
        CourseRuntime(course_root).snapshot(COURSE)


def test_same_ids_in_separate_registry_roots_do_not_share_modules(course_root):
    other = course_root.parent / "other"
    shutil.copytree(course_root, other)
    a, b = CourseRuntime(course_root).snapshot(COURSE), CourseRuntime(other).snapshot(COURSE)
    assert a.catalog is not b.catalog and a.catalog.TRACKS is not b.catalog.TRACKS
    a.catalog.LESSONS[0]["title"] = "Changed in one runtime"
    assert b.catalog.LESSONS[0]["title"] == "Welcome"


def test_missing_or_linked_course_catalog_never_uses_shared_fallback(course_root):
    catalog = course_root / COURSE / "backend/catalog.py"
    saved = catalog.read_bytes()
    catalog.unlink()
    with pytest.raises(CoursePackageError, match="missing course resource"):
        CourseRuntime(course_root).snapshot(COURSE)
    original = catalog.with_name("original.py")
    original.write_bytes(saved)
    try:
        catalog.symlink_to(original)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(CoursePackageError, match="links are not permitted"):
        CourseRuntime(course_root).snapshot(COURSE)


def test_registry_notification_slug_and_inactive_settings_are_generic(course_root, monkeypatch):
    # SQL model declarations require a configured URL at import. A synthetic,
    # unreachable URL plus a connection tripwire keeps this a pure unit check.
    from sqlalchemy.engine import Engine
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://fixture:fixture@127.0.0.1:1/unused")
    def no_connection(*args, **kwargs):
        pytest.fail("unit notification routing must not connect to SQL")
    monkeypatch.setattr(Engine, "connect", no_connection)
    monkeypatch.setattr(Engine, "raw_connection", no_connection)
    from pymentor_core.adapters.course_local import runtime as runtime_module
    from pymentor_core.modules.landings.contracts import course_landing_slugs, LandingError
    from pymentor_core.adapters.sql import course_destinations, landings
    from pymentor_core.shared import bindings
    from types import SimpleNamespace

    monkeypatch.setattr(bindings, "_bindings", SimpleNamespace(landings=landings))

    runtime = CourseRuntime(course_root)
    monkeypatch.setattr(runtime_module, "default_runtime", lambda: runtime)
    monkeypatch.setattr(course_destinations, "COURSES_ROOT", course_root)
    assert course_landing_slugs() == {COURSE: "isolation-fixture"}
    settings = course_destinations.read_trusted_course_settings()
    assert settings[COURSE]["telegram"] == {"enabled": False, "chat_id": None, "topic_id": None}
    db = Mock()
    with pytest.raises(LandingError) as rejected:
        landings.sync_course_destinations(db, settings=settings, expected_versions={COURSE: 0})
    assert rejected.value.status_code == 409
    assert not db.mock_calls
    entries = {COURSE: replace(runtime.registry.entries[COURSE], enabled=False)}
    runtime.registry = replace(runtime.registry, entries=MappingProxyType(entries))
    assert course_landing_slugs() == {}
    assert course_landing_slugs(include_disabled=True) == {COURSE: "isolation-fixture"}
    with pytest.raises(LandingError) as rejected:
        landings.submit_course_consultation(db, "isolation-fixture", "event-id", {})
    assert rejected.value.status_code == 404 and not db.mock_calls


def test_creator_validator_supports_an_independent_program_and_root_guidance(course_root):
    import importlib.util
    spec = importlib.util.spec_from_file_location("course_isolation_validator", ROOT / "services/core-api/tools/check_courses.py")
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    (course_root / "README.md").write_text("# Course creator guide", encoding="utf-8")
    (course_root / "AGENTS.md").write_text("Keep course ownership local.", encoding="utf-8")
    result = validator.validate_courses(course_root)
    assert result["tracks"] == result["isolation_fixture_tracks"] == 1
    assert "backend_tracks" not in result and "backend_copy_tracks" not in result
