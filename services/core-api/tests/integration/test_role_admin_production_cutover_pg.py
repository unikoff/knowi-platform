"""Disposable database checks for the caller-owned cutover transaction."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tools.maintenance.role_admin_production_cutover import cutover, plan

_spec = importlib.util.spec_from_file_location('leader_rehearsal', Path(__file__).with_name('test_course_leader_config_pg.py'))
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
database = _helpers.database
enabled = lambda course: course == 'backend'


def test_transaction_rollback_stale_and_conflicts(database):
    _helpers.migrate(database)
    engine = create_engine(database.set(drivername='postgresql+psycopg'))
    try:
        with engine.begin() as c:
            c.execute(text("INSERT INTO users(id,username,email,password_hash,verified,is_admin) VALUES (10,'admin','admin@example.test','x',true,true),(11,'student','student@example.test','x',true,false)"))
            c.execute(text("INSERT INTO booking_slots(id,slot_date,start_time,duration_minutes,student_id) VALUES (10,'2099-11-20','10:00',60,11)"))
            c.execute(text("INSERT INTO booking_quotas(id,user_id,year,month,granted_slots) VALUES (20,11,2099,11,1)"))
        with engine.begin() as c:
            digest = plan(c, admin_user_id=10, course_enabled=enabled)['sha256']
        with pytest.raises(RuntimeError, match='caller failure'):
            with engine.begin() as c:
                assert cutover(c, admin_user_id=10, expected_sha256=digest, course_enabled=enabled)['status'] == 'applied_pending_commit'
                raise RuntimeError('caller failure')
        with engine.begin() as c:
            assert c.execute(text('SELECT is_admin FROM users WHERE id=10')).scalar_one()
            assert not c.execute(text('SELECT scoped_enabled FROM role_admin_cutover WHERE id=1')).scalar_one()
            assert c.execute(text('SELECT course_id FROM booking_slots WHERE id=10')).scalar_one() is None
            assert c.execute(text('SELECT course_id FROM booking_quotas WHERE id=20')).scalar_one() is None
            assert c.execute(text('SELECT count(*) FROM booking_history')).scalar_one() == 0
            assert c.execute(text('SELECT count(*) FROM course_leadership')).scalar_one() == 0
            assert c.execute(text('SELECT count(*) FROM role_admin_audit')).scalar_one() == 0
            c.execute(text('UPDATE booking_quotas SET granted_slots=2 WHERE id=20'))
        with engine.begin() as c:
            with pytest.raises(ValueError, match='digest'):
                cutover(c, admin_user_id=10, expected_sha256=digest, course_enabled=enabled)
            assert c.execute(text('SELECT is_admin FROM users WHERE id=10')).scalar_one()
            c.execute(text('UPDATE booking_quotas SET granted_slots=0 WHERE id=20'))
            blocked_digest = plan(c, admin_user_id=10, course_enabled=enabled)['sha256']
            with pytest.raises(ValueError, match='activation blocked'):
                cutover(c, admin_user_id=10, expected_sha256=blocked_digest, course_enabled=enabled)
            assert c.execute(text('SELECT is_admin FROM users WHERE id=10')).scalar_one()
            assert c.execute(text('SELECT count(*) FROM booking_history')).scalar_one() == 0
            assert c.execute(text('SELECT course_id FROM booking_quotas WHERE id=20')).scalar_one() is None
            assert not c.execute(text('SELECT scoped_enabled FROM role_admin_cutover WHERE id=1')).scalar_one()
            c.execute(text('UPDATE booking_quotas SET granted_slots=2 WHERE id=20'))
            c.execute(text("UPDATE booking_slots SET course_id='backend' WHERE id=10"))
            with pytest.raises(ValueError, match='Partial'):
                plan(c, admin_user_id=10, course_enabled=enabled)
            c.execute(text('UPDATE booking_slots SET course_id=NULL WHERE id=10'))
            digest = plan(c, admin_user_id=10, course_enabled=enabled)['sha256']
            assert cutover(c, admin_user_id=10, expected_sha256=digest, course_enabled=enabled)['status'] == 'applied_pending_commit'
        with engine.begin() as c:
            assert not c.execute(text('SELECT is_admin FROM users WHERE id=10')).scalar_one()
            assert c.execute(text('SELECT scoped_enabled FROM role_admin_cutover WHERE id=1')).scalar_one()
            assert c.execute(text('SELECT course_id,calendar_owner_id FROM booking_slots WHERE id=10')).one() == ('backend', 10)
            assert c.execute(text('SELECT course_id FROM booking_quotas WHERE id=20')).scalar_one() == 'backend'
            assert c.execute(text('SELECT count(*) FROM booking_history')).scalar_one() == 1
            assert c.execute(text('''SELECT actor_role, action, course_id, target_user_id,
                before_value->>'role', after_value->>'role' FROM role_admin_audit''')).one() == (
                    'system', 'bootstrap_leader', 'backend', 10, 'admin', 'leader')
    finally:
        engine.dispose()
