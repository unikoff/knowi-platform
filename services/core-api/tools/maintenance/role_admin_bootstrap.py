"""Rehearse the one-time backend leadership bootstrap on the disposable local DB only."""
from __future__ import annotations

import argparse
import json
import re
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from pymentor_core.adapters.course_local.runtime import default_runtime

MARKER = 'backend-leader-v1'
EMAIL = 'kiryukhin_nik@bk.ru'
COURSE = 'backend'


def allowlisted_url(raw: str):
    try:
        url = make_url(raw)
        valid = (url.drivername == 'postgresql+psycopg' and url.host == '127.0.0.1'
                 and url.port == 57382 and url.username == 'pymentor_test'
                 and (url.database == 'pymentor_test' or bool(re.fullmatch(r'c527_role_ci_[0-9a-f]{32}', url.database or '')))
                 and not url.query)
    except Exception:
        valid = False
    if not valid:
        raise ValueError('Target rejected: only the dedicated local disposable PostgreSQL is allowed') from None
    return url


def bootstrap(conn, *, apply: bool, demote_admin: bool = False, course_enabled: bool = True):
    """Caller owns the transaction; a blocked/dry-run call never mutates rows."""
    conn.execute(text("SET LOCAL lock_timeout = '5s'"))
    conn.execute(text("SET LOCAL statement_timeout = '30s'"))
    # Serializes concurrent seed attempts, including absent marker/user rows.
    conn.execute(text('SELECT pg_advisory_xact_lock(527, 1)'))
    marker = conn.execute(text('SELECT user_id, account_ref FROM role_admin_bootstrap_markers WHERE marker=:marker FOR UPDATE'), {'marker': MARKER}).mappings().first()
    if marker:
        return {'status': 'already_applied', 'user_id': marker['user_id'], 'account_ref': marker['account_ref']}
    enabled = conn.execute(text('SELECT scoped_enabled FROM role_admin_cutover WHERE id=1 FOR SHARE')).scalar_one_or_none()
    if enabled is not False:
        return {'status': 'blocked', 'reason': 'scoped_cutover'}
    if not course_enabled:
        return {'status': 'blocked', 'reason': 'course_disabled'}
    # Blocks existing admin writers throughout the last-admin test and optional demotion.
    conn.execute(text('LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE'))
    users = conn.execute(text('SELECT id, verified, is_admin FROM users WHERE lower(email)=:email ORDER BY id FOR UPDATE'), {'email': EMAIL}).mappings().all()
    if len(users) != 1:
        return {'status': 'blocked', 'reason': 'account_missing' if not users else 'account_ambiguous'}
    user = users[0]
    if not user['verified']:
        return {'status': 'blocked', 'reason': 'account_unverified'}
    if user['is_admin']:
        if not demote_admin:
            return {'status': 'blocked', 'reason': 'admin_collision'}
        others = conn.execute(text('SELECT count(*) FROM users WHERE is_admin AND id<>:uid'), {'uid': user['id']}).scalar_one()
        if not others:
            return {'status': 'blocked', 'reason': 'last_admin'}
    conflicting = conn.execute(text('SELECT id FROM course_leadership WHERE revoked_at IS NULL AND (course_id=:course OR user_id=:uid) FOR UPDATE'), {'course': COURSE, 'uid': user['id']}).first()
    if conflicting:
        return {'status': 'blocked', 'reason': 'assignment_conflict'}
    account_ref = conn.execute(text('SELECT lifecycle_id FROM account_lifecycles WHERE user_id=:uid'), {'uid': user['id']}).scalar_one_or_none()
    if not apply:
        return {'status': 'ready', 'user_id': user['id'], 'account_ref': account_ref, 'needs_lifecycle': account_ref is None, 'demote_admin': bool(user['is_admin'])}
    if account_ref is None:
        account_ref = str(uuid4())
        conn.execute(text('INSERT INTO account_lifecycles(user_id, lifecycle_id) VALUES (:uid,:ref)'), {'uid': user['id'], 'ref': account_ref})
    if user['is_admin']:
        conn.execute(text('UPDATE users SET is_admin=false WHERE id=:uid'), {'uid': user['id']})
    # The leadership trigger owns the increment; predict its next value for the
    # assignment row and then read the authoritative value after insertion.
    revision = conn.execute(text('SELECT COALESCE((SELECT revision FROM role_admin_revisions WHERE user_id=:uid),0)+1'), {'uid': user['id']}).scalar_one()
    conn.execute(text('''INSERT INTO course_leadership(course_id,user_id,account_ref,revision)
        VALUES (:course,:uid,:ref,:revision)'''), {'course': COURSE, 'uid': user['id'], 'ref': account_ref, 'revision': revision})
    revision = conn.execute(text('SELECT revision FROM role_admin_revisions WHERE user_id=:uid'), {'uid': user['id']}).scalar_one()
    conn.execute(text('INSERT INTO role_admin_bootstrap_markers(marker,user_id,account_ref) VALUES (:marker,:uid,:ref)'), {'marker': MARKER, 'uid': user['id'], 'ref': account_ref})
    conn.execute(text('''INSERT INTO role_admin_audit(command_id,actor_role,target_user_id,target_account_ref,scope,course_id,action,reason,subject_user_ids)
        VALUES (:cmd,'system',:uid,:ref,'course',:course,'bootstrap_leader',:reason,ARRAY[:uid])'''),
        {'cmd': str(uuid4()), 'uid': user['id'], 'ref': account_ref, 'course': COURSE,
         'reason': 'one-time bootstrap; admin demotion' if user['is_admin'] else 'one-time bootstrap'})
    return {'status': 'applied', 'user_id': user['id'], 'account_ref': account_ref, 'revision': revision}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--dry-run', action='store_true')
    group.add_argument('--apply', action='store_true')
    parser.add_argument('--demote-admin', action='store_true')
    args = parser.parse_args()
    url = allowlisted_url(args.database_url)
    try:
        default_runtime().registry.require_enabled(COURSE)
        enabled = True
    except Exception:
        enabled = False
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as conn:
            database, user, version = conn.execute(text("SELECT current_database(),current_user,current_setting('server_version_num')::int")).one()
            if (database, user) != (url.database, 'pymentor_test') or not 160000 <= version < 170000:
                raise RuntimeError('Connected database is not the allowlisted PostgreSQL 16 test instance')
            result = bootstrap(conn, apply=args.apply, demote_admin=args.demote_admin, course_enabled=enabled)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result['status'] == 'blocked' else 0
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
