"""Read-only scoped reports. Authorization is repeated against current SQL state."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json

from fastapi import HTTPException
from sqlalchemy import text

from pymentor_core.adapters.course_local.runtime import default_runtime
from pymentor_core.adapters.course_local.packages import CourseNotFoundError, CoursePackageError
from pymentor_core.adapters.sql.role_identity import account_ref
from pymentor_core.modules.identity.activity_policy import MOSCOW_TZ
from pymentor_core.modules.learning.progress_policy import (
    is_overview_lesson, lesson_completion_requirements,
)


def _error(status, detail):
    raise HTTPException(status_code=status, detail=detail)


def booking_mode(db):
    enabled = db.execute(text('SELECT scoped_enabled FROM role_admin_cutover WHERE id=1')).scalar_one_or_none()
    if enabled is None:
        _error(503, 'Cutover state unavailable')
    return {'mode': 'scoped' if enabled else 'legacy'}


def _enabled(course):
    try:
        default_runtime().registry.require_enabled(course)
    except CourseNotFoundError:
        _error(404, 'Course not found')
    except Exception:
        _error(503, 'Course catalog unavailable')


def authorize(db, principal, course=None):
    """Never trust role or course claims from the client or an older session snapshot."""
    if not getattr(principal, 'account_ref', None):
        _error(401, 'Account lifecycle unavailable')
    actor = db.execute(text('''SELECT u.id,u.is_admin,l.course_id,COALESCE(r.revision,0) AS revision
        FROM users u JOIN account_lifecycles a ON a.user_id=u.id
        LEFT JOIN course_leadership l ON l.user_id=u.id AND l.revoked_at IS NULL AND l.account_ref=a.lifecycle_id
        LEFT JOIN role_admin_revisions r ON r.user_id=u.id
        WHERE u.id=:uid AND a.lifecycle_id=:ref'''), {'uid': principal.user_id, 'ref': principal.account_ref}).mappings().one_or_none()
    if actor is None:
        _error(401, 'Account lifecycle changed')
    if course is None:
        if not actor['is_admin']:
            _error(403, 'Platform administrator required')
    elif not actor['is_admin'] and actor['course_id'] != course:
        _error(403, 'Course access denied')
    if course is not None:
        _enabled(course)
        from pymentor_core.adapters.sql.course_leader_config import require_projection
        require_projection(db, course)
    return actor


def _cursor(cursor, *, actor, scope, filters):
    if not cursor:
        return 0
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor.encode() + b'=' * (-len(cursor) % 4)))
        if (set(value) != {'actor', 'revision', 'scope', 'filters', 'offset'}
            or value['actor'] != actor['ref'] or value['revision'] != actor['revision']
            or value['scope'] != scope or value['filters'] != filters
            or not isinstance(value['offset'], int) or isinstance(value['offset'], bool)
            or not 0 <= value['offset'] <= 100000):
            raise ValueError
        return value['offset']
    except (ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError):
        _error(400, 'Invalid cursor')


def _next(offset, limit, rows, *, actor, scope, filters):
    if len(rows) <= limit:
        return None
    value = {'actor': actor['ref'], 'revision': actor['revision'], 'scope': scope,
             'filters': filters, 'offset': offset + limit}
    return base64.urlsafe_b64encode(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).decode().rstrip('=')


def _page(db, query, params, actor, scope, filters, cursor, limit):
    offset = _cursor(cursor, actor=actor, scope=scope, filters=filters)
    rows = db.execute(text(query), {**params, 'limit': limit + 1, 'offset': offset}).mappings().all()
    return rows[:limit], _next(offset, limit, rows, actor=actor, scope=scope, filters=filters)


def _actor(db, principal, course=None):
    actor = dict(authorize(db, principal, course))
    actor['ref'] = principal.account_ref
    return actor


def _require_reconciled(db):
    unknown = db.execute(text('''SELECT
        EXISTS(SELECT 1 FROM booking_slots WHERE (course_id IS NULL OR calendar_owner_id IS NULL)
          AND archived_at IS NULL AND (slot_date >= (now() AT TIME ZONE 'UTC' + interval '3 hours')::date
            OR (student_id IS NOT NULL AND slot_date >= date_trunc('month', now() AT TIME ZONE 'UTC' + interval '3 hours')::date)))
        OR EXISTS(SELECT 1 FROM booking_quotas WHERE course_id IS NULL AND granted_slots>0
          AND (year,month) >= (extract(year FROM (now() AT TIME ZONE 'UTC'+interval '3 hours'))::int,
                                extract(month FROM (now() AT TIME ZONE 'UTC'+interval '3 hours'))::int))
        OR EXISTS(SELECT 1 FROM booking_slots s WHERE s.student_id IS NOT NULL AND s.archived_at IS NULL
          AND s.slot_date >= date_trunc('month',now() AT TIME ZONE 'UTC'+interval '3 hours')::date
          AND NOT EXISTS(SELECT 1 FROM booking_history h WHERE h.slot_id=s.id AND h.student_id=s.student_id
            AND h.course_id=s.course_id AND h.status IN ('booked','completed')))
        OR EXISTS(SELECT 1 FROM booking_slots s WHERE s.student_id IS NOT NULL AND s.archived_at IS NOT NULL
          AND ((s.slot_date+s.start_time::time)>(now() AT TIME ZONE 'UTC'+interval '3 hours')
            OR (s.slot_date>=date_trunc('month',now() AT TIME ZONE 'UTC'+interval '3 hours')::date
              AND NOT EXISTS(SELECT 1 FROM booking_history h WHERE h.slot_id=s.id AND h.student_id=s.student_id
                AND h.course_id=s.course_id AND h.status IN ('booked','completed')))))''')).scalar_one()
    if unknown:
        _error(503, 'Legacy booking scope requires reconciliation')


def _mask(email):
    local, _, domain = email.partition('@')
    return (local[:1] or '*') + '***@' + domain


# The directory's presentation and partial-name search share one projection.
# A leading @handle contains no local part, so it is preserved.
_USERNAME_EMAIL_PATTERN = r'[^[:space:]@<>()\[\],;:]+@[^[:space:]@<>()\[\],;:]+'
_DIRECTORY_USERNAME = """CASE
    WHEN lower(btrim(u.username)) = lower(btrim(u.email))
        OR btrim(u.username) ~ ('^' || :username_email_pattern || '$')
        THEN 'Аккаунт №' || u.id
    ELSE regexp_replace(u.username, :username_email_pattern, '—', 'g')
END"""


def _person(db, row):
    reference = account_ref(db, row['id'])
    if not reference:
        _error(404, 'Account not found')
    current = db.execute(text(f'''SELECT u.id,{_DIRECTORY_USERNAME} AS username,u.email
        FROM users u JOIN account_lifecycles a ON a.user_id=u.id
        WHERE u.id=:uid AND a.lifecycle_id=:ref'''),
        {'uid': row['id'], 'ref': reference, 'username_email_pattern': _USERNAME_EMAIL_PATTERN}).mappings().one_or_none()
    if current is None:
        _error(404, 'Account not found')
    return {'id': current['id'], 'account_ref': reference, 'username': current['username'], 'masked_email': _mask(current['email'])}


def _bounds(year, month):
    tz = timezone(timedelta(hours=3))
    start = datetime(year, month, 1, tzinfo=tz)
    end = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


_PERSONAL_BOOKING = '''EXISTS (
    SELECT 1 FROM booking_history h WHERE h.course_id=:course AND h.student_id=u.id
)'''
_COURSE_MEMBER = f'''({_PERSONAL_BOOKING} OR EXISTS (
    SELECT 1 FROM booking_quotas q WHERE q.course_id=:course AND q.user_id=u.id AND q.granted_slots>0
))'''
_QUOTA_USAGE = """h.course_id=:course AND h.status IN ('booked','completed')
    AND h.starts_at>=:start AND h.starts_at<:end"""


def _is_course_member(db, course, user_id, *, bookings_only=False):
    membership = _PERSONAL_BOOKING if bookings_only else _COURSE_MEMBER
    return db.execute(text(f'SELECT EXISTS(SELECT 1 FROM users u WHERE u.id=:uid AND {membership})'),
                      {'course': course, 'uid': user_id}).scalar_one()


def _add_roster_balances(db, course, users, year, month):
    if not users:
        return
    start, end = _bounds(year, month)
    results = db.execute(text(f'''
        SELECT target.user_id, COALESCE(q.granted_slots,0) AS granted, COALESCE(usage.used,0) AS used
        FROM unnest(CAST(:ids AS integer[]), CAST(:refs AS text[])) AS target(user_id, account_ref)
        JOIN account_lifecycles a ON a.user_id=target.user_id AND a.lifecycle_id=target.account_ref
        LEFT JOIN booking_quotas q ON q.user_id=target.user_id AND q.course_id=:course
            AND q.year=:year AND q.month=:month
        LEFT JOIN (
            SELECT h.student_id, count(*) AS used FROM booking_history h
            WHERE {_QUOTA_USAGE} AND h.student_id=ANY(CAST(:ids AS integer[]))
            GROUP BY h.student_id
        ) usage ON usage.student_id=target.user_id
    '''), {'course': course, 'year': year, 'month': month, 'start': start, 'end': end,
           'ids': [user['id'] for user in users], 'refs': [user['account_ref'] for user in users]}).mappings()
    balances = {row['user_id']: row for row in results}
    if len(balances) != len(users):
        _error(404, 'Roster member not found')
    for user in users:
        row = balances[user['id']]
        user['remaining_lessons'] = row['granted'] - row['used'] if row['used'] <= row['granted'] else None


def roster(db, principal, course, search, cursor, limit, *, period=None):
    actor = _actor(db, principal, course)
    _require_reconciled(db)
    filters = {'search': search}
    if period is not None:
        filters.update(year=period[0], month=period[1])
    rows, nxt = _page(db, f'''SELECT u.id FROM users u WHERE {_COURSE_MEMBER}
        AND (:search='' OR strpos(lower({_DIRECTORY_USERNAME}),lower(:search))>0
            OR lower(btrim(u.username))=lower(btrim(:search)) OR lower(u.email)=lower(btrim(:search)))
        ORDER BY u.id LIMIT :limit OFFSET :offset''',
        {'course': course, 'search': search, 'username_email_pattern': _USERNAME_EMAIL_PATTERN},
        actor, 'roster:' + course, filters, cursor, limit)
    users = []
    for row in rows:
        person = _person(db, row)
        current = db.execute(text(f'''SELECT u.subscription_until,
            CASE WHEN latest.status='booked' AND latest.ends_at<=clock_timestamp() THEN 'completed'
                 ELSE latest.status END AS booking_status,
            CASE WHEN preview.is_upcoming THEN 'upcoming'
                 WHEN preview.starts_at IS NOT NULL THEN 'latest' END AS display_booking_kind,
            preview.starts_at AS display_starts_at,
            preview.ends_at AS display_ends_at,
            preview.status AS display_booking_status
            FROM users u JOIN account_lifecycles a ON a.user_id=u.id
            LEFT JOIN LATERAL (
                SELECT h.starts_at,h.ends_at,h.status
                FROM booking_history h
                WHERE h.course_id=:course AND h.student_id=u.id
                ORDER BY h.starts_at DESC,h.id DESC LIMIT 1
            ) latest ON TRUE
            LEFT JOIN LATERAL (
                SELECT candidate.starts_at,candidate.ends_at,candidate.status,candidate.is_upcoming
                FROM (
                    SELECT h.id,h.starts_at,h.ends_at,h.status,
                           h.status='booked' AND h.starts_at>now() AS is_upcoming
                    FROM booking_history h
                    WHERE h.course_id=:course AND h.student_id=u.id
                        AND h.status IN ('booked','completed')
                ) candidate
                ORDER BY candidate.is_upcoming DESC,
                         CASE WHEN candidate.is_upcoming THEN candidate.starts_at END,
                         candidate.starts_at DESC,
                         CASE WHEN candidate.is_upcoming THEN candidate.id END,
                         candidate.id DESC
                LIMIT 1
            ) preview ON TRUE
            WHERE u.id=:uid AND a.lifecycle_id=:ref AND {_COURSE_MEMBER}'''),
            {'course': course, 'uid': person['id'], 'ref': person['account_ref']}).mappings().one_or_none()
        if current is None:
            _error(404, 'Roster member not found')
        preview = None
        if current['display_booking_kind'] is not None:
            preview = {'kind': current['display_booking_kind'], 'status': current['display_booking_status'],
                       'starts_at': current['display_starts_at'], 'ends_at': current['display_ends_at']}
        users.append({**person, 'last_booking_status': current['booking_status'], 'display_booking': preview,
                      'subscription_until': current['subscription_until']})
    if period is not None:
        _add_roster_balances(db, course, users, *period)
    _actor(db, principal, course)
    return {'course_id': course, 'users': users, 'next_cursor': nxt, 'history_coverage': 'legacy_partial'}


def grant_search(db, principal, course, query, cursor, limit):
    actor = _actor(db, principal, course)
    q = query.strip()
    if len(q) < 3 or any(ch in q for ch in ('%', '*', '?')) or ('@' not in q and '_' in q):
        _error(422, 'Enter a full email or at least three name characters')
    is_handle = q.startswith('@') and q[1:].replace('_', '').isalnum()
    is_exact = '@' in q and not is_handle
    if is_exact and (q.count('@') != 1 or not all(q.split('@')) or '.' not in q.split('@')[1]):
        _error(422, 'Enter a full email')
    filters = {'q': q.lower()}
    rows, nxt = _page(db, f'''SELECT u.id FROM users u WHERE
        ((:exact AND (lower(u.email)=lower(:q) OR lower(btrim(u.username))=lower(:q)))
          OR (NOT :exact AND strpos(lower({_DIRECTORY_USERNAME}),lower(:q))>0))
        ORDER BY u.id LIMIT :limit OFFSET :offset''',
        {'q': q, 'exact': is_exact, 'username_email_pattern': _USERNAME_EMAIL_PATTERN}, actor,
        'search:' + course, filters, cursor, limit)
    users = [_person(db, r) for r in rows]
    current = _actor(db, principal, course)
    return {'course_id': course, 'users': users, 'next_cursor': nxt}, current['revision']


def progress(db, principal, course, user_id):
    _actor(db, principal, course)
    # Use the same identity/assignment lock order as mutations. A deleted and
    # reused numeric ID must never inherit the old roster authorization.
    from pymentor_core.adapters.sql import role_admin_commands as commands
    users = commands._lock_users(db, [principal.user_id, user_id])
    commands._authority(db, principal, users, course)
    if user_id not in users:
        _error(404, 'Roster member not found')
    lifecycle = db.execute(text('SELECT lifecycle_id FROM account_lifecycles WHERE user_id=:uid FOR UPDATE'), {'uid': user_id}).scalar_one_or_none()
    _require_reconciled(db)
    if lifecycle is None or not _is_course_member(db, course, user_id):
        _error(404, 'Roster member not found')
    lessons = db.execute(text('SELECT lesson_id AS id,completed_at FROM user_lesson_progress WHERE course_id=:course AND user_id=:uid ORDER BY lesson_id'), {'course': course, 'uid': user_id}).mappings().all()
    tasks = db.execute(text('SELECT task_id AS id,completed_at FROM user_task_progress WHERE course_id=:course AND user_id=:uid ORDER BY task_id'), {'course': course, 'uid': user_id}).mappings().all()
    commands._authority(db, principal, users, course)
    current_lifecycle = commands._ref(db, user_id)
    if current_lifecycle != lifecycle or not _is_course_member(db, course, user_id):
        _error(404, 'Roster member not found')
    return {'course_id': course, 'user_id': user_id, 'completed_lessons': [dict(x) for x in lessons],
            'completed_tasks': [dict(x) for x in tasks], 'lessons_count': len(lessons), 'tasks_count': len(tasks)}


def lock_statistics_target(db, principal, course, user_id, reference, selection_revision, *, bookings_only=False):
    """Verify live course authority and pin the exact target for this transaction."""
    from pymentor_core.adapters.sql import role_admin_commands as commands

    users = commands._lock_users(db, [principal.user_id, user_id])
    role, _, revision = commands._authority(db, principal, users, course)
    lifecycle = db.execute(
        text('SELECT lifecycle_id FROM account_lifecycles WHERE user_id=:uid FOR UPDATE'),
        {'uid': user_id},
    ).scalar_one_or_none()
    if user_id not in users or lifecycle is None or str(lifecycle) != reference:
        _error(404, 'Account not found')
    if role == 'leader' and selection_revision != revision:
        if not _is_course_member(db, course, user_id, bookings_only=bookings_only):
            _error(403, 'Select an account through course search')
    return role


def student_statistics(db, principal, course, user_id, reference, content, selection_revision):
    from pymentor_core.modules.learning.api.service import build_catalog_progress
    from pymentor_core.modules.learning.course_context import course_scope

    lock_statistics_target(db, principal, course, user_id, reference, selection_revision)
    with course_scope(course):
        catalog = build_catalog_progress(db, user_id=user_id, content=content)
    track_fields = ('id', 'title', 'lessons_completed', 'lessons_countable', 'blocks')
    lesson_fields = ('id', 'title', 'completed', 'completed_on', 'counts_toward_statistics', 'historical_completion_credit')
    return {
        'course_id': course,
        'user_id': user_id,
        'account_ref': reference,
        'tracks': [{key: track[key] for key in track_fields} for track in catalog['tracks']],
        'courses': {
            track: [{key: lesson[key] for key in lesson_fields} for lesson in lessons]
            for track, lessons in catalog['courses'].items()
        },
    }


def student_profile_details(db, principal, course, user_id, reference, year, month, cursor, limit,
                            selection_revision):
    lock_statistics_target(db, principal, course, user_id, reference, selection_revision)
    actor = _actor(db, principal, course)
    start, end = _bounds(year, month)
    period = {'year': year, 'month': month}
    bookings, next_cursor = _page(db, '''SELECT h.id,h.starts_at,h.ends_at,h.status
        FROM booking_history h
        WHERE h.course_id=:course AND h.student_id=:uid AND h.starts_at>=:start AND h.starts_at<:end
        ORDER BY h.starts_at DESC,h.id DESC LIMIT :limit OFFSET :offset''',
        {'course': course, 'uid': user_id, 'start': start, 'end': end},
        actor, 'profile-details:' + course, {**period, 'user_id': user_id, 'account_ref': reference},
        cursor, limit)
    subscription = db.execute(text('SELECT subscription_until,subscription_plan FROM users WHERE id=:uid'),
                              {'uid': user_id}).mappings().one()
    until = subscription['subscription_until']
    return {
        'course_id': course, 'user_id': user_id, 'account_ref': reference, 'period': period,
        'subscription_until': until, 'subscription_plan': subscription['subscription_plan'] if until is not None else None,
        'bookings': [dict(row) for row in bookings], 'next_cursor': next_cursor,
    }


def quota_data(db, course, user_id, year, month, reference):
    start, end = _bounds(year, month)
    row = db.execute(text(f'''SELECT COALESCE((SELECT granted_slots FROM booking_quotas WHERE course_id=:course AND user_id=:uid AND year=:year AND month=:month),0) AS granted,
        (SELECT count(*) FROM booking_history h WHERE {_QUOTA_USAGE} AND h.student_id=:uid) AS used
        WHERE EXISTS (SELECT 1 FROM account_lifecycles WHERE user_id=:uid AND lifecycle_id=:ref)'''),
        {'course': course, 'uid': user_id, 'year': year, 'month': month, 'start': start, 'end': end, 'ref': reference}).mappings().one_or_none()
    if row is None:
        _error(404, 'Account not found')
    if row['used'] > row['granted']:
        _error(503, 'Quota reconciliation required')
    return {'course_id': course, 'user_id': user_id, 'year': year, 'month': month,
            'granted': row['granted'], 'used': row['used'], 'remaining': row['granted'] - row['used']}


def quota(db, principal, course, user_id, reference, year, month):
    _actor(db, principal, course)
    _require_reconciled(db)
    target = db.execute(text('SELECT 1 FROM account_lifecycles WHERE user_id=:uid AND lifecycle_id=:ref'), {'uid': user_id, 'ref': reference}).scalar_one_or_none()
    if not target:
        _error(404, 'Account not found')
    return {'quota': quota_data(db, course, user_id, year, month, reference)}


def course_overview(db, principal, course, year, month, *, include_completions=True):
    _actor(db, principal, course)
    _require_reconciled(db)
    start, end = _bounds(year, month)
    row = db.execute(text(f'''SELECT
        (SELECT count(*) FROM users u WHERE {_COURSE_MEMBER}) AS roster_count,
        (SELECT count(*) FROM booking_history WHERE course_id=:course AND starts_at>=:start AND starts_at<:end) AS booking_count,
        (SELECT COALESCE(sum(q.granted_slots),0) FROM booking_quotas q WHERE q.course_id=:course AND q.year=:year AND q.month=:month) AS lessons_granted,
        (SELECT count(*) FROM booking_history WHERE course_id=:course AND status IN ('booked','completed') AND starts_at>=:start AND starts_at<:end) AS lessons_used,
        ''' + ("(SELECT count(*) FROM user_lesson_progress p WHERE p.course_id=:course AND p.completed_at>=:start AND p.completed_at<:end AND EXISTS(SELECT 1 FROM booking_history h WHERE h.course_id=:course AND h.student_id=p.user_id))" if include_completions else "NULL") + ''' AS completed_lessons'''),
        {'course': course, 'year': year, 'month': month, 'start': start, 'end': end}).mappings().one()
    unavailable = {'status': 'unavailable', 'quality': 'unknown', 'daily': []}
    # Core owns lesson and task receipts even when legacy roster summaries are offloaded.
    completions = unavailable
    credit = None
    try:
        catalog = default_runtime().snapshot(course).catalog
    except CoursePackageError:
        pass
    else:
        now = db.execute(text('SELECT clock_timestamp()')).scalar_one()
        completions = _course_completion_trend(db, course, catalog, now)
        if course == 'backend':
            credit = _course_completion_trend(db, course, catalog, now, historical_credit=True)
    return {'course_id': course, 'period': {'year': year, 'month': month}, **dict(row),
            'course_activity_days': None, 'course_trends': {'visits': unavailable, 'completions': completions,
                                                          'estimated_completion_time': credit},
            'dashboard': _course_dashboard(db, course, include_completions=include_completions)}


_COURSE_COMPLETION_TREND = text('''
    WITH requirements AS MATERIALIZED (
        SELECT * FROM jsonb_to_recordset(CAST(:requirements AS jsonb))
            AS rule(lesson_id text, proof integer, task_id text, revision text)
    ), candidates AS (
        SELECT fact.user_id, rule.lesson_id
        FROM user_lesson_progress fact
        JOIN requirements rule ON rule.lesson_id=fact.lesson_id AND rule.task_id IS NULL
        WHERE fact.course_id=:course AND fact.completed_at BETWEEN :start AND :now
        UNION
        SELECT fact.user_id, rule.lesson_id
        FROM user_task_progress fact
        JOIN requirements rule ON rule.task_id=fact.task_id AND rule.revision=fact.task_revision
        WHERE fact.course_id=:course AND fact.completed_at BETWEEN :start AND :now
    ), proofs AS (
        SELECT candidate.user_id, rule.lesson_id, rule.proof,
               max(coalesce(lesson.completed_at, task.completed_at)) AS completed_at
        FROM candidates candidate
        JOIN requirements rule ON rule.lesson_id=candidate.lesson_id
        LEFT JOIN user_lesson_progress lesson ON rule.task_id IS NULL AND lesson.course_id=:course
                 AND lesson.user_id=candidate.user_id AND lesson.lesson_id=rule.lesson_id
        LEFT JOIN user_task_progress task ON task.course_id=:course AND task.user_id=candidate.user_id
                 AND task.task_id=rule.task_id AND task.task_revision=rule.revision
        GROUP BY candidate.user_id, rule.lesson_id, rule.proof
        HAVING count(*)=count(coalesce(lesson.completed_at, task.completed_at))
    ), completions AS (
        SELECT user_id, lesson_id, min(completed_at) AS completed_at
        FROM proofs GROUP BY user_id, lesson_id
    )
    SELECT CAST(timezone('Europe/Moscow', c.completed_at) AS date) AS day,
           count(*) FILTER (WHERE u.subscription_until>:now) AS with_subscription,
           count(*) FILTER (WHERE NOT coalesce(u.subscription_until>:now, false)) AS without_subscription
    FROM completions c
    JOIN users u ON u.id=c.user_id
    WHERE c.completed_at BETWEEN :start AND :through AND NOT u.is_admin
      AND (NOT :historical_credit OR EXISTS (SELECT 1 FROM account_lifecycles l WHERE l.user_id=u.id))
    GROUP BY 1
''')


def _course_completion_trend(db, course, catalog, now, *, historical_credit=False):
    from pymentor_core.modules.learning.complete_lesson import BACKEND_COMPLETION_CREDIT_CUTOFF

    countable = {lesson_id for track in catalog.get_tracks_summary() for lesson_id in track['countable_lesson_ids']}
    requirements = []
    for lesson in catalog.LESSONS:
        if lesson['id'] not in countable or is_overview_lesson(lesson):
            continue
        lesson_id = lesson['id']
        for index, (lesson_required, tasks) in enumerate(lesson_completion_requirements(lesson)):
            if lesson_required:
                requirements.append({'lesson_id': lesson_id, 'proof': index, 'task_id': None, 'revision': None})
            requirements.extend({'lesson_id': lesson_id, 'proof': index, 'task_id': task_id, 'revision': revision}
                                for task_id, revision in tasks)
    first_day = now.astimezone(MOSCOW_TZ).date() - timedelta(days=29)
    start = datetime.combine(first_day, datetime.min.time(), tzinfo=MOSCOW_TZ)
    # The latest receipt dates one proof, and the earliest complete proof dates the lesson.
    rows = db.execute(_COURSE_COMPLETION_TREND, {
        'course': course, 'start': start, 'now': now, 'requirements': json.dumps(requirements),
        'through': min(now, BACKEND_COMPLETION_CREDIT_CUTOFF) if historical_credit else now,
        'historical_credit': historical_credit,
    }).mappings()
    by_day = {row['day']: (row['with_subscription'], row['without_subscription']) for row in rows}
    daily = []
    for offset in range(30):
        day = first_day + timedelta(days=offset)
        subscribed, unsubscribed = by_day.get(day, (0, 0))
        multiplier = 7_200_000 if historical_credit else 1
        daily.append({'date': day, 'with_subscription': subscribed * multiplier,
                      'without_subscription': unsubscribed * multiplier})
    return {'status': 'available', 'quality': 'complete', 'daily': daily}


def calendar_context(db, principal, course):
    actor = _actor(db, principal, course)
    _require_reconciled(db)
    rows = db.execute(text('''SELECT u.id,u.username,true AS active,0 AS pending_slots
        FROM course_leadership l JOIN users u ON u.id=l.user_id
        WHERE l.course_id=:course AND l.revoked_at IS NULL AND (:admin OR u.id=:actor)'''),
        {'course': course, 'admin': actor['is_admin'], 'actor': principal.user_id}).mappings().all()
    return {'course_id': course, 'owners': [dict(r) for r in rows]}


def _course_dashboard(db, course, *, include_completions=True):
    """Legacy dashboard shape, constrained to the course's personal-booking roster."""
    now = db.execute(text('SELECT clock_timestamp()')).scalar_one()
    tz = timezone(timedelta(hours=3))
    today = now.astimezone(tz).date()
    trend_start = today - timedelta(days=29)
    params = {'course': course, 'now': now, 'week': now-timedelta(days=7),
              'trend': datetime.combine(trend_start, datetime.min.time(), tzinfo=tz),
              'today': today, 'time': now.astimezone(tz).strftime('%H:%M'),
              'end7': today+timedelta(days=6), 'end30': today+timedelta(days=29)}
    members = '''EXISTS(SELECT 1 FROM booking_history h WHERE h.course_id=:course AND h.student_id=u.id)'''
    totals = db.execute(text('''SELECT count(*) AS total,
        count(*) FILTER (WHERE u.subscription_until>:now) AS active,
        count(*) FILTER (WHERE u.subscription_until>:now AND u.subscription_until<=CAST(:now AS timestamptz)+interval '7 days') AS expiring
        FROM users u WHERE NOT u.is_admin AND ''' + members), params).mappings().one()
    expiring = db.execute(text('''SELECT u.id,u.username,COALESCE(u.subscription_plan,'pro') AS subscription_plan,
        u.subscription_until FROM users u WHERE NOT u.is_admin AND u.subscription_until>:now
        AND u.subscription_until<=CAST(:now AS timestamptz)+interval '7 days' AND ''' + members + '''
        ORDER BY u.subscription_until LIMIT 6'''), params).mappings().all()
    lessons = db.execute(text('''SELECT p.user_id,u.username,p.completed_at FROM user_lesson_progress p
        JOIN users u ON u.id=p.user_id WHERE p.course_id=:course AND NOT u.is_admin
        AND p.completed_at>=:trend AND p.completed_at<=:now AND ''' + members), params).mappings().all() if include_completions else []
    by_day, by_user = {}, {}
    week_count = 0
    for row in lessons:
        day = row['completed_at'].astimezone(tz).date()
        by_day[day] = by_day.get(day, 0) + 1
        if row['completed_at'] >= params['week']:
            week_count += 1
            key = (row['user_id'], row['username'])
            by_user[key] = by_user.get(key, 0) + 1
    active = [{'id': uid, 'username': name, 'lessons_completed': count}
              for (uid, name), count in sorted(by_user.items(), key=lambda x: (-x[1], x[0][1]))[:6]]
    slots = db.execute(text('''SELECT s.id,s.slot_date,s.start_time,s.duration_minutes,s.student_id,u.username,u.email
        FROM booking_slots s LEFT JOIN users u ON u.id=s.student_id
        WHERE s.course_id=:course AND s.archived_at IS NULL AND s.slot_date<=:end30
          AND (s.slot_date>:today OR (s.slot_date=:today AND s.start_time>=:time))
        ORDER BY s.slot_date,s.start_time,s.id'''), params).mappings().all()
    def summary(rows, with_percent):
        booked = sum(row['student_id'] is not None for row in rows)
        result = {'booked': booked, 'total': len(rows), 'free': len(rows)-booked}
        if with_percent: result['occupancy_percent'] = round(booked / len(rows) * 100) if rows else None
        return result
    week_slots = [row for row in slots if row['slot_date'] <= params['end7']]
    upcoming = [{'id':row['id'], 'date':row['slot_date'].isoformat(), 'start_time':row['start_time'],
                 'duration_minutes':row['duration_minutes'], 'student':{'id':row['student_id'],
                 'username':row['username'],'email':row['email']}}
                for row in week_slots if row['student_id'] is not None][:6]
    return {'total_students':totals['total'], 'active_students_7d':None, 'inactive_students_7d':None,
            'active_subscriptions_count':totals['active'], 'lessons_completed_7d':week_count,
            'expiring_subscriptions_count':totals['expiring'],
            'expiring_subscriptions':[{**dict(row), 'subscription_until':row['subscription_until'].isoformat()} for row in expiring],
            'slots_30d':summary(slots, True), 'slots_7d':summary(week_slots, False), 'upcoming_bookings':upcoming,
            'most_active_students':active, 'daily_active_students':None,
            'daily_lessons_completed':[{'date':(trend_start+timedelta(days=i)).isoformat(),
                                        'lessons_completed':by_day.get(trend_start+timedelta(days=i),0)} for i in range(30)]}


def platform_overview(db, principal, course=None, *, include_activity=True, include_completions=True):
    _actor(db, principal)
    if course is not None:
        _enabled(course)
        from pymentor_core.adapters.sql.course_leader_config import require_projection
        require_projection(db, course)
        _require_reconciled(db)
    row = db.execute(text('''SELECT count(*) AS users,
        count(*) FILTER (WHERE subscription_until > now()) AS active_subscribers FROM users''')).mappings().one()
    today = db.execute(text("SELECT (clock_timestamp() AT TIME ZONE 'Europe/Moscow')::date")).scalar_one()
    visits = None if course is not None or not include_activity else db.execute(text('SELECT count(DISTINCT user_id) FROM user_activity_days WHERE activity_date=:day'), {'day':today}).scalar_one()
    if course is None:
        from pymentor_core.adapters.sql.administration import admin_overview
        if not include_completions:
            dashboard = admin_overview(principal, db, include_activity=include_activity, include_completions=False)
        else:
            dashboard = admin_overview(principal, db) if include_activity else admin_overview(principal, db, include_activity=False)
    else:
        dashboard = _course_dashboard(db, course, include_completions=include_completions)
    return {**dict(row), 'courses': sum(1 for c in default_runtime().registry.entries.values() if c.enabled),
            'course_id':course, 'timezone':'Europe/Moscow',
            'activity_today':{'date':today, 'unique_users':visits, 'coverage_since':None, 'partial_day':course is None},
            'dashboard':dashboard}


def platform_courses(db, principal, cursor, limit):
    actor = _actor(db, principal)
    _require_reconciled(db)
    entries = sorted(entry.course_id for entry in default_runtime().registry.entries.values() if entry.enabled)
    offset = _cursor(cursor, actor=actor, scope='platform:courses', filters={})
    selected = entries[offset:offset+limit+1]
    nxt = _next(offset, limit, selected, actor=actor, scope='platform:courses', filters={})
    courses = []
    for course in selected[:limit]:
        from pymentor_core.adapters.sql.course_leader_config import require_projection
        require_projection(db, course)
        row = db.execute(text('''SELECT
            (SELECT user_id FROM course_leadership WHERE course_id=:course AND revoked_at IS NULL) AS leader_id'''), {'course': course}).mappings().one()
        courses.append({'course_id': course, **dict(row)})
    return {'courses': courses, 'next_cursor': nxt}


def _platform_user(db, row):
    user_id = row['id']
    ref = account_ref(db, user_id)
    if not ref:
        _error(404, 'Account not found')
    row = db.execute(text('''SELECT u.id,u.username,u.email,u.is_admin,u.subscription_until,u.subscription_plan
        FROM users u JOIN account_lifecycles a ON a.user_id=u.id WHERE u.id=:uid AND a.lifecycle_id=:ref'''),
        {'uid': user_id, 'ref': ref}).mappings().one_or_none()
    if row is None:
        _error(404, 'Account not found')
    leadership = db.execute(text('SELECT course_id FROM course_leadership WHERE user_id=:uid AND account_ref=:ref AND revoked_at IS NULL'), {'uid': user_id, 'ref': ref}).scalar_one_or_none()
    if leadership:
        from pymentor_core.adapters.sql.course_leader_config import require_projection
        require_projection(db, leadership)
    role = 'admin' if row['is_admin'] else 'leader' if leadership else 'user'
    quotas = db.execute(text('SELECT course_id,year,month FROM booking_quotas WHERE user_id=:uid AND course_id IS NOT NULL ORDER BY course_id,year,month'), {'uid': user_id}).mappings().all()
    return {'id': user_id, 'account_ref': ref, 'username': row['username'], 'email': row['email'], 'role': role,
            'subscription': {'scope': 'platform', 'user_id': user_id, 'subscription_until': row['subscription_until'],
                             'subscription_plan': row['subscription_plan']},
            'quotas': [quota_data(db, q['course_id'], user_id, q['year'], q['month'], ref) for q in quotas]}


def platform_users(db, principal, search, cursor, limit):
    actor = _actor(db, principal)
    _require_reconciled(db)
    filters = {'search': search}
    rows, nxt = _page(db, '''SELECT id,username,email,is_admin,subscription_until,subscription_plan FROM users
        WHERE (:search='' OR strpos(lower(username),lower(:search))>0 OR strpos(lower(email),lower(:search))>0)
        ORDER BY id LIMIT :limit OFFSET :offset''', {'search': search}, actor, 'platform:users', filters, cursor, limit)
    users = [_platform_user(db, r) for r in rows]
    _actor(db, principal)
    return {'users': users, 'next_cursor': nxt}


def platform_user(db, principal, user_id):
    _actor(db, principal)
    _require_reconciled(db)
    row = db.execute(text('SELECT id,username,email,is_admin,subscription_until,subscription_plan FROM users WHERE id=:uid'), {'uid': user_id}).mappings().one_or_none()
    if row is None:
        _error(404, 'Account not found')
    user = _platform_user(db, row)
    _actor(db, principal)
    return user


def operations(db, principal, cursor, limit, course, actor_id):
    actor = _actor(db, principal)
    filters = {'course_id': course, 'actor_id': actor_id}
    rows, nxt = _page(db, '''SELECT command_id,actor_id,actor_account_ref,actor_role,authority_course_id,
        target_user_id,target_account_ref,scope,course_id,action,before_value AS before,after_value AS after,
        reason,created_at,anonymized FROM role_admin_audit
        WHERE (CAST(:course AS varchar) IS NULL OR course_id=:course)
          AND (CAST(:actor_id AS integer) IS NULL OR actor_id=:actor_id)
        ORDER BY id DESC LIMIT :limit OFFSET :offset''', {'course': course, 'actor_id': actor_id}, actor,
        'platform:operations', filters, cursor, limit)
    return {'operations': [dict(r) for r in rows], 'next_cursor': nxt}
