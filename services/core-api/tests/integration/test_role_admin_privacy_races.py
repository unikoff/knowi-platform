"""Deterministic identity-deletion races against disposable PostgreSQL 16."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, current_thread
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from pymentor_core.adapters.sql import role_admin_commands as commands
from pymentor_core.adapters.sql import role_admin_reads as reads
from pymentor_core.adapters.sql import scoped_booking
from pymentor_core.adapters.sql import course_leader_config
from tools.maintenance.course_leader_config import reconcile as reconcile_leader
from tools.maintenance.role_admin_reconcile import activate_scoped

_spec = importlib.util.spec_from_file_location('role_migration_privacy', Path(__file__).with_name('test_role_admin_migration.py'))
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
database = _m.database


def _migrate(url):
    env = {k: v for k, v in os.environ.items() if k in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
    env.update(PYTHONDONTWRITEBYTECODE='1',
               KNOWI_MIGRATION_URL=url.set(drivername='postgresql+asyncpg').render_as_string(hide_password=False),
               KNOWI_MIGRATION_HOST='127.0.0.1', KNOWI_MIGRATION_DATABASE=url.database,
               KNOWI_MIGRATION_USER=url.username)
    script = ("from alembic import command; from alembic.config import Config; import sys; "
              "c=Config(sys.argv[1]); c.set_main_option('core_runtime_migrations','true'); "
              "command.upgrade(c,'c528_course_leaders')")
    result = subprocess.run([sys.executable, '-c', script, str(_m.CORE / 'alembic.ini')],
                            env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def state(database, monkeypatch):
    _migrate(database)
    monkeypatch.setattr(course_leader_config, 'configured_leader', lambda course: 4)
    engine = create_engine(database.set(drivername='postgresql+psycopg'))
    refs = {i: str(uuid4()) for i in range(1, 5)}
    try:
        with engine.begin() as c:
            for uid, admin in [(1, True), (2, False), (3, False), (4, False)]:
                c.execute(text('INSERT INTO users(id,username,email,password_hash,verified,is_admin) VALUES (:id,:name,:email,\'x\',true,:admin)'),
                          {'id': uid, 'name': f'user{uid}', 'email': f'user{uid}@example.test', 'admin': admin})
                c.execute(text('INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (:id,:ref)'), {'id': uid, 'ref': refs[uid]})
            c.execute(text("INSERT INTO course_leadership(course_id,user_id,account_ref,revision,assigned_by) VALUES ('backend',4,:ref,1,1)"), {'ref': refs[4]})
            assert reconcile_leader(c, {'backend':4}, apply=True)[0]['status'] == 'applied'
            c.execute(text("INSERT INTO booking_slots(id,course_id,calendar_owner_id,slot_date,start_time,duration_minutes) VALUES (10,'backend',4,'2020-01-01','10:00',60)"))
            c.execute(text("""INSERT INTO booking_history(id,course_id,slot_id,student_id,owner_at_booking_id,status,starts_at,ends_at,cancelled_at)
                VALUES (20,'backend',10,3,2,'cancelled','2020-01-01T07:00:00Z','2020-01-01T08:00:00Z',now())"""))
            c.execute(text("INSERT INTO user_lesson_progress(user_id,lesson_id,course_id,completed_at) VALUES (3,'old-lesson','backend',now())"))
            assert activate_scoped(c, apply=True, confirmed=True, course_enabled=lambda _: True)['status'] == 'applied'
        yield engine, refs
    finally:
        engine.dispose()


def _delete_owner(engine):
    with engine.begin() as c:
        c.execute(text('DELETE FROM users WHERE id=2'))


def _replace_student(engine):
    with engine.begin() as c:
        c.execute(text('SELECT id FROM users WHERE id=3 FOR UPDATE'))
        c.execute(text('DELETE FROM user_lesson_progress WHERE user_id=3'))
        c.execute(text('DELETE FROM booking_quotas WHERE user_id=3'))
        c.execute(text('DELETE FROM users WHERE id=3'))
        c.execute(text("INSERT INTO users(id,username,email,password_hash,verified) VALUES (3,'replacement','replacement@example.test','x',true)"))
        c.execute(text('INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (3,:ref)'), {'ref': str(uuid4())})
        c.execute(text("INSERT INTO user_lesson_progress(user_id,lesson_id,course_id,completed_at) VALUES (3,'replacement-private','backend',now())"))


@pytest.mark.parametrize('deletion_wins', [True, False])
def test_cancel_replay_and_former_owner_deletion(state, monkeypatch, deletion_wins):
    engine, refs = state
    principal = SimpleNamespace(user_id=3, account_ref=refs[3])
    command = commands.Command('booking.cancel', '/api/courses/backend/bookings/20/cancel', str(uuid4()), {}, course='backend')
    with Session(engine) as db:
        commands._store(db, principal, command, 3, refs[3], commands.request_hash(command),
                        {'booking': {'owner_at_booking_id': 2}}, subjects={2, 3, 4})
        db.commit()
    entered, release, deleted = Event(), Event(), Event()
    original = commands._command_row
    count = 0

    def pause(db, actor, cmd, **kwargs):
        nonlocal count
        result = original(db, actor, cmd, **kwargs)
        if current_thread().name.startswith('replay'):
            count += 1
            if count == (1 if deletion_wins else 2):
                entered.set()
                assert release.wait(10)
        return result

    monkeypatch.setattr(commands, '_command_row', pause)

    def replay():
        with Session(engine) as db:
            try:
                return scoped_booking.execute(db, principal, command, resource_id=20)
            except HTTPException as exc:
                return exc.status_code

    def delete():
        _delete_owner(engine)
        deleted.set()

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='replay') as pool, ThreadPoolExecutor(max_workers=1) as deleter:
        pending = pool.submit(replay)
        assert entered.wait(10)
        try:
            deletion = deleter.submit(delete)
            if deletion_wins:
                assert deleted.wait(10)
            else:
                assert not deleted.wait(.25)
        finally:
            release.set()
        result = pending.result(timeout=10)
        deletion.result(timeout=10)
    assert result == (409 if deletion_wins else {'booking': {'owner_at_booking_id': 2}})
    with engine.connect() as c:
        row = c.execute(text('SELECT redacted,response_body FROM role_admin_commands')).one()
        assert row[0] and '2' not in str(row[1])
        assert c.execute(text('SELECT owner_at_booking_id FROM booking_history WHERE id=20')).scalar_one() is None


@pytest.mark.parametrize('deletion_wins', [True, False])
@pytest.mark.parametrize('view', ['progress', 'statistics', 'profile-details'])
@pytest.mark.parametrize('quota_only', [False, True], ids=['booking', 'quota'])
def test_roster_progress_and_target_id_reuse(state, monkeypatch, deletion_wins, view, quota_only):
    engine, refs = state
    if quota_only:
        with engine.begin() as c:
            c.execute(text('DELETE FROM booking_history WHERE student_id=3'))
            c.execute(text("INSERT INTO booking_quotas(user_id,course_id,year,month,granted_slots) VALUES (3,'backend',2020,1,1)"))
    if view == 'profile-details':
        with engine.begin() as c:
            c.execute(text("UPDATE users SET subscription_until='2099-12-31T21:00:00Z',subscription_plan='pro' WHERE id=3"))
    principal = SimpleNamespace(user_id=4, account_ref=refs[4])
    entered, release, replaced = Event(), Event(), Event()
    original_lock = commands._lock_users
    original_membership = reads._is_course_member
    lesson_ids = ['old-lesson', 'replacement-private']
    content = SimpleNamespace(
        get_tracks_summary=lambda: [{
            'id': 'track', 'title': 'Track', 'lessons_countable': len(lesson_ids),
            'countable_lesson_ids': lesson_ids,
        }],
        find_track=lambda track: {'lessons': [{'id': item, 'title': item} for item in lesson_ids]},
        default_track_id=lambda: 'track',
        get_content_revision=lambda: 'fixture',
    )
    if view == 'statistics':
        from pymentor_core.app.wiring import configure_bindings
        configure_bindings()

    def pause_lock(db, ids):
        if deletion_wins and current_thread().name.startswith('reader'):
            entered.set()
            assert release.wait(10)
        return original_lock(db, ids)

    monkeypatch.setattr(commands, '_lock_users', pause_lock)

    def pause_membership(db, course, user_id, **kwargs):
        result = original_membership(db, course, user_id, **kwargs)
        if not deletion_wins and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return result

    monkeypatch.setattr(reads, '_is_course_member', pause_membership)

    def read():
        with Session(engine) as db:
            try:
                if view == 'statistics':
                    return reads.student_statistics(db, principal, 'backend', 3, refs[3], content, None)
                if view == 'profile-details':
                    return reads.student_profile_details(db, principal, 'backend', 3, refs[3], 2020, 1, None, 20, None)
                return reads.progress(db, principal, 'backend', 3)
            except HTTPException as exc:
                return exc.status_code

    def replace():
        _replace_student(engine)
        replaced.set()

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='reader') as pool, ThreadPoolExecutor(max_workers=1) as deleter:
        pending = pool.submit(read)
        assert entered.wait(10)
        try:
            replacement = deleter.submit(replace)
            if deletion_wins:
                assert replaced.wait(10)
            else:
                assert not replaced.wait(.25)
        finally:
            release.set()
        result = pending.result(timeout=10)
        replacement.result(timeout=10)
    if deletion_wins:
        assert result == 404
    elif view == 'statistics':
        assert [lesson['id'] for lesson in result['courses']['track'] if lesson['completed']] == ['old-lesson']
    elif view == 'profile-details':
        assert result['account_ref'] == refs[3] and result['subscription_plan'] == 'pro'
        assert [booking['id'] for booking in result['bookings']] == ([] if quota_only else [20])
    else:
        assert [lesson['id'] for lesson in result['completed_lessons']] == ['old-lesson']
        assert 'replacement-private' not in str(result)
