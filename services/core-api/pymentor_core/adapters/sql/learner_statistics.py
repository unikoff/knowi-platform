"""Read only the authenticated account's source facts in one SQL snapshot."""
from datetime import datetime, time, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from pymentor_core.adapters.sql import models
from pymentor_core.modules.identity.activity_policy import MOSCOW_TZ


def identity_revision(db, principal):
    ref = principal.account_ref
    try:
        if not ref or str(UUID(ref)) != ref:
            raise ValueError
    except (TypeError, ValueError):
        raise HTTPException(401, "Account lifecycle unavailable") from None
    row = db.execute(text('''SELECT COALESCE(r.revision, 0) AS revision
        FROM users u JOIN account_lifecycles a ON a.user_id = u.id
        LEFT JOIN role_admin_revisions r ON r.user_id = u.id
        WHERE u.id = :uid AND a.lifecycle_id = :ref'''),
        {"uid": principal.user_id, "ref": ref}).one_or_none()
    if row is None:
        raise HTTPException(401, "Account lifecycle changed")
    return row.revision


def read_completions(db, principal, course_id, start, end):
    """Return bounded, deduplicated Core completions for this account and course."""
    query = (db.query(models.UserLessonProgress.course_id, models.UserLessonProgress.lesson_id,
                      models.UserLessonProgress.completed_at)
        .filter(models.UserLessonProgress.user_id == principal.user_id,
                models.UserLessonProgress.completed_at >= start,
                models.UserLessonProgress.completed_at <= end))
    if course_id is not None:
        query = query.filter(models.UserLessonProgress.course_id == course_id)
    rows = query.order_by(models.UserLessonProgress.id).limit(5001).all()
    if len(rows) > 5000:
        raise HTTPException(503, "Statistics unavailable")
    first = {}
    for course, lesson, completed_at in rows:
        stamp = completed_at if completed_at.tzinfo else completed_at.replace(tzinfo=timezone.utc)
        key = (course, lesson)
        if key not in first or stamp < first[key]:
            first[key] = stamp
    return tuple(sorted((course, lesson, stamp.isoformat()) for (course, lesson), stamp in first.items()))


def read_snapshot(db, principal, days, course_id, now):
    # This must be the first statement on the fresh PostgreSQL transaction.
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    revision = identity_revision(db, principal)
    today = now.astimezone(MOSCOW_TZ).date()
    start = datetime.combine(today - timedelta(days=days - 1), time.min, MOSCOW_TZ).astimezone(timezone.utc)
    activity = (db.query(models.UserActivityDay.activity_date)
        .filter(models.UserActivityDay.user_id == principal.user_id,
                models.UserActivityDay.activity_date <= today)
        .order_by(models.UserActivityDay.activity_date.desc()).limit(10001).all())
    query = (db.query(models.UserLessonProgress.course_id, models.UserLessonProgress.lesson_id,
                      models.UserLessonProgress.completed_at)
        .filter(models.UserLessonProgress.user_id == principal.user_id,
                models.UserLessonProgress.completed_at >= start,
                models.UserLessonProgress.completed_at <= now))
    if course_id is not None:
        query = query.filter(models.UserLessonProgress.course_id == course_id)
    completions = query.order_by(models.UserLessonProgress.id).limit(5001).all()
    if len(completions) > 5000:
        raise HTTPException(503, "Statistics unavailable")
    def timestamp(value):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    return revision, {
        "schema_version": 1, "subject_ref": principal.account_ref,
        "snapshot_at": now.isoformat(), "period_days": days, "course_id": course_id,
        "activity_complete": len(activity) <= 10000,
        "activity_dates": [row[0].isoformat() for row in activity[:10000]],
        "lesson_completions": [{"course_id": row[0], "lesson_id": row[1],
                                "completed_at": timestamp(row[2])} for row in completions],
    }
