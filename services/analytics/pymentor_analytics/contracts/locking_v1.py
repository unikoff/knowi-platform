"""Shared subject lifecycle advisory-lock identity; changing it requires migration."""
from hashlib import sha256
from uuid import UUID

SUBJECT_LOCK_DOMAIN = b"analytics:subject-lock:v1\0"
EVENT_DEDUP_LOCK_DOMAIN = b"analytics:event-dedup-lock:v1\0"


def subject_lock_key(subject_ref: UUID) -> int:
    """PostgreSQL signed bigint; collisions serialize unrelated subjects safely."""
    return int.from_bytes(sha256(SUBJECT_LOCK_DOMAIN + subject_ref.bytes).digest()[:8],
                          byteorder="big", signed=True)


def event_dedup_lock_key(producer_namespace: str, event_id: UUID) -> tuple[int, int]:
    """Two-int PostgreSQL advisory key, distinct from the subject bigint space."""
    encoded = producer_namespace.encode("utf-8")
    digest = sha256(EVENT_DEDUP_LOCK_DOMAIN + len(encoded).to_bytes(4, "big")
                    + encoded + event_id.bytes).digest()
    return (int.from_bytes(digest[:4], "big", signed=True),
            int.from_bytes(digest[4:8], "big", signed=True))
