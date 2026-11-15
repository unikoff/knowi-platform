"""Validate registry resources, digests and trusted course catalog protocols."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[3]
CORE_ROOT = REPO_ROOT / "services" / "core-api"
TOOLS_ROOT = CORE_ROOT / "tools" / "courses"
for import_root in (CORE_ROOT, TOOLS_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from pymentor_core.adapters.course_local.packages import (  # noqa: E402
    CoursePackageError,
    load_course_manifest,
    load_registry,
)
from multicourse_packages import PackageBuildError, compute_content_version  # noqa: E402
from pymentor_core.adapters.course_local.runtime import CourseRuntime  # noqa: E402


FORBIDDEN_COMPONENTS = {"node_modules", ".git", "__pycache__"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key", ".pyc"}


def _inside(path: Path, root: Path, *, strict: bool = True) -> Path:
    try:
        resolved = path.resolve(strict=strict)
        base = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CoursePackageError(f"path does not resolve safely: {path}") from exc
    if resolved != base and base not in resolved.parents:
        raise CoursePackageError(f"path escapes courses root: {path}")
    return resolved


def _validate_tree(root: Path) -> int:
    """Reject symlinks, escapes, and runtime/user artifacts without opening code."""
    file_count = 0
    for path in (root, *root.rglob("*")):
        if path.is_symlink():
            raise CoursePackageError(f"symlinks are not permitted in course roots: {path.relative_to(root)}")
        _inside(path, root, strict=True)
        if path.is_dir():
            if path.name in FORBIDDEN_COMPONENTS:
                raise CoursePackageError(f"runtime/generated directory is not allowed: {path.relative_to(root)}")
            continue
        if not path.is_file():
            raise CoursePackageError(f"unsupported filesystem object: {path.relative_to(root)}")
        relative = path.relative_to(root)
        if any(part.lower() == ".env" or part.lower().startswith(".env.") for part in relative.parts):
            raise CoursePackageError(f"environment files are not allowed: {relative}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise CoursePackageError(f"runtime or secret artifact is not allowed: {relative}")
        file_count += 1
    return file_count


def _safe_relative(value: str, where: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise CoursePackageError(f"{where} must be a POSIX relative path")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise CoursePackageError(f"{where} contains an unsafe path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise CoursePackageError(f"{where} contains an unsafe path")
    return relative


def validate_courses(courses_root: str | Path) -> dict[str, int]:
    """Validate every declared package; execute only enabled trusted catalogs. No app/SQL imports."""
    requested_root = Path(courses_root)
    if requested_root.is_symlink():
        raise CoursePackageError("explicit --courses-root must not be a symlink")
    try:
        root = requested_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CoursePackageError("explicit --courses-root is missing or invalid") from exc
    if not root.is_dir():
        raise CoursePackageError("explicit --courses-root must be a directory")

    file_count = _validate_tree(root)
    registry = load_registry(root)
    actual_root_entries = {path.name for path in root.iterdir()}
    if actual_root_entries - {*registry.entries, "registry.json", "shared", "README.md", "AGENTS.md"}:
        raise CoursePackageError("courses root contains an unregistered package")
    runtime = CourseRuntime(root)
    result: dict[str, int] = {"files": file_count, "tracks": 0}
    for course_id in registry.entries:
        manifest = load_course_manifest(root / course_id, expected_course_id=course_id)
        package_root = _inside(root / course_id, root, strict=True)
        result["tracks"] = max(result["tracks"], len(manifest.tracks))
        entrypoint = _inside(package_root / "backend/catalog.py", package_root, strict=True)
        if not entrypoint.is_file():
            raise CoursePackageError(f"missing trusted catalog entrypoint for {course_id}")
        for name in ("plans", "practice", "frontend"):
            relative = _safe_relative(manifest.layout[name], f"{course_id}.layout.{name}")
            directory = _inside(package_root.joinpath(*relative.parts), package_root, strict=True)
            if not directory.is_dir():
                raise CoursePackageError(f"{course_id} layout.{name} is not a directory")

        for track in manifest.tracks:
            track_id = track["track_id"]
            plans_root = package_root / Path(manifest.layout["plans"])
            for source_dir in track["source_dirs"]:
                source_path = _safe_relative(source_dir, f"{course_id}.tracks.{track_id}.source_dirs")
                candidate_dir = plans_root.joinpath(*source_path.parts)
                if not candidate_dir.exists():
                    raise CoursePackageError(f"missing source directory for {course_id}/{track_id}: {source_dir}")
                directory = _inside(candidate_dir, package_root, strict=True)
                if not directory.is_dir():
                    raise CoursePackageError(f"missing source directory for {course_id}/{track_id}: {source_dir}")
        for key in ("course", "personal_landing", "course_landing"):
            relative = _safe_relative(manifest.frontend[key], f"{course_id}.frontend.{key}")
            candidate_entrypoint = package_root.joinpath(*relative.parts)
            if not candidate_entrypoint.exists():
                raise CoursePackageError(f"missing frontend scaffold for {course_id}: {relative}")
            entrypoint = _inside(candidate_entrypoint, package_root, strict=True)
            if not entrypoint.is_file():
                raise CoursePackageError(f"missing frontend scaffold for {course_id}: {relative}")

        settings_path = _inside(package_root / "personal.settings.yaml", package_root, strict=True)
        try:
            import yaml

            settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise CoursePackageError(f"personal settings placeholder is invalid for {course_id}") from exc
        expected_settings = {
            "schema_version": 1,
            "course_id": course_id,
            "contacts": None,
            "telegram": {"enabled": False, "chat_id": None, "topic_id": None},
        }
        if settings != expected_settings:
            raise CoursePackageError(f"personal settings placeholder differs from the disabled v1 schema for {course_id}")

        try:
            digest = compute_content_version(package_root)
        except (PackageBuildError, OSError) as exc:
            raise CoursePackageError(f"cannot calculate authoring digest for {course_id}: {exc}") from exc
        if digest != manifest.content_version:
            raise CoursePackageError(f"content_version does not match authoring files for {course_id}")
        if registry.entries[course_id].enabled:
            runtime.snapshot(course_id)
        result[f"{course_id}_tracks"] = len(manifest.tracks)

    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--courses-root", required=True, type=Path, help="explicit courses directory containing registry.json")
    args = parser.parse_args(argv)
    try:
        result = validate_courses(args.courses_root)
    except (CoursePackageError, OSError, ValueError) as exc:
        print(f"course package validation failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"course packages valid; {result['files']} files; trusted catalog protocol validation"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
