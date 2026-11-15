from __future__ import annotations

import json
from pathlib import Path
import shutil
import uuid

import pytest
import yaml

from pymentor_core.adapters.course_local.packages import (
    CourseNotFoundError,
    CoursePackageError,
    load_course,
    load_course_manifest,
    load_legacy_backend_manifest,
    load_registry,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "services/core-api/tests/fixtures/contracts" / "multicourse" / "MC1.4" / "examples"
CORE_EXAMPLES = REPO_ROOT / "services" / "core-api" / "contracts" / "examples"


@pytest.fixture
def package_root():
    tests_root = Path(__file__).resolve().parent
    directory = tests_root / f".corp201-packages-{uuid.uuid4().hex}"
    directory.mkdir()
    try:
        yield directory
    finally:
        resolved = directory.resolve(strict=True)
        if resolved.parent != tests_root or not resolved.name.startswith(".corp201-packages-"):
            raise RuntimeError("refusing to clean an unexpected temporary package directory")
        shutil.rmtree(resolved)


def _copy_contract_packages(root: Path) -> None:
    for course_id in ("backend", "backend_copy"):
        package = root / course_id
        package.mkdir(parents=True)
        shutil.copyfile(EXAMPLES / f"{course_id}.course.json", package / "course.yaml")
    shutil.copyfile(EXAMPLES / "registry.json", root / "registry.json")


def _manifest(course_id: str = "backend") -> dict:
    return {
        "schema_version": 2,
        "course_id": course_id,
        "slug": course_id,
        "title": f"Course {course_id}",
        "content_version": "a" * 64,
        "layout": {"plans": "backend/plans", "practice": "backend/practice", "frontend": "frontend"},
        "frontend": {
            "course": "frontend/course/index.ts",
            "personal_landing": "frontend/landing-personal/index.ts",
            "course_landing": "frontend/landing-course/index.ts",
        },
        "tracks": [{"track_id": "legacy-track-id", "title": "Track", "order": 1, "source_dirs": ["chapter_01"]}],
    }


def _write_package(root: Path, manifest: dict | str, *, course_id: str = "backend", path: str = "course.yaml") -> None:
    package = root / course_id
    package.mkdir(parents=True, exist_ok=True)
    content = json.dumps(manifest) if isinstance(manifest, dict) else manifest
    (package / path).write_text(content, encoding="utf-8")


def _registry(root: Path, entries: list[dict]) -> None:
    (root / "registry.json").write_text(json.dumps({"schema_version": 1, "courses": entries}), encoding="utf-8")


def _entry(course_id: str = "backend", *, enabled: bool = True, manifest: str | None = None) -> dict:
    return {"course_id": course_id, "enabled": enabled, "manifest": manifest or f"{course_id}/course.yaml"}


def test_contract_examples_load_as_two_distinct_products_with_stable_tracks(package_root: Path) -> None:
    _copy_contract_packages(package_root)
    registry = load_registry(package_root)
    backend = load_course(registry, "backend")
    copy = load_course(registry, "backend_copy")

    assert backend.course_id == "backend" and copy.course_id == "backend_copy"
    assert backend.slug != copy.slug
    assert backend.content_version == copy.content_version
    assert len(backend.tracks) == len(copy.tracks) == 9
    assert [track["track_id"] for track in backend.tracks] == [track["track_id"] for track in copy.tracks]
    assert [track["order"] for track in backend.tracks] == list(range(1, 10))
    with pytest.raises(TypeError):
        backend.metadata["course_id"] = "other"
    with pytest.raises(TypeError):
        registry.entries["new"] = registry.entries["backend"]


def test_yaml_registry_and_manifest_are_loaded_as_safe_data(package_root: Path) -> None:
    _write_package(package_root, yaml.safe_dump(_manifest(), sort_keys=False))
    (package_root / "registry.json").write_text(
        yaml.safe_dump({"schema_version": 1, "courses": [_entry()]}, sort_keys=False), encoding="utf-8"
    )
    loaded = load_course(load_registry(package_root), "backend")
    assert loaded.course_id == "backend"
    assert loaded.tracks[0]["track_id"] == "legacy-track-id"


def test_explicit_legacy_adapter_preserves_old_track_and_lesson_task_refs(package_root: Path) -> None:
    fixture = package_root / "current-course.json"
    shutil.copyfile(CORE_EXAMPLES / "current-course.json", fixture)
    legacy = load_legacy_backend_manifest(package_root)
    original = json.loads(fixture.read_text(encoding="utf-8"))

    assert legacy.product_course_id == "backend"
    assert legacy.legacy_course_id == original["course_id"]
    assert legacy.legacy_course_id != legacy.product_course_id
    assert legacy.metadata["lessons"] == tuple(
        {
            **lesson,
            "task_refs": tuple(lesson["task_refs"]),
        }
        for lesson in original["lessons"]
    )
    assert legacy.metadata["lessons"][1]["lesson_id"] == original["lessons"][1]["lesson_id"]
    assert legacy.metadata["lessons"][1]["task_refs"][0]["task_id"] == original["lessons"][1]["task_refs"][0]["task_id"]


@pytest.mark.parametrize(
    "bad_path",
    ["../escape", "plans/../../outside", "/absolute/path", "C:/absolute", "\\\\server\\share", "plans\\chapter", "plans:stream", "./plans", "plans//chapter", "NUL", "CON.txt", "plans.", "plan\x00s", "plan\x1f"] ,
)
def test_manifest_rejects_unsafe_relative_paths(package_root: Path, bad_path: str) -> None:
    data = _manifest()
    data["layout"]["plans"] = bad_path
    _write_package(package_root, data)
    with pytest.raises(CoursePackageError):
        load_course_manifest(package_root / "backend")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema_version=3),
        lambda d: d.update(schema_version=True),
        lambda d: d.update(course_id="../backend"),
        lambda d: d.update(course_id="b" * 65),
        lambda d: d["tracks"].append(dict(d["tracks"][0])),
        lambda d: d["tracks"][0].update(order=True),
        lambda d: d.update(unexpected=True),
        lambda d: d["layout"].update(extra="x"),
        lambda d: d.update(content_version="short"),
    ],
)
def test_manifest_rejects_invalid_schema_ids_and_duplicate_tracks(package_root: Path, mutate) -> None:
    data = _manifest()
    mutate(data)
    _write_package(package_root, data)
    with pytest.raises(CoursePackageError):
        load_course_manifest(package_root / "backend")


@pytest.mark.parametrize(
    "document",
    [
        '{"schema_version": 1, "schema_version": 1, "courses": []}',
        "schema_version: 1\nschema_version: 1\ncourses: []\n",
        "!!python/object/apply:os.system ['echo unsafe']",
        "a: &anchor [x]\nb: *anchor\n",
        "schema_version: [unterminated\n",
    ],
)
def test_registry_rejects_duplicate_keys_unsafe_tags_aliases_malformed_and_oversized(package_root: Path, document: str) -> None:
    (package_root / "registry.json").write_text(document, encoding="utf-8")
    with pytest.raises(CoursePackageError):
        load_registry(package_root)


def test_registry_rejects_oversized_document(package_root: Path) -> None:
    (package_root / "registry.json").write_text("schema_version: 1\ncourses: []\n" + (" " * (256 * 1024)), encoding="utf-8")
    with pytest.raises(CoursePackageError, match="exceeds"):
        load_registry(package_root)


def test_yaml_nesting_is_bounded(package_root: Path) -> None:
    nested = "x"
    for _ in range(36):
        nested = f"[{nested}]"
    (package_root / "registry.json").write_text(nested, encoding="utf-8")
    with pytest.raises(CoursePackageError):
        load_registry(package_root)


def test_registry_fails_closed_for_missing_root_file_course_and_disabled_course(package_root: Path) -> None:
    with pytest.raises(CoursePackageError):
        load_registry(package_root / "does-not-exist")
    (package_root / "registry.json").write_text('{"schema_version":1,"courses":[]}', encoding="utf-8")
    registry = load_registry(package_root)
    with pytest.raises(CourseNotFoundError):
        load_course(registry, "backend")

    _write_package(package_root, _manifest())
    _registry(package_root, [_entry(enabled=False)])
    registry = load_registry(package_root)
    with pytest.raises(CourseNotFoundError):
        load_course(registry, "backend")


def test_registry_rejects_duplicate_ids_missing_manifest_and_manifest_id_mismatch(package_root: Path) -> None:
    _write_package(package_root, _manifest())
    _registry(package_root, [_entry(), _entry()])
    with pytest.raises(CoursePackageError, match="duplicate registry course_id"):
        load_registry(package_root)

    _registry(package_root, [_entry(manifest="missing/course.yaml")])
    with pytest.raises(CoursePackageError):
        load_registry(package_root)

    _registry(package_root, [_entry()])
    load_registry(package_root)
    _write_package(package_root, _manifest("other"))
    with pytest.raises(CoursePackageError, match="does not match"):
        load_registry(package_root)


def test_registry_rejects_boolean_version_and_cross_package_manifest_path(package_root: Path) -> None:
    (package_root / "registry.json").write_text('{"schema_version":true,"courses":[]}', encoding="utf-8")
    with pytest.raises(CoursePackageError, match="schema_version"):
        load_registry(package_root)

    _write_package(package_root, _manifest("backend"), course_id="backend")
    _write_package(package_root, _manifest("backend_copy"), course_id="backend_copy")
    _registry(package_root, [_entry("backend", manifest="backend_copy/course.yaml")])
    with pytest.raises(CoursePackageError, match="exactly backend/course.yaml"):
        load_registry(package_root)

    _registry(package_root, [_entry("backend", manifest="backend/other.yaml")])
    with pytest.raises(CoursePackageError, match="exactly backend/course.yaml"):
        load_registry(package_root)


def test_registry_rejects_duplicate_product_slugs(package_root: Path) -> None:
    _write_package(package_root, _manifest("backend"), course_id="backend")
    second = _manifest("backend_copy")
    second["slug"] = "backend"
    _write_package(package_root, second, course_id="backend_copy")
    _registry(package_root, [_entry("backend"), _entry("backend_copy")])
    with pytest.raises(CoursePackageError, match="duplicate registry course slug"):
        load_registry(package_root)


def test_registry_rejects_symlink_escape_when_supported(package_root: Path) -> None:
    outside = package_root.parent / f"{package_root.name}-outside"
    outside.mkdir()
    (outside / "course.yaml").write_text(json.dumps(_manifest()), encoding="utf-8")
    backend_package = package_root / "backend"
    backend_package.mkdir()
    try:
        (backend_package / "course.yaml").symlink_to(outside / "course.yaml")
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable in this Windows environment")
    try:
        _registry(package_root, [_entry(manifest="backend/course.yaml")])
        with pytest.raises(CoursePackageError, match="filesystem link"):
            load_registry(package_root)
    finally:
        (backend_package / "course.yaml").unlink(missing_ok=True)
        shutil.rmtree(outside, ignore_errors=True)

    _write_package(package_root, _manifest())
    outside.mkdir()
    linked_frontend = backend_package / "frontend"
    try:
        linked_frontend.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        shutil.rmtree(outside, ignore_errors=True)
        pytest.skip("directory symlinks are unavailable in this Windows environment")
    try:
        with pytest.raises(CoursePackageError, match="filesystem link"):
            load_course_manifest(backend_package)
    finally:
        linked_frontend.unlink(missing_ok=True)
        shutil.rmtree(outside, ignore_errors=True)


def test_legacy_adapter_rejects_v2_and_preserves_v1_identity_only(package_root: Path) -> None:
    _write_package(package_root, _manifest(), path="current-course.json")
    with pytest.raises(CoursePackageError, match="schema_version 1 only"):
        load_legacy_backend_manifest(package_root / "backend")
