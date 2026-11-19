"""Version-pinned course snapshots, with a native Python namespace per package.

Only Core's explicit registry can select trusted authoring Python. No request
path or module name is imported, and no shared content.* modules are switched.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from threading import RLock
from types import MappingProxyType, ModuleType

from pymentor_core.app.paths import COURSES_ROOT
from pymentor_core.modules.practice.public_specs import public_task
from pymentor_core.modules.learning.course_context import course_scope, current_course_id
from .packages import CourseManifest, CoursePackageError, load_course, load_registry


_IMPORT_LOCK = RLock()


def _inside(root: Path, relative: str, *, directory: bool = False) -> Path:
    path = root.joinpath(*relative.split("/"))
    for candidate in (path, *path.parents):
        if candidate == root:
            break
        if candidate.is_symlink() or os.path.isjunction(candidate):
            raise CoursePackageError(f"course resource links are not permitted: {relative}")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CoursePackageError(f"missing course resource: {relative}") from exc
    if root not in resolved.parents or (directory and not resolved.is_dir()) or (not directory and not resolved.is_file()):
        raise CoursePackageError(f"invalid course resource: {relative}")
    return resolved


def _content_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix.lower() in {".md", ".json", ".py"}:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CoursePackageError("course text resource must be UTF-8") from exc
        return data.replace(b"\r\n", b"\n")
    return data


def content_version(root: Path, manifest: CourseManifest) -> str:
    """Digest authoring resources and trusted catalog code as canonical JSON."""
    rows = []
    server_manifest = {"schema_version": manifest.metadata["schema_version"],
                       "layout": dict(manifest.layout),
                       "tracks": [{**dict(track), "source_dirs": list(track["source_dirs"])} for track in manifest.tracks]}
    rows.append({"path": "course.yaml:server", "sha256": hashlib.sha256(
        json.dumps(server_manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()})
    for relative in (manifest.layout["plans"], manifest.layout["practice"]):
        directory = _inside(root, relative, directory=True)
        for path in directory.rglob("*"):
            if path.is_symlink() or os.path.isjunction(path):
                raise CoursePackageError("course authoring links are not permitted")
            if "__pycache__" in path.parts or path.suffix == ".pyc" or not path.is_file():
                continue
            if path.suffix.lower() not in {".md", ".json", ".py"}:
                raise CoursePackageError("unrecognized authoring resource")
            resolved = path.resolve(strict=True)
            if root not in resolved.parents:
                raise CoursePackageError("course authoring file escapes its package")
            rows.append({"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(_content_bytes(path)).hexdigest()})
    backend = _inside(root, "backend", directory=True)
    for path in backend.rglob("*"):
        relative_backend = path.relative_to(backend)
        if relative_backend.parts[0] in {"plans", "practice", "archive"} or "__pycache__" in relative_backend.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink() or os.path.isjunction(path):
            raise CoursePackageError("course backend links are not permitted")
        if not path.is_file():
            continue
        resolved = path.resolve(strict=True)
        if root not in resolved.parents:
            raise CoursePackageError("course backend file escapes its package")
        rows.append({"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(_content_bytes(path)).hexdigest()})
    shared = root.parent / "shared" / "backend"
    if shared.exists():
        if shared.is_symlink() or os.path.isjunction(shared) or not shared.is_dir():
            raise CoursePackageError("shared backend helpers are not a trusted directory")
        for path in shared.rglob("*"):
            if "__pycache__" in path.parts:
                continue
            if path.is_symlink() or os.path.isjunction(path):
                raise CoursePackageError("shared backend links are not permitted")
            if not path.is_file() or path.suffix == ".pyc":
                continue
            resolved = path.resolve(strict=True)
            if shared.resolve(strict=True) not in resolved.parents:
                raise CoursePackageError("shared backend file escapes its package")
            rows.append({"path": path.relative_to(root.parent).as_posix(), "sha256": hashlib.sha256(_content_bytes(path)).hexdigest()})
    rows.sort(key=lambda row: row["path"])
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CourseContext:
    course_id: str
    version: str
    root: Path
    plans: Path
    backend: Path
    shared_backend: Path
    namespace: str
    authoring_name: str

    def get_authoring(self, module: str) -> ModuleType:
        # Called only with constant names in our shared loader, never URL input.
        if not module or any(not part.isidentifier() for part in module.split(".")):
            raise CoursePackageError("invalid authoring module")
        return importlib.import_module(f"{self.authoring_name}.{module}")


@dataclass(frozen=True)
class CourseSnapshot:
    context: CourseContext
    manifest: CourseManifest
    catalog: ModuleType


def _namespace(name: str, paths: list[str]) -> ModuleType:
    module = ModuleType(name)
    module.__package__ = name
    module.__path__ = paths
    sys.modules[name] = module
    return module


def _validate_catalog(catalog: ModuleType, manifest: CourseManifest) -> None:
    methods = ("default_track_id", "find_track", "find_lesson", "find_task",
               "find_lesson_by_task", "get_content_revision", "get_tracks_summary", "get_public_lessons")
    if any(not callable(getattr(catalog, name, None)) for name in methods):
        raise CoursePackageError("course catalog does not implement the catalog protocol")
    if not isinstance(getattr(catalog, "TRACKS", None), list) or not isinstance(getattr(catalog, "LESSONS", None), list):
        raise CoursePackageError("course catalog TRACKS and LESSONS must be lists")
    try:
        expected = [(track["track_id"], track["order"]) for track in manifest.tracks]
        actual = [(track["id"], track["order"]) for track in catalog.TRACKS]
        if sorted(expected) != sorted(actual):
            raise CoursePackageError("manifest track identities/order do not match package data")
        lessons = [lesson for track in catalog.TRACKS for lesson in track["lessons"]]
        if lessons != catalog.LESSONS:
            raise CoursePackageError("catalog LESSONS does not match its track lessons")
        lesson_ids, task_ids, aliases = set(), set(), set()
        task_aliases = set()
        for track in catalog.TRACKS:
            for lesson in track["lessons"]:
                lesson_id = lesson["id"]
                if not isinstance(lesson_id, str) or not lesson_id or lesson_id in lesson_ids:
                    raise CoursePackageError("duplicate or invalid lesson identity within a course")
                lesson_ids.add(lesson_id)
                if lesson["track"] != track["id"] or lesson["access"] not in {"free", "registered", "subscription"}:
                    raise CoursePackageError("invalid lesson track/access metadata")
                if not isinstance(lesson["source_file"], str) or not lesson["source_file"].strip():
                    raise CoursePackageError("lesson source_file must be non-empty text")
                alias = lesson.get("legacy_id")
                if alias and alias != lesson_id:
                    if alias in aliases:
                        raise CoursePackageError("duplicate lesson alias within a course")
                    aliases.add(alias)
                policy = lesson.get("progress_policy", {})
                if not isinstance(policy, dict) or policy.get("completion", "cards") not in {"cards", "lesson"}:
                    raise CoursePackageError("invalid lesson progress policy")
                for name in ("overview", "revision_tracked"):
                    if name in policy and type(policy[name]) is not bool:
                        raise CoursePackageError("invalid lesson progress policy boolean")
                for task in lesson["tasks"]:
                    if not task["id"] or task["id"] in task_ids:
                        raise CoursePackageError("duplicate task identity within a course")
                    task_ids.add(task["id"])
                    public_task(task)
                    for alias in task.get("legacy_ids", []):
                        if alias == task["id"] or alias in task_aliases:
                            raise CoursePackageError("duplicate task alias within a course")
                        task_aliases.add(alias)
        if aliases & lesson_ids or task_aliases & task_ids:
            raise CoursePackageError("course alias conflicts with a canonical identity")
        if catalog.default_track_id() not in {track["id"] for track in catalog.TRACKS}:
            raise CoursePackageError("invalid catalog default track")
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, CoursePackageError):
            raise
        raise CoursePackageError("invalid course catalog data") from exc


class CourseRuntime:
    """One validated registry revision; construct a new instance for explicit reload."""

    def __init__(self, courses_root: str | Path):
        self.registry = load_registry(courses_root)
        self.leader_user_ids = MappingProxyType({entry.course_id: load_course(self.registry, entry.course_id).leader_user_id
                                                 for entry in self.registry.entries.values() if entry.enabled})
        self._contexts: dict[str, CourseContext] = {}
        self._snapshots: dict[str, CourseSnapshot] = {}

    def context(self, course_id: str) -> CourseContext:
        self.registry.require_enabled(course_id)
        with _IMPORT_LOCK:
            if course_id in self._contexts:
                return self._contexts[course_id]
            manifest = load_course(self.registry, course_id)
            root = (self.registry.root / course_id).resolve(strict=True)
            plans = _inside(root, manifest.layout["plans"], directory=True)
            practice = _inside(root, manifest.layout["practice"], directory=True)
            backend = _inside(root, "backend", directory=True)
            _inside(root, "backend/catalog.py")
            shared_backend = self.registry.root / "shared" / "backend"
            if shared_backend.exists():
                _inside(self.registry.root, "shared", directory=True)
                shared_backend = _inside(self.registry.root, "shared/backend", directory=True)
            else:
                shared_backend = backend
            for entry in manifest.frontend.values():
                _inside(root, entry)
            for track in manifest.tracks:
                for source_dir in track["source_dirs"]:
                    _inside(plans, source_dir, directory=True)
            if content_version(root, manifest) != manifest.content_version:
                raise CoursePackageError("course content_version does not match authoring files")
            root_key = hashlib.sha256(str(self.registry.root).encode("utf-8")).hexdigest()[:20]
            parent = __package__ + "._instances"
            if parent not in sys.modules:
                _namespace(parent, [])
            name = f"{parent}.r_{root_key}_{course_id}_{manifest.content_version}"
            context = CourseContext(course_id, manifest.content_version, root, plans, backend, shared_backend, name, name + ".authoring")
            if name not in sys.modules:
                _namespace(name, [str(backend), str(shared_backend)])
                bound = ModuleType(name + "._context")
                bound.COURSE_ROOT = plans
                bound.get_authoring = context.get_authoring
                sys.modules[bound.__name__] = bound
                spec = importlib.util.spec_from_file_location(context.authoring_name, practice / "__init__.py", submodule_search_locations=[str(practice)])
                if spec is None or spec.loader is None:
                    raise CoursePackageError("course practice package is missing")
                package = importlib.util.module_from_spec(spec)
                sys.modules[context.authoring_name] = package
                try:
                    spec.loader.exec_module(package)
                except BaseException:
                    self._discard(name)
                    raise
            self._contexts[course_id] = context
            return context

    @staticmethod
    def _discard(namespace: str) -> None:
        for name in list(sys.modules):
            if name == namespace or name.startswith(namespace + "."):
                del sys.modules[name]

    def snapshot(self, course_id: str) -> CourseSnapshot:
        self.registry.require_enabled(course_id)
        with _IMPORT_LOCK:
            if course_id in self._snapshots:
                return self._snapshots[course_id]
            context = self.context(course_id)
            manifest = load_course(self.registry, course_id)
            if manifest.content_version != context.version:
                raise CoursePackageError("manifest version changed during snapshot selection")
            leader = self.leader_user_ids[course_id]
            if manifest.leader_user_id != leader:
                manifest = replace(manifest, leader_user_id=leader,
                                   metadata=MappingProxyType({**manifest.metadata, 'leader_user_id': leader}))
            try:
                catalog = importlib.import_module(context.namespace + ".catalog")
                _validate_catalog(catalog, manifest)
                if content_version(context.root, manifest) != context.version:
                    raise CoursePackageError("course files changed while snapshot was loading")
            except BaseException:
                self._contexts.pop(course_id, None)
                self._discard(context.namespace)
                raise
            snapshot = CourseSnapshot(context, manifest, catalog)
            self._snapshots[course_id] = snapshot
            return snapshot

    def products(self) -> list[dict[str, str]]:
        result = []
        for entry in self.registry.entries.values():
            if entry.enabled:
                manifest = load_course(self.registry, entry.course_id)
                result.append({"course_id": manifest.course_id, "slug": manifest.slug, "title": manifest.title, "content_version": manifest.content_version})
        return result


@lru_cache(maxsize=1)
def default_runtime() -> CourseRuntime:
    return CourseRuntime(COURSES_ROOT)
