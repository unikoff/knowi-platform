"""R4 HTTP commands against an isolated PostgreSQL 16 and Redis instance."""
import asyncio
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

_spec = importlib.util.spec_from_file_location('role_migration_write_http', Path(__file__).with_name('test_role_admin_migration.py'))
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
database = _m.database
_leader_spec = importlib.util.spec_from_file_location('leader_pg_write', Path(__file__).with_name('test_course_leader_config_pg.py'))
_leader = importlib.util.module_from_spec(_leader_spec)
_leader_spec.loader.exec_module(_leader)
_courses_spec = importlib.util.spec_from_file_location('role_test_courses_write', Path(__file__).with_name('role_admin_test_courses.py'))
_courses = importlib.util.module_from_spec(_courses_spec)
_courses_spec.loader.exec_module(_courses)


def test_role_admin_write_http(database):
    _leader.migrate(database)
    env = os.environ.copy()
    env.update(DATABASE_URL=database.set(drivername='postgresql+psycopg').render_as_string(hide_password=False),
               REDIS_URL='redis://127.0.0.1:57381/15', KNOWI_REDIS_PREFIX='c527-write-http-' + uuid4().hex,
               KNOWI_LOGIN_RECOVERY_KEY='a' * 64, PYMENTOR_PROFILE='postgres_redis', PYMENTOR_SCHEMA_MODE='managed',
               PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    env.pop('PYMENTOR_REDIS_PREFIX', None)
    env.pop('PYMENTOR_LOGIN_RECOVERY_KEY', None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    with _courses.courses_for({'backend':2}) as copied:
        env['PYMENTOR_COURSES_ROOT'] = str(copied)
        result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), '--exercise'], env=env,
                                cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=150)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr


async def exercise():
    from datetime import datetime, timedelta, timezone
    import httpx
    from sqlalchemy import create_engine, text
    from pymentor_core.modules.identity.passwords import hash_password
    from pymentor_core.app.asgi import create_app
    from tools.maintenance.course_leader_config import reconcile

    engine = create_engine(os.environ['DATABASE_URL'])
    refs = {i: str(uuid4()) for i in range(1, 6)}
    password = 'synthetic-password'
    with engine.begin() as conn:
        for uid, name, admin in [(1, 'Admin', True), (2, 'Leader', False), (3, 'Student', False), (4, 'Outsider', False), (5, 'Candidate', False)]:
            conn.execute(text('INSERT INTO users(id,username,email,password_hash,verified,is_admin,subscription_until,subscription_plan) VALUES (:id,:name,:email,:password,true,:admin,:until,:plan)'),
                         dict(id=uid, name=name, email=f'{name.lower()}@example.test', password=hash_password(password), admin=admin,
                              until=datetime(2099,1,1,tzinfo=timezone.utc) if uid == 3 else None, plan='pro' if uid == 3 else None))
            conn.execute(text('INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (:id,:ref)'), dict(id=uid, ref=refs[uid]))
        conn.execute(text("INSERT INTO course_leadership(course_id,user_id,account_ref,revision,assigned_by) VALUES ('backend',2,:ref,1,1)"), {'ref':refs[2]})
        assert reconcile(conn, {'backend':2}, apply=True)[0]['status'] == 'applied'
        conn.execute(text("INSERT INTO booking_slots(id,slot_date,start_time,duration_minutes,course_id,calendar_owner_id) VALUES (10,'2020-01-10','10:00',60,'backend',2)"))
        conn.execute(text("INSERT INTO booking_history(slot_id,course_id,student_id,owner_at_booking_id,status,starts_at,ends_at) VALUES (10,'backend',3,2,'completed','2020-01-10T07:00:00Z','2020-01-10T08:00:00Z')"))
        conn.execute(text("INSERT INTO user_lesson_progress(user_id,lesson_id,course_id,completed_at) VALUES (3,'lesson-a','backend','2020-01-01T00:00:00Z')"))
    app = create_app()
    async with app.router.lifespan_context(app):
        clients = {i:httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(uuid4().hex,1234)), base_url='http://test') for i in (1,2,3)}
        try:
            for uid, name in [(1,'admin'),(2,'leader'),(3,'student')]:
                response = await clients[uid].post('/api/user/login', json={'email':name+'@example.test','password':password}, headers={'Idempotency-Key':str(uuid4())})
                assert response.status_code == 200, (uid,response.status_code,response.text)
            async def post(uid, path, body, status, *, ref=None, key=None, actor_ref=None):
                headers={'Idempotency-Key':key or str(uuid4())}
                if ref is not None: headers['X-Account-Ref']=ref
                if actor_ref is not None: headers['X-Actor-Ref']=actor_ref
                r=await clients[uid].post(path,json=body,headers=headers)
                expected = status if isinstance(status, tuple) else (status,)
                assert r.status_code in expected, (uid,path,expected,r.status_code,r.text)
                return r
            async def get(uid, path, status, **kwargs):
                r=await clients[uid].get(path,**kwargs)
                assert r.status_code == status, (uid,path,status,r.status_code,r.text)
                return r
            base='/api/admin/courses/backend'
            quota=lambda uid:base+f'/users/{uid}/quota-grants'
            extend=lambda uid:base+f'/users/{uid}/subscription-extensions'
            platform='/api/admin-platform'
            grant={'year':2099,'month':1,'add':3,'reason':'synthetic'}
            await post(2,quota(3),grant,503,ref=refs[3])
            await post(2,base+'/users/3/subscription-and-quota',{**grant, 'days':5},503,ref=refs[3],actor_ref=refs[2])
            with engine.connect() as conn:
                assert conn.execute(text('SELECT count(*) FROM role_admin_commands')).scalar_one()==0
                assert conn.execute(text("SELECT count(*) FROM booking_quotas WHERE course_id='backend'")).scalar_one()==0
            # Real operator dry-run and apply will be invoked here once CLI is available.
            await apply_cutover()
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO booking_quotas(user_id,year,month,granted_slots,course_id) VALUES (3,2099,1,7,'backend_copy')"))
            await post(2,quota(4),grant,403,ref=refs[4])
            await get(2,base+'/grant-search',200,params={'q':'Outsider'})
            store=app.state.local_async_identity.redis
            selection=store._role_selection_key(refs[2],'backend',refs[4])
            assert await store._client.expire(selection, 0)
            await post(2,quota(4),grant,403,ref=refs[4])
            await get(2,base+'/grant-search',200,params={'q':'Outsider'})
            reason={'reason':'synthetic'}
            with engine.begin() as conn:
                conn.execute(text('UPDATE users SET is_admin=true WHERE id=2'))
                conn.execute(text('UPDATE users SET is_admin=false WHERE id=2'))
            await post(2,quota(4),grant,403,ref=refs[4])
            await get(2,base+'/grant-search',200,params={'q':'Outsider'})
            await post(2,quota(4),grant,200,ref=refs[4])
            assert await store._client.expire(selection, 0)
            repeated = await post(2,quota(4),grant,200,ref=refs[4])
            assert repeated.json()['quota']['granted'] == 6
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM booking_quotas WHERE user_id=4 AND course_id='backend'"))
            await post(2,quota(4),grant,403,ref=refs[4])
            await post(2,quota(3),grant,409,ref=str(uuid4()))
            key=str(uuid4())
            first=await post(2,quota(3),grant,200,ref=refs[3],key=key)
            # The first response is treated as lost; retry must return the same body without another grant.
            replay=await post(2,quota(3),grant,200,ref=refs[3],key=key)
            assert replay.json()==first.json()
            await post(2,quota(3),{**grant,'add':4},409,ref=refs[3],key=key)
            one=await post(2,extend(3),{'days':2,**reason},200,ref=refs[3])
            two=await post(1,platform+'/users/3/subscription-extensions',{'days':3,**reason},200,ref=refs[3])
            assert one.json()['scope']==two.json()['scope']=='platform'
            assert datetime.fromisoformat(two.json()['subscription_until'].replace('Z','+00:00')) == datetime(2099,1,6,tzinfo=timezone.utc)
            with engine.connect() as conn:
                assert conn.execute(text('SELECT count(*) FROM role_admin_commands WHERE command_id=:key'), {'key':key}).scalar_one()==1
                assert conn.execute(text('SELECT count(*) FROM role_admin_audit WHERE command_id=:key'), {'key':key}).scalar_one()==1
                assert conn.execute(text("SELECT course_id,granted_slots FROM booking_quotas WHERE user_id=3 ORDER BY course_id")).all()==[('backend',3),('backend_copy',7)]
                assert conn.execute(text('SELECT lesson_id,course_id FROM user_lesson_progress WHERE user_id=3')).all()==[('lesson-a','backend')]
            combined_key = await exercise_combined_grant(engine, post, get, refs, base)
            reset=platform+'/users/3/subscription-reset'
            role=platform+'/users/5/admin-role'
            await post(2,reset,{'confirmed':True,**reason},403,ref=refs[3])
            await post(2,role,{'is_admin':True,'confirmed':True,**reason},(404,405),ref=refs[5])
            await post(1,role,{'is_admin':True,'confirmed':True,**reason},(404,405),ref=refs[5])
            response=await post(1,reset,{'confirmed':True,**reason},200,ref=refs[3])
            assert response.json()['subscription_until'] is None and response.json()['subscription_plan'] is None
            with engine.begin() as conn:
                conn.execute(text("UPDATE course_leadership SET revoked_at=now() WHERE course_id='backend'"))
            await post(2,quota(3),grant,403,ref=refs[3],key=key)
            await post(2, base+'/users/3/subscription-and-quota',
                       {'year':2099,'month':2,'add':3,'days':5,'reason':'combined'}, 403,
                       ref=refs[3], key=combined_key, actor_ref=refs[2])
            print('R4 HTTP scenarios passed')
        finally:
            for client in clients.values(): await client.aclose()
            store=app.state.local_async_identity.redis
            keys=[key async for key in store._client.scan_iter(match=store._prefix+'*')]
            if keys: await store._client.delete(*keys)
    engine.dispose()


async def exercise_combined_grant(engine, post, get, refs, base):
    from datetime import datetime, timezone
    from sqlalchemy import text

    target = base+'/users/3/subscription-and-quota'
    outsider = base+'/users/4/subscription-and-quota'
    body = {'year':2099,'month':2,'add':3,'days':5,'reason':'combined'}
    headers = {'ref':refs[3], 'actor_ref':refs[2], 'key':str(uuid4())}
    await post(2, target, body, 422, ref=refs[3])
    await post(2, target, body, 401, ref=refs[3], actor_ref=refs[1])
    await post(2, target, body, 409, ref=str(uuid4()), actor_ref=refs[2])
    await post(3, target, body, 403, ref=refs[3], actor_ref=refs[3])
    await post(2, target.replace('/backend/', '/backend_copy/'), body, 403, **headers)
    for invalid in ({'year':1999}, {'month':13}, {'add':0}, {'add':101}, {'add':True}, {'days':'5'}, {'days':3651},
                    {'reason':' '}, {'reason':'x' * 501}):
        await post(2, target, {**body, **invalid}, 422, **headers)

    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO booking_slots(id,slot_date,start_time,duration_minutes,course_id,calendar_owner_id)
                            VALUES (11,'2099-02-01','00:00',60,'backend',2)"""))
        conn.execute(text("""INSERT INTO booking_history(slot_id,course_id,student_id,owner_at_booking_id,status,starts_at,ends_at)
                            VALUES (11,'backend',3,2,'completed','2099-01-31T21:00:00Z','2099-01-31T22:00:00Z')"""))
        conn.execute(text("""INSERT INTO booking_quotas(course_id,user_id,year,month,granted_slots)
                            VALUES ('backend',3,2099,2,1)"""))
        conn.execute(text("""ALTER TABLE booking_quotas ADD CONSTRAINT synthetic_reject_second_effect
                            CHECK (granted_slots < 4 OR user_id <> 3 OR month <> 2)"""))
    await post(2, target, body, 503, **headers)
    with engine.begin() as conn:
        assert conn.execute(text('SELECT subscription_until FROM users WHERE id=3')).scalar_one() == datetime(2099,1,6,tzinfo=timezone.utc)
        assert conn.execute(text("SELECT granted_slots FROM booking_quotas WHERE course_id='backend' AND user_id=3 AND year=2099 AND month=2")).scalar_one() == 1
        for table in ('role_admin_audit', 'role_admin_commands'):
            assert conn.execute(text(f'SELECT count(*) FROM {table} WHERE command_id=:key'), headers).scalar_one() == 0
        conn.execute(text('ALTER TABLE booking_quotas DROP CONSTRAINT synthetic_reject_second_effect'))
        conn.execute(text("DELETE FROM booking_quotas WHERE user_id=4 AND course_id='backend'"))

    first = await post(2, target, {**body, 'reason':' combined '}, 200, **headers)
    replay = await post(2, target, body, 200, **headers)
    assert replay.json() == first.json()
    assert replay.json()['scope'] == 'platform' and replay.json()['subscription_plan'] == 'pro'
    assert datetime.fromisoformat(replay.json()['subscription_until'].replace('Z','+00:00')) == datetime(2099,1,11,tzinfo=timezone.utc)
    assert replay.json()['quota'] == {'course_id':'backend','user_id':3,'year':2099,'month':2,'granted':4,'used':1,'remaining':3}
    await post(2, target, {**body, 'days':6}, 409, **headers)
    await post(2, target, {**body, 'add':4}, 409, **headers)
    await post(2, outsider, body, 409, **{**headers, 'ref':refs[4]})
    with engine.connect() as conn:
        events = conn.execute(text("""SELECT action,scope,course_id,authority_course_id,reason
                                      FROM role_admin_audit WHERE command_id=:key ORDER BY action"""), headers).all()
        assert events == [
            ('quota.grant','course','backend','backend','combined'),
            ('subscription.extend.course','platform',None,'backend','combined'),
        ]
        assert conn.execute(text('SELECT count(*) FROM role_admin_commands WHERE command_id=:key'), headers).scalar_one() == 1

    await post(2, outsider, body, 403, ref=refs[4], actor_ref=refs[2])
    await get(2, base+'/grant-search', 200, params={'q':'Outsider'})
    await post(2, outsider, body, 200, ref=refs[4], actor_ref=refs[2])
    platform_result = await post(1, outsider, body, 200, ref=refs[4], actor_ref=refs[1])
    assert platform_result.json()['quota']['granted'] == 6
    return headers['key']


async def apply_cutover():
    import json
    cmd = [sys.executable, '-m', 'tools.maintenance.role_admin_reconcile', '--database-url', os.environ['DATABASE_URL'], '--activate-scoped']
    for args, expected in [(['--dry-run'], 'ready'), (['--apply', '--confirm-retire-legacy'], 'applied')]:
        process = subprocess.run(cmd + args, capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stderr
        result = json.loads(process.stdout)
        assert result['status'] == expected, result


if __name__ == '__main__':
    asyncio.run(exercise())
