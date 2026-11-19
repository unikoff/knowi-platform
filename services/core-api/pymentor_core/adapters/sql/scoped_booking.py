"""Explicit course/owner booking read and transaction primitives."""
from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from pymentor_core.adapters.sql import role_admin_reads as reads
from pymentor_core.adapters.sql import role_admin_commands as commands
from pymentor_core.adapters.sql import models
from pymentor_core.adapters.sql.bookings import _add_linked_notification
from pymentor_core.adapters.sql.role_admin_cutover import require_mode

MOSCOW = timezone(timedelta(hours=3))


def _error(status, detail):
    raise HTTPException(status_code=status, detail=detail)


def student_authorize(db, principal, course):
    if not getattr(principal, 'account_ref', None):
        _error(401, 'Account lifecycle unavailable')
    exists = db.execute(text('''SELECT 1 FROM account_lifecycles WHERE user_id=:uid AND lifecycle_id=:ref'''),
                        {'uid': principal.user_id, 'ref': principal.account_ref}).scalar_one_or_none()
    if not exists:
        _error(401, 'Account lifecycle changed')
    reads._enabled(course)
    from pymentor_core.adapters.sql.course_leader_config import require_projection
    require_projection(db, course)
    reads._require_reconciled(db)


def _times(row):
    start = datetime.combine(row['slot_date'], time.fromisoformat(row['start_time']), tzinfo=MOSCOW).astimezone(timezone.utc)
    return start, start + timedelta(minutes=row['duration_minutes'])


def _slot(row, *, viewer=None, now=None):
    start, end = _times(row)
    if viewer is not None:
        mine = row['student_id'] == viewer
        return {'id': row['id'], 'course_id': row['course_id'], 'owner_id': row['calendar_owner_id'],
                'starts_at': start, 'ends_at': end, 'status': 'mine' if mine else 'free',
                'can_cancel': bool(mine and start - (now or datetime.now(timezone.utc)) >= timedelta(hours=36))}
    return {'id': row['id'], 'course_id': row['course_id'], 'owner_id': row['calendar_owner_id'],
            'starts_at': start, 'ends_at': end,
            'status': 'archived' if row['archived_at'] else 'booked' if row['student_id'] else 'free',
            'student_id': row['student_id']}


def _booking(row, *, now=None):
    value = dict(row)
    if value['status'] == 'booked' and value['ends_at'] <= (now or datetime.now(timezone.utc)):
        value['status'] = 'completed'
        value['completed_at'] = value['ends_at']
    return value


def _month_dates(year, month):
    start, end = reads._bounds(year, month)
    return start.astimezone(MOSCOW).date(), end.astimezone(MOSCOW).date()


def _owner_exists(db, course, owner_id):
    return db.execute(text('''SELECT EXISTS(
        SELECT 1 FROM course_leadership WHERE course_id=:course AND user_id=:owner AND revoked_at IS NULL)'''),
        {'course': course, 'owner': owner_id}).scalar_one()


def staff_slots(db, principal, course, owner_id, year, month):
    actor = reads.authorize(db, principal, course)
    reads._require_reconciled(db)
    if not actor['is_admin'] and owner_id != principal.user_id:
        _error(404, 'Calendar not found')
    if not _owner_exists(db, course, owner_id):
        _error(404, 'Calendar not found')
    start, end = _month_dates(year, month)
    rows = db.execute(text('''SELECT id,course_id,calendar_owner_id,slot_date,start_time,duration_minutes,student_id,archived_at
        FROM booking_slots WHERE course_id=:course AND calendar_owner_id=:owner AND slot_date>=:start AND slot_date<:end
        ORDER BY slot_date,start_time,id'''), {'course': course, 'owner': owner_id, 'start': start, 'end': end}).mappings().all()
    return {'course_id': course, 'owner_id': owner_id, 'period': {'year': year, 'month': month}, 'slots': [_slot(r) for r in rows]}


def student_context(db, principal, course):
    student_authorize(db, principal, course)
    owner = db.execute(text('''SELECT u.id,u.username FROM course_leadership l JOIN users u ON u.id=l.user_id
        JOIN account_lifecycles a ON a.user_id=u.id AND a.lifecycle_id=l.account_ref
        WHERE l.course_id=:course AND l.revoked_at IS NULL'''), {'course': course}).mappings().one_or_none()
    rows = db.execute(text('''SELECT h.id,h.course_id,h.slot_id,h.student_id,h.owner_at_booking_id,h.status,h.starts_at,h.ends_at,h.created_at,
        h.cancelled_at,h.completed_at,h.legacy_coverage FROM booking_history h
        JOIN account_lifecycles a ON a.user_id=h.student_id AND a.lifecycle_id=:ref
        WHERE h.course_id=:course AND h.student_id=:student AND h.status='booked' AND h.starts_at>clock_timestamp()
        ORDER BY h.starts_at,h.id'''), {'course': course, 'student': principal.user_id, 'ref': principal.account_ref}).mappings().all()
    student_authorize(db, principal, course)
    return {'course_id': course, 'active_owner_id': owner['id'] if owner else None,
            'active_owner_name': owner['username'] if owner else None,
            'my_upcoming_bookings': [_booking(row) for row in rows]}


def student_slots(db, principal, course, owner_id, year, month):
    student_authorize(db, principal, course)
    if not _owner_exists(db, course, owner_id):
        _error(404, 'Calendar not found')
    active = db.execute(text('''SELECT EXISTS(SELECT 1 FROM course_leadership WHERE course_id=:course
        AND user_id=:owner AND revoked_at IS NULL)'''), {'course': course, 'owner': owner_id}).scalar_one()
    start, end = _month_dates(year, month)
    rows = db.execute(text('''SELECT id,course_id,calendar_owner_id,slot_date,start_time,duration_minutes,student_id,archived_at
        FROM booking_slots WHERE course_id=:course AND calendar_owner_id=:owner AND archived_at IS NULL
        AND slot_date>=:start AND slot_date<:end AND (student_id=:student OR (:active AND student_id IS NULL))
        AND EXISTS(SELECT 1 FROM account_lifecycles WHERE user_id=:student AND lifecycle_id=:ref)
        ORDER BY slot_date,start_time,id'''),
        {'course': course, 'owner': owner_id, 'start': start, 'end': end,
         'student': principal.user_id, 'ref': principal.account_ref, 'active': active}).mappings().all()
    now = datetime.now(timezone.utc)
    visible = [_slot(row, viewer=principal.user_id, now=now) for row in rows if _times(row)[0] > now]
    return {'course_id': course, 'owner_id': owner_id, 'period': {'year': year, 'month': month},
            'slots': visible, 'quota': reads.quota_data(db, course, principal.user_id, year, month, principal.account_ref)}


_SLOT_FIELDS = 'id,course_id,calendar_owner_id,slot_date,start_time,duration_minutes,student_id,archived_at'
_HISTORY_FIELDS = 'id,course_id,slot_id,student_id,owner_at_booking_id,status,starts_at,ends_at,created_at,cancelled_at,completed_at,legacy_coverage'


def _slot_rows(db, course, ids, *, locked=False):
    from sqlalchemy import bindparam
    query = text('SELECT ' + _SLOT_FIELDS + ' FROM booking_slots WHERE course_id=:course AND id IN :ids ORDER BY id' + (' FOR UPDATE' if locked else '')).bindparams(bindparam('ids', expanding=True))
    return db.execute(query, {'course': course, 'ids': sorted(set(ids))}).mappings().all()


def _identity_ref(db, user_id):
    reference = commands._ref(db, user_id)
    if not reference:
        _error(404, 'Calendar owner not found')
    return reference


def _lock_calendar_dates(db, rows, *, extra=(), students=()):
    owner_keys = set(extra)
    student_keys = set()
    for row in rows:
        start, end = _times(row)
        first, last = start.astimezone(MOSCOW).date(), (end - timedelta(microseconds=1)).astimezone(MOSCOW).date()
        days = {first, last}
        for day in days:
            owner_keys.add((row['calendar_owner_id'], day))
            for student in students:
                student_keys.add((student, day))
    for owner, day in sorted(owner_keys):
        db.execute(text('SELECT pg_advisory_xact_lock(529,hashtext(:key))'), {'key': f'{owner}:{day.isoformat()}'})
    for student, day in sorted(student_keys):
        db.execute(text('SELECT pg_advisory_xact_lock(530,hashtext(:key))'), {'key': f'{student}:{day.isoformat()}'})


def _locked_authority(db, principal, command, users, *, student=False):
    if student:
        if principal.user_id not in users or commands._ref(db, principal.user_id) != principal.account_ref:
            _error(401, 'Account lifecycle changed')
        commands.lock_course_assignment(db, command.course)
        reads._enabled(command.course)
        from pymentor_core.adapters.sql.course_leader_config import require_projection
        require_projection(db, command.course)
        assignment = db.execute(text('''SELECT id,user_id,account_ref,course_id FROM course_leadership
            WHERE course_id=:course AND revoked_at IS NULL FOR UPDATE'''), {'course': command.course}).mappings().one_or_none()
        actor_course = db.execute(text('''SELECT course_id FROM course_leadership WHERE user_id=:uid
            AND account_ref=:ref AND revoked_at IS NULL'''),
            {'uid': principal.user_id, 'ref': principal.account_ref}).scalar_one_or_none()
        role = 'admin' if users[principal.user_id]['is_admin'] else 'leader' if actor_course else 'user'
        return role, assignment, actor_course if role == 'leader' else None
    if command.operation == 'slot.transfer':
        role, _, _ = commands._authority(db, principal, users, None)
        commands.lock_course_assignment(db, command.course)
        reads._enabled(command.course)
        assignment = db.execute(text('''SELECT id,user_id,account_ref,course_id FROM course_leadership
            WHERE course_id=:course AND revoked_at IS NULL FOR UPDATE'''), {'course': command.course}).mappings().one_or_none()
        return role, assignment, None
    role, assignment, _ = commands._authority(db, principal, users, command.course)
    return role, assignment, command.course if role == 'leader' else None


def _check_interval(db, owner_id, start, end, *, except_ids=(), occupied_only=False):
    local_start, local_end = start.astimezone(MOSCOW).replace(tzinfo=None), end.astimezone(MOSCOW).replace(tzinfo=None)
    query = text('''SELECT EXISTS(SELECT 1 FROM booking_slots s WHERE s.calendar_owner_id=:owner
        AND s.archived_at IS NULL AND (:occupied_only=false OR s.student_id IS NOT NULL)
        AND NOT (s.id = ANY(CAST(:excluded AS integer[])))
        AND (s.slot_date+s.start_time::time)<:end_time
        AND (s.slot_date+s.start_time::time+s.duration_minutes*interval '1 minute')>:start_time)''')
    if db.execute(query, {'owner': owner_id, 'occupied_only': occupied_only, 'excluded': list(except_ids),
                          'start_time': local_start, 'end_time': local_end}).scalar_one():
        _error(409, 'Owner calendar overlaps another slot')


def _notify(db, slot, booking_id, student_id, event_type):
    person = db.execute(text('SELECT username,email FROM users WHERE id=:uid'), {'uid': student_id}).mappings().one()
    _add_linked_notification(db, student_id, models.BookingNotificationOutbox(
        event_type=event_type, slot_id=slot['id'], course_id=slot['course_id'],
        calendar_owner_id=slot['calendar_owner_id'], booking_id=booking_id,
        username=person['username'], email=person['email'], slot_date=slot['slot_date'], start_time=slot['start_time']))


def _replay(db, principal, command, hint, users):
    """Read the response again only after locking every subject it may expose."""
    subjects = set(hint['subject_user_ids'] or ())
    current = commands._command_row(db, principal, command)
    if (current is None or current['redacted'] or hint['redacted']
            or set(current['subject_user_ids'] or ()) != subjects or not subjects.issubset(users)):
        _error(409, 'Prior command was redacted or its subjects changed')
    fresh = commands._command_row(db, principal, command, for_update=True)
    if (fresh is None or fresh['redacted']
            or set(fresh['subject_user_ids'] or ()) != subjects
            or fresh['target_user_id'] != hint['target_user_id']
            or fresh['target_account_ref'] != hint['target_account_ref']):
        _error(409, 'Prior command was redacted or its subjects changed')
    if fresh['request_hash'] != commands.request_hash(command):
        _error(409, 'Idempotency key reused with different request')
    if (not fresh['target_user_id'] or fresh['target_user_id'] not in users
            or commands._ref(db, fresh['target_user_id']) != fresh['target_account_ref']):
        _error(409, 'Account lifecycle changed')
    return fresh['response_body']


def execute(db, principal, command, *, resource_id=None):
    """Acquire cutover, identities, authority, calendar and slot locks in order."""
    require_mode(db, scoped=True)
    if db.execute(text('SHOW transaction_isolation')).scalar_one().lower() != 'read committed':
        _error(503, 'Unsupported transaction isolation')
    is_student = command.operation in {'booking.book', 'booking.cancel'}
    if is_student:
        student_authorize(db, principal, command.course)
    else:
        reads.authorize(db, principal, None if command.operation == 'slot.transfer' else command.course)
        if command.operation == 'slot.transfer':
            reads._enabled(command.course)
        reads._require_reconciled(db)
    prior = commands._command_row(db, principal, command)
    if prior is not None:
        ids = [principal.user_id, *(prior['subject_user_ids'] or ())] + ([prior['target_user_id']] if prior['target_user_id'] else [])
        users = commands._lock_users(db, ids)
        _locked_authority(db, principal, command, users, student=is_student)
        return _replay(db, principal, command, prior, users)

    history = None
    rows = []
    payload = command.payload
    if command.operation == 'slot.create':
        owner_ids = {payload['owner_id']}
        target_id = payload['owner_id']
    elif command.operation == 'slot.transfer':
        ids = payload['slot_ids']
        if len(ids) != len(set(ids)):
            _error(422, 'Duplicate slot IDs')
        rows = _slot_rows(db, command.course, ids)
        if len(rows) != len(ids):
            _error(404, 'Slot not found')
        owner_ids = {payload['from_owner_id'], payload['to_owner_id']}
        target_id = payload['to_owner_id']
    else:
        if command.operation == 'booking.cancel':
            history = db.execute(text('SELECT ' + _HISTORY_FIELDS + ' FROM booking_history WHERE course_id=:course AND id=:id'),
                {'course': command.course, 'id': resource_id}).mappings().one_or_none()
            if history is None or history['student_id'] != principal.user_id:
                _error(404, 'Booking not found')
            resource_id = history['slot_id']
        rows = _slot_rows(db, command.course, [resource_id])
        if not rows:
            _error(404, 'Slot not found')
        owner_ids = {rows[0]['calendar_owner_id']}
        target_id = principal.user_id if is_student else rows[0]['calendar_owner_id']
    if None in owner_ids or target_id is None:
        _error(503, 'Calendar owner requires reconciliation')
    target_ref = principal.account_ref if target_id == principal.user_id else _identity_ref(db, target_id)
    users_to_lock = {principal.user_id, target_id, *owner_ids}
    users_to_lock.update(row['student_id'] for row in rows if row['student_id'] is not None)
    if history is not None and history['owner_at_booking_id'] is not None:
        users_to_lock.add(history['owner_at_booking_id'])
    users = commands._lock_users(db, users_to_lock)
    if any(uid not in users for uid in users_to_lock) or commands._ref(db, target_id) != target_ref:
        _error(409, 'Account lifecycle changed')
    role, assignment, authority_course = _locked_authority(db, principal, command, users, student=is_student)
    if not is_student and role == 'leader' and any(owner != principal.user_id for owner in owner_ids):
        _error(404, 'Calendar not found')
    prior = commands._command_row(db, principal, command)
    if prior is not None:
        if prior['target_user_id'] != target_id or prior['target_account_ref'] != target_ref:
            _error(409, 'Account lifecycle changed')
        return _replay(db, principal, command, prior, users)
    extra = []
    if command.operation == 'slot.create':
        created_date = date.fromisoformat(payload['slot_date'])
        local_start = datetime.combine(created_date, time.fromisoformat(payload['start_time']), tzinfo=MOSCOW)
        local_end = local_start + timedelta(minutes=payload['duration_minutes'])
        extra = [(target_id, day) for day in {local_start.date(), (local_end-timedelta(microseconds=1)).date()}]
    elif command.operation == 'slot.transfer':
        for row in rows:
            start, end = _times(row)
            extra.extend((payload['to_owner_id'], day) for day in {start.astimezone(MOSCOW).date(), (end-timedelta(microseconds=1)).astimezone(MOSCOW).date()})
    _lock_calendar_dates(db, rows, extra=extra, students=[principal.user_id] if is_student else ())
    if rows:
        locked = _slot_rows(db, command.course, [row['id'] for row in rows], locked=True)
        keys = ('id', 'calendar_owner_id', 'slot_date', 'start_time', 'duration_minutes', 'student_id', 'archived_at')
        if len(locked) != len(rows) or any(tuple(a[k] for k in keys) != tuple(b[k] for k in keys) for a, b in zip(rows, locked)):
            _error(409, 'Slot changed concurrently')
        rows = locked
    prior = commands._command_row(db, principal, command)
    if prior is not None:
        if prior['target_user_id'] != target_id or prior['target_account_ref'] != target_ref:
            _error(409, 'Account lifecycle changed')
        return _replay(db, principal, command, prior, users)
    try:
        response, before, after = _apply(db, principal, command, rows, history, assignment)
        commands._audit(db, principal, role, authority_course,
                        command, target_id, target_ref, 'course', before, after, subjects=users_to_lock)
        commands._store(db, principal, command, target_id, target_ref, commands.request_hash(command), response, subjects=users_to_lock)
        db.commit()
        return response
    except IntegrityError as exc:
        if commands._constraint_name(exc) in {'uq_booking_slot_owner_datetime', 'uq_booking_history_active_slot'}:
            _error(409, 'Slot changed concurrently')
        raise


def _apply(db, principal, command, rows, history, assignment):
    op, payload = command.operation, command.payload
    now = db.execute(text('SELECT clock_timestamp()')).scalar_one()
    if op == 'slot.create':
        owner = payload['owner_id']
        if assignment is None or assignment['user_id'] != owner or commands._ref(db, owner) != assignment['account_ref']:
            _error(409, 'Slot owner is not the active course leader')
        local = datetime.combine(date.fromisoformat(payload['slot_date']), time.fromisoformat(payload['start_time']), tzinfo=MOSCOW)
        start = local.astimezone(timezone.utc)
        end = start + timedelta(minutes=payload['duration_minutes'])
        if start <= now:
            _error(409, 'Slot time has passed')
        _check_interval(db, owner, start, end)
        row = db.execute(text('''INSERT INTO booking_slots(course_id,calendar_owner_id,slot_date,start_time,duration_minutes)
            VALUES (:course,:owner,:day,:start,:duration) RETURNING ''' + _SLOT_FIELDS),
            {'course': command.course, 'owner': owner, 'day': local.date(),
             'start': payload['start_time'], 'duration': payload['duration_minutes']}).mappings().one()
        result = _slot(row)
        return {'slot': result}, None, result
    if op == 'slot.archive':
        row = rows[0]
        if row['archived_at'] is not None or row['student_id'] is not None:
            _error(409, 'Occupied or archived slot cannot be archived')
        updated = db.execute(text('UPDATE booking_slots SET archived_at=clock_timestamp() WHERE id=:id RETURNING ' + _SLOT_FIELDS),
                             {'id': row['id']}).mappings().one()
        return {'slot': _slot(updated)}, _slot(row), _slot(updated)
    if op == 'slot.transfer':
        source, destination = payload['from_owner_id'], payload['to_owner_id']
        if source == destination or assignment is None or assignment['user_id'] != destination or commands._ref(db, destination) != assignment['account_ref']:
            _error(409, 'Destination must be the active course leader')
        intervals = []
        for row in rows:
            start, end = _times(row)
            if row['calendar_owner_id'] != source or row['archived_at'] is not None or start <= now:
                _error(409, 'Only future slots of the selected former owner can be transferred')
            if any(start < old_end and old_start < end for old_start, old_end in intervals):
                _error(409, 'Transferred slots overlap')
            _check_interval(db, destination, start, end, except_ids=[r['id'] for r in rows])
            intervals.append((start, end))
        ids = [row['id'] for row in rows]
        from sqlalchemy import bindparam
        stmt = text('UPDATE booking_slots SET calendar_owner_id=:owner WHERE id IN :ids').bindparams(bindparam('ids', expanding=True))
        db.execute(stmt, {'owner': destination, 'ids': ids})
        result = {'course_id': command.course, 'transferred_slot_ids': ids}
        return result, {'owner_id': source, 'slot_ids': ids}, {'owner_id': destination, 'slot_ids': ids}
    if op == 'booking.book':
        row = rows[0]
        start, end = _times(row)
        if (row['archived_at'] is not None or row['student_id'] is not None or start <= now):
            _error(409, 'Slot unavailable')
        if assignment is None or assignment['user_id'] != row['calendar_owner_id'] or commands._ref(db, row['calendar_owner_id']) != assignment['account_ref']:
            _error(409, 'Slot is awaiting owner transfer')
        _check_interval(db, row['calendar_owner_id'], start, end, except_ids=[row['id']], occupied_only=True)
        overlaps = db.execute(text('''SELECT EXISTS(SELECT 1 FROM booking_history WHERE student_id=:student
            AND status IN ('booked','completed') AND starts_at<:end AND ends_at>:start)'''),
            {'student': principal.user_id, 'start': start, 'end': end}).scalar_one()
        if overlaps:
            _error(409, 'Student has an overlapping booking')
        local = start.astimezone(MOSCOW)
        before = reads.quota_data(db, command.course, principal.user_id, local.year, local.month, principal.account_ref)
        if before['remaining'] <= 0:
            _error(409, 'No available course quota')
        updated = db.execute(text('UPDATE booking_slots SET student_id=:student WHERE id=:id RETURNING ' + _SLOT_FIELDS),
            {'student': principal.user_id, 'id': row['id']}).mappings().one()
        saved = db.execute(text('''INSERT INTO booking_history(course_id,slot_id,student_id,owner_at_booking_id,status,starts_at,ends_at,created_at,legacy_coverage)
            VALUES (:course,:slot,:student,:owner,'booked',:start,:end,clock_timestamp(),false) RETURNING ''' + _HISTORY_FIELDS),
            {'course': command.course, 'slot': row['id'], 'student': principal.user_id,
             'owner': row['calendar_owner_id'], 'start': start, 'end': end}).mappings().one()
        _notify(db, updated, saved['id'], principal.user_id, 'booking_created')
        after = reads.quota_data(db, command.course, principal.user_id, local.year, local.month, principal.account_ref)
        return {'booking': _booking(saved, now=now), 'quota': after}, {'quota': before}, {'booking_id': saved['id'], 'quota': after}
    if op == 'booking.cancel':
        row = rows[0]
        current = db.execute(text('SELECT ' + _HISTORY_FIELDS + ' FROM booking_history WHERE id=:id AND course_id=:course FOR UPDATE'),
                             {'id': history['id'], 'course': command.course}).mappings().one_or_none()
        if current is None or current['student_id'] != principal.user_id:
            _error(404, 'Booking not found')
        if current['status'] != 'booked' or row['student_id'] != principal.user_id:
            _error(409, 'Booking already cancelled or changed')
        if current['starts_at'] - now < timedelta(hours=36):
            _error(409, 'Cancellation requires at least 36 hours')
        local = current['starts_at'].astimezone(MOSCOW)
        before = reads.quota_data(db, command.course, principal.user_id, local.year, local.month, principal.account_ref)
        updated = db.execute(text('UPDATE booking_slots SET student_id=NULL WHERE id=:id RETURNING ' + _SLOT_FIELDS),
                             {'id': row['id']}).mappings().one()
        saved = db.execute(text("UPDATE booking_history SET status='cancelled',cancelled_at=clock_timestamp() WHERE id=:id RETURNING " + _HISTORY_FIELDS),
                           {'id': current['id']}).mappings().one()
        _notify(db, updated, saved['id'], principal.user_id, 'booking_cancelled')
        after = reads.quota_data(db, command.course, principal.user_id, local.year, local.month, principal.account_ref)
        return {'booking': _booking(saved, now=now), 'quota': after}, {'booking_id': current['id'], 'quota': before}, {'booking_id': saved['id'], 'quota': after}
    _error(400, 'Unknown booking command')
