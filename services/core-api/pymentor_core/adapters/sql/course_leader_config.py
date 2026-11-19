"""Validate that a configured course leader matches its lifecycle-pinned SQL projection."""
from fastapi import HTTPException
from sqlalchemy import text

from pymentor_core.adapters.course_local.runtime import default_runtime


def configured_leader(course):
    runtime = default_runtime()
    runtime.registry.require_enabled(course)
    return runtime.leader_user_ids[course]


def state(db, course):
    configured = configured_leader(course)
    row = db.execute(text('''SELECT b.user_id AS pinned_user,b.account_ref AS pinned_ref,
        l.user_id AS active_user,l.account_ref AS active_ref,a.lifecycle_id,
        u.verified,u.is_admin FROM (SELECT CAST(:course AS varchar) AS course_id) c
        LEFT JOIN course_leader_bindings b ON b.course_id=c.course_id
        LEFT JOIN course_leadership l ON l.course_id=c.course_id AND l.revoked_at IS NULL
        LEFT JOIN users u ON u.id=l.user_id
        LEFT JOIN account_lifecycles a ON a.user_id=l.user_id'''), {'course': course}).mappings().one()
    if configured is None:
        return 'unconfigured' if row['active_user'] is None else 'active_without_config'
    if row['pinned_ref'] is None:
        return 'binding_not_applied'
    if row['pinned_user'] is None:
        return 'binding_tombstoned'
    if row['pinned_user'] != configured:
        return 'configured_owner_changed'
    if row['active_user'] != configured or row['active_ref'] != row['pinned_ref']:
        return 'projection_changed_or_revoked'
    if row['lifecycle_id'] != row['pinned_ref']:
        return 'lifecycle_changed'
    if not row['verified'] or row['is_admin']:
        return 'account_not_eligible'
    return 'ready'


def require_projection(db, course):
    result = state(db, course)
    if result not in {'ready', 'unconfigured'}:
        raise HTTPException(status_code=503, detail='Course leader configuration requires reconciliation: ' + result)
    return result
