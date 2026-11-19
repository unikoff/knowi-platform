"""Catalog v1 reference contract, not a delivery service or durable store."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
# Historical course aliases may contain Unicode and spaces, unlike canonical IDs.
Alias = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f-\x9f]+$")]
TrackId = Annotated[str, Field(min_length=1, max_length=256)]
Title = Annotated[str, Field(min_length=1, max_length=256)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Task(Contract):
    task_id: Id
    task_revision: Id
    aliases: tuple[Alias, ...]
    title: Title
    order: Annotated[int, Field(ge=1)]
    tombstone: bool


class Lesson(Contract):
    lesson_id: Id
    track_id: TrackId
    aliases: tuple[Alias, ...]
    title: Title
    order: Annotated[int, Field(ge=1)]
    tombstone: bool
    tasks: tuple[Task, ...]

    @model_validator(mode="after")
    def unique_tasks(self):
        unique_names([(t.task_id, t.aliases) for t in self.tasks])
        return self


def unique_names(items):
    names = [name for primary, aliases in items for name in (primary, *aliases)]
    if len(names) != len(set(names)):
        raise ValueError("duplicate or ambiguous identity/alias")


class Track(Contract):
    track_id: TrackId
    title: Title
    order: Annotated[int, Field(ge=1)]
    tombstone: bool


class Catalog(Contract):
    course_id: Id
    tracks: tuple[Track, ...]
    lessons: tuple[Lesson, ...]

    @model_validator(mode="after")
    def unique_lessons(self):
        unique_names([(l.lesson_id, l.aliases) for l in self.lessons])
        ids = [t.track_id for t in self.tracks]
        if len(ids) != len(set(ids)) or any(l.track_id not in ids for l in self.lessons):
            raise ValueError("duplicate or unknown track")
        return self


class Snapshot(Contract):
    schema_version: Literal[1]
    kind: Literal["analytics_catalog_snapshot"]
    sequence: Annotated[int, Field(ge=1)]
    catalog_version: Id
    catalog: Catalog


class Delta(Contract):
    schema_version: Literal[1]
    kind: Literal["analytics_catalog_delta"]
    sequence: Annotated[int, Field(ge=2)]
    base_version: Id
    catalog_version: Id
    # Complete new course image; deletion is an explicit tombstone.
    catalog: Catalog


def map_legacy_track(track_id: str, candidates: dict[str, tuple[str, ...]]) -> str:
    """A mapping is explicit and must select exactly one product; no default."""
    matches = candidates.get(track_id, ())
    if len(matches) != 1:
        raise ValueError("missing or ambiguous v1 track mapping")
    from pydantic import TypeAdapter
    return TypeAdapter(Id).validate_python(matches[0], strict=True)


def _preserves(old: Catalog, new: Catalog):
    if old.course_id != new.course_id:
        raise ValueError("product identity changed")
    tracks = {x.track_id: x for x in new.tracks}
    for old_track in old.tracks:
        track = tracks.get(old_track.track_id)
        if track is None or (old_track.tombstone and not track.tombstone):
            raise ValueError("track history removed or tombstone reversed")
    lessons = {x.lesson_id: x for x in new.lessons}
    for previous in old.lessons:
        current = lessons.get(previous.lesson_id)
        if current is None or not set(previous.aliases).issubset(current.aliases):
            raise ValueError("lesson history removed")
        if previous.tombstone and not current.tombstone:
            raise ValueError("lesson tombstone reversed")
        tasks = {x.task_id: x for x in current.tasks}
        for old_task in previous.tasks:
            task = tasks.get(old_task.task_id)
            if task is None or not set(old_task.aliases).issubset(task.aliases):
                raise ValueError("task history removed")
            if old_task.tombstone and not task.tombstone:
                raise ValueError("task tombstone reversed")


class ReferenceConsumer:
    """In-memory oracle. Retain every confirmed version; callers own persistence."""
    def __init__(self):
        self.history: dict[str, dict[str, Snapshot]] = {}
        self.current: dict[str, Snapshot] = {}

    def apply(self, message: Snapshot | Delta) -> str:
        course = message.catalog.course_id
        prior = self.current.get(course)
        versions = self.history.get(course, {})
        known = versions.get(message.catalog_version)
        candidate = Snapshot(schema_version=1, kind="analytics_catalog_snapshot", sequence=message.sequence,
                             catalog_version=message.catalog_version, catalog=message.catalog)
        if known is not None:
            if known != candidate:
                raise ValueError("catalog version conflict")
            # A delta's base is part of its identity even when its resulting image matches.
            if isinstance(message, Delta):
                before = next((s for s in versions.values() if s.sequence == message.sequence - 1), None)
                if before is None or before.catalog_version != message.base_version:
                    raise ValueError("delta base conflict")
            return "duplicate"
        if prior is None:
            if not isinstance(message, Snapshot):
                raise ValueError("snapshot required")
        else:
            if not isinstance(message, Delta) or message.sequence != prior.sequence + 1 or message.base_version != prior.catalog_version:
                raise ValueError("missing or out-of-order delta")
            _preserves(prior.catalog, message.catalog)
        self.history.setdefault(course, {})[message.catalog_version] = candidate
        self.current[course] = candidate
        return "applied"

    def classify(self, course_id: str, catalog_version: str, lesson_id: str, task_id: str | None = None, task_revision: str | None = None) -> str:
        snapshot = self.history.get(course_id, {}).get(catalog_version)
        if snapshot is None:
            return "pending/unknown_catalog"
        lesson = next((x for x in snapshot.catalog.lessons if lesson_id in (x.lesson_id, *x.aliases)), None)
        track = next((x for x in snapshot.catalog.tracks if lesson and x.track_id == lesson.track_id), None)
        if lesson is None or lesson.tombstone or track is None or track.tombstone:
            return "invalid_identity"
        if task_id is None:
            return "valid" if task_revision is None else "invalid_identity"
        task = next((x for x in lesson.tasks if task_id in (x.task_id, *x.aliases)), None)
        return "valid" if task and not task.tombstone and task.task_revision == task_revision else "invalid_identity"
