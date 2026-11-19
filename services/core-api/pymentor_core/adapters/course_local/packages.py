"""Safe, read-only access to the Core-owned local course package registry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
import os
import re
from types import MappingProxyType
from typing import Any, Mapping

import yaml
from yaml.tokens import AliasToken

from pymentor_core.modules.learning.ports import CourseNotFoundError, CoursePackageError


MAX_DOCUMENT_BYTES = 256 * 1024
MAX_DOCUMENT_DEPTH = 32
_COURSE_ID = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
_SLUG = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
RESERVED_COURSE_SLUGS = frozenset({
    "shared", "courses", "api", "assets", "devtools", "src", "node_modules",
    "public", "config", "tools", "authoring", "preview-loading", "platform",
})
_WINDOWS_RESERVED = re.compile(
    r"^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$",
    re.IGNORECASE,
)


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader variant which rejects repeated mapping keys."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise CoursePackageError("mapping keys must be scalar and hashable") from exc
            if duplicate:
                raise CoursePackageError(f"duplicate YAML key: {key!r}")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


@dataclass(frozen=True)
class RegistryEntry:
    course_id: str
    enabled: bool
    manifest: str


@dataclass(frozen=True)
class CourseRegistry:
    root: Path
    entries: Mapping[str, RegistryEntry]

    def require_enabled(self, course_id: str) -> RegistryEntry:
        entry = self.entries.get(course_id)
        if entry is None or not entry.enabled:
            raise CourseNotFoundError(f"course is unknown or disabled: {course_id!r}")
        return entry


@dataclass(frozen=True)
class CourseManifest:
    course_id: str
    slug: str
    title: str
    content_version: str
    layout: Mapping[str, str]
    frontend: Mapping[str, str]
    tracks: tuple[Mapping[str, Any], ...]
    metadata: Mapping[str, Any]
    leader_user_id: int | None = None


@dataclass(frozen=True)
class LegacyBackendManifest:
    """Explicit v1 adapter; ``legacy_course_id`` remains the old track ID."""

    product_course_id: str
    legacy_course_id: str
    metadata: Mapping[str, Any]


def _fail(message: str) -> None:
    raise CoursePackageError(message)


def _check_keys(value: Mapping[str, Any], expected: set[str], where: str) -> None:
    if any(not isinstance(key, str) for key in value):
        _fail(f"{where} keys must be strings")
    extra = set(value) - expected
    missing = expected - set(value)
    if extra or missing:
        _fail(f"{where} keys invalid (missing={sorted(missing)!r}, unexpected={sorted(extra)!r})")


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _depth(value: Any, level: int = 0) -> None:
    if level > MAX_DOCUMENT_DEPTH:
        _fail(f"document nesting exceeds {MAX_DOCUMENT_DEPTH}")
    if isinstance(value, dict):
        for key, child in value.items():
            _depth(key, level + 1)
            _depth(child, level + 1)
    elif isinstance(value, list):
        for child in value:
            _depth(child, level + 1)


def _parse_document(path: Path, *, max_bytes: int = MAX_DOCUMENT_BYTES) -> Any:
    try:
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
    except OSError as exc:
        raise CoursePackageError(f"cannot read document: {path}") from exc
    if len(raw) > max_bytes:
        _fail(f"document exceeds {max_bytes} bytes: {path}")
    try:
        text = raw.decode("utf-8")
        if any(isinstance(token, AliasToken) for token in yaml.scan(text, Loader=_UniqueKeySafeLoader)):
            _fail("YAML aliases are not permitted")
        document = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except CoursePackageError:
        raise
    except (UnicodeDecodeError, yaml.YAMLError, RecursionError) as exc:
        raise CoursePackageError(f"invalid YAML/JSON document: {path}") from exc
    _depth(document)
    if not isinstance(document, dict):
        _fail(f"document root must be an object: {path}")
    return document


def _relative_path(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        _fail(f"{where} must be a non-empty POSIX relative path")
    windows = PureWindowsPath(value)
    if windows.is_absolute() or windows.drive or value.startswith("/"):
        _fail(f"{where} must not be absolute")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        _fail(f"{where} contains an unsafe path segment")
    for part in parts:
        if any(ord(char) < 32 or ord(char) == 127 for char in part):
            _fail(f"{where} contains a control character")
        if part.endswith((".", " ")) or _WINDOWS_RESERVED.fullmatch(part):
            _fail(f"{where} contains a Windows-reserved path alias")
    return value


def _resolve_inside(root: Path, relative: str, where: str, *, must_exist: bool) -> Path:
    candidate = root.joinpath(*relative.split("/"))
    for component in (candidate, *candidate.parents):
        if component == root:
            break
        if component.is_symlink() or os.path.isjunction(component):
            _fail(f"{where} contains a filesystem link: {relative}")
    try:
        resolved = candidate.resolve(strict=must_exist)
        resolved_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CoursePackageError(f"{where} does not resolve safely: {relative}") from exc
    if resolved != resolved_root and resolved_root not in resolved.parents:
        _fail(f"{where} escapes its allowed root: {relative}")
    if must_exist and not resolved.is_file():
        _fail(f"{where} is not a regular file: {relative}")
    return resolved


def _validate_course_id(value: Any, where: str = "course_id") -> str:
    if not isinstance(value, str) or len(value) > 64 or not _COURSE_ID.fullmatch(value):
        _fail(f"{where} must be an ASCII course slug of at most 64 characters")
    if value == "shared":
        _fail(f"{where} uses the reserved shared package ID")
    return value


def load_registry(courses_root: str | Path) -> CourseRegistry:
    """Load ``registry.json`` from an explicit courses root; never uses cwd/defaults."""
    if Path(courses_root).is_symlink() or os.path.isjunction(courses_root):
        _fail("explicit courses root must not be a filesystem link")
    try:
        root = Path(courses_root).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CoursePackageError("explicit courses root is missing or invalid") from exc
    if not root.is_dir():
        _fail("explicit courses root is not a directory")
    registry_path = _resolve_inside(root, "registry.json", "registry", must_exist=True)
    data = _parse_document(registry_path)
    _check_keys(data, {"schema_version", "courses"}, "registry")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        _fail("unsupported registry schema_version")
    if not isinstance(data["courses"], list):
        _fail("registry courses must be an array")
    entries: dict[str, RegistryEntry] = {}
    manifests: set[str] = set()
    slugs: set[str] = set()
    for index, item in enumerate(data["courses"]):
        where = f"registry.courses[{index}]"
        if not isinstance(item, dict):
            _fail(f"{where} must be an object")
        _check_keys(item, {"course_id", "enabled", "manifest"}, where)
        course_id = _validate_course_id(item["course_id"], f"{where}.course_id")
        if course_id in entries:
            _fail(f"duplicate registry course_id: {course_id}")
        if type(item["enabled"]) is not bool:
            _fail(f"{where}.enabled must be boolean")
        manifest = _relative_path(item["manifest"], f"{where}.manifest")
        manifest_parts = manifest.split("/")
        if manifest_parts != [course_id, "course.yaml"]:
            _fail(f"{where}.manifest must be exactly {course_id}/course.yaml")
        if manifest in manifests:
            _fail(f"duplicate registry manifest path: {manifest}")
        manifests.add(manifest)
        manifest_path = _resolve_inside(root, manifest, f"{where}.manifest", must_exist=True)
        parsed = _validate_manifest(_parse_document(manifest_path), course_id, manifest_path.parent)
        if parsed.slug in slugs:
            _fail(f"duplicate registry course slug: {parsed.slug}")
        slugs.add(parsed.slug)
        entries[course_id] = RegistryEntry(course_id, item["enabled"], manifest)
    return CourseRegistry(root=root, entries=MappingProxyType(entries))


def _validate_manifest(
    data: dict[str, Any],
    expected_course_id: str | None = None,
    package_root: Path | None = None,
) -> CourseManifest:
    _check_keys(
        {**data, 'leader_user_id': data.get('leader_user_id')},
        {"schema_version", "course_id", "slug", "title", "content_version", "layout", "frontend", "tracks", "leader_user_id"},
        "manifest",
    )
    leader_user_id = data.get('leader_user_id')
    if leader_user_id is not None and (type(leader_user_id) is not int or leader_user_id < 1):
        _fail('manifest.leader_user_id must be a positive integer or null')
    if type(data["schema_version"]) is not int or data["schema_version"] != 2:
        _fail("unsupported manifest schema_version; expected 2")
    course_id = _validate_course_id(data["course_id"], "manifest.course_id")
    if expected_course_id is not None and course_id != expected_course_id:
        _fail("manifest course_id does not match registry course_id")
    slug = data["slug"]
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        _fail("manifest.slug must be a lowercase ASCII slug")
    if slug in RESERVED_COURSE_SLUGS:
        _fail("manifest.slug uses a reserved route slug")
    if not isinstance(data["title"], str) or not data["title"].strip():
        _fail("manifest.title must be a non-empty string")
    version = data["content_version"]
    if not isinstance(version, str) or not _HEX_DIGEST.fullmatch(version):
        _fail("manifest.content_version must be 64 lowercase hexadecimal characters")
    layout = data["layout"]
    frontend = data["frontend"]
    if not isinstance(layout, dict) or not isinstance(frontend, dict):
        _fail("manifest.layout and manifest.frontend must be objects")
    _check_keys(layout, {"plans", "practice", "frontend"}, "manifest.layout")
    _check_keys(frontend, {"course", "personal_landing", "course_landing"}, "manifest.frontend")
    for key, value in layout.items():
        _relative_path(value, f"manifest.layout.{key}")
    for key, value in frontend.items():
        _relative_path(value, f"manifest.frontend.{key}")
        if not value.startswith("frontend/") or Path(value).suffix not in {".ts", ".tsx", ".js", ".jsx"}:
            _fail(f"manifest.frontend.{key} must be a module under frontend/")
    if layout["frontend"] != "frontend":
        _fail("manifest.layout.frontend must be frontend")
    if package_root is not None:
        for group, values in (("layout", layout), ("frontend", frontend)):
            for key, value in values.items():
                _resolve_inside(package_root, value, f"manifest.{group}.{key}", must_exist=False)
    tracks = data["tracks"]
    if not isinstance(tracks, list) or not tracks:
        _fail("manifest.tracks must be a non-empty array")
    track_ids: set[str] = set()
    orders: set[int] = set()
    frozen_tracks = []
    for index, track in enumerate(tracks):
        where = f"manifest.tracks[{index}]"
        if not isinstance(track, dict):
            _fail(f"{where} must be an object")
        _check_keys(track, {"track_id", "title", "order", "source_dirs"}, where)
        if not isinstance(track["track_id"], str) or not track["track_id"].strip():
            _fail(f"{where}.track_id must be non-empty")
        if track["track_id"] in track_ids:
            _fail(f"duplicate track_id: {track['track_id']}")
        track_ids.add(track["track_id"])
        if not isinstance(track["title"], str) or not track["title"].strip():
            _fail(f"{where}.title must be non-empty")
        order = track["order"]
        if type(order) is not int or order < 1 or order in orders:
            _fail(f"{where}.order must be a unique positive integer")
        orders.add(order)
        source_dirs = track["source_dirs"]
        if not isinstance(source_dirs, list) or not source_dirs:
            _fail(f"{where}.source_dirs must be a non-empty array")
        normalized_dirs = [_relative_path(path, f"{where}.source_dirs") for path in source_dirs]
        if len(set(normalized_dirs)) != len(normalized_dirs):
            _fail(f"{where}.source_dirs contains duplicates")
        if package_root is not None:
            for source_dir in normalized_dirs:
                combined = f"{layout['plans']}/{source_dir}"
                _resolve_inside(package_root, combined, f"{where}.source_dirs", must_exist=False)
        frozen_tracks.append(_freeze(track))
    return CourseManifest(
        course_id=course_id,
        slug=slug,
        title=data["title"],
        content_version=version,
        layout=_freeze(layout),
        frontend=_freeze(frontend),
        tracks=tuple(frozen_tracks),
        metadata=_freeze(data),
        leader_user_id=leader_user_id,
    )


def load_course_manifest(package_root: str | Path, manifest: str | Path = "course.yaml", *, expected_course_id: str | None = None) -> CourseManifest:
    """Parse one v2 manifest under an explicit package root without importing code."""
    try:
        root = Path(package_root).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CoursePackageError("explicit package root is missing or invalid") from exc
    if not root.is_dir():
        _fail("explicit package root is not a directory")
    relative = _relative_path(manifest.as_posix() if isinstance(manifest, Path) else manifest, "manifest path")
    path = _resolve_inside(root, relative, "manifest", must_exist=True)
    expected = _validate_course_id(expected_course_id) if expected_course_id is not None else None
    return _validate_manifest(_parse_document(path), expected, path.parent)


def load_course(registry: CourseRegistry, course_id: str) -> CourseManifest:
    """Resolve an enabled registry entry and enforce package/manifest identity."""
    course_id = _validate_course_id(course_id)
    entry = registry.require_enabled(course_id)
    return load_course_manifest(registry.root, entry.manifest, expected_course_id=course_id)


def load_legacy_backend_manifest(legacy_root: str | Path, manifest: str | Path = "current-course.json") -> LegacyBackendManifest:
    """Read schema-v1 metadata only for the original backend compatibility path."""
    try:
        root = Path(legacy_root).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CoursePackageError("explicit legacy backend root is missing or invalid") from exc
    if not root.is_dir():
        _fail("explicit legacy backend root is not a directory")
    relative = _relative_path(manifest.as_posix() if isinstance(manifest, Path) else manifest, "legacy manifest path")
    path = _resolve_inside(root, relative, "legacy manifest", must_exist=True)
    data = _parse_document(path)
    if type(data.get("schema_version")) is not int or data.get("schema_version") != 1:
        _fail("legacy adapter accepts schema_version 1 only")
    _check_keys(
        data,
        {"schema_version", "course_id", "storage_key", "slug", "content_version", "data_digest", "runtime_image_digest", "lessons", "visibility", "legacy_aliases", "digest_scheme"},
        "legacy v1 manifest",
    )
    if not isinstance(data["course_id"], str) or not data["course_id"].strip():
        _fail("legacy v1 course_id must be preserved as the original track ID")
    if not isinstance(data["storage_key"], str) or not _SLUG.fullmatch(data["storage_key"]):
        _fail("legacy v1 storage_key must be a safe slug")
    if not isinstance(data["slug"], str) or not _SLUG.fullmatch(data["slug"]):
        _fail("legacy v1 slug must be a safe slug")
    if not isinstance(data["content_version"], str) or not data["content_version"].strip():
        _fail("legacy v1 content_version must be non-empty")
    if not isinstance(data["data_digest"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", data["data_digest"]):
        _fail("legacy v1 data_digest must be a SHA-256 digest")
    runtime_digest = data["runtime_image_digest"]
    if runtime_digest is not None and (not isinstance(runtime_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", runtime_digest)):
        _fail("legacy v1 runtime_image_digest must be null or a SHA-256 digest")
    if not isinstance(data["lessons"], list):
        _fail("legacy v1 lessons must be an array")
    lesson_ids: set[str] = set()
    for index, lesson in enumerate(data["lessons"]):
        where = f"legacy v1 lessons[{index}]"
        if not isinstance(lesson, dict):
            _fail(f"{where} must be an object")
        _check_keys(lesson, {"lesson_id", "source_file", "task_refs"}, where)
        lesson_id = lesson["lesson_id"]
        if not isinstance(lesson_id, str) or not lesson_id.strip() or lesson_id in lesson_ids:
            _fail(f"{where}.lesson_id must be non-empty and unique")
        lesson_ids.add(lesson_id)
        _relative_path(lesson["source_file"], f"{where}.source_file")
        if not isinstance(lesson["task_refs"], list):
            _fail(f"{where}.task_refs must be an array")
        for task_index, task_ref in enumerate(lesson["task_refs"]):
            task_where = f"{where}.task_refs[{task_index}]"
            if not isinstance(task_ref, dict):
                _fail(f"{task_where} must be an object")
            _check_keys(task_ref, {"task_id", "revision"}, task_where)
            if any(not isinstance(task_ref[key], str) or not task_ref[key].strip() for key in ("task_id", "revision")):
                _fail(f"{task_where} task_id and revision must be non-empty strings")
    if not isinstance(data["visibility"], str) or data["visibility"] not in {"public", "private", "draft"}:
        _fail("legacy v1 visibility is invalid")
    aliases = data["legacy_aliases"]
    if not isinstance(aliases, list) or any(not isinstance(alias, str) or not alias for alias in aliases) or len(set(aliases)) != len(aliases):
        _fail("legacy v1 legacy_aliases must contain unique non-empty strings")
    if not isinstance(data["digest_scheme"], str) or data["digest_scheme"] not in {"metadata-preview-v1", "package-tree-v1"}:
        _fail("legacy v1 digest_scheme is invalid")
    if data["digest_scheme"] == "metadata-preview-v1" and data["visibility"] != "draft":
        _fail("metadata-preview-v1 legacy manifests must be draft")
    # Explicitly retain the old resource identity and refs; it is not the product ID.
    return LegacyBackendManifest(
        product_course_id="backend",
        legacy_course_id=data["course_id"],
        metadata=_freeze(data),
    )
