"""Real PostgreSQL command-engine rehearsals; public routes remain cutover-gated."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from pymentor_core.adapters.sql.role_admin_commands import Command, _execute
from pymentor_core.adapters.sql import role_admin_commands as command_module
from pymentor_core.adapters.sql import course_leader_config
from tools.maintenance.course_leader_config import reconcile

_spec = importlib.util.spec_from_file_location('role_migration_rehearsal', Path(__file__).with_name('test_role_admin_migration.py'))
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
database = _m.database
_leader_spec = importlib.util.spec_from_file_location('leader_pg_commands', Path(__file__).with_name('test_course_leader_config_pg.py'))
_leader = importlib.util.module_from_spec(_leader_spec)
_leader_spec.loader.exec_module(_leader)


@pytest.fixture(autouse=True)
def configured_projection(monkeypatch):
    monkeypatch.setattr(course_leader_config, 'configured_leader', lambda course: 2 if course == 'backend' else None)


def setup_database(database):
    _leader.migrate(database)
    engine = create_engine(database.set(drivername='postgresql+psycopg'), pool_size=5)
    refs = {i: str(uuid4()) for i in (1, 2, 3)}
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO users(id,username,email,password_hash,verified,is_admin,subscription_until,subscription_plan)
            VALUES (1,'Admin','admin@example.test','x',true,true,NULL,NULL),
                   (2,'Leader','leader@example.test','x',true,false,NULL,NULL),
                   (3,'Student','student@example.test','x',true,false,'2099-01-01T00:00:00Z','pro')"""))
        for uid, ref in refs.items():
            conn.execute(text('INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (:uid,:ref)'), {'uid': uid, 'ref': ref})
        conn.execute(text("INSERT INTO course_leadership(course_id,user_id,account_ref,revision,assigned_by) VALUES ('backend',2,:ref,1,1)"), {'ref': refs[2]})
        assert reconcile(conn, {'backend':2}, apply=True)[0]['status'] == 'applied'
        revision = conn.execute(text('SELECT revision FROM role_admin_revisions WHERE user_id=2')).scalar_one()
    return engine, refs, revision


def call(engine, actor, command):
    with Session(engine) as session:
        return _execute(session, actor, command)


def test_concurrent_duplicate_and_distinct_grants_and_revocation(database):
    engine, refs, revision = setup_database(database)
    try:
        actor = SimpleNamespace(user_id=2, account_ref=refs[2])
        def command(key):
            return Command(operation='quota.grant', path='/api/admin/courses/backend/users/3/quota-grants',
                           key=key, payload={'year': 2099, 'month': 1, 'add': 3, 'reason': 'synthetic'},
                           course='backend', target_id=3, target_ref=refs[3], selection_revision=revision)
        same = command(str(uuid4()))
        def pair(items):
            barrier = Barrier(2)
            def run(item):
                barrier.wait(timeout=5)
                return call(engine, actor, item)
            with ThreadPoolExecutor(max_workers=2) as pool:
                return list(pool.map(run, items))
        first, replay = pair([same, same])
        assert jsonable_encoder(first) == jsonable_encoder(replay)
        pair([command(str(uuid4())), command(str(uuid4()))])
        with engine.connect() as conn:
            assert conn.execute(text("SELECT granted_slots FROM booking_quotas WHERE course_id='backend' AND user_id=3 AND year=2099 AND month=1")).scalar_one() == 9
            assert conn.execute(text('SELECT count(*) FROM role_admin_audit')).scalar_one() == 3
            assert conn.execute(text('SELECT count(*) FROM role_admin_commands')).scalar_one() == 3
        changed = Command(**{**same.__dict__, 'payload': {**same.payload, 'add': 4}})
        with pytest.raises(HTTPException) as mismatch:
            call(engine, actor, changed)
        assert mismatch.value.status_code == 409
        with engine.begin() as conn:
            conn.execute(text('SELECT id FROM users WHERE id=2 FOR UPDATE'))
            conn.execute(text("UPDATE course_leadership SET revoked_at=now() WHERE course_id='backend' AND revoked_at IS NULL"))
        with pytest.raises(HTTPException) as revoked:
            call(engine, actor, same)
        assert revoked.value.status_code == 403
    finally:
        engine.dispose()


def test_combined_grants_serialize_and_replay_as_one_command(database):
    from pymentor_core.adapters.sql.administration import delete_user
    from pymentor_core.modules.identity.principal import Principal

    engine, refs, revision = setup_database(database)
    try:
        actor = SimpleNamespace(user_id=2, account_ref=refs[2])
        command = Command(
            operation=command_module.COMBINED_GRANT, path='/api/admin/courses/backend/users/3/subscription-and-quota',
            key=str(uuid4()), payload={'year':2099, 'month':1, 'add':3, 'days':5, 'reason':'synthetic'},
            course='backend', target_id=3, target_ref=refs[3], selection_revision=revision,
        )

        def race(commands):
            starting = Barrier(len(commands))
            def run(item):
                starting.wait(timeout=5)
                try:
                    return jsonable_encoder(call(engine, actor, item))
                except HTTPException as error:
                    return error.status_code
            with ThreadPoolExecutor(max_workers=len(commands)) as pool:
                return list(pool.map(run, commands))

        one, two = race([command, command])
        assert one == two and one['quota']['granted'] == 3

        new = replace(command, key=str(uuid4()))
        mismatch = replace(new, payload={**new.payload, 'add':4, 'days':6})
        race_results = race([new, mismatch, replace(command, key=str(uuid4()))])
        assert race_results[:2].count(409) == 1
        winner = new if race_results[1] == 409 else mismatch
        assert race_results[2]['quota']['granted'] >= 6
        expected_quota = 6 + winner.payload['add']
        expected_subscription = datetime(2099,1,1,tzinfo=timezone.utc) + timedelta(days=10 + winner.payload['days'])
        with engine.connect() as conn:
            assert conn.execute(text("SELECT granted_slots FROM booking_quotas WHERE course_id='backend' AND user_id=3")).scalar_one() == expected_quota
            assert conn.execute(text('SELECT subscription_until FROM users WHERE id=3')).scalar_one() == expected_subscription
            assert conn.execute(text('SELECT count(*) FROM role_admin_commands')).scalar_one() == 3
            assert conn.execute(text('SELECT count(*) FROM role_admin_audit')).scalar_one() == 6

        admin = Principal.from_user(SimpleNamespace(id=1, is_admin=True), account_ref=refs[1])
        with Session(engine) as session:
            delete_user(3, admin, session, expected_account_ref=refs[3])
        with engine.connect() as conn:
            assert conn.execute(text('SELECT count(*) FROM role_admin_commands WHERE redacted')).scalar_one() == 3
            assert conn.execute(text('SELECT count(*) FROM role_admin_audit WHERE anonymized')).scalar_one() == 6
        with pytest.raises(HTTPException) as deleted:
            call(engine, actor, command)
        assert deleted.value.status_code == 409
    finally:
        engine.dispose()


def test_subscription_parallel_last_admin_and_deleted_target_tombstone(database):
    engine, refs, revision = setup_database(database)
    try:
        actor = SimpleNamespace(user_id=2, account_ref=refs[2])
        def command(key):
            return Command(operation='subscription.extend.course', path='/api/admin/courses/backend/users/3/subscription-extensions',
                           key=key, payload={'days': 3, 'reason': 'synthetic'}, course='backend',
                           target_id=3, target_ref=refs[3], selection_revision=revision)
        one, two = command(str(uuid4())), command(str(uuid4()))
        barrier = Barrier(2)
        def run(item):
            barrier.wait(timeout=5)
            return call(engine, actor, item)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(run, [one, two]))
        assert call(engine, actor, one)['subscription_until'] is not None
        with engine.connect() as conn:
            until = conn.execute(text('SELECT subscription_until FROM users WHERE id=3')).scalar_one()
            assert until == datetime(2099, 1, 1, tzinfo=timezone.utc) + timedelta(days=6)
        admin = SimpleNamespace(user_id=1, account_ref=refs[1])
        demote = Command(operation='admin.role', path='/api/admin-platform/users/1/admin-role', key=str(uuid4()),
                         payload={'is_admin': False, 'confirmed': True, 'reason': 'synthetic'}, target_id=1, target_ref=refs[1])
        with pytest.raises(HTTPException) as last:
            call(engine, admin, demote)
        assert last.value.status_code == 409
        with engine.begin() as conn:
            conn.execute(text('DELETE FROM users WHERE id=3'))
            row = conn.execute(text("SELECT redacted,target_user_id,target_account_ref FROM role_admin_commands WHERE command_id=:key"), {'key': one.key}).one()
            assert row == (True, None, None)
            new_ref = str(uuid4())
            conn.execute(text("INSERT INTO users(id,username,email,password_hash) VALUES (3,'Replacement','new@example.test','x')"))
            conn.execute(text('INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (3,:ref)'), {'ref': new_ref})
        replacement = Command(**{**one.__dict__, 'target_ref': new_ref})
        with pytest.raises(HTTPException) as tombstone:
            call(engine, actor, replacement)
        assert tombstone.value.status_code == 409
        with engine.connect() as conn:
            assert conn.execute(text('SELECT subscription_until FROM users WHERE id=3')).scalar_one() is None
    finally:
        engine.dispose()


def test_grant_and_revocation_serialize_in_both_orders(database, monkeypatch):
    engine, refs, revision = setup_database(database)
    try:
        actor = SimpleNamespace(user_id=2, account_ref=refs[2])
        def grant(rev):
            return Command(operation='quota.grant', path='/api/admin/courses/backend/users/3/quota-grants',
                           key=str(uuid4()), payload={'year': 2099, 'month': 1, 'add': 3, 'reason': 'synthetic'},
                           course='backend', target_id=3, target_ref=refs[3], selection_revision=rev)
        reached, release, revoke_finished = Event(), Event(), Event()
        original = command_module._apply
        def pause_apply(*args):
            reached.set()
            if not release.wait(10):
                raise TimeoutError('test release not received')
            return original(*args)
        monkeypatch.setattr(command_module, '_apply', pause_apply)
        def revoke():
            with engine.begin() as conn:
                conn.execute(text('SELECT id FROM users WHERE id=2 FOR UPDATE'))
                conn.execute(text("UPDATE course_leadership SET revoked_at=now() WHERE course_id='backend' AND revoked_at IS NULL"))
            revoke_finished.set()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(call, engine, actor, grant(revision))
            assert reached.wait(10)
            second = pool.submit(revoke)
            try:
                assert not revoke_finished.wait(.2)
            finally:
                release.set()
            first.result(timeout=10)
            second.result(timeout=10)
        monkeypatch.setattr(command_module, '_apply', original)
        with engine.begin() as conn:
            conn.execute(text("UPDATE course_leadership SET revoked_at=NULL WHERE course_id='backend'"))
            revision = conn.execute(text('SELECT revision FROM role_admin_revisions WHERE user_id=2')).scalar_one()
        with engine.connect() as conn:
            transaction = conn.begin()
            conn.execute(text('SELECT id FROM users WHERE id=2 FOR UPDATE'))
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(call, engine, actor, grant(revision))
                conn.execute(text("UPDATE course_leadership SET revoked_at=now() WHERE course_id='backend' AND revoked_at IS NULL"))
                transaction.commit()
                with pytest.raises(HTTPException) as revoked:
                    pending.result(timeout=10)
                assert revoked.value.status_code == 403
        with engine.connect() as conn:
            assert conn.execute(text("SELECT granted_slots FROM booking_quotas WHERE course_id='backend' AND user_id=3")).scalar_one() == 3
    finally:
        engine.dispose()


def test_platform_subscription_role_and_assignment_commands(database):
    engine, refs, revision = setup_database(database)
    try:
        admin = SimpleNamespace(user_id=1, account_ref=refs[1])
        leader = SimpleNamespace(user_id=2, account_ref=refs[2])
        rejected = Command(operation='quota.grant', path='/api/admin/courses/backend/users/3/quota-grants',
                           key=str(uuid4()), payload={'year': 2099, 'month': 2, 'add': 3, 'reason': 'x'},
                           course='backend', target_id=3, target_ref=refs[3])
        with pytest.raises(HTTPException) as selection_required:
            call(engine, leader, rejected)
        assert selection_required.value.status_code == 403
        extension = Command(operation='subscription.extend.platform', path='/api/admin-platform/users/3/subscription-extensions',
                            key=str(uuid4()), payload={'days': 2, 'reason': 'x'}, target_id=3, target_ref=refs[3])
        call(engine, admin, extension)
        call(engine, admin, extension)
        with engine.connect() as conn:
            assert conn.execute(text('SELECT subscription_until FROM users WHERE id=3')).scalar_one() == datetime(2099, 1, 3, tzinfo=timezone.utc)
        reset = Command(operation='subscription.reset', path='/api/admin-platform/users/3/subscription-reset',
                        key=str(uuid4()), payload={'confirmed': True, 'reason': 'x'}, target_id=3, target_ref=refs[3])
        assert call(engine, admin, reset)['subscription_until'] is None
        grant = Command(operation='assignment.grant', path='/api/admin-platform/courses/backend_copy/assignment',
                        key=str(uuid4()), payload={'user_id': 3, 'account_ref': refs[3], 'reason': 'x'},
                        course='backend_copy', target_id=3, target_ref=refs[3])
        assert call(engine, admin, grant)['assignment']['active'] is True
        assert call(engine, admin, grant)['assignment']['active'] is True
        role = Command(operation='admin.role', path='/api/admin-platform/users/3/admin-role',
                       key=str(uuid4()), payload={'is_admin': True, 'confirmed': True, 'reason': 'x'},
                       target_id=3, target_ref=refs[3])
        with pytest.raises(HTTPException) as active_leader:
            call(engine, admin, role)
        assert active_leader.value.status_code == 409
        revoke = Command(operation='assignment.revoke', path='/api/admin-platform/courses/backend_copy/assignment/revoke',
                         key=str(uuid4()), payload={'reason': 'x'}, course='backend_copy')
        assert call(engine, admin, revoke)['assignment']['active'] is False
        assert call(engine, admin, revoke)['assignment']['active'] is False
        assert call(engine, admin, role)['is_admin'] is True
        with engine.connect() as conn:
            assert conn.execute(text('SELECT count(*) FROM role_admin_commands')).scalar_one() == 5
            assert conn.execute(text('SELECT count(*) FROM role_admin_audit')).scalar_one() == 5
    finally:
        engine.dispose()
