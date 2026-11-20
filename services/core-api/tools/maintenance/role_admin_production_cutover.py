"""Guarded backend-only cutover primitives. The caller owns connection and commit.

Call plan() and retain its digest, then call cutover() in a new transaction with
that digest. Never log the underlying rows or manifest. A rehearsal can execute
cutover and explicitly roll back the outer transaction; PostgreSQL sequences are
not transactional and may have gaps after rollback.
"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from sqlalchemy import text

from tools.maintenance.course_leader_config import migrate_legacy_admins
from tools.maintenance.role_admin_reconcile import (
    activate_scoped, quota_snapshot, reconcile, slot_snapshot,
)

COURSE = 'backend'
REVISION = 'c528_course_leaders'


def _plan(conn, admin_user_id, course_enabled):
    if not conn.in_transaction():
        raise ValueError('An explicit caller-owned transaction is required')
    if type(admin_user_id) is not int or admin_user_id <= 0:
        raise ValueError('Invalid admin ID')
    if not course_enabled(COURSE):
        raise ValueError('Backend course is disabled')
    conn.execute(text("SET LOCAL lock_timeout = '5s'"))
    conn.execute(text("SET LOCAL statement_timeout = '30s'"))
    if conn.execute(text('SELECT version_num FROM alembic_version')).scalars().all() != [REVISION]:
        raise ValueError('Unexpected database revision')
    if conn.execute(text('SELECT scoped_enabled FROM role_admin_cutover WHERE id=1 FOR UPDATE')).scalar_one_or_none() is not False:
        raise ValueError('Expected legacy writer mode')
    # Gate first drains cooperating legacy writers; table locks also guard the
    # snapshot against direct writers until the caller ends the transaction.
    conn.execute(text('LOCK TABLE users, booking_slots, booking_quotas, booking_history, '
                      'course_leadership, course_leader_bindings, account_lifecycles, '
                      'role_admin_bootstrap_markers, role_admin_revisions IN SHARE ROW EXCLUSIVE MODE'))
    admins = conn.execute(text('SELECT id, verified FROM users WHERE is_admin ORDER BY id')).all()
    if len(admins) != 1 or admins[0].id != admin_user_id or admins[0].verified is not True:
        raise ValueError('Expected one verified legacy admin')
    if conn.execute(text('SELECT EXISTS(SELECT 1 FROM course_leadership WHERE revoked_at IS NULL)')).scalar_one():
        raise ValueError('Active leadership already exists')
    if conn.execute(text('SELECT EXISTS(SELECT 1 FROM course_leader_bindings)')).scalar_one():
        raise ValueError('Existing leader binding')
    slots = conn.execute(text('SELECT * FROM booking_slots ORDER BY id')).mappings().all()
    quotas = conn.execute(text('SELECT * FROM booking_quotas ORDER BY id')).mappings().all()
    manifest = {'slots': [], 'quotas': []}
    state = {'admin_id': admin_user_id, 'revision': REVISION, 'slots': [], 'quotas': []}
    for row in slots:
        scope = (row['course_id'], row['calendar_owner_id'])
        if scope not in ((None, None), (COURSE, admin_user_id)):
            raise ValueError('Partial or conflicting slot scope')
        snap = slot_snapshot(row)
        state['slots'].append({'expected': snap, 'scope': scope, 'archived_at': row['archived_at']})
        if scope == (None, None):
            manifest['slots'].append({'expected': snap, 'course_id': COURSE, 'owner_id': admin_user_id})
    for row in quotas:
        if row['course_id'] not in (None, COURSE):
            raise ValueError('Conflicting quota scope')
        snap = quota_snapshot(row)
        state['quotas'].append({'expected': snap, 'course_id': row['course_id']})
        if row['course_id'] is None:
            manifest['quotas'].append({'expected': snap, 'course_id': COURSE})
    # The legacy helper uses ON CONFLICT DO NOTHING for coverage. Refuse any
    # existing history on unmapped slots so it cannot silently mask a conflict.
    if conn.execute(text('''SELECT EXISTS(SELECT 1 FROM booking_history h
        JOIN booking_slots s ON s.id=h.slot_id WHERE s.course_id IS NULL)''')).scalar_one():
        raise ValueError('History exists for an unscoped slot')
    state['history'] = [dict(row) for row in conn.execute(
        text('SELECT * FROM booking_history ORDER BY id')).mappings()]
    state['lifecycle'] = [dict(row) for row in conn.execute(
        text('SELECT * FROM account_lifecycles WHERE user_id=:uid ORDER BY user_id'),
        {'uid': admin_user_id}).mappings()]
    state['markers'] = [dict(row) for row in conn.execute(
        text('SELECT * FROM role_admin_bootstrap_markers ORDER BY marker')).mappings()]
    state['leadership'] = [dict(row) for row in conn.execute(
        text('SELECT * FROM course_leadership ORDER BY id')).mappings()]
    state['revisions'] = [dict(row) for row in conn.execute(
        text('SELECT * FROM role_admin_revisions WHERE user_id=:uid'),
        {'uid': admin_user_id}).mappings()]
    digest = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':'),
                                       default=str).encode('utf-8')).hexdigest()
    return digest, manifest


def plan(conn, *, admin_user_id, course_enabled):
    """Return only a digest and counts; locks persist until caller ends transaction."""
    digest, manifest = _plan(conn, admin_user_id, course_enabled)
    return {'sha256': digest, 'slots': len(manifest['slots']), 'quotas': len(manifest['quotas'])}


def cutover(conn, *, admin_user_id, expected_sha256, course_enabled):
    """Apply the exact plan in a savepoint; caller must commit or roll back.

    A successful return is not a commit. Any exception rolls back all writes in
    this function, including the role, history, quotas and writer gate.
    """
    if not conn.in_transaction():
        raise ValueError('An explicit caller-owned transaction is required')
    with conn.begin_nested():
        digest, manifest = _plan(conn, admin_user_id, course_enabled)
        if digest != expected_sha256:
            raise ValueError('Plan digest changed')
        roles = migrate_legacy_admins(conn, {COURSE: admin_user_id}, apply=True)
        if len(roles) != 1 or roles[0]['status'] != 'applied':
            raise ValueError('Legacy admin migration blocked')
        result = reconcile(conn, manifest, apply=True, course_enabled=course_enabled)
        if result['status'] != 'applied':
            raise ValueError('Legacy scope reconciliation blocked')
        gate = activate_scoped(conn, apply=True, confirmed=True, course_enabled=course_enabled)
        if gate['status'] != 'applied' or not gate['inventory']['cutover_ready']:
            raise ValueError('Scoped writer activation blocked')
        conn.execute(text('''INSERT INTO role_admin_audit
            (command_id, actor_role, target_user_id, target_account_ref, scope, course_id,
             action, before_value, after_value, reason, subject_user_ids)
            SELECT :command, 'system', user_id, account_ref, 'course', course_id,
                   'bootstrap_leader', json_build_object('role', 'admin'),
                   json_build_object('role', 'leader'), 'Legacy backend calendar cutover', ARRAY[user_id]
            FROM course_leader_bindings WHERE course_id=:course AND user_id=:uid
            RETURNING id'''), {'command': str(uuid4()), 'course': COURSE, 'uid': admin_user_id}).scalar_one()
        return {'status': 'applied_pending_commit', 'sha256': digest,
                'slots': result['slots'], 'quotas': result['quotas']}
