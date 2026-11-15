from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import uuid

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
TESTS_ROOT = Path(__file__).resolve().parent
for import_root in (REPO_ROOT / "services" / "core-api" / "tools" / "courses", REPO_ROOT / "services" / "core-api" / "tools"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from check_courses import validate_courses  # noqa: E402
from multicourse_packages import PackageBuildError, check_packages, compute_content_version, create_packages  # noqa: E402
from pymentor_core.adapters.course_local.packages import CoursePackageError  # noqa: E402


@pytest.fixture
def package_root():
    directory = TESTS_ROOT / f".mc21-course-packages-{uuid.uuid4().hex}"
    directory.mkdir()
    courses = directory / "courses"
    try:
        create_packages(REPO_ROOT, courses)
        yield courses
    finally:
        resolved = directory.resolve(strict=True)
        if resolved.parent != TESTS_ROOT.resolve(strict=True) or not resolved.name.startswith(".mc21-course-packages-"):
            raise RuntimeError("refusing to clean an unexpected temporary package directory")
        shutil.rmtree(resolved)


def test_created_packages_match_every_source_byte_and_validate_from_an_unrelated_cwd(package_root: Path, monkeypatch):
    monkeypatch.chdir(TESTS_ROOT)
    counts = check_packages(REPO_ROOT, package_root)
    assert set(counts) == {"markdown_per_course", "track_json_per_course", "python_per_course"}
    assert all(value > 0 for value in counts.values())
    # Authoring evolves: compare every current source byte, not a frozen lesson count.
    def files(root):
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob("*") if path.is_file()}
    assert files(package_root) == files(REPO_ROOT / "courses")
    result = validate_courses(package_root)
    assert result["tracks"] == result["backend_tracks"] == result["backend_copy_tracks"] == 9
    assert result["files"] == len(files(package_root))


def test_validator_rejects_missing_frontend_entrypoint(package_root: Path):
    (package_root / "backend" / "frontend" / "course" / "index.ts").unlink()
    with pytest.raises(CoursePackageError, match="missing frontend scaffold"):
        validate_courses(package_root)


def test_validator_rejects_deleted_authoring_resource_by_digest(package_root: Path):
    mapped = package_root / "backend" / "backend" / "plans" / "chapter_01" / "block_00" / "План обучения.md"
    mapped.unlink()
    with pytest.raises(CoursePackageError, match="content_version"):
        validate_courses(package_root)


def test_validator_rejects_traversal_and_schema_mismatch(package_root: Path):
    manifest_path = package_root / "backend" / "course.yaml"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["layout"]["plans"] = "../outside"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CoursePackageError):
        validate_courses(package_root)


def test_source_copy_check_reports_mutated_authoring_file(package_root: Path):
    authored = package_root / "backend" / "backend" / "practice" / "chapter_01" / "block_00" / "manual.py"
    authored.write_bytes(authored.read_bytes() + b"\n# changed\n")
    with pytest.raises(PackageBuildError, match="content_version"):
        check_packages(REPO_ROOT, package_root)


def test_create_refuses_to_overwrite_existing_settings(package_root: Path):
    settings = package_root / "backend" / "personal.settings.yaml"
    original = settings.read_bytes()
    settings.write_bytes(original + b"# user change\n")
    changed = settings.read_bytes()
    with pytest.raises(PackageBuildError, match="refusing to overwrite"):
        create_packages(REPO_ROOT, package_root)
    assert settings.read_bytes() == changed


def test_validator_rejects_runtime_artifacts_unknown_resources_and_symlinks(package_root: Path):
    artifact = package_root / "backend" / "unexpected.sqlite"
    artifact.write_bytes(b"not a database")
    with pytest.raises(CoursePackageError, match="artifact"):
        validate_courses(package_root)
    artifact.unlink()

    unknown = package_root / "backend" / "backend" / "practice" / "unknown.bin"
    unknown.write_bytes(b"unknown authoring resource")
    with pytest.raises(CoursePackageError, match="unrecognized authoring resource"):
        validate_courses(package_root)
    unknown.unlink()

    target = package_root / "backend" / "frontend" / "course" / "index.ts"
    linked = target.with_name("linked.ts")
    try:
        linked.symlink_to(target)
    except (OSError, NotImplementedError):
        return
    with pytest.raises(CoursePackageError, match="symlinks are not permitted"):
        validate_courses(package_root)


def test_isolated_canonical_sources_remain_independent_and_reject_destination_changes():
    directory = TESTS_ROOT / f".mc21-course-packages-{uuid.uuid4().hex}"
    directory.mkdir()
    try:
        repo = directory / "repo"
        shutil.copytree(REPO_ROOT / "courses", repo / "courses")
        source = repo / "courses"
        extra = source / "backend_copy/backend/practice/independent.py"
        extra.write_text("# independent course content\n", encoding="utf-8")
        manifest_path = source / "backend_copy/course.yaml"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["content_version"] = compute_content_version(manifest_path.parent)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        destination = directory / "output"
        create_packages(repo, destination)
        assert (destination / "backend_copy/backend/practice/independent.py").read_bytes() == extra.read_bytes()
        assert not (destination / "backend/backend/practice/independent.py").exists()
        assert check_packages(repo, destination)
        changed = destination / "backend_copy/frontend/course/index.ts"
        changed.write_bytes(changed.read_bytes() + b"// changed\n")
        with pytest.raises(PackageBuildError, match="bytes differ"):
            check_packages(repo, destination)
        before = changed.read_bytes()
        with pytest.raises(PackageBuildError, match="refusing to overwrite"):
            create_packages(repo, destination)
        assert changed.read_bytes() == before
    finally:
        resolved = directory.resolve(strict=True)
        if resolved.parent != TESTS_ROOT.resolve(strict=True) or not resolved.name.startswith(".mc21-course-packages-"):
            raise RuntimeError("refusing to clean an unexpected temporary package directory")
        shutil.rmtree(resolved)


def test_builder_rejects_overlapping_destination_and_invalid_source():
    directory = TESTS_ROOT / f".mc21-course-packages-{uuid.uuid4().hex}"
    directory.mkdir()
    try:
        repo = directory / "repo"
        shutil.copytree(REPO_ROOT / "courses", repo / "courses")
        source = repo / "courses"
        with pytest.raises(PackageBuildError, match="must not contain"):
            create_packages(repo, source / "nested")
        manifest_path = source / "backend/course.yaml"
        original_manifest = manifest_path.read_bytes()
        manifest = json.loads(original_manifest)
        manifest["layout"]["plans"] = "../outside"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(PackageBuildError):
            create_packages(repo, directory / "output")
        manifest_path.write_bytes(original_manifest)
        artifact = source / "backend" / ".env"
        artifact.write_text("private", encoding="utf-8")
        with pytest.raises(PackageBuildError, match="environment files"):
            create_packages(repo, directory / "output")
        artifact.unlink()
        artifact = source / "backend" / "unexpected.sqlite"
        artifact.write_bytes(b"artifact")
        with pytest.raises(PackageBuildError, match="artifact"):
            create_packages(repo, directory / "output")
        artifact.unlink()
        link = source / "backend" / "link"
        try:
            link.symlink_to(source / "backend" / "course.yaml")
        except (OSError, NotImplementedError):
            return
        with pytest.raises(PackageBuildError, match="symlinks"):
            create_packages(repo, directory / "output")
    finally:
        resolved = directory.resolve(strict=True)
        if resolved.parent != TESTS_ROOT.resolve(strict=True) or not resolved.name.startswith(".mc21-course-packages-"):
            raise RuntimeError("refusing to clean an unexpected temporary package directory")
        shutil.rmtree(resolved)
