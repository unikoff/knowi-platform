"""Transactional role-admin commands. Public writes remain gated until R5."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError

from pymentor_core.adapters.sql import role_admin_reads as reads
from pymentor_core.adapters.sql.role_admin_cutover import require_mode


COMBINED_GRANT = 'subscription.extend-and-quota.grant'
COURSE_USER_OPERATIONS = frozenset({'quota.grant', 'subscription.extend.course', COMBINED_GRANT})


@dataclass(frozen=True)
class Command:
    operation: str
    path: str
    key: str
    payload: dict
    course: str | None = None
    target_id: int | None = None
    target_ref: str | None = None
    selection_revision: int | None = None


def request_hash(command: Command) -> str:
    body = {'method': 'POST', 'path': command.path, 'course_id': command.course,
            'target_ref': command.target_ref, 'body': command.payload}
    return sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def _failure(status, message):
    raise HTTPException(status_code=status, detail=message)


def guarded_execute(db, principal, command):
    reads.authorize(db, principal, command.course)
    require_mode(db, scoped=True)
    reads._require_reconciled(db)
    return _execute(db, principal, command)


def _command_row(db, principal, command, *, for_update=False):
    query = '''SELECT request_hash,response_status,response_body,redacted,target_user_id,target_account_ref,subject_user_ids
        FROM role_admin_commands WHERE actor_account_ref=:actor AND operation=:operation AND command_id=:key'''
    return db.execute(text(query + (' FOR UPDATE' if for_update else '')),
        {'actor': principal.account_ref, 'operation': command.operation, 'key': command.key}).mappings().one_or_none()


def _lock_users(db, ids):
    statement = text('SELECT id,is_admin,subscription_until,subscription_plan FROM users WHERE id IN :ids ORDER BY id FOR UPDATE').bindparams(bindparam('ids', expanding=True))
    return {row['id']: row for row in db.execute(statement, {'ids': sorted(set(ids))}).mappings().all()}


def _ref(db, user_id):
    return db.execute(text('SELECT lifecycle_id FROM account_lifecycles WHERE user_id=:uid'), {'uid': user_id}).scalar_one_or_none()


def lock_admin_invariant(db):
    """All role writers must take this after ordered identity locks."""
    db.execute(text('SELECT pg_advisory_xact_lock(527, 100)'))


def lock_course_assignment(db, course):
    """All assignment/staff writers must use this after ordered identity locks."""
    db.execute(text('SELECT pg_advisory_xact_lock(528, hashtext(:course))'), {'course': course})


def _authority(db, principal, users, course):
    actor = users.get(principal.user_id)
    if actor is None or not principal.account_ref or _ref(db, principal.user_id) != principal.account_ref:
        _failure(401, 'Account lifecycle changed')
    if course is None:
        if not actor['is_admin']:
            _failure(403, 'Platform administrator required')
        return 'admin', None, _revision(db, principal.user_id)
    lock_course_assignment(db, course)
    assignment = db.execute(text('''SELECT id,course_id,user_id,account_ref,revision FROM course_leadership
        WHERE course_id=:course AND revoked_at IS NULL FOR UPDATE'''), {'course': course}).mappings().one_or_none()
    if not actor['is_admin'] and (assignment is None or assignment['user_id'] != principal.user_id
                                 or assignment['account_ref'] != principal.account_ref):
        _failure(403, 'Course access denied')
    reads._enabled(course)
    from pymentor_core.adapters.sql.course_leader_config import require_projection
    require_projection(db, course)
    return ('admin' if actor['is_admin'] else 'leader'), assignment, _revision(db, principal.user_id)


def _revision(db, user_id):
    return db.execute(text('SELECT COALESCE((SELECT revision FROM role_admin_revisions WHERE user_id=:uid),0)'), {'uid': user_id}).scalar_one()


def _target_allowed(db, command, role, principal, revision, target_ref):
    if role == 'admin':
        return
    if command.selection_revision != revision and not reads._is_course_member(db, command.course, command.target_id):
        _failure(403, 'Select an account through course search')


def _audit(db, principal, role, authority_course, command, target_id, target_ref, scope, before, after, *, subjects=()):
    db.execute(text('''INSERT INTO role_admin_audit
        (command_id,actor_id,actor_account_ref,actor_role,authority_course_id,target_user_id,target_account_ref,scope,course_id,action,before_value,after_value,reason,subject_user_ids)
        VALUES (:key,:actor,:actor_ref,:role,:authority,:target,:target_ref,:scope,:course,:action,
                CAST(:before AS json),CAST(:after AS json),:reason,CAST(:subjects AS integer[]))'''),
        {'key': command.key, 'actor': principal.user_id, 'actor_ref': principal.account_ref,
         'role': role, 'authority': authority_course, 'target': target_id, 'target_ref': target_ref,
         'scope': scope, 'course': command.course if scope == 'course' else None,
         'action': command.operation, 'before': json.dumps(jsonable_encoder(before)),
         'after': json.dumps(jsonable_encoder(after)), 'subjects': sorted(set(subjects)), 'reason': command.payload.get('reason') or {
             'slot.create': 'Создание слота', 'booking.book': 'Запись ученика',
             'booking.cancel': 'Отмена учеником',
         }.get(command.operation, 'Операция расписания')})


def _store(db, principal, command, target_id, target_ref, digest, response, *, subjects=()):
    db.execute(text('''INSERT INTO role_admin_commands
        (actor_id,actor_account_ref,target_user_id,target_account_ref,operation,command_id,request_hash,response_status,response_body,subject_user_ids)
        VALUES (:actor,:actor_ref,:target,:target_ref,:operation,:key,:digest,200,CAST(:body AS json),CAST(:subjects AS integer[]))'''),
        {'actor': principal.user_id, 'actor_ref': principal.account_ref, 'target': target_id,
         'target_ref': target_ref, 'operation': command.operation, 'key': command.key,
         'digest': digest, 'subjects': sorted(set(subjects)), 'body': json.dumps(jsonable_encoder(response), ensure_ascii=False)})


def _constraint_name(exc):
    error = exc.orig
    for _ in range(4):
        name = getattr(getattr(error, 'diag', None), 'constraint_name', None) or getattr(error, 'constraint_name', None)
        if name:
            return name
        error = getattr(error, '__cause__', None)
        if error is None:
            break
    return None


def _execute(db, principal, command):
    """Run with an already-open DB session; atomically commits effect, audit and result.

    This private entry exists for isolated transaction tests. Serving routes must
    use guarded_execute until R5 replaces the cutover barrier.
    """
    if not getattr(principal, 'account_ref', None):
        _failure(401, 'Account lifecycle unavailable')
    try:
        return _execute_transaction(db, principal, command)
    except IntegrityError as exc:
        if _constraint_name(exc) in {'uq_course_leadership_active_course', 'uq_course_leadership_active_user',
                                     'uq_booking_quota_course_month', 'uq_booking_quota_legacy_backend'}:
            _failure(409, 'Resource changed concurrently')
        raise


def _execute_transaction(db, principal, command):
    prior_hint = _command_row(db, principal, command)
    target_id, target_ref = command.target_id, command.target_ref
    if command.operation == 'assignment.revoke':
        # A replay refers to the original target, even if the course now has a
        # different leader. This hint is re-read after locking before any use.
        if prior_hint is not None:
            target_id, target_ref = prior_hint['target_user_id'], prior_hint['target_account_ref']
        else:
            hint = db.execute(text('''SELECT user_id,account_ref FROM course_leadership
                WHERE course_id=:course AND revoked_at IS NULL'''), {'course': command.course}).mappings().one_or_none()
            if hint is not None:
                target_id, target_ref = hint['user_id'], hint['account_ref']
    ids = [principal.user_id] + ([target_id] if target_id is not None else [])
    users = _lock_users(db, ids)
    if command.operation in {'admin.role', 'assignment.grant', 'assignment.revoke'}:
        lock_admin_invariant(db)
    # Platform-only role and assignment commands still lock the course row,
    # but authorization is always global, never granted by course leadership.
    platform_only = command.operation in {'admin.role', 'assignment.grant', 'assignment.revoke', 'subscription.reset', 'subscription.extend.platform'}
    role, assignment, revision = _authority(db, principal, users, None if platform_only else command.course)
    if platform_only and command.course is not None:
        lock_course_assignment(db, command.course)
        reads._enabled(command.course)
        assignment = db.execute(text('''SELECT id,course_id,user_id,account_ref,revision FROM course_leadership
            WHERE course_id=:course AND revoked_at IS NULL FOR UPDATE'''), {'course': command.course}).mappings().one_or_none()
    prior = _command_row(db, principal, command)
    if prior is not None and prior['redacted']:
        _failure(409, 'Prior command was redacted after account deletion')
    digest = request_hash(command)
    if prior is not None and prior['request_hash'] != digest:
        _failure(409, 'Idempotency key reused with different request')
    if target_id is None or target_id not in users:
        _failure(404, 'Account not found')
    if not target_ref or _ref(db, target_id) != target_ref:
        _failure(409, 'Account lifecycle changed')
    if command.operation in COURSE_USER_OPERATIONS and (prior is None or command.operation == COMBINED_GRANT):
        _target_allowed(db, command, role, principal, revision, target_ref)
    if prior is not None:
        if prior['target_user_id'] != target_id or prior['target_account_ref'] != target_ref:
            _failure(409, 'Idempotency key reused with different request')
        return prior['response_body']
    if command.operation == 'assignment.revoke' and (assignment is None or assignment['user_id'] != target_id or assignment['account_ref'] != target_ref):
        _failure(409, 'Assignment changed concurrently')
    effects = (command,)
    if command.operation == COMBINED_GRANT:
        effects = tuple(replace(command, operation=op) for op in ('subscription.extend.course', 'quota.grant'))
    response = {}
    changes = []
    subjects = (principal.user_id, target_id)
    for effect in effects:
        result, before, after, scope = _apply(db, principal, users[target_id], effect, target_id, target_ref, assignment)
        changes.append((effect, before, after, scope))
        response.update(result)
    for effect, before, after, scope in changes:
        _audit(db, principal, role, command.course if role == 'leader' else None,
               effect, target_id, target_ref, scope, before, after, subjects=subjects)
    _store(db, principal, command, target_id, target_ref, digest, response, subjects=subjects)
    db.commit()
    return response


def _apply(db, principal, target, command, target_id, target_ref, assignment):
    payload, op = command.payload, command.operation
    if op == 'quota.grant':
        reads._require_reconciled(db)
        before = reads.quota_data(db, command.course, target_id, payload['year'], payload['month'], target_ref)
        db.execute(text('''INSERT INTO booking_quotas(course_id,user_id,year,month,granted_slots)
            VALUES (:course,:uid,:year,:month,:delta)
            ON CONFLICT (course_id,user_id,year,month) WHERE course_id IS NOT NULL
            DO UPDATE SET granted_slots=booking_quotas.granted_slots+EXCLUDED.granted_slots'''),
            {'course': command.course, 'uid': target_id, 'year': payload['year'], 'month': payload['month'], 'delta': payload['add']})
        after = reads.quota_data(db, command.course, target_id, payload['year'], payload['month'], target_ref)
        return {'quota': after}, before, after, 'course'
    if op in {'subscription.extend.course', 'subscription.extend.platform', 'subscription.reset'}:
        before = {'subscription_until': target['subscription_until'], 'subscription_plan': target['subscription_plan']}
        if op == 'subscription.reset':
            until, plan = None, None
        else:
            now = db.execute(text('SELECT clock_timestamp()')).scalar_one()
            previous = target['subscription_until']
            until = max(now, previous) + timedelta(days=payload['days']) if previous else now + timedelta(days=payload['days'])
            plan = target['subscription_plan'] or 'pro'
        db.execute(text('UPDATE users SET subscription_until=:until,subscription_plan=:plan WHERE id=:uid'),
                   {'until': until, 'plan': plan, 'uid': target_id})
        after = {'subscription_until': until, 'subscription_plan': plan}
        return {'scope': 'platform', 'user_id': target_id, **after}, before, after, 'platform'
    if op == 'admin.role':
        active = db.execute(text('SELECT EXISTS(SELECT 1 FROM course_leadership WHERE user_id=:uid AND revoked_at IS NULL)'), {'uid': target_id}).scalar_one()
        if payload['is_admin'] and active:
            _failure(409, 'Revoke course leadership first')
        if target['is_admin'] and not payload['is_admin']:
            remaining = db.execute(text('SELECT count(*) FROM users WHERE is_admin AND id<>:uid'), {'uid': target_id}).scalar_one()
            if not remaining:
                _failure(409, 'Cannot remove the last platform administrator')
        db.execute(text('UPDATE users SET is_admin=:admin WHERE id=:uid'), {'uid': target_id, 'admin': payload['is_admin']})
        after = {'user_id': target_id, 'is_admin': payload['is_admin'], 'auth_revision': _revision(db, target_id)}
        return after, {'is_admin': target['is_admin']}, after, 'platform'
    if op == 'assignment.grant':
        if target['is_admin'] or assignment is not None:
            _failure(409, 'Role or course assignment conflict')
        occupied = db.execute(text('SELECT EXISTS(SELECT 1 FROM course_leadership WHERE user_id=:uid AND revoked_at IS NULL)'), {'uid': target_id}).scalar_one()
        if occupied:
            _failure(409, 'User already leads a course')
        assignment_id = db.execute(text('''INSERT INTO course_leadership(course_id,user_id,account_ref,revision,assigned_by)
            VALUES (:course,:uid,:ref,1,:actor) RETURNING id'''),
            {'course': command.course, 'uid': target_id, 'ref': target_ref, 'actor': principal.user_id}).scalar_one()
        revision = _revision(db, target_id)
        db.execute(text('UPDATE course_leadership SET revision=:revision WHERE id=:id'), {'revision': revision, 'id': assignment_id})
        result = {'course_id': command.course, 'user_id': target_id, 'active': True, 'revision': revision}
        return {'assignment': result}, None, result, 'course'
    if op == 'assignment.revoke':
        db.execute(text('UPDATE course_leadership SET revoked_at=clock_timestamp() WHERE id=:id'), {'id': assignment['id']})
        revision = _revision(db, target_id)
        db.execute(text('UPDATE course_leadership SET revision=:revision WHERE id=:id'), {'revision': revision, 'id': assignment['id']})
        result = {'course_id': command.course, 'user_id': target_id, 'active': False, 'revision': revision}
        return {'assignment': result}, {'active': True}, result, 'course'
    _failure(400, 'Unknown command')
