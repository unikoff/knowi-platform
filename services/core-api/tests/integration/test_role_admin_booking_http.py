"""Real scoped calendar HTTP on an isolated PG16 database and synthetic Redis prefix."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

_spec = importlib.util.spec_from_file_location('role_migration_booking_http', Path(__file__).with_name('test_role_admin_migration.py'))
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
database = _m.database
_leader_spec = importlib.util.spec_from_file_location('leader_pg_booking', Path(__file__).with_name('test_course_leader_config_pg.py'))
_leader = importlib.util.module_from_spec(_leader_spec)
_leader_spec.loader.exec_module(_leader)
_courses_spec = importlib.util.spec_from_file_location('role_test_courses_booking', Path(__file__).with_name('role_admin_test_courses.py'))
_courses = importlib.util.module_from_spec(_courses_spec)
_courses_spec.loader.exec_module(_courses)


def test_scoped_booking_real_http_and_cutover(database):
    _leader.migrate(database)
    env = os.environ.copy()
    env.update(DATABASE_URL=database.set(drivername='postgresql+psycopg').render_as_string(hide_password=False),
               REDIS_URL='redis://127.0.0.1:57381/15', KNOWI_REDIS_PREFIX='c527-booking-' + uuid4().hex + ':',
               KNOWI_LOGIN_RECOVERY_KEY='b' * 64, PYMENTOR_PROFILE='postgres_redis', PYMENTOR_SCHEMA_MODE='managed',
               PYMENTOR_LOCAL_NOTIFICATIONS='0')
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])
    env.pop('PYMENTOR_REDIS_PREFIX', None)
    env.pop('PYMENTOR_LOGIN_RECOVERY_KEY', None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    with _courses.courses_for({'backend':2,'backend_copy':5}) as copied:
        env['PYMENTOR_COURSES_ROOT'] = str(copied)
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--exercise'], env=env,
                                cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr


async def exercise():
    from datetime import datetime, timedelta, timezone
    import httpx
    from sqlalchemy import create_engine, text
    from pymentor_core.modules.identity.passwords import hash_password
    from pymentor_core.app.asgi import create_app
    from tools.maintenance.course_leader_config import reconcile

    engine = create_engine(os.environ['DATABASE_URL'])
    refs = {i: str(uuid4()) for i in range(1, 8)}
    password = 'synthetic-password'
    with engine.begin() as conn:
        for uid, name, admin in [(1, 'Admin', True), (2, 'Leader', False), (3, 'Student', False),
                                 (4, 'StudentTwo', False), (5, 'OtherLeader', False), (6, 'Past', False), (7, 'Outsider', False)]:
            conn.execute(text('INSERT INTO users(id,username,email,password_hash,verified,is_admin) VALUES (:id,:name,:email,:password,true,:admin)'),
                         dict(id=uid, name=name, email=f'{name.lower()}@example.test', password=hash_password(password), admin=admin))
            conn.execute(text('INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (:id,:ref)'), {'id': uid, 'ref': refs[uid]})
        for course, uid in [('backend', 2), ('backend_copy', 5)]:
            conn.execute(text('INSERT INTO course_leadership(course_id,user_id,account_ref,revision,assigned_by) VALUES (:course,:uid,:ref,1,1)'),
                         {'course': course, 'uid': uid, 'ref': refs[uid]})
        assert all(item['status'] == 'applied' for item in reconcile(conn, {'backend':2,'backend_copy':5}, apply=True))
        for uid in (3, 4):
            for course in ('backend', 'backend_copy'):
                conn.execute(text("INSERT INTO booking_quotas(course_id,user_id,year,month,granted_slots) VALUES (:course,:uid,2099,11,3)"), {'course': course, 'uid': uid})
        # Former owner in another course must still block overlapping owner slots.
        conn.execute(text("INSERT INTO booking_slots(id,course_id,calendar_owner_id,slot_date,start_time,duration_minutes) VALUES (30,'backend_copy',2,'2099-11-15','14:00',60)"))
        conn.execute(text("INSERT INTO booking_slots(id,course_id,calendar_owner_id,slot_date,start_time,duration_minutes,student_id) VALUES (31,'backend',2,'2020-01-01','10:00',60,6)"))
        conn.execute(text("""INSERT INTO booking_history(course_id,slot_id,student_id,owner_at_booking_id,status,starts_at,ends_at,created_at,legacy_coverage)
            VALUES ('backend',31,6,2,'booked','2020-01-01T07:00:00Z','2020-01-01T08:00:00Z',NULL,true)"""))
        moscow = timezone(timedelta(hours=3))
        day = (datetime.now(moscow) + timedelta(days=2)).date()
        for uid, course in [(3,'backend'),(4,'backend'),(6,'backend_copy')]:
            conn.execute(text('''INSERT INTO booking_quotas(course_id,user_id,year,month,granted_slots)
                VALUES (:course,:uid,:year,:month,3)'''), {'course':course,'uid':uid,'year':day.year,'month':day.month})
        for slot, course, owner, hour, student in [(40,'backend',2,8,3),(41,'backend',2,9,4),
                (42,'backend',2,10,None),(43,'backend',2,11,None),(44,'backend_copy',5,8,6),(45,'backend_copy',5,9,None)]:
            conn.execute(text('''INSERT INTO booking_slots(id,course_id,calendar_owner_id,slot_date,start_time,duration_minutes,student_id)
                VALUES (:id,:course,:owner,:day,:start,60,:student)'''),
                {'id':slot,'course':course,'owner':owner,'day':day,'start':f'{hour:02}:00','student':student})
            if student:
                start = datetime(day.year, day.month, day.day, hour, tzinfo=moscow).astimezone(timezone.utc)
                conn.execute(text('''INSERT INTO booking_history(course_id,slot_id,student_id,owner_at_booking_id,status,starts_at,ends_at)
                    VALUES (:course,:slot,:student,:owner,'booked',:start,:end)'''),
                    {'course':course,'slot':slot,'student':student,'owner':owner,'start':start,'end':start+timedelta(hours=1)})
        conn.execute(text('''INSERT INTO booking_slots(id,course_id,calendar_owner_id,slot_date,start_time,duration_minutes,archived_at)
            VALUES (46,'backend',2,:day,'12:00',60,now())'''), {'day':day})
    app = create_app()
    clients = {}
    try:
        async with app.router.lifespan_context(app):
            clients = {i: httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(uuid4().hex, 1234)), base_url='http://test') for i in (1, 2, 3, 4, 5, 7)}
            for uid, name in [(1,'admin'), (2,'leader'), (3,'student'), (4,'studenttwo'), (5,'otherleader'), (7,'outsider')]:
                response = await clients[uid].post('/api/user/login', json={'email':name+'@example.test','password':password}, headers={'Idempotency-Key':str(uuid4())})
                assert response.status_code == 200, (uid, response.status_code, response.text)

            async def request(uid, method, path, expected, **kwargs):
                response = await clients[uid].request(method, path, **kwargs)
                assert response.status_code == expected, (uid, method, path, expected, response.status_code, response.text)
                return response

            def cli(mode, *extra):
                result = subprocess.run([sys.executable, '-m', 'tools.maintenance.role_admin_reconcile', '--database-url', os.environ['DATABASE_URL'],
                    '--activate-scoped', mode, *extra], capture_output=True, text=True, timeout=20, env=os.environ.copy())
                assert result.returncode == 0, result.stderr
                return json.loads(result.stdout)

            base = '/api/admin/courses/backend'
            headers = lambda: {'Idempotency-Key': str(uuid4())}
            slot_payload = {'owner_id': 2, 'slot_date': '2099-11-15', 'start_time': '12:00', 'duration_minutes': 60}
            await request(2, 'POST', base+'/slots', 503, json=slot_payload, headers=headers())
            legacy = await request(1, 'POST', '/api/admin/slots', 200,
                json={'slot_date': '2099-11-30', 'start_time': '10:00', 'duration_minutes': 60})
            assert cli('--dry-run')['reason'] == 'unreconciled_active_data'
            await request(1, 'DELETE', '/api/admin/slots/' + str(legacy.json()['slot']['id']), 200)
            assert cli('--dry-run')['status'] == 'ready'
            assert cli('--apply', '--confirm-retire-legacy')['status'] == 'applied'
            await request(1, 'POST', '/api/admin/slots', 503,
                json={'slot_date': '2099-11-30', 'start_time': '10:00', 'duration_minutes': 60})
            await request(1, 'PATCH', '/api/admin/users/7/subscription', 503, json={'days':3})
            await request(1, 'POST', '/api/admin/users/7/quota', 503, json={'year':2099,'month':11,'add':1})
            cli_env = os.environ.copy()
            cli_env.pop('DATABASE_URL', None)
            admin_cli = [sys.executable, '-m', 'tools.maintenance.make_admin', 'admin@example.test',
                         '--database-url', os.environ['DATABASE_URL'], '--revoke']
            preview = subprocess.run([*admin_cli, '--dry-run'], capture_output=True, text=True, timeout=20, env=cli_env)
            assert preview.returncode == 0 and 'Preview:' in preview.stdout, (preview.stdout, preview.stderr)
            refused = subprocess.run([*admin_cli, '--apply'], capture_output=True, text=True, timeout=20, env=cli_env)
            assert refused.returncode != 0 and 'Cannot remove the last platform administrator' in refused.stderr, (refused.stdout, refused.stderr)
            with engine.connect() as conn:
                assert conn.execute(text('SELECT is_admin FROM users WHERE id=1')).scalar_one() is True
            roster = (await request(2, 'GET', base+'/roster', 200)).json()
            assert next(u for u in roster['users'] if u['id'] == 6)['last_booking_status'] == 'completed'
            overview = (await request(1, 'GET', '/api/admin/overview', 200)).json()
            assert (overview['slots_30d']['total'], overview['slots_30d']['booked']) == (6,3), overview['slots_30d']
            assert (overview['slots_7d']['total'], overview['slots_7d']['booked']) == (6,3), overview['slots_7d']
            assert len(overview['upcoming_bookings']) == 3
            key = str(uuid4())
            one = (await request(2, 'POST', base+'/slots', 200, json=slot_payload, headers={'Idempotency-Key':key})).json()
            repeated = (await request(2, 'POST', base+'/slots', 200, json=slot_payload, headers={'Idempotency-Key':key})).json()
            assert one == repeated
            slot1 = one['slot']['id']
            await request(2, 'POST', base+'/slots', 409, json=slot_payload, headers=headers())
            await request(2, 'POST', base+'/slots', 409, json={**slot_payload, 'start_time':'14:00'}, headers=headers())
            otherbase = '/api/admin/courses/backend_copy'
            other = (await request(5, 'POST', otherbase+'/slots', 200,
                json={**slot_payload, 'owner_id':5}, headers=headers())).json()['slot']['id']
            await request(2, 'GET', otherbase+'/slots', 403, params={'owner_id':5,'year':2099,'month':11})
            await request(1, 'GET', base+'/slots', 200, params={'owner_id':2,'year':2099,'month':11})
            legacy_visible = (await request(3, 'GET', '/api/user/slots', 200, params={'year':2099,'month':11})).json()
            assert other not in {item['id'] for item in legacy_visible['slots']}
            bookpath = f'/api/courses/backend/booking/slots/{slot1}/book'
            a, b = await asyncio.gather(clients[3].post(bookpath, headers=headers()), clients[4].post(bookpath, headers=headers()))
            assert sorted([a.status_code,b.status_code]) == [200,409], (a.status_code,a.text,b.status_code,b.text)
            winner, booked = (3, a.json()) if a.status_code == 200 else (4, b.json())
            booking_id = booked['booking']['id']
            await request(winner, 'POST', f'/api/courses/backend_copy/booking/slots/{other}/book', 409, headers=headers())
            await request(2, 'POST', f'{base}/slots/{slot1}/archive', 409, json={'reason':'x'}, headers=headers())
            cancel_key = str(uuid4())
            cancelpath = f'/api/courses/backend/booking/bookings/{booking_id}/cancel'
            cancel_a, cancel_b = await asyncio.gather(
                clients[winner].post(cancelpath, headers={'Idempotency-Key':cancel_key}),
                clients[winner].post(cancelpath, headers={'Idempotency-Key':cancel_key}))
            assert cancel_a.status_code == cancel_b.status_code == 200, (cancel_a.text, cancel_b.text)
            assert cancel_a.json() == cancel_b.json()
            cancelled = cancel_a.json()
            assert cancelled['booking']['status'] == 'cancelled' and cancelled['quota']['remaining'] == 3
            assert (await request(winner, 'POST', cancelpath, 200, headers={'Idempotency-Key':cancel_key})).json() == cancelled
            await request(winner, 'POST', cancelpath, 409, headers=headers())
            await request(2, 'POST', f'{base}/slots/{slot1}/archive', 200, json={'reason':'x'}, headers=headers())
            await request(1, 'DELETE', f'/api/admin/slots/{slot1}', 409)
            await request(1, 'POST', f'/api/user/slots/{slot1}/book', 503)
            await request(2, 'POST', base+'/users/7/subscription-extensions', 403,
                json={'days':3,'reason':'x'}, headers={**headers(), 'X-Account-Ref':refs[7]})
            found = (await request(2, 'GET', base+'/grant-search', 200, params={'q':'outsider@example.test'})).json()
            assert found['users'][0]['id'] == 7
            grantkey = str(uuid4())
            grant_headers = {'Idempotency-Key':grantkey, 'X-Account-Ref':refs[7]}
            ext = (await request(2, 'POST', base+'/users/7/subscription-extensions', 200,
                                 json={'days':3,'reason':'x'}, headers=grant_headers)).json()
            store = app.state.identity_sessions.redis
            lease = store._role_selection_key(refs[2], 'backend', refs[7])
            await store._client.delete(lease)
            assert (await request(2, 'POST', base+'/users/7/subscription-extensions', 200,
                                  json={'days':3,'reason':'x'}, headers=grant_headers)).json() == ext
            await request(2, 'POST', base+'/users/7/subscription-extensions', 409,
                          json={'days':4,'reason':'x'}, headers=grant_headers)
            secondslot = (await request(2, 'POST', base+'/slots', 200,
                json={**slot_payload,'start_time':'16:00'}, headers=headers())).json()['slot']['id']
            booked2_headers = headers()
            booked2 = (await request(winner, 'POST', f'/api/courses/backend/booking/slots/{secondslot}/book', 200, headers=booked2_headers)).json()['booking']
            await request(1, 'DELETE', '/api/admin/users/2', 409, headers={'X-Account-Ref':refs[2]})
            duplicate_key = str(uuid4())
            duplicate_payload = {**slot_payload,'owner_id':2,'start_time':'18:00'}
            duplicate_a, duplicate_b = await asyncio.gather(
                clients[1].post(base+'/slots', json=duplicate_payload, headers={'Idempotency-Key':duplicate_key}),
                clients[1].post(base+'/slots', json=duplicate_payload, headers={'Idempotency-Key':duplicate_key}))
            assert duplicate_a.status_code == duplicate_b.status_code == 200, (duplicate_a.text, duplicate_b.text)
            assert duplicate_a.json() == duplicate_b.json()
            context = (await request(winner, 'GET', '/api/courses/backend/booking/context', 200)).json()
            assert context['active_owner_id'] == 2 and next(b for b in context['my_upcoming_bookings'] if b['id']==booked2['id'])['owner_at_booking_id'] == 2
            booked2_cancel_headers = headers()
            booked2_cancel_path = f"/api/courses/backend/booking/bookings/{booked2['id']}/cancel"
            await request(winner, 'POST', booked2_cancel_path, 200, headers=booked2_cancel_headers)
            # A single student racing overlapping slots in two courses gets at most one.
            slot_a = (await request(1, 'POST', base+'/slots', 200,
                json={**slot_payload,'owner_id':2,'slot_date':'2099-11-16','start_time':'10:00'}, headers=headers())).json()['slot']['id']
            slot_b = (await request(5, 'POST', otherbase+'/slots', 200,
                json={**slot_payload,'owner_id':5,'slot_date':'2099-11-16','start_time':'10:00'}, headers=headers())).json()['slot']['id']
            cross_a, cross_b = await asyncio.gather(
                clients[3].post(f'/api/courses/backend/booking/slots/{slot_a}/book', headers=headers()),
                clients[3].post(f'/api/courses/backend_copy/booking/slots/{slot_b}/book', headers=headers()))
            assert sorted([cross_a.status_code,cross_b.status_code]) == [200,409], (cross_a.text,cross_b.text)
            # Concurrent different grants and a booking share the same student lock.
            qpath = base+'/users/7/quota-grants'
            qbody = {'year':2099,'month':11,'add':1,'reason':'x'}
            await request(1, 'POST', qpath, 200, json=qbody, headers={**headers(),'X-Account-Ref':refs[7]})
            slot_c = (await request(1, 'POST', base+'/slots', 200,
                json={**slot_payload,'owner_id':2,'slot_date':'2099-11-17','start_time':'15:00'}, headers=headers())).json()['slot']['id']
            qa, qb, booking_c = await asyncio.gather(
                clients[1].post(qpath, json=qbody, headers={**headers(),'X-Account-Ref':refs[7]}),
                clients[1].post(qpath, json=qbody, headers={**headers(),'X-Account-Ref':refs[7]}),
                clients[7].post(f'/api/courses/backend/booking/slots/{slot_c}/book', headers=headers()))
            assert (qa.status_code,qb.status_code,booking_c.status_code) == (200,200,200), (qa.text,qb.text,booking_c.text)
            qfinal = (await request(1, 'GET', base+'/users/7/quota', 200,
                      params={'year':2099,'month':11},headers={'X-Account-Ref':refs[7]})).json()['quota']
            assert (qfinal['granted'],qfinal['used'],qfinal['remaining']) == (3,1,2), qfinal
            # A booking less than 36 hours away cannot be cancelled.
            near = (datetime.now(moscow) + timedelta(hours=24)).replace(second=0,microsecond=0)
            await request(1, 'POST', base+'/users/3/quota-grants', 200,
                json={'year':near.year,'month':near.month,'add':1,'reason':'x'}, headers={**headers(),'X-Account-Ref':refs[3]})
            near_slot = (await request(1, 'POST', base+'/slots', 200,
                json={'owner_id':2,'slot_date':near.date().isoformat(),'start_time':near.strftime('%H:%M'),'duration_minutes':60}, headers=headers())).json()['slot']['id']
            near_booking = (await request(3, 'POST', f'/api/courses/backend/booking/slots/{near_slot}/book', 200, headers=headers())).json()['booking']['id']
            await request(3, 'POST', f'/api/courses/backend/booking/bookings/{near_booking}/cancel', 409, headers=headers())
            # Inject a disposable DB outbox failure: neither booking nor dedup can commit.
            fault_slot = (await request(1, 'POST', base+'/slots', 200,
                json={**slot_payload,'owner_id':2,'slot_date':'2099-11-18','start_time':'19:00'}, headers=headers())).json()['slot']['id']
            with engine.begin() as conn:
                conn.execute(text("""CREATE FUNCTION r5_test_fail_outbox() RETURNS trigger AS $$
                    BEGIN RAISE EXCEPTION 'synthetic_outbox_failure'; END; $$ LANGUAGE plpgsql"""))
                conn.execute(text("""CREATE TRIGGER r5_test_fail_outbox BEFORE INSERT ON booking_notification_outbox
                    FOR EACH ROW WHEN (NEW.start_time='19:00') EXECUTE FUNCTION r5_test_fail_outbox()"""))
            fault_key = str(uuid4())
            fault_path = f'/api/courses/backend/booking/slots/{fault_slot}/book'
            try:
                await request(7, 'POST', fault_path, 503, headers={'Idempotency-Key':fault_key})
                with engine.connect() as conn:
                    assert conn.execute(text('SELECT student_id FROM booking_slots WHERE id=:id'), {'id':fault_slot}).scalar_one() is None
                    assert conn.execute(text('SELECT count(*) FROM booking_history WHERE slot_id=:id'), {'id':fault_slot}).scalar_one() == 0
                    assert conn.execute(text('SELECT count(*) FROM booking_notification_outbox WHERE slot_id=:id'), {'id':fault_slot}).scalar_one() == 0
                    assert conn.execute(text('SELECT count(*) FROM role_admin_commands WHERE command_id=:key'), {'key':fault_key}).scalar_one() == 0
            finally:
                with engine.begin() as conn:
                    conn.execute(text('DROP TRIGGER r5_test_fail_outbox ON booking_notification_outbox'))
                    conn.execute(text('DROP FUNCTION r5_test_fail_outbox()'))
            recovered = (await request(7, 'POST', fault_path, 200, headers={'Idempotency-Key':fault_key})).json()
            assert (await request(7, 'POST', fault_path, 200, headers={'Idempotency-Key':fault_key})).json() == recovered
            with engine.connect() as conn:
                events = conn.execute(text('SELECT event_type,course_id,calendar_owner_id,booking_id FROM booking_notification_outbox WHERE booking_id=:id ORDER BY id'), {'id':booked2['id']}).all()
                assert events == [('booking_created','backend',2,booked2['id']), ('booking_cancelled','backend',2,booked2['id'])]
                assert conn.execute(text('SELECT owner_at_booking_id FROM booking_history WHERE id=:id'), {'id':booked2['id']}).scalar_one() == 2
            from pymentor_core.adapters.sql.notification_outbox import _claim_next
            from sqlalchemy.orm import Session
            with Session(engine) as session:
                claimed = _claim_next(session)
                assert claimed is not None and claimed.course_id == 'backend' and claimed.booking_id is not None
            # A projection revocation and student claim cannot commit using stale authority.
            race_slot = (await request(1, 'POST', base+'/slots', 200,
                json={**slot_payload,'owner_id':2,'slot_date':'2099-11-19','start_time':'15:00'}, headers=headers())).json()['slot']['id']
            def revoke_projection():
                with engine.begin() as conn:
                    conn.execute(text('SELECT id FROM users WHERE id=2 FOR UPDATE'))
                    conn.execute(text("SELECT pg_advisory_xact_lock(528,hashtext('backend'))"))
                    conn.execute(text("UPDATE course_leadership SET revoked_at=now() WHERE course_id='backend'"))
            race_book, race_revoke = await asyncio.gather(
                clients[7].post(f'/api/courses/backend/booking/slots/{race_slot}/book', headers=headers()),
                asyncio.to_thread(revoke_projection))
            assert race_book.status_code in (200,503), race_book.text
            await request(2, 'GET', base+'/calendar-context', 403)
            with engine.connect() as conn:
                count = conn.execute(text('SELECT count(*) FROM booking_history WHERE slot_id=:id'), {'id':race_slot}).scalar_one()
                assert count == (1 if race_book.status_code == 200 else 0)
    finally:
        for client in clients.values():
            await client.aclose()
        engine.dispose()
        from redis.asyncio import Redis
        redis = Redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
        prefix = os.environ['KNOWI_REDIS_PREFIX']
        assert prefix.startswith('c527-booking-') and prefix.endswith(':') and len(prefix) == len('c527-booking-') + 32 + 1
        keys = [key async for key in redis.scan_iter(match=prefix+'*')]
        if keys:
            assert all(key.startswith(prefix) for key in keys)
            await redis.delete(*keys)
        await redis.aclose()


if __name__ == '__main__' and sys.argv[1:] == ['--exercise']:
    asyncio.run(exercise())
