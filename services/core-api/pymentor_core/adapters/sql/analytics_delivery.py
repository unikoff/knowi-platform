"""Isolated SQL primitives. Caller owns every transaction and commits explicitly.

Tables use separate metadata and are NOT included in Core's active schema/migrations.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
from uuid import UUID, uuid4

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Column, Date, DateTime, Index, Integer, MetaData, String, Table,
                        Text, UniqueConstraint, and_, insert, or_, select, text, update)
from sqlalchemy.exc import IntegrityError

from pymentor_core.modules.analytics_delivery.contracts import activity, canonical, fingerprint, progress, task_progress, validate

metadata = MetaData()
outbox = Table(
    "analytics_transfer_outbox", metadata,
    Column("event_id", String(36), primary_key=True),
    Column("subject_ref", String(36), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("entity_key", String(64), nullable=False),
    Column("revision", BigInteger, nullable=False),
    Column("payload", Text, nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("status", String(16), nullable=False, default="pending"),
    Column("attempts", Integer, nullable=False, default=0),
    Column("uncertain", Boolean, nullable=False, default=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("claim_token", String(36)),
    Column("lease_until", DateTime(timezone=True)),
    UniqueConstraint("entity_key", "revision", name="uq_analytics_transfer_revision"),
)
checkpoint = Table(
    "analytics_transfer_checkpoints", metadata,
    Column("stream", String(80), primary_key=True),
    Column("high_watermark", BigInteger, nullable=False),
    Column("cursor", BigInteger, nullable=False, default=0),
)
revisions = Table(
    "analytics_transfer_revisions", metadata,
    Column("subject_ref", String(36), primary_key=True),
    Column("revision", BigInteger, nullable=False),
)
activity_state = Table(
    "analytics_transfer_activity_state", metadata,
    Column("subject_ref", String(36), primary_key=True),
    Column("activity_date", Date, primary_key=True),
    Column("visited_at", DateTime(timezone=True)),
    Column("needs_core_replay", Boolean, nullable=False),
)
write_gate = Table(
    "analytics_transfer_write_gate", metadata,
    Column("singleton", Integer, primary_key=True),
    Column("mode", String(16), nullable=False),
    CheckConstraint("singleton = 1", name="ck_analytics_write_gate_singleton"),
    CheckConstraint("mode IN ('mirror', 'outbox_only', 'replaying')", name="ck_analytics_write_gate_mode"),
)
Index("ix_analytics_transfer_due", outbox.c.status, outbox.c.next_attempt_at)
Index("ix_analytics_transfer_subject", outbox.c.subject_ref)
Index("ix_analytics_transfer_activity_replay", activity_state.c.needs_core_replay, activity_state.c.subject_ref, activity_state.c.activity_date)


def entity_key(event: dict) -> str:
    p = event["payload"]
    identity = [event["subject_ref"], event["kind"]]
    if event["kind"] == "legacy_activity_day":
        identity.append(p["activity_date"])
    elif event["kind"] == "lesson_progress":
        identity.extend((p["course_id"], p["lesson_id"]))
    elif event["kind"] == "task_progress":
        identity.extend((p["course_id"], p["task_id"]))
    elif event["kind"] != "subject_deleted":
        raise ValueError("unknown transfer kind")
    return sha256(json.dumps(identity, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def deletion_result(db, user_id: int, subject_ref: str) -> str | None:
    """Read an authorized lifecycle deletion's durable completion, without network I/O.

    None means no deletion for this exact numeric ID and lifecycle. A missing
    transfer row after a recorded deletion is unknown and must never become 204.
    """
    recorded = db.execute(text("SELECT 1 FROM account_deletion_events WHERE user_id=:uid AND lifecycle_id=:ref"),
                          {"uid": user_id, "ref": subject_ref}).first()
    if recorded is None:
        return None
    status = db.execute(select(outbox.c.status).where(outbox.c.subject_ref == subject_ref,
        outbox.c.kind == "subject_deleted")).scalar_one_or_none()
    if status is None:
        raise RuntimeError("Analytics deletion state unavailable")
    return "complete" if status == "delivered" else "pending"


def enqueue(db, event: dict, now: datetime) -> bool:
    """Atomically stage an event inside the caller's business transaction.

    Repeated same revision and content succeeds as a no-op; conflicting content
    fails closed. A savepoint preserves the surrounding transaction on conflict.
    """
    validate(event)
    key, data, digest = entity_key(event), canonical(event), fingerprint(event)
    try:
        with db.begin_nested():
            db.execute(insert(outbox).values(event_id=event["event_id"], subject_ref=event["subject_ref"], kind=event["kind"], entity_key=key,
                revision=event["revision"], payload=data, fingerprint=digest, created_at=now,
                status="pending", attempts=0, uncertain=False, next_attempt_at=now))
        return True
    except IntegrityError:
        row = db.execute(select(outbox.c.payload).where(
            outbox.c.entity_key == key, outbox.c.revision == event["revision"])).scalar_one_or_none()
        if row != data:
            raise ValueError("transfer revision conflict") from None
        return False


def next_revision(db, subject_ref: UUID) -> int:
    """Allocate within the business transaction while holding its lifecycle lock."""
    if not isinstance(subject_ref, UUID):
        raise TypeError("stored lifecycle UUID required")
    ref = str(subject_ref)
    value = db.execute(update(revisions).where(revisions.c.subject_ref == ref)
                       .values(revision=revisions.c.revision + 1).returning(revisions.c.revision)).scalar_one_or_none()
    if value is not None:
        return value
    # The existing account lifecycle lock must serialize first allocation.
    db.execute(insert(revisions).values(subject_ref=ref, revision=1))
    return 1


def start_checkpoint(db, stream: str, high_watermark: int) -> None:
    if not stream or high_watermark < 0:
        raise ValueError("invalid checkpoint")
    db.execute(insert(checkpoint).values(stream=stream, high_watermark=high_watermark, cursor=0))


def scan_page(db, stream: str, cursor: int, high_watermark: int, *, limit: int, dates_only: bool = False) -> list[tuple[int, dict]]:
    """Read a bounded Core keyset page. Missing lifecycle mapping stops the scan.

    Capture and privacy fencing must already be installed before this is usable
    against concurrently changing data. No lifecycle is created by this reader.
    """
    if not 1 <= limit <= 500 or cursor < 0 or high_watermark < cursor:
        raise ValueError("invalid scan bounds")
    if stream == "activity":
        timestamp_column = "NULL AS visited_at" if dates_only else "a.visited_at"
        query = text(f"""SELECT a.id, a.activity_date, {timestamp_column}, l.lifecycle_id
            FROM user_activity_days a LEFT JOIN account_lifecycles l ON l.user_id=a.user_id
            WHERE a.id > :cursor AND a.id <= :high ORDER BY a.id LIMIT :limit""").columns(
                id=BigInteger, activity_date=Date, visited_at=DateTime, lifecycle_id=String)
    elif stream == "progress":
        query = text("""SELECT p.id, p.course_id, p.lesson_id, p.completed_at, l.lifecycle_id
            FROM user_lesson_progress p LEFT JOIN account_lifecycles l ON l.user_id=p.user_id
            WHERE p.id > :cursor AND p.id <= :high ORDER BY p.id LIMIT :limit""").columns(
                id=BigInteger, course_id=String, lesson_id=String, completed_at=DateTime, lifecycle_id=String)
    elif stream == "tasks":
        query = text("""SELECT p.id, p.course_id, p.task_id, p.task_revision, p.completed_at, l.lifecycle_id
            FROM user_task_progress p LEFT JOIN account_lifecycles l ON l.user_id=p.user_id
            WHERE p.id > :cursor AND p.id <= :high ORDER BY p.id LIMIT :limit""").columns(
                id=BigInteger, course_id=String, task_id=String, task_revision=String, completed_at=DateTime, lifecycle_id=String)
    else:
        raise ValueError("unknown stream")
    rows = db.execute(query, {"cursor": cursor, "high": high_watermark, "limit": limit}).mappings()
    result = []
    for row in rows:
        if row["lifecycle_id"] is None:
            raise ValueError("account lifecycle mapping missing; stop export")
        subject = UUID(str(row["lifecycle_id"]))
        if stream == "activity":
            event = activity(subject, row["activity_date"], row["visited_at"], revision=0)
        elif stream == "progress":
            event = progress(subject, row["course_id"], row["lesson_id"], row["completed_at"], revision=0)
        else:
            event = task_progress(subject, row["course_id"], row["task_id"], row["task_revision"], row["completed_at"], revision=0)
        result.append((row["id"], event))
    return result


def stage_page(db, stream: str, rows: list[tuple[int, dict]], now: datetime, *, limit: int) -> int:
    """Stage a scanned, ordered page and checkpoint it in the same transaction.

    Caller must supply the next contiguous keyset page from its source query; a
    short/empty page does not claim any unseen range or declare reconciliation.
    """
    if not 1 <= limit <= 500 or len(rows) > limit:
        raise ValueError("invalid page size")
    state = db.execute(select(checkpoint).where(checkpoint.c.stream == stream).with_for_update()).mappings().one()
    ids = [row_id for row_id, _ in rows]
    if ids != sorted(set(ids)) or any(i <= state["cursor"] or i > state["high_watermark"] for i in ids):
        raise ValueError("page outside ordered checkpoint range")
    for _, event in rows:
        if event["revision"] != 0:
            raise ValueError("backfill snapshots require revision zero")
        enqueue(db, event, now)
    if ids:
        db.execute(update(checkpoint).where(checkpoint.c.stream == stream).values(cursor=ids[-1]))
    return ids[-1] if ids else state["cursor"]


def claim(db, now: datetime, *, limit: int, lease_seconds: int, max_attempts: int) -> list[dict]:
    """Claim due rows with compare-and-set; no network activity occurs here."""
    if not 1 <= limit <= 100 or not 1 <= lease_seconds <= 300 or not 1 <= max_attempts <= 100:
        raise ValueError("invalid claim bounds")
    due = and_(outbox.c.attempts < max_attempts, outbox.c.next_attempt_at <= now,
               or_(outbox.c.status == "pending", and_(outbox.c.status == "claimed", outbox.c.lease_until <= now)))
    ids = db.execute(select(outbox.c.event_id).where(due).order_by(outbox.c.next_attempt_at, outbox.c.event_id).limit(limit)).scalars().all()
    claimed = []
    for event_id in ids:
        token = str(uuid4())
        result = db.execute(update(outbox).where(outbox.c.event_id == event_id, due).values(
            status="claimed", claim_token=token, lease_until=now + timedelta(seconds=lease_seconds),
            attempts=outbox.c.attempts + 1))
        if result.rowcount:
            claimed.append(dict(db.execute(select(outbox).where(outbox.c.event_id == event_id)).mappings().one()))
    return claimed


def mark_sending(db, event_id: str, token: str) -> None:
    """Commit this transition before issuing HTTP, including first attempts."""
    result = db.execute(update(outbox).where(outbox.c.event_id == event_id,
        outbox.c.status == "claimed", outbox.c.claim_token == token).values(uncertain=True))
    if result.rowcount != 1:
        raise ValueError("stale claim")


def finish(db, event_id: str, token: str, action: str, now: datetime, *, delay_seconds: int = 0) -> None:
    if action not in {"ack", "retry", "reconcile", "quarantine"} or not 0 <= delay_seconds <= 3600:
        raise ValueError("invalid relay decision")
    status = {"ack": "delivered", "retry": "pending", "reconcile": "pending", "quarantine": "quarantined"}[action]
    result = db.execute(update(outbox).where(outbox.c.event_id == event_id,
        outbox.c.status == "claimed", outbox.c.claim_token == token).values(
            status=status, uncertain=action == "reconcile", claim_token=None, lease_until=None,
            next_attempt_at=now + timedelta(seconds=delay_seconds)))
    if result.rowcount != 1:
        raise ValueError("stale claim")
