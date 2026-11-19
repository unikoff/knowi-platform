"""Authorize short-lived Analytics grants using current Core identity and roster."""
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text

from pymentor_core.app.course_context import enabled_course_ids
from pymentor_core.app.wiring import role_admin_commands, role_admin_reads


PLATFORM_TOOL_SCOPE = "platform-tools"


def authorize_student_statistics_grant(
    db, principal, *, course_id: str, user_id: int, target_ref: str, selection_revision: int | None,
):
    """Authorize one student's statistics in one course, using the same lock as Core."""
    if course_id == PLATFORM_TOOL_SCOPE:
        raise HTTPException(404, "Course not found")
    reads = role_admin_reads()
    reads.authorize(db, principal, course_id)
    role = reads.lock_statistics_target(
        db, principal, course_id, user_id, target_ref, selection_revision, bookings_only=True,
    )
    return {
        "subject": principal.account_ref,
        "role": role,
        "scope": "report:student",
        "courses": [course_id],
        "subjects": [target_ref],
        "global_read": False,
        "target_subject_ref": target_ref,
    }


def authorize_grant(db, principal, *, purpose: str, course_id: str | None, target_ref: str | None,
                    target_user_id: int | None = None):
    commands = role_admin_commands()
    reads = role_admin_reads()
    if not principal.account_ref:
        raise HTTPException(401, "Account lifecycle unavailable")
    if course_id == PLATFORM_TOOL_SCOPE and purpose != "collect":
        raise HTTPException(404, "Course not found")
    actor = db.execute(text('''SELECT u.is_admin,l.course_id FROM users u
        JOIN account_lifecycles a ON a.user_id=u.id
        LEFT JOIN course_leadership l ON l.user_id=u.id AND l.account_ref=a.lifecycle_id AND l.revoked_at IS NULL
        WHERE u.id=:uid AND a.lifecycle_id=:ref'''),
        {"uid": principal.user_id, "ref": principal.account_ref}).mappings().one_or_none()
    if actor is None:
        raise HTTPException(401, "Account lifecycle changed")
    role = "admin" if actor["is_admin"] else "leader" if actor["course_id"] else "user"
    if purpose == "self_report":
        if target_ref is not None or target_user_id is not None:
            raise HTTPException(422, "Invalid self report")
        if role == "user":
            return authorize_grant(db, principal, purpose="report", course_id=course_id, target_ref=None)
        if course_id is not None:
            raise HTTPException(403, "Analytics grant denied")
        users = commands._lock_users(db, [principal.user_id])
        actual_role, _, _ = commands._authority(db, principal, users, actor["course_id"] if role == "leader" else None)
        if actual_role != role:
            raise HTTPException(403, "Analytics grant denied")
        return {"subject": principal.account_ref, "role": role, "scope": "report:read",
                "courses": [actor["course_id"]] if role == "leader" else [], "subjects": [], "global_read": role == "admin"}
    if target_user_id is not None and (type(target_user_id) is not int or target_user_id <= 0 or target_ref or purpose != "report"):
        raise HTTPException(422, "Invalid target")
    if role == "user" and purpose in {"collect", "report"} and not target_ref:
        if target_user_id is not None and target_user_id != principal.user_id:
            raise HTTPException(403, "Analytics grant denied")
        users = commands._lock_users(db, [principal.user_id])
        current = users.get(principal.user_id)
        if (current is None or current['is_admin'] or commands._ref(db, principal.user_id) != principal.account_ref
                or db.execute(text('SELECT EXISTS(SELECT 1 FROM course_leadership WHERE user_id=:uid AND account_ref=:ref AND revoked_at IS NULL)'),
                              {'uid': principal.user_id, 'ref': principal.account_ref}).scalar_one()):
            raise HTTPException(403, "Analytics grant denied")
        if course_id == PLATFORM_TOOL_SCOPE:
            courses = [PLATFORM_TOOL_SCOPE]
        else:
            if not course_id and purpose == "collect":
                raise HTTPException(422, "Course required")
            courses = sorted(value for value in enabled_course_ids(course_id or None) if value != PLATFORM_TOOL_SCOPE)
            if not courses or len(courses) > 100:
                raise HTTPException(503, "Course scope unavailable")
        result = {"subject": principal.account_ref, "role": "user",
                "scope": "collect" if purpose == "collect" else "report:read",
                "courses": courses, "subjects": [], "global_read": False}
        if target_user_id is not None:
            result["target_subject_ref"] = principal.account_ref
        return result
    if role == "admin" and purpose == "report":
        if course_id:
            reads._enabled(course_id)
        target_id = target_user_id
        if target_ref:
            try:
                if str(UUID(target_ref)) != target_ref:
                    raise ValueError
            except (ValueError, TypeError):
                raise HTTPException(422, "Invalid subject") from None
            target_id = db.execute(text("SELECT user_id FROM account_lifecycles WHERE lifecycle_id=:ref"),
                                   {"ref": target_ref}).scalar_one_or_none()
            if target_id is None:
                raise HTTPException(404, "Account not found")
        users = commands._lock_users(db, [principal.user_id] + ([target_id] if target_id is not None else []))
        commands._authority(db, principal, users, None)
        if target_id is not None and target_id not in users:
            raise HTTPException(404, "Account not found")
        reference = commands._ref(db, target_id) if target_id is not None else None
        if target_id is not None and reference is None:
            raise HTTPException(503, "Account lifecycle unavailable")
        if target_ref is not None and reference != target_ref:
            raise HTTPException(404, "Account not found")
        result = {"subject": principal.account_ref, "role": "admin", "scope": "report:read",
                "courses": [], "subjects": [], "global_read": True}
        if target_user_id is not None:
            result["target_subject_ref"] = reference
        return result
    if role == "leader" and purpose == "report" and course_id:
        reads.authorize(db, principal, course_id)
        if target_ref:
            try:
                if str(UUID(target_ref)) != target_ref:
                    raise ValueError
            except (ValueError, TypeError):
                raise HTTPException(422, "Invalid subject") from None
        def roster():
            target_filter = " AND a.lifecycle_id=:ref" if target_ref else " AND u.id=:uid" if target_user_id is not None else ""
            rows = db.execute(text('''SELECT DISTINCT u.id, a.lifecycle_id FROM users u
                JOIN booking_history h ON h.student_id=u.id AND h.course_id=:course
                LEFT JOIN account_lifecycles a ON a.user_id=u.id
                WHERE TRUE''' + target_filter + ''' ORDER BY u.id LIMIT 101'''),
                {"course": course_id, "ref": target_ref, "uid": target_user_id}).mappings().all()
            if len(rows) > 100:
                raise HTTPException(503, "Roster exceeds Analytics grant limit")
            if any(r['lifecycle_id'] is None for r in rows):
                raise HTTPException(503, "Roster lifecycle unavailable")
            return [(r['id'], r['lifecycle_id']) for r in rows]
        selected = roster()
        if (target_ref or target_user_id is not None) and not selected:
            raise HTTPException(404, "Roster member not found")
        users = commands._lock_users(db, [principal.user_id, *(row[0] for row in selected)])
        actual_role, _, _ = commands._authority(db, principal, users, course_id)
        if actual_role != "leader" or any(commands._ref(db, uid) != ref for uid, ref in selected):
            raise HTTPException(403, "Course access denied")
        reads._require_reconciled(db)
        if roster() != selected:
            raise HTTPException(503, "Roster changed; retry")
        result = {"subject": principal.account_ref, "role": "leader", "scope": "report:read",
                "courses": [course_id], "subjects": [ref for _, ref in selected], "global_read": False}
        if target_user_id is not None:
            result["target_subject_ref"] = selected[0][1]
        return result
    raise HTTPException(403, "Analytics grant denied")
