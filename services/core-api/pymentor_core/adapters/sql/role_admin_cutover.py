"""Transactional barrier between legacy and scoped writers."""
from fastapi import HTTPException
from sqlalchemy import text


def require_mode(db, *, scoped):
    # The shared lock remains held until the caller's transaction ends.  The
    # operator's exclusive lock cannot switch modes in the middle of a write.
    enabled = db.execute(text('SELECT scoped_enabled FROM role_admin_cutover WHERE id=1 FOR SHARE')).scalar_one_or_none()
    if enabled is None or bool(enabled) != scoped:
        detail = 'Scoped writes unavailable until cutover' if scoped else 'Legacy booking writes retired after cutover'
        raise HTTPException(status_code=503, detail=detail)


def lock_mode(db):
    enabled = db.execute(text('SELECT scoped_enabled FROM role_admin_cutover WHERE id=1 FOR SHARE')).scalar_one_or_none()
    if enabled is None:
        raise HTTPException(status_code=503, detail='Cutover state unavailable')
    return bool(enabled)
