#!/usr/bin/env python3
"""Build a deterministic, read-only-source inventory for multicourse MC1.2.

The only writes are the two explicit MC1.2 JSON artifacts. Current catalog
identities are obtained from the trusted course_local getter under import and
network guards; no app factory, database module, or database connection is used.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.abc
import json
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[4]
OUTPUT_DIR = ROOT / "docs" / "course-isolation" / "inventory"
INVENTORY_PATH = OUTPUT_DIR / "inventory.json"
ID_MAP_PATH = OUTPUT_DIR / "id-map.json"
MATERIALS = ROOT / "courses" / "backend" / "backend" / "plans"
PRACTICE = ROOT / "courses" / "backend" / "backend" / "practice"
LESSONS = ROOT / "courses" / "backend" / "frontend" / "course"
LANDING = ROOT / "courses" / "backend" / "frontend" / "landing-personal"
REGISTRY = LESSONS


class DatabaseImportBlocker(importlib.abc.MetaPathFinder):
    """Fail closed if loading the catalog attempts to load database drivers."""

    BLOCKED = (
        "sqlite3",
        "sqlalchemy",
        "psycopg",
        "asyncpg",
        "pymysql",
        "mysql",
        "pymentor_core.adapters.sql",
        "pymentor_core.database",
        "pymentor_core.app.factory",
    )

    def find_spec(self, fullname: str, path: Any = None, target: Any = None):
        if any(fullname == item or fullname.startswith(item + ".") for item in self.BLOCKED):
            raise RuntimeError(f"Database-related import blocked: {fullname}")
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _manifest_path(value: str) -> Path:
    """Resolve a manifest's slash-separated relative path without shell rules."""
    return Path(*value.split("/"))


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _load_catalog():
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["PYMENTOR_PROFILE"] = "mc460_inventory_read_only"
    sys.path[:0] = [str(ROOT / "services" / "core-api"), str(ROOT / "services/core-api")]
    sys.meta_path.insert(0, DatabaseImportBlocker())
    socket.socket = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("Network access blocked during inventory generation")
    )
    from pymentor_core.adapters.course_local import catalog

    return catalog


def _registered_pages() -> dict[str, str]:
    """Read only explicit markdown-to-page pairs from chapter index manifests."""
    pages: dict[str, str] = {}
    entry_re = re.compile(r'"([^"]+\.md)"\s*:\s*"([^"]+)"')
    chapter_re = re.compile(r'defineLessonPages\(\s*"(chapter_[^"]+)"')
    for index_file in sorted(LESSONS.glob("chapter_*/index.ts")):
        source = index_file.read_text(encoding="utf-8")
        chapter_match = chapter_re.search(source)
        if chapter_match is None:
            continue
        chapter = chapter_match.group(1)
        for source_path, page_path in entry_re.findall(source):
            key = f"{chapter}/{source_path}"
            if key in pages:
                raise ValueError(f"Duplicate frontend lesson mapping: {key}")
            target_rel = f"{chapter}/{page_path}.tsx"
            target = LESSONS / _manifest_path(target_rel)
            export_name = page_path.rsplit("/", 1)[-1]
            if not target.is_file():
                raise ValueError(f"Manifest page does not exist: {target_rel}")
            if not re.search(rf"export\s+function\s+{re.escape(export_name)}\b", target.read_text(encoding="utf-8")):
                raise ValueError(f"Manifest export is missing: {target_rel}#{export_name}")
            pages[key] = target_rel
    return pages


def _tsx_inventory(page_map: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reverse_map: dict[str, list[str]] = {}
    for markdown, tsx in page_map.items():
        reverse_map.setdefault(tsx, []).append(markdown)

    pages: list[dict[str, Any]] = []
    asset_exts = {".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm"}
    assets: list[dict[str, Any]] = []
    import_re = re.compile(r"(?:from\s+|import\s*)[\"']([^\"']+)[\"']")
    for path in sorted(LESSONS.rglob("*")):
        if not path.is_file():
            continue
        page_key = path.relative_to(LESSONS).as_posix()
        if path.suffix == ".tsx" and (path.name.startswith("Lesson") or page_key in reverse_map):
            source = path.read_text(encoding="utf-8")
            exports = sorted(set(re.findall(r"export\s+function\s+(\w+)", source)))
            imports: list[str] = []
            for specifier in import_re.findall(source):
                if not specifier.startswith("."):
                    continue
                resolved = (path.parent / specifier).resolve()
                candidates = [resolved, resolved.with_suffix(".css")]
                for candidate in candidates:
                    if candidate.is_file() and candidate.suffix.lower() in asset_exts:
                        imports.append(_relative(candidate))
                        break
            pages.append({
                "path": _relative(path),
                "sha256": _sha256(path),
                "page_type": "lesson_page" if path.name.startswith("Lesson") else "registered_overview_or_nonlesson_page",
                "exports": exports,
                "explicit_markdown_sources": sorted(reverse_map.get(path.relative_to(LESSONS).as_posix(), [])),
                "imports_assets": sorted(set(imports)),
                "mapping_status": "explicit_manifest" if path.relative_to(LESSONS).as_posix() in reverse_map else "unlinked",
            })
        elif path.suffix.lower() in asset_exts:
            assets.append({"path": _relative(path), "sha256": _sha256(path)})
    return pages, assets


def _landing_inventory() -> list[dict[str, Any]]:
    paths = [path for path in LANDING.rglob("*") if path.is_file()]
    route = ROOT / "apps" / "web" / "src" / "app" / "BackendLandingRoute.tsx"
    app_entry = ROOT / "apps" / "web" / "src" / "app" / "App.tsx"
    paths.extend(path for path in (route, app_entry) if path.is_file())
    return [
        {"path": _relative(path), "sha256": _sha256(path)}
        for path in sorted(set(paths))
    ]


def _compare_fixture(catalog: Any) -> dict[str, Any]:
    fixture_path = ROOT / "services" / "core-api" / "contracts" / "examples" / "current-course.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    current_track = next((track for track in catalog.TRACKS if track["id"] == fixture.get("course_id")), None)
    fixture_lessons = {item.get("source_file"): item for item in fixture.get("lessons", [])}
    live_lessons = {item["source_file"]: item for item in (current_track or {}).get("lessons", [])}
    common_sources = sorted(fixture_lessons.keys() & live_lessons.keys())
    lesson_id_differences = [
        {"source_file": source, "fixture": fixture_lessons[source].get("lesson_id"), "live": live_lessons[source].get("id")}
        for source in common_sources
        if fixture_lessons[source].get("lesson_id") != live_lessons[source].get("id")
    ]
    task_revision_differences: list[dict[str, Any]] = []
    fixture_task_refs = 0
    for source in common_sources:
        old_tasks = {
            item.get("task_id"): item.get("revision")
            for item in fixture_lessons[source].get("task_refs", [])
        }
        live_tasks = {
            item.get("id"): item.get("revision")
            for item in live_lessons[source].get("tasks", [])
        }
        fixture_task_refs += len(old_tasks)
        for task_id in sorted(old_tasks.keys() | live_tasks.keys()):
            if old_tasks.get(task_id) != live_tasks.get(task_id):
                task_revision_differences.append({
                    "source_file": source,
                    "task_id": task_id,
                    "fixture_revision": old_tasks.get(task_id),
                    "live_revision": live_tasks.get(task_id),
                })
    return {
        "path": _relative(fixture_path),
        "classification": "example_fixture_compared_with_live_course_local_getter",
        "fixture_course_id": fixture.get("course_id"),
        "matched_live_track": current_track is not None,
        "fixture_lesson_count": len(fixture_lessons),
        "live_track_lesson_count": len(live_lessons),
        "fixture_task_ref_count": fixture_task_refs,
        "live_task_count": sum(len(item.get("tasks", [])) for item in live_lessons.values()),
        "missing_from_live_sources": sorted(fixture_lessons.keys() - live_lessons.keys()),
        "new_in_live_sources": sorted(live_lessons.keys() - fixture_lessons.keys()),
        "lesson_id_differences": lesson_id_differences,
        "task_revision_differences": task_revision_differences,
        "fixture_content_version": fixture.get("content_version"),
        "fixture_data_digest": fixture.get("data_digest"),
        "digest_comparison": "not directly comparable: fixture data_digest uses an undocumented snapshot algorithm; the live catalog exposes content_revision",
    }


def build_artifacts() -> tuple[dict[str, Any], dict[str, Any]]:
    catalog = _load_catalog()
    explicit_pages = _registered_pages()
    tsx_pages, tsx_assets = _tsx_inventory(explicit_pages)
    lesson_by_source: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    track_rows: list[dict[str, Any]] = []
    id_rows: list[dict[str, Any]] = []

    for track in catalog.TRACKS:
        lessons = track["lessons"]
        source_dirs = sorted({lesson["source_file"].split("/", 1)[0] for lesson in lessons})
        source_dir = source_dirs[0] if len(source_dirs) == 1 else None
        track_row = {
            "track_id": track["id"],
            "title": track["title"],
            "tagline": track.get("tagline", ""),
            "order": track.get("order"),
            "source_dirs": source_dirs,
            "track_json": (
                {"path": f"courses/backend/backend/plans/{source_dir}/track.json",
                 "sha256": _sha256(MATERIALS / source_dir / "track.json")}
                if source_dir and (MATERIALS / source_dir / "track.json").is_file()
                else None
            ),
            "lessons": [],
        }
        for lesson in lessons:
            source = lesson["source_file"]
            lesson_by_source[source] = (track, lesson)
            tasks = [
                {"task_id": task["id"], "revision": task["revision"]}
                for task in lesson.get("tasks", [])
            ]
            lesson_row = {
                "source_file": source,
                "source_sha256": _sha256(MATERIALS / source),
                "lesson_id": lesson["id"],
                "legacy_id": lesson.get("legacy_id"),
                "title": lesson.get("title", ""),
                "tasks": tasks,
                "frontend_page": explicit_pages.get(source),
                "frontend_mapping": "explicit_manifest" if source in explicit_pages else "no_explicit_manifest_entry",
            }
            track_row["lessons"].append(lesson_row)
            id_rows.append({
                "source_file": f"courses/backend/backend/plans/{source}",
                "track_id": track["id"],
                "lesson_id": lesson["id"],
                "legacy_id": lesson.get("legacy_id"),
                "task_ids": [item["task_id"] for item in tasks],
                "task_revisions": {item["task_id"]: item["revision"] for item in tasks},
                "planned_paths": {
                    course: f"courses/{course}/backend/plans/{source}"
                    for course in ("backend", "backend_copy")
                },
                "planned_identity": {
                    course: {"course_id": course, "track_id": track["id"], "lesson_id": lesson["id"],
                             "task_ids": [item["task_id"] for item in tasks]}
                    for course in ("backend", "backend_copy")
                },
                "progress_migration": "none; user progress is not copied, and identity is course-scoped",
            })
        track_rows.append(track_row)

    markdown_files: list[dict[str, Any]] = []
    for path in sorted(MATERIALS.rglob("*.md")):
        source = path.relative_to(MATERIALS).as_posix()
        record: dict[str, Any] = {
            "path": _relative(path),
            "sha256": _sha256(path),
            "catalog_status": "getter_lesson" if source in lesson_by_source else "not_emitted_as_getter_lesson",
        }
        if source in lesson_by_source:
            track, lesson = lesson_by_source[source]
            record.update({"track_id": track["id"], "lesson_id": lesson["id"]})
        markdown_files.append(record)

    python_sources = [
        {"path": _relative(path), "sha256": _sha256(path), "kind": path.name.removesuffix(".py")}
        for pattern in ("manual.py", "code_tasks.py")
        for path in sorted(PRACTICE.rglob(pattern))
    ]
    ignored_or_missing_registry = [page for page in tsx_pages if not page["explicit_markdown_sources"]]
    source_artifacts: list[dict[str, Any]] = []
    for row in markdown_files:
        source = row["path"].removeprefix("courses/backend/backend/plans/")
        source_artifacts.append({
            "source_file": row["path"],
            "artifact_kind": "markdown",
            "mapping_status": row["catalog_status"],
            "lesson_id": row.get("lesson_id"),
            "planned_paths": {course: f"courses/{course}/backend/plans/{source}" for course in ("backend", "backend_copy")},
        })
    for row in python_sources:
        source = row["path"].removeprefix("courses/backend/backend/practice/")
        source_artifacts.append({
            "source_file": row["path"],
            "artifact_kind": row["kind"],
            "mapping_status": "path_preserving_source_artifact_no_semantic_lesson_link",
            "lesson_id": None,
            "planned_paths": {course: f"courses/{course}/backend/practice/{source}" for course in ("backend", "backend_copy")},
        })

    inventory = {
        "schema_version": 1,
        "inventory_kind": "isolated_source_snapshot_with_live_course_local_getter",
        "generated_by": "tools/multicourse_inventory.py",
        "source_roots": ["courses/backend/backend/plans", "courses/backend/backend/practice", "courses/backend/frontend/course", "courses/backend/frontend/landing-personal"],
        "identity_authority": {
            "getter": "services/core-api/pymentor_core/adapters/course_local/catalog.py",
            "identity_and_revision": "courses/backend/backend/identity.py",
            "db_access": "blocked by import guard; no app factory imported",
            "content_revision": catalog.get_content_revision(),
            "lesson_count": len(catalog.LESSONS),
            "task_count": sum(len(lesson.get("tasks", [])) for lesson in catalog.LESSONS),
        },
        "counts": {
            "tracks": len(track_rows),
            "authoring_markdown": len(markdown_files),
            "manual_and_code_tasks": len(python_sources),
            "registered_markdown_to_tsx_pairs": len(explicit_pages),
            "tsx_lesson_pages": len(tsx_pages),
            "unlinked_tsx_lesson_pages": len(ignored_or_missing_registry),
            "tsx_lesson_assets": len(tsx_assets),
        },
        "tracks": track_rows,
        "authoring_markdown": markdown_files,
        "manual_and_code_tasks": python_sources,
        "frontend": {
            "lesson_page_manifest": "courses/backend/frontend/course/chapter_*/index.ts",
            "mapping_rule": "Only exact entries in defineLessonPages manifests create Markdown-to-TSX links; no number-based inference.",
            "tsx_lessons": tsx_pages,
            "lesson_assets": tsx_assets,
        },
        "landing_inputs": _landing_inventory(),
        "fixture_comparison": _compare_fixture(catalog),
    }
    id_map = {
        "schema_version": 1,
        "mapping_kind": "planned_path_map_only_no_content_migration",
        "source_of_ids": "isolated current course_local getter, with current task revisions",
        "course_namespaces": {
            "backend": "courses/backend",
            "backend_copy": "courses/backend_copy",
        },
        "identity_rule": "Track IDs are preserved. Lesson/task IDs and revisions are recorded per source; planned identities are scoped by course_id so the copy has its own namespace.",
        "progress_rule": "No user progress is copied between backend and backend_copy.",
        "path_rule": "Each source lesson maps to its same relative track and lesson path below both planned course roots; this records destinations only and does not create course packages.",
        "track_entries": [
            {
                "source_track_id": track["track_id"],
                "source_dirs": track["source_dirs"],
                "preserve_track_id": True,
                "planned_paths": {course: [f"courses/{course}/backend/plans/{source_dir}" for source_dir in track["source_dirs"]]
                                  for course in ("backend", "backend_copy")},
            }
            for track in track_rows
        ],
        "entries": id_rows,
        "source_artifacts": source_artifacts,
        "unmapped_authoring_sources": [
            row["path"] for row in markdown_files if row["catalog_status"] != "getter_lesson"
        ],
    }
    return inventory, id_map


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare generated bytes without writing")
    args = parser.parse_args()
    inventory, id_map = build_artifacts()
    expected = {INVENTORY_PATH: _json_bytes(inventory), ID_MAP_PATH: _json_bytes(id_map)}
    if args.check:
        failures = [str(path.relative_to(ROOT)) for path, data in expected.items()
                    if not path.is_file() or path.read_bytes() != data]
        if failures:
            print("OUT_OF_DATE " + ", ".join(failures))
            return 1
        print(f"OK tracks={len(inventory['tracks'])} lessons={inventory['identity_authority']['lesson_count']} "
              f"tasks={inventory['identity_authority']['task_count']} markdown={inventory['counts']['authoring_markdown']} "
              f"tsx={inventory['counts']['tsx_lesson_pages']} assets={inventory['counts']['tsx_lesson_assets']}")
        return 0
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    INVENTORY_PATH.write_bytes(expected[INVENTORY_PATH])
    ID_MAP_PATH.write_bytes(expected[ID_MAP_PATH])
    print(f"WROTE {INVENTORY_PATH.relative_to(ROOT)} and {ID_MAP_PATH.relative_to(ROOT)}")
    print(f"tracks={len(inventory['tracks'])} lessons={inventory['identity_authority']['lesson_count']} "
          f"tasks={inventory['identity_authority']['task_count']} markdown={inventory['counts']['authoring_markdown']} "
          f"tsx={inventory['counts']['tsx_lesson_pages']} assets={inventory['counts']['tsx_lesson_assets']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
