"""Synthetic cutover gate; callers own transactions and must quiesce old writers."""
from sqlalchemy import BigInteger, Date, DateTime, String, insert, select, text, update

from pymentor_core.adapters.sql.analytics_delivery import activity_state, write_gate
from pymentor_core.adapters.sql.analytics_delivery_ops.integration import enabled
from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_sql import preparation


def _locked(db):
    mode = db.execute(select(write_gate.c.mode).where(write_gate.c.singleton == 1).with_for_update()).scalar_one_or_none()
    if mode is None:
        raise ValueError("activity write gate unavailable")
    return mode


def _clean(db):
    return db.execute(select(activity_state.c.subject_ref).where(activity_state.c.needs_core_replay.is_(True)).limit(1)).first() is None


def begin_preparation(db, *, old_writers_stopped: bool):
    from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_export import proof_enabled
    if not enabled() or not proof_enabled() or old_writers_stopped is not True or _locked(db) != "mirror" or not _clean(db):
        raise ValueError("activity preparation unavailable")
    if db.execute(select(preparation.c.singleton).where(preparation.c.singleton == 1)).first():
        raise ValueError("activity preparation already exists")
    high = db.execute(text("SELECT COALESCE(MAX(id),0) FROM user_activity_days")).scalar_one()
    db.execute(insert(preparation).values(singleton=1, high_watermark=high, cursor=0, sealed=False))
    return {"high_watermark": high}


def prepare_page(db, *, limit: int = 100):
    from pymentor_core.adapters.sql.analytics_delivery_ops.integration import guard_lifecycle, sync_prepared_day
    if not enabled() or not 1 <= limit <= 100 or _locked(db) != "mirror":
        raise ValueError("activity preparation unavailable")
    state = db.execute(select(preparation).where(preparation.c.singleton == 1).with_for_update()).mappings().one()
    if state["sealed"]:
        raise ValueError("activity preparation sealed")
    query = text("""SELECT d.id,d.user_id,d.activity_date,d.visited_at,a.lifecycle_id
        FROM user_activity_days d LEFT JOIN account_lifecycles a ON a.user_id=d.user_id
        WHERE d.id>:cursor AND d.id<=:high ORDER BY d.id LIMIT :limit""").columns(
            id=BigInteger, user_id=BigInteger, activity_date=Date, visited_at=DateTime, lifecycle_id=String)
    rows = db.execute(query, {"cursor": state["cursor"], "high": state["high_watermark"], "limit": limit}).mappings().all()
    for row in rows:
        ref = guard_lifecycle(db, row["user_id"], row["lifecycle_id"])
        sync_prepared_day(db, ref, row["activity_date"], row["visited_at"])
    cursor = rows[-1]["id"] if rows else state["cursor"]
    db.execute(update(preparation).where(preparation.c.singleton == 1).values(cursor=cursor))
    return {"count": len(rows), "cursor": cursor, "high_watermark": state["high_watermark"], "scan_exhausted": len(rows) < limit}


def seal_preparation(db):
    if not enabled() or _locked(db) != "mirror" or not _clean(db):
        raise ValueError("activity preparation unavailable")
    state = db.execute(select(preparation).where(preparation.c.singleton == 1).with_for_update()).mappings().one()
    if db.execute(text("SELECT 1 FROM user_activity_days WHERE id>:cursor AND id<=:high LIMIT 1"),
                  {"cursor": state["cursor"], "high": state["high_watermark"]}).first():
        raise ValueError("activity preparation has unscanned rows")
    db.execute(update(preparation).where(preparation.c.singleton == 1).values(sealed=True))


def begin_outbox(db, *, writers_stopped: bool, coverage_proved: bool):
    if not enabled() or writers_stopped is not True or coverage_proved is not True:
        raise ValueError("cutover prerequisites not asserted")
    mode = _locked(db)
    if mode == "outbox_only":
        return
    if mode != "mirror" or not _clean(db) or db.execute(select(preparation.c.sealed).where(preparation.c.singleton == 1)).scalar_one_or_none() is not True:
        raise ValueError("activity gate is not ready")
    db.execute(update(write_gate).where(write_gate.c.singleton == 1).values(mode="outbox_only"))


def begin_replay(db, *, writers_stopped: bool):
    if not enabled() or writers_stopped is not True:
        raise ValueError("writers must be stopped")
    mode = _locked(db)
    if mode == "replaying":
        return
    if mode != "outbox_only":
        raise ValueError("activity gate is not in outbox-only mode")
    db.execute(update(write_gate).where(write_gate.c.singleton == 1).values(mode="replaying"))


def finish_replay(db, *, writers_stopped: bool):
    if not enabled() or writers_stopped is not True:
        raise ValueError("writers must be stopped")
    mode = _locked(db)
    if mode not in {"replaying", "mirror"} or not _clean(db):
        raise ValueError("activity replay remains incomplete")
    if mode == "replaying":
        db.execute(update(write_gate).where(write_gate.c.singleton == 1).values(mode="mirror"))
        from sqlalchemy import delete
        db.execute(delete(preparation).where(preparation.c.singleton == 1))
