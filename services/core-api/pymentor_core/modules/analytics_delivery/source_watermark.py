"""Bounded source watermark for a verified Analytics read; never performs I/O over HTTP."""
from uuid import UUID

from sqlalchemy import text

from pymentor_core.modules.analytics_delivery.grants import authorize_grant
from pymentor_core.modules.analytics_delivery.integration import enabled, require_write_gate, write_mode
from pymentor_core.modules.analytics_delivery.snapshot_export import proof_enabled


def capture(db, principal, kind):
    if not enabled() or not proof_enabled():
        raise ValueError("source watermark unavailable")
    require_write_gate(db, write_mode())
    claims = authorize_grant(db, principal, purpose="report" if kind == "admin" else "self_report", course_id=None, target_ref=None)
    if claims["role"] not in ({"admin"} if kind == "admin" else {"user", "leader", "admin"}):
        raise ValueError("source role unavailable")
    if kind != "admin":
        revision = db.execute(text("SELECT revision FROM analytics_transfer_revisions WHERE subject_ref=:ref"),
                              {"ref": claims["subject"]}).scalar_one_or_none()
        return claims, 0 if revision is None else revision
    rows = db.execute(text("""SELECT u.is_admin,a.lifecycle_id,COALESCE(r.revision,0) AS revision
        FROM users u LEFT JOIN account_lifecycles a ON a.user_id=u.id
        LEFT JOIN analytics_transfer_revisions r ON r.subject_ref=a.lifecycle_id
        ORDER BY u.id LIMIT 81""")).mappings().all()
    if len(rows) > 80:
        raise ValueError("source roster limit exceeded")
    members, seen = [], set()
    for row in rows:
        ref, revision = row["lifecycle_id"], row["revision"]
        if (not isinstance(ref, str) or str(UUID(ref)) != ref or ref in seen
                or type(revision) is not int or not 0 <= revision <= (1 << 63) - 1):
            raise ValueError("source roster unavailable")
        seen.add(ref)
        members.append({"subject_ref": ref, "revision": revision, "student": not bool(row["is_admin"])})
    return claims, sorted(members, key=lambda row: row["subject_ref"])


def capture_profile(db, principal, user_id):
    """Authorize and lock admin and target, then snapshot the target generation/revision."""
    if not enabled() or not proof_enabled() or type(user_id) is not int or user_id <= 0:
        raise ValueError("profile source watermark unavailable")
    require_write_gate(db, write_mode())
    claims = authorize_grant(db, principal, purpose="report", course_id=None, target_ref=None, target_user_id=user_id)
    reference = claims.pop("target_subject_ref", None)
    if claims["role"] != "admin" or not isinstance(reference, str) or str(UUID(reference)) != reference:
        raise ValueError("profile source unavailable")
    revision = db.execute(text("SELECT revision FROM analytics_transfer_revisions WHERE subject_ref=:ref"),
                          {"ref": reference}).scalar_one_or_none()
    return claims, reference, 0 if revision is None else revision


def capture_admin_labels(db):
    """Core collation orders equal completion counts; compare before and after HTTP."""
    rows = db.execute(text("""SELECT u.id,u.username,a.lifecycle_id FROM users u
        LEFT JOIN account_lifecycles a ON a.user_id=u.id WHERE u.is_admin=false
        ORDER BY u.username ASC, u.id ASC LIMIT 81""")).all()
    if len(rows) > 80 or any(not isinstance(row.lifecycle_id, str) for row in rows):
        raise ValueError("source labels unavailable")
    return [(row.lifecycle_id, row.id, row.username) for row in rows]


def capture_course(db, principal, course, year, month):
    """Capture booking membership, account generations, revisions and labels under current authority."""
    from pymentor_core.app.wiring import role_admin_reads

    reads = role_admin_reads()
    if not enabled() or not proof_enabled():
        raise ValueError("source watermark unavailable")
    require_write_gate(db, write_mode())
    actor = reads.authorize(db, principal, course)
    reads._require_reconciled(db)
    claims = authorize_grant(db, principal, purpose="report", course_id=course, target_ref=None)
    start, end = reads._bounds(year, month)
    rows = db.execute(text("""SELECT u.id,u.username,u.is_admin,a.lifecycle_id,COALESCE(r.revision,0) AS revision
        FROM users u LEFT JOIN account_lifecycles a ON a.user_id=u.id
        LEFT JOIN analytics_transfer_revisions r ON r.subject_ref=a.lifecycle_id
        WHERE EXISTS(SELECT 1 FROM booking_history h WHERE h.course_id=:course AND h.student_id=u.id AND h.starts_at>=:start AND h.starts_at<:end
            AND (:is_admin OR h.owner_at_booking_id=:actor_id))
        ORDER BY u.username ASC,u.id ASC LIMIT 81"""), {"course": course, "start": start, "end": end, "is_admin": bool(actor["is_admin"]), "actor_id": principal.user_id}).mappings().all()
    if len(rows) > 80:
        raise ValueError("source roster limit exceeded")
    members, labels, seen = [], [], set()
    for row in rows:
        ref, revision = row["lifecycle_id"], row["revision"]
        if (not isinstance(ref, str) or str(UUID(ref)) != ref or ref in seen
                or type(revision) is not int or not 0 <= revision <= (1 << 63) - 1):
            raise ValueError("source roster unavailable")
        seen.add(ref)
        members.append({"subject_ref": ref, "revision": revision, "student": not bool(row["is_admin"])})
        if not row["is_admin"]:
            labels.append((ref, row["id"], row["username"]))
    if claims["role"] == "leader":
        if not seen.issubset(set(claims["subjects"])):
            raise ValueError("source roster changed")
        claims = {**claims, "subjects": sorted(seen)}
    return claims, sorted(members, key=lambda row: row["subject_ref"]), labels, dict(actor)
