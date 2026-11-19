"""c504 -> c527 rehearsal on a freshly created disposable PostgreSQL 16 DB."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from tools.maintenance.role_admin_bootstrap import bootstrap
from tools.maintenance.role_admin_reconcile import inventory, quota_snapshot, reconcile, slot_snapshot

CORE = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('course_progress_rehearsal', Path(__file__).with_name('test_course_progress_release.py'))
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)


@pytest.fixture
def database():
    raw = os.environ.get('KNOWI_TEST_PG_URL')
    if not raw:
        pytest.skip('KNOWI_TEST_PG_URL required for disposable PostgreSQL rehearsal')
    base = _helpers._allowlisted_url(raw)
    name = 'c527_role_ci_' + uuid4().hex
    assert re.fullmatch(r'c527_role_ci_[0-9a-f]{32}', name)
    admin_url = base.set(drivername='postgresql').render_as_string(hide_password=False)
    url = base.set(drivername='postgresql', database=name)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        db, user, version = admin.execute("SELECT current_database(),current_user,current_setting('server_version_num')::int").fetchone()
        assert (db, user) == ('pymentor_test', 'pymentor_test') and 160000 <= version < 170000
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            yield url
        finally:
            assert re.fullmatch(r'c527_role_ci_[0-9a-f]{32}', name)
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


def migrate(url, revision):
    result = _helpers._migrate(url, 'upgrade', revision)
    assert result.returncode == 0, result.stderr


def test_migration_reconciliation_and_legacy_safety(database):
    migrate(database, 'c504_course_progress')
    engine = create_engine(database.set(drivername='postgresql+psycopg'))
    try:
        with engine.begin() as c:
            c.execute(text("INSERT INTO users(id,username,email,password_hash,verified,subscription_until,subscription_plan) VALUES (1,'Student','student@example.test','synthetic',true,'2099-12-01T00:00:00Z','pro'), (2,'Owner','owner@example.test','synthetic',true,NULL,NULL)"))
            c.execute(text("INSERT INTO booking_slots(id,slot_date,start_time,duration_minutes,student_id,created_at) VALUES (10,'2099-11-20','10:00',60,1,'2020-01-02T03:04:05Z')"))
            c.execute(text("INSERT INTO booking_quotas(id,user_id,year,month,granted_slots) VALUES (20,1,2099,11,3)"))
            c.execute(text("INSERT INTO user_lesson_progress(id,user_id,lesson_id,course_id,completed_at) VALUES (30,1,'lesson-a','backend','2021-01-01T00:00:00Z')"))
            slot_before = c.execute(text('SELECT id,slot_date,start_time,duration_minutes,student_id,created_at FROM booking_slots')).mappings().one()
            quota_before = c.execute(text('SELECT id,user_id,year,month,granted_slots FROM booking_quotas')).mappings().one()
            user_before = c.execute(text('SELECT id,subscription_until,subscription_plan FROM users ORDER BY id')).all()
            progress_before = c.execute(text('SELECT id,user_id,lesson_id,course_id,completed_at FROM user_lesson_progress')).all()
            manifest = {'slots': [{'expected': slot_snapshot(slot_before), 'course_id': 'backend', 'owner_id': 2}],
                        'quotas': [{'expected': quota_snapshot(quota_before), 'course_id': 'backend'}]}
        migrate(database, 'c527_role_admin')
        with engine.begin() as c:
            assert c.execute(text('SELECT id,slot_date,start_time,duration_minutes,student_id,created_at FROM booking_slots')).mappings().one() == slot_before
            assert c.execute(text('SELECT id,user_id,year,month,granted_slots FROM booking_quotas')).mappings().one() == quota_before
            assert c.execute(text('SELECT id,subscription_until,subscription_plan FROM users ORDER BY id')).all() == user_before
            assert c.execute(text('SELECT id,user_id,lesson_id,course_id,completed_at FROM user_lesson_progress')).all() == progress_before
            assert c.execute(text('SELECT course_id,calendar_owner_id FROM booking_slots')).one() == (None, None)
            assert c.execute(text('SELECT course_id FROM booking_quotas')).scalar_one() is None
            before = inventory(c)
            assert before['mapping_blocked'] and before['writer_cutover_blocked'] and not before['cutover_ready']
            assert reconcile(c, manifest, apply=False, course_enabled=lambda x: x == 'backend')['status'] == 'ready'
            assert c.execute(text('SELECT course_id FROM booking_quotas')).scalar_one() is None
            assert reconcile(c, manifest, apply=True, course_enabled=lambda x: x == 'backend')['status'] == 'applied'
            assert reconcile(c, manifest, apply=True, course_enabled=lambda x: x == 'backend')['status'] == 'applied'
            history = c.execute(text('SELECT slot_id,course_id,student_id,owner_at_booking_id,status,created_at,legacy_coverage FROM booking_history')).all()
            assert history == [(10, 'backend', 1, 2, 'booked', None, True)]
            assert c.execute(text('SELECT id,slot_date,start_time,duration_minutes,student_id,created_at FROM booking_slots')).mappings().one() == slot_before
            assert not inventory(c)['mapping_blocked'] and inventory(c)['writer_cutover_blocked']
            bad = {'slots': [], 'quotas': [{'expected': {**manifest['quotas'][0]['expected'], 'granted_slots': 999}, 'course_id': 'backend'}]}
            with pytest.raises(ValueError, match='changed'):
                reconcile(c, bad, apply=True, course_enabled=lambda x: True)
        with engine.begin() as c:
            c.execute(text("INSERT INTO booking_quotas(user_id,year,month,granted_slots,course_id) VALUES (1,2099,11,4,'backend_copy')"))
        with pytest.raises(IntegrityError):
            with engine.begin() as c:
                c.execute(text("INSERT INTO booking_quotas(user_id,year,month,granted_slots,course_id) VALUES (1,2099,11,4,'backend')"))
        refused = _helpers._migrate(database, 'downgrade', 'c504_course_progress')
        assert refused.returncode != 0 and 'Refusing downgrade' in refused.stderr
    finally:
        engine.dispose()


def test_seed_guards_repeat_revoke_and_lifecycle(database):
    migrate(database, 'c527_role_admin')
    engine = create_engine(database.set(drivername='postgresql+psycopg'))
    try:
        with engine.begin() as c:
            assert bootstrap(c, apply=True)['reason'] == 'account_missing'
            assert bootstrap(c, apply=True, course_enabled=False)['reason'] == 'course_disabled'
            c.execute(text("INSERT INTO users(id,username,email,password_hash,verified,is_admin) VALUES (7,'Leader','kiryukhin_nik@bk.ru','synthetic',false,false)"))
            assert bootstrap(c, apply=True)['reason'] == 'account_unverified'
            c.execute(text("INSERT INTO users(id,username,email,password_hash,verified,is_admin) VALUES (6,'Duplicate','KIRYUKHIN_NIK@BK.RU','synthetic',true,false)"))
            assert bootstrap(c, apply=True)['reason'] == 'account_ambiguous'
            c.execute(text('DELETE FROM users WHERE id=6'))
            c.execute(text('UPDATE users SET verified=true,is_admin=true WHERE id=7'))
            assert bootstrap(c, apply=True)['reason'] == 'admin_collision'
            assert bootstrap(c, apply=True, demote_admin=True)['reason'] == 'last_admin'
            c.execute(text("INSERT INTO users(id,username,email,password_hash,verified,is_admin) VALUES (8,'Admin','admin@example.test','synthetic',true,true)"))
            c.execute(text("INSERT INTO course_leadership(course_id,user_id,account_ref,revision) VALUES ('backend',8,'00000000-0000-4000-8000-000000000008',1)"))
            assert bootstrap(c, apply=True, demote_admin=True)['reason'] == 'assignment_conflict'
            c.execute(text('DELETE FROM course_leadership WHERE user_id=8'))
            preview = bootstrap(c, apply=False, demote_admin=True)
            assert preview['status'] == 'ready' and preview['needs_lifecycle']
            assert c.execute(text('SELECT count(*) FROM account_lifecycles')).scalar_one() == 0
            applied = bootstrap(c, apply=True, demote_admin=True)
            assert applied['status'] == 'applied'
            assert c.execute(text('SELECT is_admin FROM users WHERE id=7')).scalar_one() is False
            assert bootstrap(c, apply=True)['status'] == 'already_applied'
            c.execute(text("UPDATE course_leadership SET revoked_at=now() WHERE user_id=7"))
            c.execute(text("UPDATE users SET email='new-address@example.test' WHERE id=7"))
            c.execute(text("INSERT INTO users(id,username,email,password_hash,verified,is_admin) VALUES (9,'Replacement','kiryukhin_nik@bk.ru','synthetic',true,false)"))
            assert bootstrap(c, apply=True)['user_id'] == 7
            assert c.execute(text('SELECT count(*) FROM course_leadership WHERE revoked_at IS NULL')).scalar_one() == 0
            c.execute(text('DELETE FROM users WHERE id=7'))
            marker = c.execute(text('SELECT user_id,account_ref FROM role_admin_bootstrap_markers')).one()
            assert marker == (None, None)
            assert bootstrap(c, apply=True)['status'] == 'already_applied'
            assert c.execute(text('SELECT count(*) FROM course_leadership')).scalar_one() == 0
            audit = c.execute(text('SELECT target_user_id,target_account_ref,reason,anonymized FROM role_admin_audit')).one()
            assert audit == (None, None, None, True)
    finally:
        engine.dispose()


def test_concurrent_bootstrap_is_applied_once(database):
    migrate(database, 'c527_role_admin')
    engine = create_engine(database.set(drivername='postgresql+psycopg'))
    try:
        with engine.begin() as c:
            c.execute(text("INSERT INTO users(id,username,email,password_hash,verified) VALUES (7,'Leader','kiryukhin_nik@bk.ru','synthetic',true)"))
        barrier = Barrier(2)
        def attempt():
            with engine.begin() as c:
                barrier.wait(timeout=5)
                return bootstrap(c, apply=True)['status']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        assert sorted(results) == ['already_applied', 'applied']
        with engine.connect() as c:
            assert c.execute(text('SELECT count(*) FROM course_leadership')).scalar_one() == 1
            assert c.execute(text('SELECT count(*) FROM role_admin_bootstrap_markers')).scalar_one() == 1
            assert c.execute(text('SELECT count(*) FROM role_admin_audit')).scalar_one() == 1
    finally:
        engine.dispose()


def test_schema_assignment_conflict_and_deletion_cleanup(database):
    migrate(database, 'c527_role_admin')
    engine = create_engine(database.set(drivername='postgresql+psycopg'))
    try:
        with engine.begin() as c:
            c.execute(text("INSERT INTO users(id,username,email,password_hash,verified) VALUES (1,'Student','a@example.test','x',true),(2,'Owner','b@example.test','x',true),(3,'Other','c@example.test','x',true),(4,'Target','d@example.test','x',true)"))
            c.execute(text("INSERT INTO course_leadership(course_id,user_id,account_ref,revision) VALUES ('backend',2,'00000000-0000-4000-8000-000000000002',1)"))
            c.execute(text("INSERT INTO booking_slots(id,slot_date,start_time,duration_minutes,course_id,calendar_owner_id) VALUES (10,'2099-11-20','10:00',60,'backend',2),(11,'2099-11-20','10:00',60,'backend_copy',3)"))
            c.execute(text("INSERT INTO booking_history(slot_id,course_id,student_id,owner_at_booking_id,status,starts_at,ends_at,created_at,legacy_coverage) VALUES (10,'backend',1,2,'booked','2099-11-20T07:00:00Z','2099-11-20T08:00:00Z','2026-11-15T00:00:00Z',false)"))
            c.execute(text("INSERT INTO role_admin_audit(command_id,actor_id,actor_account_ref,actor_role,target_user_id,target_account_ref,scope,course_id,action,before_value,after_value,reason) VALUES ('00000000-0000-4000-8000-000000000010',2,'00000000-0000-4000-8000-000000000002','leader',4,'00000000-0000-4000-8000-000000000004','course','backend','quota','{}','{}','personal')"))
            c.execute(text("INSERT INTO role_admin_commands(actor_id,actor_account_ref,target_user_id,operation,command_id,request_hash,response_status,response_body) VALUES (2,'00000000-0000-4000-8000-000000000002',4,'grant','00000000-0000-4000-8000-000000000010',repeat('a',64),200,'{}')"))
        with pytest.raises(IntegrityError):
            with engine.begin() as c:
                c.execute(text("INSERT INTO course_leadership(course_id,user_id,account_ref,revision) VALUES ('backend_copy',2,'00000000-0000-4000-8000-000000000002',2)"))
        with engine.begin() as c:
            c.execute(text('DELETE FROM users WHERE id=4'))
            tombstone = c.execute(text('SELECT target_user_id,target_account_ref,request_hash,response_status,response_body,redacted FROM role_admin_commands')).one()
            assert tombstone == (None, None, '0' * 64, 409, {}, True)
            assert c.execute(text('SELECT target_user_id,target_account_ref,reason,before_value,after_value,anonymized FROM role_admin_audit')).one() == (None, None, None, None, None, True)
            with pytest.raises(IntegrityError, match='role_admin_pending_booking_transfer_required'):
                with c.begin_nested():
                    c.execute(text('DELETE FROM users WHERE id=2'))
            assert c.execute(text('SELECT count(*) FROM users WHERE id=2')).scalar_one() == 1
            c.execute(text("UPDATE booking_slots SET slot_date='2020-01-01' WHERE id=10"))
            c.execute(text('DELETE FROM users WHERE id=2'))
            assert c.execute(text('SELECT calendar_owner_id,archived_at IS NOT NULL FROM booking_slots WHERE id=10')).one() == (None, True)
            assert c.execute(text('SELECT student_id,owner_at_booking_id FROM booking_history WHERE slot_id=10')).one() == (1, None)
            assert c.execute(text('SELECT count(*) FROM course_leadership')).scalar_one() == 0
    finally:
        engine.dispose()


def test_authority_revisions_follow_all_writers_and_deletion(database):
    migrate(database, 'c527_role_admin')
    engine = create_engine(database.set(drivername='postgresql+psycopg'))
    try:
        with engine.begin() as c:
            c.execute(text("INSERT INTO users(id,username,email,password_hash,verified) VALUES (1,'Leader','leader@example.test','x',true),(2,'Other','other@example.test','x',true)"))
            def revision(uid):
                return c.execute(text('SELECT COALESCE((SELECT revision FROM role_admin_revisions WHERE user_id=:uid),0)'), {'uid': uid}).scalar_one()
            assert revision(1) == 0
            c.execute(text('UPDATE users SET is_admin=true WHERE id=1'))
            assert revision(1) == 1
            c.execute(text('UPDATE users SET is_admin=true WHERE id=1'))
            assert revision(1) == 1
            c.execute(text('UPDATE users SET is_admin=false WHERE id=1'))
            assert revision(1) == 2
            c.execute(text("INSERT INTO course_leadership(course_id,user_id,account_ref,revision) VALUES ('backend',1,'00000000-0000-4000-8000-000000000001',3)"))
            assert revision(1) == 3
            c.execute(text('UPDATE course_leadership SET assigned_by=2 WHERE user_id=1'))
            assert revision(1) == 3
            c.execute(text("UPDATE course_leadership SET course_id='backend_copy' WHERE user_id=1"))
            assert revision(1) == 4
            c.execute(text('UPDATE course_leadership SET revoked_at=now() WHERE user_id=1'))
            assert revision(1) == 5
            c.execute(text('UPDATE course_leadership SET revoked_at=NULL WHERE user_id=1'))
            assert revision(1) == 6
            c.execute(text('UPDATE course_leadership SET user_id=2 WHERE user_id=1'))
            assert revision(1) == 7 and revision(2) == 1
            c.execute(text('DELETE FROM course_leadership WHERE user_id=2'))
            assert revision(2) == 2
            c.execute(text("INSERT INTO course_leadership(course_id,user_id,account_ref,revision) VALUES ('backend',1,'00000000-0000-4000-8000-000000000001',8)"))
            c.execute(text('DELETE FROM users WHERE id=1'))
            assert c.execute(text('SELECT count(*) FROM role_admin_revisions WHERE user_id=1')).scalar_one() == 0
    finally:
        engine.dispose()


def test_unmapped_booking_in_current_quota_month_blocks_inventory(database):
    migrate(database, 'c527_role_admin')
    engine = create_engine(database.set(drivername='postgresql+psycopg'))
    try:
        with engine.begin() as c:
            c.execute(text("INSERT INTO users(id,username,email,password_hash) VALUES (1,'Student','s@example.test','x')"))
            c.execute(text("""INSERT INTO booking_slots(slot_date,start_time,duration_minutes,student_id)
                VALUES (date_trunc('month',now() AT TIME ZONE 'UTC' + interval '3 hours')::date,'10:00',60,1)"""))
            result = inventory(c)
            assert result['current_month_unknown_bookings'] == 1
            assert result['mapping_blocked'] and not result['cutover_ready']
    finally:
        engine.dispose()
