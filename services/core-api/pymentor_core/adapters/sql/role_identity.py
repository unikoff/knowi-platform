"""Fresh, server-owned role projection for authentication and staff reads."""
from uuid import uuid4

from sqlalchemy import text

from pymentor_core.modules.identity.access import serialize_user
from pymentor_core.adapters.sql import models

COURSE_CAPABILITIES = ('course.read', 'course.roster', 'course.search_grant',
                       'course.quota.grant', 'course.subscription.extend', 'course.calendar.manage')


def account_ref(db, user_id):
    """Create at most one stable lifecycle for an existing account, then persist it."""
    existing = db.execute(text('SELECT lifecycle_id FROM account_lifecycles WHERE user_id=:uid'), {'uid': user_id}).scalar_one_or_none()
    if existing is None:
        db.execute(text('''INSERT INTO account_lifecycles(user_id,lifecycle_id)
            SELECT id,:ref FROM users WHERE id=:uid ON CONFLICT (user_id) DO NOTHING'''),
                   {'uid': user_id, 'ref': str(uuid4())})
        db.commit()
        existing = db.execute(text('SELECT lifecycle_id FROM account_lifecycles WHERE user_id=:uid'), {'uid': user_id}).scalar_one_or_none()
    return existing


def project(db, user):
    reference = account_ref(db, user.id)
    if reference is None:
        raise ValueError('account lifecycle unavailable')
    row = db.execute(text('''SELECT u.is_admin, l.course_id, COALESCE(r.revision,0) AS revision
        FROM users u LEFT JOIN course_leadership l ON l.user_id=u.id AND l.revoked_at IS NULL AND l.account_ref=:ref
        LEFT JOIN role_admin_revisions r ON r.user_id=u.id WHERE u.id=:uid'''),
        {'uid': user.id, 'ref': reference}).mappings().one_or_none()
    if row is None:
        raise ValueError('account unavailable')
    if not row['is_admin'] and row['course_id']:
        from pymentor_core.adapters.sql.course_leader_config import require_projection
        require_projection(db, row['course_id'])
    role = 'admin' if row['is_admin'] else 'leader' if row['course_id'] else 'user'
    capabilities = [] if role == 'user' else list(COURSE_CAPABILITIES)
    if role == 'admin':
        capabilities += ['platform.read', 'platform.manage']
    return {**serialize_user(user), 'is_admin': bool(row['is_admin']), 'account_ref': reference, 'role': role,
            'capabilities': capabilities, 'course_id': row['course_id'] if role == 'leader' else None,
            'auth_revision': row['revision']}


def lock_lifecycle_user(db, user_id, reference):
    """Protect a profile write against deletion or replacement after authentication."""
    if not reference:
        return None
    user = db.query(models.User).filter(models.User.id == user_id).with_for_update().one_or_none()
    if user is None:
        return None
    current = db.execute(text('SELECT lifecycle_id FROM account_lifecycles WHERE user_id=:uid'), {'uid': user_id}).scalar_one_or_none()
    return user if current == reference else None
