"""Local isolated c528 migration and lifecycle-pinned leader reconciliation."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from sqlalchemy import create_engine, text
from tools.maintenance.course_leader_config import migrate_legacy_admins, reconcile

_spec = importlib.util.spec_from_file_location('role_migration_leader', Path(__file__).with_name('test_role_admin_migration.py'))
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
database = _m.database


def migrate(url):
    env = {k:v for k,v in os.environ.items() if k in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP'}}
    env.update(PYTHONDONTWRITEBYTECODE='1', KNOWI_MIGRATION_URL=url.set(drivername='postgresql+asyncpg').render_as_string(hide_password=False),
               KNOWI_MIGRATION_HOST='127.0.0.1', KNOWI_MIGRATION_DATABASE=url.database, KNOWI_MIGRATION_USER=url.username)
    script = ("from alembic import command; from alembic.config import Config; import sys; "
              "c=Config(sys.argv[1]); c.set_main_option('core_runtime_migrations','true'); command.upgrade(c,'c528_course_leaders')")
    result = subprocess.run([sys.executable,'-B','-c',script,str(_m.CORE/'alembic.ini')], env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr


def test_projection_binding_lifecycle_and_conflicts(database):
    migrate(database)
    engine = create_engine(database.set(drivername='postgresql+psycopg'))
    try:
        with engine.begin() as c:
            c.execute(text("INSERT INTO users(id,username,email,password_hash,verified,is_admin) VALUES (1,'admin','a@example.test','x',true,true),(2,'leader','b@example.test','x',true,false),(3,'unverified','c@example.test','x',false,false)"))
            assert reconcile(c, {'backend':999})[0]['reason'] == 'account_missing'
            assert reconcile(c, {'backend':1})[0]['reason'] == 'admin_collision'
            assert reconcile(c, {'backend':3})[0]['reason'] == 'account_unverified'
            assert all(x['reason'] == 'duplicate_configured_user' for x in reconcile(c, {'backend':2,'backend_copy':2}))
            assert reconcile(c, {'backend':2})[0]['status'] == 'ready'
            assert reconcile(c, {'backend':2}, apply=True)[0]['status'] == 'applied'
            first = c.execute(text('SELECT user_id,account_ref FROM course_leader_bindings WHERE course_id=\'backend\'')).one()
            assert reconcile(c, {'backend':2}, apply=True)[0]['status'] == 'applied'
            assert c.execute(text('SELECT count(*) FROM course_leadership WHERE revoked_at IS NULL')).scalar_one() == 1
        with engine.begin() as c:
            c.execute(text("UPDATE course_leadership SET revoked_at=now() WHERE course_id='backend'"))
            assert reconcile(c, {'backend':2}, apply=True)[0]['reason'] == 'projection_revoked'
        with engine.begin() as c:
            c.execute(text('DELETE FROM users WHERE id=2'))
            c.execute(text("INSERT INTO users(id,username,email,password_hash,verified) VALUES (2,'replacement','replacement@example.test','x',true)"))
            c.execute(text('INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (2,:ref)'), {'ref':str(uuid4())})
            assert reconcile(c, {'backend':2}, apply=True)[0]['reason'] == 'binding_tombstoned'
            row = c.execute(text('SELECT user_id,account_ref FROM course_leader_bindings WHERE course_id=\'backend\'')).one()
            assert row == (None, first.account_ref)
    finally:
        engine.dispose()


def test_existing_owner_future_conflict_and_legacy_seed_tombstone(database):
    migrate(database)
    engine = create_engine(database.set(drivername='postgresql+psycopg'))
    ref = str(uuid4())
    try:
        with engine.begin() as c:
            c.execute(text("INSERT INTO users(id,username,email,password_hash,verified) VALUES (2,'owner','owner@example.test','x',true),(3,'former','former@example.test','x',true)"))
            c.execute(text('INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (2,:ref)'), {'ref':ref})
            c.execute(text("INSERT INTO course_leadership(course_id,user_id,account_ref,revision) VALUES ('backend',2,:ref,1)"), {'ref':ref})
            c.execute(text("INSERT INTO role_admin_bootstrap_markers(marker,user_id,account_ref) VALUES ('backend-leader-v1',2,:ref)"), {'ref':ref})
            c.execute(text("INSERT INTO booking_slots(id,course_id,calendar_owner_id,slot_date,start_time,duration_minutes) VALUES (10,'backend',3,'2099-01-01','10:00',60)"))
            assert reconcile(c, {'backend':2}, apply=True)[0]['reason'] == 'future_owner_conflict'
            assert c.execute(text('SELECT count(*) FROM course_leader_bindings')).scalar_one() == 0
            c.execute(text('UPDATE booking_slots SET archived_at=now() WHERE id=10'))
            assert reconcile(c, {'backend':2}, apply=True)[0]['status'] == 'applied'
            assert c.execute(text('SELECT count(*) FROM course_leadership')).scalar_one() == 1
        with engine.begin() as c:
            c.execute(text("DELETE FROM course_leader_bindings WHERE course_id='backend'"))
            c.execute(text("UPDATE course_leadership SET revoked_at=now() WHERE course_id='backend'"))
            assert reconcile(c, {'backend':2}, apply=True)[0]['reason'] == 'legacy_seed_revoked'
            c.execute(text('DELETE FROM users WHERE id=2'))
            c.execute(text("INSERT INTO users(id,username,email,password_hash,verified) VALUES (2,'replacement','replacement@example.test','x',true)"))
            c.execute(text('INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (2,:ref)'), {'ref':str(uuid4())})
            assert reconcile(c, {'backend':2}, apply=True)[0]['reason'] == 'legacy_seed_tombstoned'
    finally:
        engine.dispose()


def test_legacy_admin_migration_is_atomic_and_one_time(database):
    migrate(database)
    engine = create_engine(database.set(drivername='postgresql+psycopg'))
    try:
        with engine.begin() as c:
            c.execute(text("INSERT INTO users(id,username,email,password_hash,verified,is_admin) VALUES (1,'one','one@example.test','x',true,true),(2,'two','two@example.test','x',true,true)"))
            assert migrate_legacy_admins(c, {'backend': 1}, apply=True)[0]['reason'] == 'admin_mapping_not_unique'
            assert any(item['reason'] == 'account_missing' for item in migrate_legacy_admins(c, {'backend': 1, 'other': 2, 'missing': 999}, apply=True))
            assert c.execute(text('SELECT count(*) FROM users WHERE is_admin')).scalar_one() == 2
            assert c.execute(text('SELECT count(*) FROM course_leadership')).scalar_one() == 0
            result = migrate_legacy_admins(c, {'backend': 1, 'other': 2})
            assert all(item['status'] == 'ready' for item in result)
            assert c.execute(text('SELECT count(*) FROM users WHERE is_admin')).scalar_one() == 2
            assert c.execute(text('SELECT count(*) FROM course_leader_bindings')).scalar_one() == 0
            assert all(item['status'] == 'applied' for item in migrate_legacy_admins(c, {'backend': 1, 'other': 2}, apply=True))
            assert c.execute(text('SELECT count(*) FROM users WHERE is_admin')).scalar_one() == 0
            assert c.execute(text('SELECT count(*) FROM course_leadership WHERE revoked_at IS NULL')).scalar_one() == 2
            c.execute(text("INSERT INTO users(id,username,email,password_hash,verified,is_admin) VALUES (3,'new','new@example.test','x',true,true)"))
            assert migrate_legacy_admins(c, {'backend': 1, 'other': 2}, apply=True)[0]['reason'] == 'legacy_admins_already_migrated'
            assert c.execute(text('SELECT is_admin FROM users WHERE id=3')).scalar_one()
    finally:
        engine.dispose()


def test_empty_legacy_admin_migration_sets_marker(database):
    migrate(database)
    engine = create_engine(database.set(drivername='postgresql+psycopg'))
    try:
        with engine.begin() as c:
            assert migrate_legacy_admins(c, {}, apply=True)[0]['status'] == 'applied'
            assert migrate_legacy_admins(c, {}, apply=True)[0]['reason'] == 'legacy_admins_already_migrated'
    finally:
        engine.dispose()


def test_admin_cli_only_promotes_an_unassigned_user_after_migration(database):
    migrate(database)
    url = database.set(drivername='postgresql+psycopg')
    engine = create_engine(url)

    def apply(email, mode='--apply'):
        env = {key: value for key, value in os.environ.items() if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
        run = subprocess.run([sys.executable, '-B', '-m', 'tools.maintenance.make_admin', email,
                              '--database-url', url.render_as_string(hide_password=False), mode], cwd=_m.CORE,
                             env=env, capture_output=True, timeout=25)
        return run.returncode

    try:
        with engine.begin() as c:
            c.execute(text("""INSERT INTO users(id,username,email,password_hash,verified,is_admin)
                VALUES (1,'legacy','legacy@example.test','x',true,true), (2,'candidate','candidate@example.test','x',true,false)"""))
        assert apply('candidate@example.test') == 2
        with engine.begin() as c:
            assert migrate_legacy_admins(c, {'backend': 1}, apply=True)[0]['status'] == 'applied'
        assert apply('legacy@example.test') == 2
        assert apply('candidate@example.test', '--dry-run') == 0
        with engine.connect() as c:
            assert not c.execute(text('SELECT count(*) FROM users WHERE is_admin')).scalar_one()
        assert apply('candidate@example.test') == 0
        with engine.connect() as c:
            assert c.execute(text('SELECT id FROM users WHERE is_admin')).scalars().all() == [2]
            assert c.execute(text('SELECT user_id FROM course_leadership WHERE revoked_at IS NULL')).scalars().all() == [1]
    finally:
        engine.dispose()
