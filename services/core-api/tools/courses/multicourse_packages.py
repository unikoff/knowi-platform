"""Copy and verify the current canonical course packages."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Iterable


AUTHORING_SUFFIXES = {".md", ".json", ".py"}


class PackageBuildError(ValueError):
    """Raised when source or destination packages are invalid or differ."""


def _inside(path: Path, root: Path, *, strict: bool = False) -> Path:
    resolved = path.resolve(strict=strict)
    base = root.resolve(strict=True)
    if resolved != base and base not in resolved.parents:
        raise PackageBuildError(f"path escapes its allowed root: {path}")
    return resolved


def compute_content_version(package_root: str | Path) -> str:
    """Use the same trusted server-input digest as the runtime loader."""
    from pymentor_core.adapters.course_local.packages import load_course_manifest
    from pymentor_core.adapters.course_local.runtime import content_version

    root = Path(package_root).resolve(strict=True)
    try:
        return content_version(root, load_course_manifest(root))
    except (OSError, ValueError) as exc:
        raise PackageBuildError(str(exc)) from exc


def _package_files(root: Path) -> dict[str, Path]:
    """Validate the complete package tree before reading or copying any files."""
    if root.is_symlink():
        raise PackageBuildError("courses root must not be a symlink")
    from check_courses import validate_courses

    try:
        validate_courses(root)
    except (OSError, ValueError) as exc:
        raise PackageBuildError(f"invalid course packages: {exc}") from exc
    return {path.relative_to(root).as_posix(): path for path in root.rglob("*") if path.is_file()}


def _roots(repo_root: str | Path, courses_root: str | Path | None) -> tuple[Path, Path]:
    repo = Path(repo_root).resolve(strict=True)
    source = repo / "courses"
    if source.is_symlink():
        raise PackageBuildError("canonical courses root must not be a symlink")
    source = source.resolve(strict=True)
    requested = Path(courses_root) if courses_root is not None else source
    if requested.is_symlink():
        raise PackageBuildError("courses destination must not be a symlink")
    destination = requested.resolve(strict=False)
    if destination != source and (destination in source.parents or source in destination.parents):
        raise PackageBuildError("source and destination must not contain one another")
    return source, destination


def _counts(root: Path) -> dict[str, int]:
    # Retain the original return keys for callers; counts describe backend only.
    plans = root / "backend/backend/plans"
    practice = root / "backend/backend/practice"
    return {
        "markdown_per_course": sum(1 for p in plans.rglob("*.md") if p.is_file()),
        "track_json_per_course": sum(1 for p in plans.rglob("track.json") if p.is_file()),
        "python_per_course": sum(1 for p in practice.rglob("*.py") if p.is_file()),
    }


def check_packages(repo_root: str | Path, courses_root: str | Path | None = None) -> dict[str, int]:
    """Validate and compare each destination file with its canonical counterpart."""
    source, destination = _roots(repo_root, courses_root)
    source_files = _package_files(source)
    destination_files = _package_files(destination)
    if source_files.keys() != destination_files.keys():
        raise PackageBuildError("source/package file paths differ")
    for relative, source_file in source_files.items():
        if source_file.read_bytes() != destination_files[relative].read_bytes():
            raise PackageBuildError(f"source/package bytes differ: {relative}")
    return _counts(source)


def create_packages(repo_root: str | Path, courses_root: str | Path | None = None) -> dict[str, int]:
    """Copy current canonical packages without replacing any existing files."""
    source, destination = _roots(repo_root, courses_root)
    source_files = _package_files(source)
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        try:
            return check_packages(repo_root, destination)
        except (OSError, ValueError) as exc:
            raise PackageBuildError("refusing to overwrite an existing non-empty courses destination") from exc
    destination.mkdir(parents=True, exist_ok=True)
    for relative, source_file in source_files.items():
        target = destination / relative
        _inside(target, destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation avoids overwriting if the destination changes concurrently.
        with target.open("xb") as output:
            with source_file.open("rb") as input_file:
                shutil.copyfileobj(input_file, output)
    return check_packages(repo_root, destination)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--create", action="store_true", help="copy current canonical packages to an empty destination")
    action.add_argument("--check", action="store_true", help="validate and compare with current canonical packages")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--courses-root", type=Path, help="destination courses directory")
    args = parser.parse_args(argv)
    try:
        result = create_packages(args.repo_root, args.courses_root) if args.create else check_packages(args.repo_root, args.courses_root)
    except (OSError, ValueError) as exc:
        print(f"course package {'create' if args.create else 'check'} failed: {exc}", file=sys.stderr)
        return 1
    print(f"course packages {'created' if args.create else 'checked'}: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
