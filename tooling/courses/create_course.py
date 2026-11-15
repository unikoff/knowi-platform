"""Create an independent course package and register it without platform edits."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import sys
from types import MappingProxyType
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
ID = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
SLUG = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
RESERVED = {"shared", "api", "assets", "admin", "user", "courses"}


@contextmanager
def registry_lock(root: Path):
    """OS-released lock for cooperating creator commands; no stale lock recovery."""
    lock = root / "tooling/.local/course-registry.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("Another creator command holds the registry; retry after it finishes") from error
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise RuntimeError("Another creator command holds the registry; retry after it finishes") from error
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def create_course(root: Path, course_id: str, source_id: str, slug: str, title: str) -> dict:
    root = root.resolve(strict=True)
    with registry_lock(root):
        return _create_course(root, course_id, source_id, slug, title)


def _create_course(root: Path, course_id: str, source_id: str, slug: str, title: str) -> dict:
    if not ID.fullmatch(course_id) or len(course_id) > 64 or course_id in RESERVED:
        raise ValueError("Choose a non-reserved lowercase course ID with underscores")
    if not SLUG.fullmatch(slug) or slug in RESERVED or not title.strip():
        raise ValueError("Choose a non-reserved public slug and a non-empty title")
    root = root.resolve(strict=True)
    courses = (root / "courses").resolve(strict=True)
    os.environ.update(PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1")
    sys.path.insert(0, str(root / "services/core-api"))
    import yaml
    from pymentor_core.adapters.course_local.packages import (
        CourseRegistry, RegistryEntry, RESERVED_COURSE_SLUGS, load_course, load_registry,
    )
    from pymentor_core.adapters.course_local.runtime import CourseRuntime, content_version

    if slug in RESERVED_COURSE_SLUGS:
        raise ValueError('This public slug is reserved by the platform')

    registry_file = courses / "registry.json"
    registry_before = registry_file.read_bytes()
    registry = load_registry(courses)
    if course_id in registry.entries or (courses / course_id).exists():
        raise ValueError("Course ID or folder already exists; existing work was not changed")
    registry.require_enabled(source_id)
    source_manifest = load_course(registry, source_id)
    if slug in {load_course(registry, cid).slug for cid in registry.entries}:
        raise ValueError("Public slug is already registered")
    source = (courses / source_id).resolve(strict=True)
    if source.parent != courses or source.is_symlink() or os.path.isjunction(source):
        raise ValueError("Source course must be a regular course folder")
    target = courses / course_id
    if target.resolve().parent != courses:
        raise ValueError("Course folder escapes courses root")
    temporary = courses / (".creating-" + course_id + "-" + uuid4().hex)
    temporary.mkdir()
    for directory in ("backend", "frontend"):
        origin = source / directory
        for path in origin.rglob("*"):
            if path.is_symlink() or os.path.isjunction(path):
                raise ValueError("Course packages cannot contain linked files")
        shutil.copytree(origin, temporary / directory,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".local", ".cache", "node_modules"))
    manifest = yaml.safe_load((source / registry.entries[source_id].manifest.split("/", 1)[-1]).read_text(encoding="utf-8"))
    manifest.update(course_id=course_id, slug=slug, title=title.strip())
    manifest_file = temporary / "course.yaml"
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Never clone recipient settings; authored landing copy remains editable content.
    settings = {"schema_version": 1, "course_id": course_id, "contacts": None,
                "telegram": {"enabled": False, "chat_id": None, "topic_id": None}}
    (temporary / "personal.settings.yaml").write_text(yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8")
    for key in ("personal_landing", "course_landing"):
        entry = temporary / source_manifest.frontend[key]
        text = entry.read_text(encoding="utf-8")
        text = re.sub(r"(courseId\s*:\s*)([\"'])" + re.escape(source_id) + r"\2",
                      lambda m: m.group(1) + json.dumps(course_id), text)
        entry.write_text(text, encoding="utf-8")
    temporary.rename(target)
    entries = dict(registry.entries)
    entries[course_id] = RegistryEntry(course_id, True, f"{course_id}/course.yaml")
    proposed = CourseRegistry(courses, MappingProxyType(entries))
    parsed = load_course(proposed, course_id)
    manifest["content_version"] = content_version(target, parsed)
    (target / "course.yaml").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Validate the trusted package before publishing its registry entry.
    check = CourseRuntime(courses)
    check.registry = proposed
    snapshot = check.snapshot(course_id)
    if registry_file.read_bytes() != registry_before:
        raise RuntimeError("Registry changed concurrently; the new folder is retained but not registered")
    document = json.loads(registry_before)
    document["courses"].append({"course_id": course_id, "enabled": True, "manifest": f"{course_id}/course.yaml"})
    registry_temp = registry_file.with_name(".registry-create-" + uuid4().hex + ".json")
    with registry_temp.open("x", encoding="utf-8") as output:
        output.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        output.flush()
        os.fsync(output.fileno())
    if registry_file.read_bytes() != registry_before:
        raise RuntimeError("Registry changed concurrently; review retained folder and registry temp file")
    os.replace(registry_temp, registry_file)
    return {"course_id": course_id, "slug": slug, "folder": str(target), "registered": True,
            "lessons": len(snapshot.catalog.LESSONS),
            "notifications_enabled": False, "platform_source_edited": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("course_id")
    parser.add_argument("--from", dest="source_id", default="backend")
    parser.add_argument("--slug")
    parser.add_argument("--title", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        result = create_course(args.root, args.course_id, args.source_id, args.slug or args.course_id, args.title)
    except (ValueError, RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
