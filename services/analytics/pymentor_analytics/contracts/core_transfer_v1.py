"""Independent copy of the Core transfer v1 wire algorithm; no Core imports."""
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from uuid import UUID, NAMESPACE_URL, uuid5

NAMESPACE = uuid5(NAMESPACE_URL, "https://pymentor.ru/contracts/core-analytics-transfer/v1")
MAX_BODY_BYTES = 8192


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(event):
    return sha256(canonical(event).encode("utf-8")).hexdigest()


def _timestamp(value):
    if value is None:
        return None
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat()


def validate_event(event, *, check_id=True):
    """Validate exact Core normalization, including UUIDv5 and field spelling."""
    try:
        if not isinstance(event, dict) or set(event) != {"event_id", "schema_version", "kind", "subject_ref", "revision", "payload"}:
            raise ValueError
        if type(event["schema_version"]) is not int or event["schema_version"] != 1 or type(event["revision"]) is not int or not 0 <= event["revision"] <= 9223372036854775807:
            raise ValueError
        subject = str(UUID(event["subject_ref"]))
        payload, kind = event["payload"], event["kind"]
        if not isinstance(payload, dict):
            raise ValueError
        if kind == "legacy_activity_day" and set(payload) == {"activity_date", "visited_at", "provenance"} and payload["provenance"] == "legacy_global":
            normalized = {"activity_date": date.fromisoformat(payload["activity_date"]).isoformat(),
                          "visited_at": _timestamp(payload["visited_at"]), "provenance": "legacy_global"}
        elif kind == "lesson_progress" and set(payload) == {"course_id", "lesson_id", "completed_at"}:
            if any(not isinstance(payload[k], str) or not payload[k] for k in ("course_id", "lesson_id")):
                raise ValueError
            normalized = {"course_id": payload["course_id"], "lesson_id": payload["lesson_id"],
                          "completed_at": _timestamp(payload["completed_at"])}
        elif kind == "task_progress" and set(payload) == {"course_id", "task_id", "task_revision", "completed_at"}:
            limits = {"course_id": 80, "task_id": 160, "task_revision": 64}
            if any(not isinstance(payload[key], str) or not 1 <= len(payload[key]) <= limit for key, limit in limits.items()):
                raise ValueError
            normalized = {**payload, "completed_at": _timestamp(payload["completed_at"])}
        else:
            # subject_deleted belongs to the independent privacy journal.
            raise ValueError
        body = {"schema_version": 1, "kind": kind, "subject_ref": subject,
                "revision": event["revision"], "payload": normalized}
        expected = {"event_id": str(uuid5(NAMESPACE, canonical(body))), **body}
        if str(UUID(event["event_id"])) != event["event_id"] or any(event[k] != value for k, value in body.items()):
            raise ValueError
        if check_id and event != expected:
            raise ValueError
        return event
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise ValueError("invalid transfer event") from None


def entity_key(event):
    p = event["payload"]
    kind = event["kind"]
    if kind == "legacy_activity_day":
        value = [kind, p["activity_date"]]
    else:
        value = [kind, p["course_id"], p["task_id" if kind == "task_progress" else "lesson_id"]]
    return sha256(canonical(value).encode("utf-8")).hexdigest()
