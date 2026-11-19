"""Versioned, minimal snapshots of existing Core facts.

These are an independent proposal, not Analytics' browser or v2 control API.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
import json
from uuid import UUID, NAMESPACE_URL, uuid5

NAMESPACE = uuid5(NAMESPACE_URL, "https://pymentor.ru/contracts/core-analytics-transfer/v1")


def _time(value: datetime | None) -> str | None:
    if value is None:
        return None
    # Legacy Core activity timestamps without a timezone are interpreted as UTC.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _make(kind: str, subject_ref: UUID, revision: int, payload: dict) -> dict:
    if not isinstance(subject_ref, UUID):
        raise TypeError("subject_ref must be the stored lifecycle UUID")
    if type(revision) is not int or revision < 0:
        raise ValueError("revision must be a nonnegative integer")
    body = {"schema_version": 1, "kind": kind, "subject_ref": str(subject_ref),
            "revision": revision, "payload": payload}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {"event_id": str(uuid5(NAMESPACE, canonical)), **body}


def activity(subject_ref: UUID, activity_date: date, visited_at: datetime | None, *, revision: int) -> dict:
    if not isinstance(activity_date, date) or isinstance(activity_date, datetime):
        raise TypeError("activity_date must be a date")
    return _make("legacy_activity_day", subject_ref, revision,
                 {"activity_date": activity_date.isoformat(), "visited_at": _time(visited_at),
                  "provenance": "legacy_global"})


def progress(subject_ref: UUID, course_id: str, lesson_id: str,
             completed_at: datetime | None, *, revision: int) -> dict:
    if not isinstance(course_id, str) or not isinstance(lesson_id, str) or not course_id or not lesson_id:
        raise ValueError("actual course and lesson identifiers are required")
    return _make("lesson_progress", subject_ref, revision,
                 {"course_id": course_id, "lesson_id": lesson_id,
                  "completed_at": _time(completed_at)})


def task_progress(subject_ref: UUID, course_id: str, task_id: str, task_revision: str,
                  completed_at: datetime | None, *, revision: int) -> dict:
    if any(not isinstance(value, str) or not value for value in (course_id, task_id, task_revision)):
        raise ValueError("actual course, task and task revision are required")
    if len(course_id) > 80 or len(task_id) > 160 or len(task_revision) > 64:
        raise ValueError("task identity exceeds transfer limits")
    return _make("task_progress", subject_ref, revision,
                 {"course_id": course_id, "task_id": task_id, "task_revision": task_revision,
                  "completed_at": _time(completed_at)})


def deletion(subject_ref: UUID, *, revision: int) -> dict:
    if revision < 1:
        raise ValueError("deletion requires a positive lifecycle revision")
    return _make("subject_deleted", subject_ref, revision, {})


def canonical(event: dict) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(event: dict) -> str:
    return sha256(canonical(event).encode("utf-8")).hexdigest()


def validate(event: dict) -> None:
    """Reject unknown fields, forged IDs, malformed data and noncanonical values."""
    try:
        if set(event) != {"event_id", "schema_version", "kind", "subject_ref", "revision", "payload"} or type(event["schema_version"]) is not int or event["schema_version"] != 1:
            raise ValueError
        subject = UUID(event["subject_ref"])
        kind, revision, p = event["kind"], event["revision"], event["payload"]
        if kind == "legacy_activity_day" and set(p) == {"activity_date", "visited_at", "provenance"} and p["provenance"] == "legacy_global":
            expected = activity(subject, date.fromisoformat(p["activity_date"]),
                datetime.fromisoformat(p["visited_at"]) if p["visited_at"] is not None else None, revision=revision)
        elif kind == "lesson_progress" and set(p) == {"course_id", "lesson_id", "completed_at"}:
            expected = progress(subject, p["course_id"], p["lesson_id"],
                datetime.fromisoformat(p["completed_at"]) if p["completed_at"] is not None else None, revision=revision)
        elif kind == "task_progress" and set(p) == {"course_id", "task_id", "task_revision", "completed_at"}:
            expected = task_progress(subject, p["course_id"], p["task_id"], p["task_revision"],
                datetime.fromisoformat(p["completed_at"]) if p["completed_at"] is not None else None, revision=revision)
        elif kind == "subject_deleted" and p == {}:
            expected = deletion(subject, revision=revision)
        else:
            raise ValueError
        if event != expected:
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("invalid transfer event") from None
