"""Versioned, per-subject Core snapshot proof; no Core runtime dependency."""
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import re
from uuid import NAMESPACE_URL, UUID, uuid5

from pymentor_analytics.contracts.core_transfer_v1 import canonical

NAMESPACE = uuid5(NAMESPACE_URL, "https://pymentor.ru/contracts/core-analytics-snapshot-proof/v1")
DOMAIN = b"pymentor:core-snapshot:v1\0"
MAX_ENTITIES = 15000
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_HISTORIC_KEYS = 20000


def proof_fingerprint(proof):
    return sha256(canonical(proof).encode("utf-8")).hexdigest()


def validate_proof(proof, digest, *, check_id=True):
    try:
        fields = {"schema_version", "proof_id", "subject_ref", "scope", "cut_revision", "entity_count", "entities_digest", "created_at"}
        if not isinstance(proof, dict) or set(proof) != fields:
            raise ValueError
        if (type(proof["schema_version"]) is not int
                or (proof["schema_version"], proof["scope"]) not in {(1, "subject_all_v1"), (2, "subject_all_v2")}
                or type(proof["cut_revision"]) is not int
                or not 0 <= proof["cut_revision"] <= 2**63-1 or type(proof["entity_count"]) is not int
                or not 0 <= proof["entity_count"] <= MAX_ENTITIES):
            raise ValueError
        for key in ("proof_id", "subject_ref"):
            if str(UUID(proof[key])) != proof[key]:
                raise ValueError
        if (not isinstance(proof["entities_digest"], str) or re.fullmatch(r"[0-9a-f]{64}", proof["entities_digest"]) is None
                or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
            raise ValueError
        stamp = datetime.fromisoformat(proof["created_at"])
        if stamp.tzinfo is None or stamp.astimezone(timezone.utc).isoformat() != proof["created_at"]:
            raise ValueError
        body = {k: v for k, v in proof.items() if k != "proof_id"}
        if check_id and str(uuid5(NAMESPACE, canonical(body))) != proof["proof_id"]:
            raise ValueError
        if not hmac.compare_digest(proof_fingerprint(proof), digest):
            raise ValueError
        return proof
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError):
        raise ValueError("invalid snapshot proof") from None


def active_line(event):
    """Return canonical active entity line, or None for a progress tombstone."""
    if event["kind"] in {"lesson_progress", "task_progress"} and event["payload"]["completed_at"] is None:
        return None
    return (canonical({"kind": event["kind"], "payload": event["payload"]}) + "\n").encode("utf-8")
