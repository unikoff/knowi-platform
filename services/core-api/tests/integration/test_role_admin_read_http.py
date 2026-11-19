"""Disposable PG16/Redis HTTP integration; child process isolates application database identity."""
import asyncio
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

_spec = importlib.util.spec_from_file_location('role_migration_http', Path(__file__).with_name('test_role_admin_migration.py'))
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
database = _m.database
_courses_spec = importlib.util.spec_from_file_location('role_test_courses_read', Path(__file__).with_name('role_admin_test_courses.py'))
_courses = importlib.util.module_from_spec(_courses_spec)
_courses_spec.loader.exec_module(_courses)


def test_role_admin_http_on_real_services(database):
    migration_env = {k: v for k, v in os.environ.items() if k in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
    migration_env.update(PYTHONDONTWRITEBYTECODE='1',
                         KNOWI_MIGRATION_URL=database.set(drivername='postgresql+asyncpg').render_as_string(hide_password=False),
                         KNOWI_MIGRATION_HOST='127.0.0.1', KNOWI_MIGRATION_DATABASE=database.database,
                         KNOWI_MIGRATION_USER=database.username)
    script = ("from alembic import command; from alembic.config import Config; import sys; "
              "c=Config(sys.argv[1]); c.set_main_option('core_runtime_migrations','true'); "
              "command.upgrade(c,'c528_course_leaders')")
    migrated = subprocess.run([sys.executable, '-B', '-c', script, str(_m.CORE / 'alembic.ini')],
                              env=migration_env, capture_output=True, text=True, timeout=90)
    assert migrated.returncode == 0, migrated.stderr
    env = os.environ.copy()
    env.update(DATABASE_URL=database.set(drivername='postgresql+psycopg').render_as_string(hide_password=False),
               REDIS_URL='redis://127.0.0.1:57381/15', KNOWI_REDIS_PREFIX='c527-http-' + uuid4().hex,
               KNOWI_LOGIN_RECOVERY_KEY='a' * 64, PYMENTOR_PROFILE='postgres_redis', PYMENTOR_SCHEMA_MODE='managed')
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])
    env.pop('PYMENTOR_REDIS_PREFIX', None)
    env.pop('PYMENTOR_LOGIN_RECOVERY_KEY', None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    with _courses.courses_for({'backend':2}) as copied:
        env['PYMENTOR_COURSES_ROOT'] = str(copied)
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--exercise'], env=env,
                                cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr


async def exercise():
    import httpx
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from fastapi import HTTPException
    from pymentor_core.modules.analytics_delivery.grants import authorize_student_statistics_grant
    from pymentor_core.modules.identity.passwords import hash_password
    from pymentor_core.modules.identity.principal import Principal
    from pymentor_core.app.asgi import create_app
    from pymentor_core.modules.learning.api.service import prepare_request_content
    from pymentor_core.modules.learning.course_context import course_scope
    from pymentor_core.modules.learning.progress_policy import is_overview_lesson, lesson_completion_requirements
    from tools.maintenance.course_leader_config import reconcile

    engine = create_engine(os.environ['DATABASE_URL'])
    refs = {i: str(uuid4()) for i in range(1, 10)}
    password = 'synthetic-password'
    embedded_email = 'private.member@example.test'
    embedded_name = f'Granted <{embedded_email}>'
    standalone_email = 'archived.private@example.test'
    with engine.begin() as conn:
        for uid, name, admin in [
            (1, 'Admin', True), (2, 'Leader', False), (3, 'Student', False), (4, 'StudentTwo', False),
            (5, 'Outsider', False), (6, 'Disposable', False), (7, embedded_name, False),
            (8, '@zero_quota', False), (9, standalone_email, False),
        ]:
            email = {7: 'granted@example.test', 8: 'zeroquota@example.test', 9: standalone_email}.get(
                uid, f'{name.lower()}@example.test',
            )
            conn.execute(text('INSERT INTO users(id,username,email,password_hash,verified,is_admin) VALUES (:id,:name,:email,:password,true,:admin)'),
                         dict(id=uid, name=name, email=email, password=hash_password(password), admin=admin))
            conn.execute(text('INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (:id,:ref)'), dict(id=uid, ref=refs[uid]))
        conn.execute(text("INSERT INTO course_leadership(course_id,user_id,account_ref,revision,assigned_by) VALUES ('backend',2,:ref,1,1)"), {'ref':refs[2]})
        assert reconcile(conn, {'backend':2}, apply=True)[0]['status'] == 'applied'
        for slot, uid, date, status, course in [
            (10,3,'2020-01-10','booked','backend'), (11,3,'2099-01-10','booked','backend'),
            (12,4,'2099-01-11','cancelled','backend'), (13,5,'2099-01-12','booked','other'),
            (14,9,'2020-02-01','completed','backend'),
            (20,3,'2100-02-10','booked','backend'), (21,3,'2100-03-10','cancelled','backend'),
            (22,3,'2098-06-01','booked','other'), (23,9,'2100-04-10','cancelled','backend'),
        ]:
            conn.execute(text('INSERT INTO booking_slots(id,slot_date,start_time,duration_minutes,course_id,calendar_owner_id) VALUES (:id,:date,\'10:00\',60,:course,2)'), dict(id=slot,date=date,course=course))
            conn.execute(text("INSERT INTO booking_history(slot_id,course_id,student_id,owner_at_booking_id,status,starts_at,ends_at) VALUES (:slot,:course,:uid,2,:status,CAST(:date AS timestamp) AT TIME ZONE 'UTC',(CAST(:date AS timestamp) + interval '1 hour') AT TIME ZONE 'UTC')"), dict(slot=slot,course=course,uid=uid,status=status,date=date))
        conn.execute(text("UPDATE booking_slots SET archived_at='2020-02-02' WHERE id=14"))
        conn.execute(text("UPDATE booking_history SET owner_at_booking_id=5 WHERE slot_id=10"))
        conn.execute(text("UPDATE users SET subscription_until='2099-02-01T21:00:00Z',subscription_plan='pro' WHERE id=3"))
        conn.execute(text("""INSERT INTO booking_quotas(user_id,year,month,granted_slots,course_id)
            VALUES (3,2099,1,3,'backend'),(5,2099,1,7,'other'),(7,2099,1,2,'backend'),
                   (3,2020,1,1,'backend'),(7,2020,1,1,'backend'),(8,2099,1,0,'backend')"""))
        conn.execute(text("INSERT INTO user_lesson_progress(user_id,lesson_id,course_id,completed_at) VALUES (3,'a','backend','2099-01-03'),(3,'foreign','other','2099-01-03')"))
    app = create_app()
    async with app.router.lifespan_context(app):
        clients = {i:httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(uuid4().hex,1234)), base_url='http://test') for i in (1,2,3,6)}
        try:
            for uid, name in [(1,'admin'),(2,'leader'),(3,'student'),(6,'disposable')]:
                response = await clients[uid].post('/api/user/login', json={'email':name+'@example.test','password':password}, headers={'Idempotency-Key':str(uuid4())})
                assert response.status_code == 200, (uid,response.status_code,response.text)
            async def check(uid, path, status, **kwargs):
                r = await clients[uid].get(path, **kwargs)
                assert r.status_code == status, (uid,path,status,r.status_code,r.text)
                return r
            base='/api/admin/courses/backend'
            await check(3, base+'/roster',403)
            await check(2, '/api/admin/courses/other/roster',403)
            await check(2, '/api/admin-platform/overview',403)
            await check(3, '/api/admin-platform/overview',403)
            await check(2, '/api/admin/overview',403)
            await check(3, '/api/admin/overview',403)
            await check(1, '/api/admin-platform/overview',200)
            journal = '/api/admin-platform/operations'
            assert (await check(1, journal, 200)).json() == {'operations': [], 'next_cursor': None}
            await check(2, journal, 403)
            with engine.begin() as conn:
                for actor_id, course, action in [(1, 'backend', 'first'), (2, 'backend', 'second'), (2, 'other', 'third')]:
                    conn.execute(text('''INSERT INTO role_admin_audit(command_id,actor_id,actor_account_ref,actor_role,
                        scope,course_id,action,reason) VALUES (:key,:actor,:ref,:role,'course',:course,:action,'test')'''),
                        {'key':str(uuid4()), 'actor':actor_id, 'ref':refs[actor_id],
                         'role':'admin' if actor_id == 1 else 'leader', 'course':course, 'action':action})
            async def actions(params):
                return [row['action'] for row in (await check(1, journal, 200, params=params)).json()['operations']]
            assert await actions({}) == ['third', 'second', 'first']
            assert await actions({'course_id':'backend'}) == ['second', 'first']
            assert await actions({'actor_id':2}) == ['third', 'second']
            assert await actions({'course_id':'backend', 'actor_id':2}) == ['second']
            first_page = (await check(1, journal, 200, params={'course_id':'backend','limit':1})).json()
            assert [row['action'] for row in first_page['operations']] == ['second'] and first_page['next_cursor']
            second_page = (await check(1, journal, 200, params={'course_id':'backend','limit':1,'cursor':first_page['next_cursor']})).json()
            assert [row['action'] for row in second_page['operations']] == ['first'] and second_page['next_cursor'] is None
            await check(1, journal, 400, params={'course_id':'other','limit':1,'cursor':first_page['next_cursor']})
            page = (await check(1, '/api/admin-platform/users', 200, params={'limit':1})).json()
            await check(1, '/api/admin-platform/operations', 400, params={'cursor':page['next_cursor']})
            roster=(await check(2,base+'/roster',200)).json()
            assert {u['id']:u['last_booking_status'] for u in roster['users']} == {
                3:'cancelled', 4:'cancelled', 7:None, 9:'cancelled',
            }
            assert {u['id'] for u in (await check(1, base+'/roster', 200)).json()['users']} == {3, 4, 7, 9}
            assert all('email' not in u and u['masked_email'].endswith('***@example.test') for u in roster['users'])
            by_id = {u['id']: u for u in roster['users']}
            assert by_id[3]['display_booking'] == {
                'kind':'upcoming', 'status':'booked', 'starts_at':'2099-01-10T00:00:00Z',
                'ends_at':'2099-01-10T01:00:00Z',
            }
            assert by_id[4]['display_booking'] is None
            assert by_id[9]['display_booking'] == {
                'kind':'latest', 'status':'completed', 'starts_at':'2020-02-01T00:00:00Z',
                'ends_at':'2020-02-01T01:00:00Z',
            }
            assert by_id[7]['display_booking'] is None
            assert by_id[3]['username'] == 'Student'
            assert by_id[7]['username'].startswith('Granted') and embedded_email not in str(roster)
            assert standalone_email not in str(roster)
            assert all('remaining_lessons' not in person for person in roster['users'])

            async def monthly(year, month, *, uid=2, **params):
                return (await check(uid, base+'/roster', 200, params={'year':year, 'month':month, **params})).json()

            def balances(page):
                return {person['id']: person['remaining_lessons'] for person in page['users']}

            future = await monthly(2099, 1)
            assert balances(future) == {3:2, 4:0, 7:2, 9:0}
            assert balances(await monthly(2099, 1, uid=1, search='Student')) == {3:2, 4:0}
            assert balances(await monthly(2020, 1)) == {3:0, 4:0, 7:1, 9:0}
            assert balances(await monthly(2020, 2)) == {3:0, 4:0, 7:0, 9:None}
            first_month = await monthly(2099, 1, limit=2)
            assert balances(first_month) == {3:2, 4:0}
            second_month = await monthly(2099, 1, limit=2, cursor=first_month['next_cursor'])
            assert balances(second_month) == {7:2, 9:0} and second_month['next_cursor'] is None
            await check(2, base+'/roster', 400, params={'year':2098, 'month':12, 'cursor':first_month['next_cursor']})
            await check(3, base+'/roster', 403, params={'year':2099, 'month':1})
            await check(2, '/api/admin/courses/other/roster', 403, params={'year':2099, 'month':1})
            for invalid in ({'year':2099}, {'month':1}, {'year':1999,'month':1}, {'year':2101,'month':1},
                            {'year':2099,'month':0}, {'year':2099,'month':13}, {'year':'x','month':1}):
                await check(2, base+'/roster', 422, params=invalid)
            found_granted = (await check(2, base+'/roster', 200, params={'search':'gRaNt'})).json()
            assert [u['id'] for u in found_granted['users']] == [7]
            for raw_search, expected in [(embedded_name.upper(), 7), (standalone_email.upper(), 9),
                                         ('GRANTED@EXAMPLE.TEST', 7)]:
                roster_match = (await check(2, base+'/roster', 200, params={'search':raw_search})).json()['users']
                assert [u['id'] for u in roster_match] == [expected]
                assert roster_match[0] == by_id[expected]
            for fragment in ('private.member', 'archived.private'):
                assert (await check(2, base+'/roster', 200, params={'search':fragment})).json()['users'] == []
                assert (await check(2, base+'/grant-search', 200, params={'q':fragment})).json()['users'] == []
            for malformed in ('@example.test', 'bad@@example.test', 'bad@'):
                await check(2, base+'/grant-search', 422, params={'q':malformed})
            by_partial_address = (await check(2, base+'/grant-search', 200, params={'q':'member@example.test'})).json()
            assert by_partial_address['users'] == []
            handle = (await check(2, base+'/grant-search', 200, params={'q':'@zero_quota'})).json()['users']
            assert [(u['id'], u['username']) for u in handle] == [(8, '@zero_quota')]
            p=(await check(2,base+'/roster/3/progress',200)).json()
            assert p['lessons_count']==1 and p['completed_lessons'][0]['id']=='a',p
            await check(2,base+'/roster/5/progress',404)
            def stats_headers(target, actor=2):
                return {'X-Actor-Ref': refs[actor], 'X-Account-Ref': refs[target]}

            def profile_path(target):
                return base + f'/users/{target}/profile-details'

            async def profile(target, year, month, *, uid=2, status=200, **pagination):
                return await check(uid, profile_path(target), status, headers=stats_headers(target, uid),
                                   params={'year':year, 'month':month, **pagination})

            past_profile = await profile(3, 2020, 1)
            assert past_profile.headers['Cache-Control'] == 'no-store'
            past = past_profile.json()
            assert set(past) == {'course_id', 'user_id', 'account_ref', 'period', 'subscription_until',
                                 'subscription_plan', 'bookings', 'next_cursor'}
            assert (past['course_id'], past['user_id'], past['account_ref'], past['period']) == (
                'backend', 3, refs[3], {'year':2020, 'month':1},
            )
            assert past['subscription_until'] == '2099-02-01T21:00:00Z' and past['subscription_plan'] == 'pro'
            assert len(past['bookings']) == 1
            assert set(past['bookings'][0]) == {'id', 'status', 'starts_at', 'ends_at'}
            assert past['bookings'][0]['status'] == 'booked'
            archived = (await profile(9, 2020, 2)).json()
            assert len(archived['bookings']) == 1 and archived['bookings'][0]['status'] == 'completed'
            cancelled = (await profile(4, 2099, 1, uid=1)).json()
            assert len(cancelled['bookings']) == 1 and cancelled['bookings'][0]['status'] == 'cancelled'
            assert cancelled['subscription_until'] is None and cancelled['subscription_plan'] is None
            empty = (await profile(7, 2098, 4)).json()
            assert empty['bookings'] == [] and empty['next_cursor'] is None
            await profile(3, 2099, 1, uid=3, status=403)
            await profile(3, 2099, 1, limit=101, status=422)
            await profile(3, 2099, 13, status=422)
            await check(2, profile_path(3), 401, headers=stats_headers(3, 1), params={'year':2099, 'month':1})
            await check(2, profile_path(3), 404, headers=stats_headers(4), params={'year':2099, 'month':1})
            await check(2, '/api/admin/courses/other/users/3/profile-details', 403,
                        headers=stats_headers(3), params={'year':2099, 'month':1})
            roster_stats = base + '/users/3/statistics'
            non_roster_stats = base + '/users/6/statistics'
            granted_stats = base + '/users/7/statistics'
            granted_progress = base + '/roster/7/progress'
            assert (await check(2, granted_stats, 200, headers=stats_headers(7))).json()['course_id'] == 'backend'
            assert (await check(2, granted_progress, 200)).json()['lessons_count'] == 0
            await check(2, granted_stats, 404, headers=stats_headers(8))
            await check(2, base+'/roster/8/progress',404)
            actor = Principal(user_id=2, account_ref=refs[2], username='Leader', email='leader@example.test',
                              roles=frozenset({'student'}), permissions=frozenset(), subscription_until=None)
            def student_grant(target, revision=None):
                with Session(engine) as db:
                    return authorize_student_statistics_grant(
                        db, actor, course_id='backend', user_id=target, target_ref=refs[target],
                        selection_revision=revision,
                    )
            assert student_grant(3)['subjects'] == [refs[3]]
            with pytest.raises(HTTPException) as denied_analytics:
                student_grant(7)
            assert denied_analytics.value.status_code == 403
            await check(2, roster_stats, 422, headers={'X-Account-Ref': refs[3]})
            await check(2, roster_stats, 401, headers=stats_headers(3, 1))
            await check(3, roster_stats, 403, headers=stats_headers(3, 3))
            await check(2, '/api/admin/courses/other/users/3/statistics', 403, headers=stats_headers(3))
            await check(2, roster_stats, 404, headers=stats_headers(4))
            assert (await check(2, roster_stats, 200, headers=stats_headers(3))).headers['Cache-Control'] == 'no-store'
            denied = await check(2, non_roster_stats, 403, headers=stats_headers(6))
            assert denied.json()['detail'] == 'Select an account through course search'
            await profile(6, 2099, 1, status=403)
            await check(1, non_roster_stats, 200, headers=stats_headers(6, 1))
            await check(2, base+'/grant-search', 200, params={'q':'disposable@example.test'})
            assert (await profile(6, 2099, 1)).json()['bookings'] == []
            await check(2, base+'/roster/6/progress', 404)
            response = await check(2, non_roster_stats, 200, headers=stats_headers(6))
            before = response.json()
            assert response.headers['Cache-Control'] == 'no-store'
            assert (before['course_id'], before['user_id'], before['account_ref']) == ('backend', 6, refs[6])
            assert set(before) == {'course_id', 'user_id', 'account_ref', 'tracks', 'courses'}
            with course_scope('backend'):
                content = prepare_request_content('catalog')
                track_id, lesson_id, lesson_receipt, task_receipts = next(
                    (summary['id'], lesson['id'], lesson_receipt, task_receipts)
                    for summary in content.get_tracks_summary()
                    for lesson in content.find_track(summary['id'])['lessons']
                    if lesson['id'] in summary['countable_lesson_ids'] and not is_overview_lesson(lesson)
                    for lesson_receipt, task_receipts in lesson_completion_requirements(lesson)
                    if lesson_receipt or task_receipts
                )
            def selected_lesson(payload):
                return next(item for item in payload['courses'][track_id] if item['id'] == lesson_id)
            assert selected_lesson(before)['completed'] is False
            with engine.begin() as conn:
                if lesson_receipt:
                    conn.execute(text("""INSERT INTO user_lesson_progress(user_id,lesson_id,course_id,completed_at)
                        VALUES (6,:lesson,'backend','2026-11-18 21:00:00')"""), {'lesson': lesson_id})
                for task, revision in task_receipts:
                    conn.execute(text("""INSERT INTO user_task_progress(user_id,task_id,task_revision,course_id,completed_at)
                        VALUES (6,:task,:revision,'backend','2026-11-18 21:00:00')"""), {'task': task, 'revision': revision})
            after = (await check(2, non_roster_stats, 200, headers=stats_headers(6))).json()
            assert selected_lesson(after) == {
                'id': lesson_id, 'title': selected_lesson(before)['title'], 'completed': True,
                'completed_on': '2026-11-19', 'counts_toward_statistics': True,
            }
            personal = (await check(6, '/api/learning/catalog', 200, params={'course_id':'backend'})).json()
            for field, value in selected_lesson(after).items():
                assert selected_lesson(personal)[field] == value
            assert {track['id'] for track in after['tracks']} == set(after['courses'])
            assert any(lesson_id in block['lesson_ids'] for track in after['tracks'] for block in track['blocks'])
            identity = app.state.local_async_identity
            selection_store = identity.redis
            selection_revision = await selection_store.get_role_selection(refs[2], 'backend', refs[6])
            assert selection_revision is not None and student_grant(6, selection_revision)['subjects'] == [refs[6]]
            selection_key = selection_store._role_selection_key(refs[2], 'backend', refs[6])
            await selection_store._client.delete(selection_key)
            await check(2, non_roster_stats, 403, headers=stats_headers(6))
            await profile(6, 2099, 1, status=403)
            await check(2, roster_stats, 200, headers=stats_headers(3))
            overview=(await check(2,base+'/overview',200,params={'year':2099,'month':1})).json()
            assert (overview['roster_count'],overview['booking_count'],overview['lessons_granted'],overview['lessons_used'],overview['completed_lessons']) == (4,2,5,1,1),overview
            with engine.begin() as conn:
                for slot, course, start in [
                    (15,'backend','2098-12-31T20:59:59Z'), (16,'backend','2098-12-31T21:00:00Z'),
                    (17,'backend','2099-01-31T21:00:00Z'), (18,'other','2099-01-13T10:00:00Z'),
                ]:
                    conn.execute(text("""INSERT INTO booking_slots(id,slot_date,start_time,duration_minutes,course_id,calendar_owner_id)
                        VALUES (:slot,CAST(CAST(:start AS timestamptz) AT TIME ZONE 'Europe/Moscow' AS date),:time,60,:course,2)"""),
                        {'slot':slot,'course':course,'start':start,'time':f'{slot}:00'})
                    conn.execute(text("""INSERT INTO booking_history(slot_id,course_id,student_id,owner_at_booking_id,status,starts_at,ends_at)
                        VALUES (:slot,:course,3,2,'completed',CAST(:start AS timestamptz),CAST(:start AS timestamptz)+interval '1 hour')"""),
                        {'slot':slot,'course':course,'start':start})
            assert balances(await monthly(2099, 1, search='Student')) == {3:1, 4:0}
            assert balances(await monthly(2098, 12, search='Student')) == {3:None, 4:0}
            assert balances(await monthly(2099, 2, search='Student')) == {3:None, 4:0}
            first_details_page = (await profile(3, 2099, 1, limit=1)).json()
            assert len(first_details_page['bookings']) == 1 and first_details_page['next_cursor']
            assert first_details_page['bookings'][0]['starts_at'] == '2099-01-10T00:00:00Z'
            cursor_details = first_details_page['next_cursor']
            second_details_page = (await profile(3, 2099, 1, limit=1, cursor=cursor_details)).json()
            assert len(second_details_page['bookings']) == 1 and second_details_page['next_cursor'] is None
            assert second_details_page['bookings'][0]['starts_at'] == '2098-12-31T21:00:00Z'
            december = (await profile(3, 2098, 12)).json()['bookings']
            assert [entry['starts_at'] for entry in december] == ['2098-12-31T20:59:59Z']
            february = (await profile(3, 2099, 2)).json()['bookings']
            assert [entry['starts_at'] for entry in february] == ['2099-01-31T21:00:00Z']
            await profile(3, 2099, 1, uid=1, cursor=cursor_details, status=400)
            await profile(4, 2099, 1, cursor=cursor_details, status=400)
            await profile(3, 2099, 2, cursor=cursor_details, status=400)
            quota=base+'/users/3/quota'
            await check(2,quota,422,params={'year':2099,'month':1})
            q=(await check(2,quota,200,params={'year':2099,'month':1},headers={'X-Account-Ref':refs[3]})).json()['quota']
            assert (q['granted'],q['used'],q['remaining'])==(3,2,1),q
            await check(2,quota,404,params={'year':2099,'month':1},headers={'X-Account-Ref':str(uuid4())})
            await check(2,base+'/grant-search',422,params={'q':'ab'})
            await check(2,base+'/grant-search',422,params={'q':'%abc'})
            found=(await check(2,base+'/grant-search',200,params={'q':'Student'})).json()
            assert {u['id'] for u in found['users']}=={3,4} and all('email' not in u for u in found['users']),found
            first=(await check(2,base+'/roster',200,params={'limit':1})).json()
            cursor=first['next_cursor']; assert cursor
            second=(await check(2,base+'/roster',200,params={'limit':2,'cursor':cursor})).json()
            assert [u['id'] for u in second['users']] == [4,7]
            third=(await check(2,base+'/roster',200,params={'limit':2,'cursor':second['next_cursor']})).json()
            assert [u['id'] for u in third['users']] == [9] and third['next_cursor'] is None
            await check(1,base+'/roster',400,params={'cursor':cursor})
            await check(2,base+'/roster',400,params={'cursor':cursor,'search':'Student'})
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM booking_quotas WHERE course_id='backend' AND user_id=7"))
            await check(2, granted_stats, 403, headers=stats_headers(7))
            await check(2, granted_progress, 404)
            assert (await check(2, base+'/roster', 200, params={'search':'gRaNt'})).json()['users'] == []
            for query in ('GRANTED@EXAMPLE.TEST', embedded_name):
                exact = (await check(2, base+'/grant-search', 200, params={'q':query})).json()['users']
                assert [(u['id'], u['account_ref'], u['username']) for u in exact] == [
                    (7, refs[7], by_id[7]['username']),
                ]
                assert embedded_email not in str(exact)
            with engine.begin() as conn:
                conn.execute(text("UPDATE course_leadership SET revoked_at=now() WHERE user_id=2"))
            await check(2,base+'/roster',403)
            await check(2, roster_stats, 403, headers=stats_headers(3))
            await profile(3, 2099, 1, status=403)
            with engine.begin() as conn:
                conn.execute(text("UPDATE course_leadership SET revoked_at=NULL WHERE user_id=2"))
            await check(2,base+'/roster',400,params={'cursor':cursor})
            with engine.begin() as conn:
                conn.execute(text('DELETE FROM account_lifecycles WHERE user_id=3'))
                conn.execute(text('INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (3,:ref)'), {'ref':str(uuid4())})
            await check(2,quota,404,params={'year':2099,'month':1},headers={'X-Account-Ref':refs[3]})
            await profile(3, 2099, 1, status=404)
            deleted = await clients[1].delete('/api/admin/users/6', headers={'X-Account-Ref':refs[6]})
            assert deleted.status_code == 200, (deleted.status_code, deleted.text)
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO users(id,username,email,password_hash,verified,is_admin) VALUES (6,'Replacement','replacement@example.test','x',true,false)"))
                conn.execute(text('INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (6,:ref)'), {'ref':str(uuid4())})
            await check(6,base+'/roster',401)
            await check(1, non_roster_stats, 404, headers=stats_headers(6, 1))
            for _ in range(25):
                limited = await clients[2].get(base+'/grant-search', params={'q':'Student'})
                if limited.status_code == 429:
                    assert limited.headers.get('Retry-After') == '60'
                    break
                assert limited.status_code == 200, limited.text
            else:
                raise AssertionError('search limit was not enforced')
            from redis.asyncio import Redis
            store = app.state.local_async_identity.redis
            original_client = store._client
            broken = Redis(host='127.0.0.1', port=1, socket_connect_timeout=.2, socket_timeout=.2)
            store._client = broken
            try:
                await check(2,base+'/grant-search',503,params={'q':'Student'})
            finally:
                store._client = original_client
                await broken.aclose()
            print('HTTP scenarios passed')
        finally:
            for client in clients.values(): await client.aclose()
            store = app.state.local_async_identity.redis
            keys = [key async for key in store._client.scan_iter(match=store._prefix + '*')]
            if keys:
                await store._client.delete(*keys)
    engine.dispose()


if __name__ == '__main__':
    asyncio.run(exercise())
