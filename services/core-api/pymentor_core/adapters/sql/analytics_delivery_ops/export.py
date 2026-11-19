"""Explicit, operator-driven bounded export; never started by application startup."""
from datetime import datetime, timezone
import os

from sqlalchemy import inspect, select, text, update
from pymentor_core.adapters.sql import models
from pymentor_core.adapters.sql.analytics_delivery import activity_state, checkpoint, scan_page, stage_page, start_checkpoint
from pymentor_core.adapters.sql.analytics_delivery_ops.integration import enabled, task_enabled, guard_lifecycle, lifecycle, require_write_gate, write_mode

_SOURCE = {"activity": "user_activity_days", "progress": "user_lesson_progress", "tasks": "user_task_progress"}


def provision_lifecycles_page(db, *, cursor: int, high_watermark: int, limit: int = 100) -> dict:
    """Explicit bounded provisioning for existing accounts; caller commits the page."""
    if not enabled() or not 1 <= limit <= 100 or cursor < 0 or high_watermark < cursor:
        raise ValueError("invalid lifecycle provisioning bounds")
    ids = db.execute(text("SELECT id FROM users WHERE id > :cursor AND id <= :high ORDER BY id LIMIT :limit"),
                     {"cursor": cursor, "high": high_watermark, "limit": limit}).scalars().all()
    for user_id in ids:
        lifecycle(db, user_id)
    return {"count": len(ids), "cursor": ids[-1] if ids else cursor,
            "scan_exhausted": len(ids) < limit}


def initialize(db, stream: str) -> int:
    """Caller must first establish exclusive capture cutover and identity coverage."""
    if not enabled() or stream not in _SOURCE or (stream == "tasks" and not task_enabled()):
        raise ValueError("capture disabled or invalid stream")
    high = db.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {_SOURCE[stream]}")).scalar_one()
    start_checkpoint(db, stream, high)
    return high


def export_page(db, stream: str, *, limit: int = 100) -> dict:
    if not enabled() or stream not in _SOURCE or (stream == "tasks" and not task_enabled()):
        raise ValueError("capture disabled or invalid stream")
    state = db.execute(select(checkpoint).where(checkpoint.c.stream == stream).with_for_update()).mappings().one()
    from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_export import proof_enabled
    from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_sql import barriers
    if proof_enabled() or inspect(db.connection()).has_table(barriers.name):
        if db.execute(select(barriers.c.stream).where(barriers.c.stream == stream)).first():
            raise ValueError("backfill stream is sealed")
    rows = scan_page(db, stream, state["cursor"], state["high_watermark"], limit=limit,
                     dates_only=os.environ.get("PYMENTOR_ANALYTICS_HISTORY_DATES_ONLY") == "1")
    cursor = stage_page(db, stream, rows, datetime.now(timezone.utc), limit=limit)
    # A short page ends scanning, not live-tail reconciliation or parity.
    return {"count": len(rows), "cursor": cursor, "high_watermark": state["high_watermark"],
            "scan_exhausted": len(rows) < limit}


def seal_backfill(db, stream: str) -> None:
    """Seal only after the bounded keyset scanner has exhausted its fixed range."""
    from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_export import proof_enabled
    from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_sql import barriers
    if not enabled() or not proof_enabled() or stream not in _SOURCE or (stream == "tasks" and not task_enabled()):
        raise ValueError("snapshot barrier unavailable")
    require_write_gate(db, write_mode())
    state = db.execute(select(checkpoint).where(checkpoint.c.stream == stream).with_for_update()).mappings().one()
    existing = db.execute(select(barriers).where(barriers.c.stream == stream)).mappings().one_or_none()
    if existing is not None:
        if existing["high_watermark"] != state["high_watermark"]:
            raise ValueError("backfill barrier conflict")
        return
    pending = db.execute(text(f"SELECT 1 FROM {_SOURCE[stream]} WHERE id > :cursor AND id <= :high LIMIT 1"),
                         {"cursor": state["cursor"], "high": state["high_watermark"]}).first()
    if pending is not None:
        raise ValueError("backfill stream has unscanned rows")
    from sqlalchemy import insert
    db.execute(insert(barriers).values(stream=stream, high_watermark=state["high_watermark"], sealed_at=datetime.now(timezone.utc)))


def replay_core_page(db, *, writers_stopped: bool, limit: int = 100) -> dict:
    """Replay dirty cutover days to Core inside caller's transaction.

    Operator must quiesce all old and outbox-only writers across processes first.
    Missing lifecycle or conflicting old timestamp aborts the whole page.
    """
    if not enabled() or write_mode() != "mirror" or writers_stopped is not True or not 1 <= limit <= 100:
        raise ValueError("invalid rollback replay state")
    require_write_gate(db, "replaying")
    candidates = db.execute(select(activity_state.c.subject_ref, activity_state.c.activity_date)
        .where(activity_state.c.needs_core_replay.is_(True))
        .order_by(activity_state.c.subject_ref, activity_state.c.activity_date).limit(limit)).all()
    applied = 0
    for ref, day in candidates:
        user_id = db.execute(text("SELECT user_id FROM account_lifecycles WHERE lifecycle_id=:ref"), {"ref": ref}).scalar_one_or_none()
        if user_id is None:
            raise ValueError("replay lifecycle unavailable")
        guard_lifecycle(db, user_id, ref)
        row = db.execute(select(activity_state).where(activity_state.c.subject_ref == ref,
            activity_state.c.activity_date == day).with_for_update()).mappings().one_or_none()
        if row is None or not row["needs_core_replay"]:
            continue
        old = db.query(models.UserActivityDay).filter_by(user_id=user_id, activity_date=day).one_or_none()
        incoming = row["visited_at"]
        def normalized(value):
            return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        if old is None:
            db.add(models.UserActivityDay(user_id=user_id, activity_date=day, visited_at=incoming))
        elif old.visited_at is None:
            old.visited_at = incoming
        elif incoming is not None and normalized(old.visited_at) != normalized(incoming):
            raise ValueError("replay timestamp conflict")
        db.execute(update(activity_state).where(activity_state.c.subject_ref == ref,
            activity_state.c.activity_date == day).values(needs_core_replay=False))
        applied += 1
    return {"count": applied, "scan_exhausted": len(candidates) < limit}
