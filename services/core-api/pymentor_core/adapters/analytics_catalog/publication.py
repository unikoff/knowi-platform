"""Explicit Core projection and private publication receipt, never learner content."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from contextlib import contextmanager
from uuid import UUID

from pymentor_core.adapters.course_local.runtime import content_version

MAX_BYTES = 2 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def current_digest(snapshot):
    actual = content_version(snapshot.context.root, snapshot.manifest)
    if actual != snapshot.context.version or actual != snapshot.manifest.content_version:
        raise ValueError("course snapshot changed")
    return actual


def project(snapshot, previous=None):
    """Project allowlisted stable identities; retain prior identities as tombstones."""
    source_digest = current_digest(snapshot)
    tracks, lessons = [], []
    for track in snapshot.catalog.TRACKS:
        tracks.append({"track_id": track["id"], "title": track["title"], "order": track["order"], "tombstone": False})
        for position, lesson in enumerate(track["lessons"], 1):
            alias = lesson.get("legacy_id")
            tasks = [{"task_id": task["id"], "task_revision": task["revision"],
                      "aliases": list(task.get("legacy_ids", [])), "title": task["title"],
                      "order": index, "tombstone": False} for index, task in enumerate(lesson["tasks"], 1)]
            lessons.append({"lesson_id": lesson["id"], "track_id": track["id"],
                            "aliases": [alias] if alias and alias != lesson["id"] else [],
                            "title": lesson["title"], "order": position, "tombstone": False, "tasks": tasks})
    catalog = {"course_id": snapshot.context.course_id, "tracks": tracks, "lessons": lessons}
    if previous is not None:
        old = previous["catalog"]
        if old["course_id"] != catalog["course_id"]:
            raise ValueError("course identity changed")
        new_tracks = {x["track_id"]: x for x in tracks}
        for item in old["tracks"]:
            if item["track_id"] not in new_tracks:
                tracks.append({**item, "tombstone": True})
            elif item["tombstone"]:
                raise ValueError("track tombstone cannot be reversed")
        new_lessons = {x["lesson_id"]: x for x in lessons}
        for item in old["lessons"]:
            current = new_lessons.get(item["lesson_id"])
            if current is None:
                lessons.append({**item, "tombstone": True, "tasks": [{**t, "tombstone": True} for t in item["tasks"]]})
                continue
            if item["tombstone"]:
                raise ValueError("lesson tombstone cannot be reversed")
            current["aliases"] = sorted(set(current["aliases"]) | set(item["aliases"]))
            task_map = {t["task_id"]: t for t in current["tasks"]}
            for old_task in item["tasks"]:
                task = task_map.get(old_task["task_id"])
                if task is None:
                    current["tasks"].append({**old_task, "tombstone": True})
                elif old_task["tombstone"]:
                    raise ValueError("task tombstone cannot be reversed")
                else:
                    task["aliases"] = sorted(set(task["aliases"]) | set(old_task["aliases"]))
    version = "sha256:" + digest(catalog)
    if previous and version == previous["catalog_version"]:
        return previous, source_digest
    message = {"schema_version": 1, "kind": "analytics_catalog_delta" if previous else "analytics_catalog_snapshot",
               "sequence": previous["sequence"] + 1 if previous else 1,
               "catalog_version": version, "catalog": catalog}
    if previous:
        message["base_version"] = previous["catalog_version"]
    if len(canonical({"message": message, "fingerprint": digest(message)})) > MAX_BYTES:
        raise ValueError("catalog exceeds size limit")
    return message, source_digest


def receipt_path(course_id, *, root=None):
    if not _ID.fullmatch(course_id):
        raise ValueError("invalid course")
    # Fixed private directory; never accept a path from an HTTP request.
    fixture = os.environ.get("PYMENTOR_ANALYTICS_CATALOG_FIXTURE_DIR")
    if root is not None:
        root = Path(root)
        base = Path(__file__).resolve().parents[3] / ".local" / "analytics-live" / "catalog"
        if not root.is_absolute() or len(root.parts) != len(base.parts) + 2 or root.parents[1] != base:
            raise ValueError("invalid local catalog root")
        if str(UUID(root.parent.name)) != root.parent.name or not re.fullmatch(r"[0-9a-f]{64}", root.name):
            raise ValueError("invalid local catalog identity")
        for part in (base.parent.parent, base.parent, base, root.parent, root):
            if part.is_symlink() or os.path.isjunction(part):
                raise ValueError("invalid local catalog directory")
    elif fixture:
        from .fixture_guard import fixture_root, read_json
        folder = fixture_root(fixture)
        owner = read_json(folder / "owner.json")
        if (not isinstance(owner.get("token"), str) or not re.fullmatch(r"[0-9a-f]{64}", owner["token"])
                or owner.get("state") not in {"processes_started", "http_acceptance_running", "browser_hold"}):
            raise ValueError("invalid fixture owner")
        root = folder / "analytics-catalog"
    else:
        root = Path(__file__).resolve().parents[3] / ".local" / "analytics-catalog"
    for part in (root.parent, root):
        if part.is_symlink() or os.path.isjunction(part):
            raise ValueError("invalid receipt directory")
    # Hash the identity for a portable filename: identifiers may contain ':',
    # and Windows filesystems also compare names case-insensitively.
    return root / (hashlib.sha256(course_id.encode("utf-8")).hexdigest() + ".json")


def read_receipt(course_id, *, pending=False, root=None):
    path = receipt_path(course_id) if root is None else receipt_path(course_id, root=root)
    if pending:
        path = path.with_suffix(".pending.json")
    if path.is_symlink() or os.path.isjunction(path) or path.stat().st_size > MAX_BYTES + 2048:
        raise ValueError("invalid receipt file")
    receipt = json.loads(path.read_bytes())
    if set(receipt) != {"message", "fingerprint", "source_digest"}:
        raise ValueError("invalid receipt")
    message = receipt["message"]
    if message["catalog"]["course_id"] != course_id or message["catalog_version"] != "sha256:" + digest(message["catalog"]) or receipt["fingerprint"] != digest(message) or not re.fullmatch(r"[0-9a-f]{64}", receipt["source_digest"]):
        raise ValueError("invalid receipt")
    return receipt


def write_receipt(course_id, receipt, *, pending=False, root=None):
    path = receipt_path(course_id) if root is None else receipt_path(course_id, root=root)
    if pending:
        path = path.with_suffix(".pending.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive temp creation and atomic replace; no partial receipt is visible.
    fd, temp = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(receipt))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def clear_pending(course_id, *, root=None):
    path = (receipt_path(course_id) if root is None else receipt_path(course_id, root=root)).with_suffix(".pending.json")
    path.unlink(missing_ok=True)


@contextmanager
def publication_lock(course_id, *, root=None):
    path = (receipt_path(course_id) if root is None else receipt_path(course_id, root=root)).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or os.path.isjunction(path):
        raise ValueError("invalid publication lock")
    with open(path, "a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ValueError("publication already running") from None
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ValueError("publication already running") from None
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
