"""Inspect/apply lifecycle-pinned local course leader bindings; no owner transfer."""
import argparse
import json
from collections import Counter
from uuid import uuid4

from sqlalchemy import create_engine, text

from pymentor_core.adapters.course_local.runtime import default_runtime
from tools.maintenance.role_admin_bootstrap import allowlisted_url

LEGACY_ADMINS_MARKER = 'legacy-admins-to-leaders-v1'


def migrate_legacy_admins(conn, config, *, apply=False):
    """Atomically map every legacy admin to exactly one configured course, once."""
    conn.execute(text("SET LOCAL lock_timeout='5s'"))
    conn.execute(text("SET LOCAL statement_timeout='30s'"))
    conn.execute(text('SELECT pg_advisory_xact_lock(528, 527)'))
    conn.execute(text('LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE'))
    if conn.execute(text('SELECT 1 FROM role_admin_bootstrap_markers WHERE marker=:marker'),
                    {'marker': LEGACY_ADMINS_MARKER}).first():
        return [{'status': 'blocked', 'reason': 'legacy_admins_already_migrated'}]
    admins = conn.execute(text('SELECT id, verified FROM users WHERE is_admin ORDER BY id FOR UPDATE')).mappings().all()
    counts = Counter(uid for uid in config.values() if uid is not None)
    conflicts = [{'status': 'blocked', 'reason': 'admin_mapping_not_unique', 'user_id': user['id']}
                 for user in admins if counts[user['id']] != 1]
    conflicts += [{'status': 'blocked', 'reason': 'account_unverified', 'user_id': user['id']}
                  for user in admins if not user['verified']]
    if conflicts:
        return conflicts
    # Reconcile sees the proposed role state. Roll back even the preview, and
    # roll back every proposed demotion if any configured course conflicts.
    savepoint = conn.begin_nested()
    try:
        for user in admins:
            conn.execute(text('UPDATE users SET is_admin=false WHERE id=:uid'), {'uid': user['id']})
        result = reconcile(conn, config, apply=True)
        if any(item['status'] == 'blocked' for item in result):
            savepoint.rollback()
            return result
        if not apply:
            savepoint.rollback()
            for item in result:
                if item['status'] == 'applied':
                    item['status'] = 'ready'
            return result or [{'status': 'ready', 'reason': 'no_configured_courses'}]
        conn.execute(text('INSERT INTO role_admin_bootstrap_markers(marker) VALUES (:marker)'),
                     {'marker': LEGACY_ADMINS_MARKER})
        savepoint.commit()
        return result or [{'status': 'applied', 'reason': 'no_configured_courses'}]
    except Exception:
        if savepoint.is_active:
            savepoint.rollback()
        raise


def reconcile(conn, config, *, apply=False):
    """Caller owns transaction; fail closed without partial writes on any conflict."""
    conn.execute(text("SET LOCAL lock_timeout='5s'"))
    conn.execute(text("SET LOCAL statement_timeout='30s'"))
    conn.execute(text('LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE'))
    duplicates = {uid for uid, n in Counter(uid for uid in config.values() if uid is not None).items() if n > 1}
    results = []
    for course, uid in sorted(config.items()):
        conn.execute(text('SELECT pg_advisory_xact_lock(528,hashtext(:course))'), {'course': course})
        binding = conn.execute(text('SELECT user_id,account_ref FROM course_leader_bindings WHERE course_id=:course FOR UPDATE'), {'course':course}).mappings().one_or_none()
        active = conn.execute(text('SELECT user_id,account_ref FROM course_leadership WHERE course_id=:course AND revoked_at IS NULL FOR UPDATE'), {'course':course}).mappings().one_or_none()
        legacy_marker = (conn.execute(text("SELECT user_id,account_ref FROM role_admin_bootstrap_markers WHERE marker='backend-leader-v1' FOR UPDATE")).mappings().one_or_none()
                         if course == 'backend' else None)
        reason = None
        user = None
        if uid is None:
            reason = 'active_without_config' if active else None
        elif uid in duplicates:
            reason = 'duplicate_configured_user'
        else:
            user = conn.execute(text('''SELECT u.id,u.verified,u.is_admin,a.lifecycle_id FROM users u
                LEFT JOIN account_lifecycles a ON a.user_id=u.id WHERE u.id=:uid FOR UPDATE OF u'''), {'uid':uid}).mappings().one_or_none()
            if user is None: reason = 'account_missing'
            elif not user['verified']: reason = 'account_unverified'
            elif user['is_admin']: reason = 'admin_collision'
            elif binding and binding['user_id'] is None: reason = 'binding_tombstoned'
            elif binding and (binding['user_id'] != uid or binding['account_ref'] != user['lifecycle_id']): reason = 'binding_changed'
            elif legacy_marker and (legacy_marker['user_id'] is None or legacy_marker['account_ref'] is None): reason = 'legacy_seed_tombstoned'
            elif legacy_marker and (legacy_marker['user_id'] != uid or legacy_marker['account_ref'] != user['lifecycle_id']): reason = 'legacy_seed_changed'
            elif legacy_marker and not active: reason = 'legacy_seed_revoked'
            elif active and (active['user_id'] != uid or active['account_ref'] != user['lifecycle_id']): reason = 'active_owner_conflict'
            elif binding and not active: reason = 'projection_revoked'
            elif conn.execute(text('SELECT EXISTS(SELECT 1 FROM course_leadership WHERE user_id=:uid AND course_id<>:course AND revoked_at IS NULL)'), {'uid':uid,'course':course}).scalar_one(): reason = 'user_assigned_elsewhere'
            elif not active and conn.execute(text('SELECT EXISTS(SELECT 1 FROM course_leadership WHERE course_id=:course AND user_id=:uid AND revoked_at IS NOT NULL)'), {'course':course,'uid':uid}).scalar_one(): reason = 'previously_revoked'
            elif conn.execute(text('''SELECT EXISTS(SELECT 1 FROM booking_slots WHERE course_id=:course AND archived_at IS NULL
                AND (slot_date+start_time::time)>(now() AT TIME ZONE 'Europe/Moscow') AND calendar_owner_id IS DISTINCT FROM :uid)'''), {'course':course,'uid':uid}).scalar_one(): reason = 'future_owner_conflict'
        results.append({'course_id':course,'user_id':uid,'status':'blocked' if reason else 'unconfigured' if uid is None else 'ready', 'reason':reason})
    if not apply or any(x['status'] == 'blocked' for x in results):
        return results
    for item in results:
        uid, course = item['user_id'], item['course_id']
        if uid is None: continue
        ref = conn.execute(text('SELECT lifecycle_id FROM account_lifecycles WHERE user_id=:uid'), {'uid':uid}).scalar_one_or_none()
        if ref is None:
            ref = str(uuid4())
            conn.execute(text('INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (:uid,:ref)'), {'uid':uid,'ref':ref})
        conn.execute(text('INSERT INTO course_leader_bindings(course_id,user_id,account_ref) VALUES (:course,:uid,:ref) ON CONFLICT (course_id) DO NOTHING'), {'course':course,'uid':uid,'ref':ref})
        active = conn.execute(text('SELECT 1 FROM course_leadership WHERE course_id=:course AND revoked_at IS NULL'), {'course':course}).scalar_one_or_none()
        if not active:
            revision = conn.execute(text('SELECT COALESCE((SELECT revision FROM role_admin_revisions WHERE user_id=:uid),0)+1'), {'uid':uid}).scalar_one()
            conn.execute(text('INSERT INTO course_leadership(course_id,user_id,account_ref,revision) VALUES (:course,:uid,:ref,:revision)'), {'course':course,'uid':uid,'ref':ref,'revision':revision})
        item['status'] = 'applied'
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--dry-run', action='store_true')
    group.add_argument('--apply', action='store_true')
    parser.add_argument('--migrate-legacy-admins', action='store_true',
                        help='one-time migration of all legacy admins to configured course leaders')
    args = parser.parse_args()
    url = allowlisted_url(args.database_url)
    runtime = default_runtime()
    config = dict(runtime.leader_user_ids)
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as conn:
            database, user, version = conn.execute(text("SELECT current_database(),current_user,current_setting('server_version_num')::int")).one()
            if (database, user) != (url.database, 'pymentor_test') or not 160000 <= version < 170000:
                raise RuntimeError('Connected database is not the allowlisted PostgreSQL 16 test instance')
            result = (migrate_legacy_admins(conn, config, apply=args.apply)
                      if args.migrate_legacy_admins else reconcile(conn, config, apply=args.apply))
        print(json.dumps(result, ensure_ascii=False))
        return int(any(item['status'] == 'blocked' for item in result))
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
