"""Optional a009 proof outbox. It is not part of Core/A007 startup metadata."""
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, Integer, MetaData, String, Table, Text, UniqueConstraint, and_, exists, or_, select, update
from pymentor_core.adapters.sql.analytics_delivery import outbox

metadata = MetaData()
proofs = Table("analytics_transfer_snapshot_proofs", metadata,
    Column("proof_id", String(36), primary_key=True),
    Column("subject_ref", String(36), nullable=False),
    Column("cut_revision", BigInteger, nullable=False),
    Column("payload", Text, nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("accepted", Boolean, nullable=False),
    Column("uncertain", Boolean, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("claim_token", String(36)),
    Column("lease_until", DateTime(timezone=True)),
    UniqueConstraint("subject_ref", "cut_revision", name="uq_analytics_proof_cut"))
Index("ix_analytics_proof_due", proofs.c.status, proofs.c.next_attempt_at)
barriers = Table("analytics_transfer_snapshot_barriers", metadata,
    Column("stream", String(16), primary_key=True),
    Column("high_watermark", BigInteger, nullable=False),
    Column("sealed_at", DateTime(timezone=True), nullable=False))
preparation = Table("analytics_transfer_activity_preparation", metadata,
    Column("singleton", Integer, primary_key=True),
    Column("high_watermark", BigInteger, nullable=False),
    Column("cursor", BigInteger, nullable=False),
    Column("sealed", Boolean, nullable=False))


def claim(db, now, *, limit: int, lease_seconds: int, max_attempts: int):
    if not 1 <= limit <= 50 or not 1 <= lease_seconds <= 300 or not 1 <= max_attempts <= 100:
        raise ValueError("invalid proof claim bounds")
    outstanding = exists(select(outbox.c.event_id).where(outbox.c.subject_ref == proofs.c.subject_ref,
        outbox.c.kind != "subject_deleted", outbox.c.revision <= proofs.c.cut_revision, outbox.c.status != "delivered"))
    due = and_(proofs.c.attempts < max_attempts, proofs.c.next_attempt_at <= now, ~outstanding,
        or_(proofs.c.status == "pending", and_(proofs.c.status == "claimed", proofs.c.lease_until <= now)))
    ids = db.execute(select(proofs.c.proof_id).where(due).order_by(proofs.c.next_attempt_at, proofs.c.proof_id).limit(limit)).scalars().all()
    claimed = []
    for proof_id in ids:
        token = str(uuid4())
        result = db.execute(update(proofs).where(proofs.c.proof_id == proof_id, due).values(
            status="claimed", claim_token=token, lease_until=now + timedelta(seconds=lease_seconds), attempts=proofs.c.attempts + 1))
        if result.rowcount:
            claimed.append(dict(db.execute(select(proofs).where(proofs.c.proof_id == proof_id)).mappings().one()))
    return claimed


def mark_sending(db, proof_id, token):
    result = db.execute(update(proofs).where(proofs.c.proof_id == proof_id,
        proofs.c.status == "claimed", proofs.c.claim_token == token).values(uncertain=True))
    if result.rowcount != 1:
        raise ValueError("stale proof claim")


def finish(db, proof_id, token, action, now, *, delay_seconds=0):
    if action not in {"accepted", "verified", "retry", "reconcile", "quarantine"} or not 0 <= delay_seconds <= 3600:
        raise ValueError("invalid proof decision")
    values = {"claim_token": None, "lease_until": None, "next_attempt_at": now + timedelta(seconds=delay_seconds)}
    if action in {"accepted", "verified"}:
        values.update(status="verified" if action == "verified" else "pending", accepted=True, uncertain=False)
    else:
        values.update(status="quarantined" if action == "quarantine" else "pending", uncertain=action == "reconcile")
    result = db.execute(update(proofs).where(proofs.c.proof_id == proof_id,
        proofs.c.status == "claimed", proofs.c.claim_token == token).values(**values))
    if result.rowcount != 1:
        raise ValueError("stale proof claim")
