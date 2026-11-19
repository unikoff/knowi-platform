"""Core-owned a009 snapshot proof wire contract; independent of raw event v1."""
from datetime import datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from pymentor_core.modules.analytics_delivery.contracts import _time, canonical

PROOF_NAMESPACE = uuid5(NAMESPACE_URL, "https://pymentor.ru/contracts/core-analytics-snapshot-proof/v1")
DOMAIN = b"pymentor:core-snapshot:v1\0"
MAX_DAYS = 10_000
MAX_PROGRESS = 5_000
MAX_BYTES = 8 * 1024 * 1024
MAX_KEYS = 20_000


def proof_entity_key(event):
    payload = event["payload"]
    if event["kind"] == "legacy_activity_day":
        identity = [event["kind"], payload["activity_date"]]
    elif event["kind"] == "lesson_progress":
        identity = [event["kind"], payload["course_id"], payload["lesson_id"]]
    elif event["kind"] == "task_progress":
        identity = [event["kind"], payload["course_id"], payload["task_id"]]
    else:
        raise ValueError("unsupported snapshot entity")
    return sha256(canonical(identity).encode("utf-8")).hexdigest()


def digest_entities(events):
    ordered = sorted((proof_entity_key(event), event) for event in events)
    if len({key for key, _ in ordered}) != len(ordered):
        raise ValueError("duplicate snapshot identity")
    digest = sha256(DOMAIN)
    size = len(DOMAIN)
    for _, event in ordered:
        line = (canonical({"kind": event["kind"], "payload": event["payload"]}) + "\n").encode("utf-8")
        size += len(line)
        if size > MAX_BYTES:
            raise ValueError("snapshot byte limit exceeded")
        digest.update(line)
    return digest.hexdigest()


def make_proof(subject: UUID, cut_revision: int, events: list[dict], created_at, *, tasks=False) -> dict:
    if not isinstance(subject, UUID) or type(cut_revision) is not int or not 0 <= cut_revision <= (1 << 63) - 1:
        raise ValueError("invalid snapshot identity")
    if len(events) > MAX_DAYS + MAX_PROGRESS:
        raise ValueError("snapshot entity limit exceeded")
    if created_at is None or created_at.utcoffset() is None:
        raise ValueError("aware snapshot time required")
    body = {"schema_version": 2 if tasks else 1, "subject_ref": str(subject), "scope": "subject_all_v2" if tasks else "subject_all_v1",
            "cut_revision": cut_revision, "entity_count": len(events),
            "entities_digest": digest_entities(events), "created_at": _time(created_at)}
    return {"proof_id": str(uuid5(PROOF_NAMESPACE, canonical(body))), **body}


def proof_fingerprint(proof):
    return sha256(canonical(proof).encode("utf-8")).hexdigest()


def validate_proof(proof):
    try:
        if set(proof) != {"proof_id", "schema_version", "subject_ref", "scope", "cut_revision", "entity_count", "entities_digest", "created_at"}:
            raise ValueError
        if (type(proof["schema_version"]) is not int or (proof["schema_version"], proof["scope"]) not in ((1, "subject_all_v1"), (2, "subject_all_v2"))
                or type(proof["cut_revision"]) is not int or not 0 <= proof["cut_revision"] <= (1 << 63) - 1
                or type(proof["entity_count"]) is not int or not 0 <= proof["entity_count"] <= MAX_DAYS + MAX_PROGRESS
                or not isinstance(proof["entities_digest"], str) or len(proof["entities_digest"]) != 64
                or any(c not in "0123456789abcdef" for c in proof["entities_digest"])):
            raise ValueError
        if str(UUID(proof["subject_ref"])) != proof["subject_ref"]:
            raise ValueError
        timestamp = datetime.fromisoformat(proof["created_at"])
        if timestamp.utcoffset() is None or _time(timestamp) != proof["created_at"]:
            raise ValueError
        body = {key: value for key, value in proof.items() if key != "proof_id"}
        if proof["proof_id"] != str(uuid5(PROOF_NAMESPACE, canonical(body))):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise ValueError("invalid snapshot proof") from None
