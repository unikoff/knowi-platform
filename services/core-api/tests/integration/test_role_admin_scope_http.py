"""Scoped overview and retired operations over real ASGI, PG16 and Redis."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

_spec = importlib.util.spec_from_file_location('leader_pg_scope', Path(__file__).with_name('test_course_leader_config_pg.py'))
_leader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_leader)
database = _leader.database


def test_scoped_dashboard_and_removed_routes(database):
    _leader.migrate(database)
    root = Path(__file__).resolve().parents[4]
    parent = Path(__file__).resolve().parents[2]
    copied = parent / ('.scope-test-courses-' + uuid4().hex)
    assert copied.resolve().parent == parent and copied.name.startswith('.scope-test-courses-')
    try:
        shutil.copytree(root/'courses', copied, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'node_modules'))
        manifest = copied/'backend'/'course.yaml'
        data = json.loads(manifest.read_text(encoding='utf-8'))
        data['leader_user_id'] = 2
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        env = os.environ.copy()
        env.update(PYTHONDONTWRITEBYTECODE='1', PYMENTOR_COURSES_ROOT=str(copied),
                   DATABASE_URL=database.set(drivername='postgresql+psycopg').render_as_string(hide_password=False),
                   REDIS_URL='redis://127.0.0.1:57381/15', KNOWI_REDIS_PREFIX='scope-http-'+uuid4().hex+':',
                   KNOWI_LOGIN_RECOVERY_KEY='a'*64, PYMENTOR_PROFILE='postgres_redis', PYMENTOR_SCHEMA_MODE='managed',
                   PYTHONPATH=str(parent))
        env.pop('PYMENTOR_REDIS_PREFIX', None)
        env.pop('PYMENTOR_LOGIN_RECOVERY_KEY', None)
        result = subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--exercise'], env=env,
                                cwd=parent, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + '\n' + result.stderr
    finally:
        if copied.exists():
            assert copied.resolve().parent == parent
            shutil.rmtree(copied)


async def exercise():
    import httpx
    from sqlalchemy import create_engine, text
    from unittest.mock import patch
    from pymentor_core.adapters.course_local.runtime import default_runtime
    from pymentor_core.modules.analytics_delivery import legacy_bridge
    from pymentor_core.modules.identity.passwords import hash_password
    from pymentor_core.modules.learning.progress_policy import is_overview_lesson, lesson_completion_requirements
    from pymentor_core.app.asgi import create_app
    from tools.maintenance.course_leader_config import reconcile

    engine = create_engine(os.environ['DATABASE_URL'])
    refs = {i:str(uuid4()) for i in range(1,5)}
    password = 'synthetic-password'
    with engine.begin() as c:
        for uid, name, admin in [(1,'admin',True),(2,'leader',False),(3,'member',False),(4,'outsider',False)]:
            c.execute(text('INSERT INTO users(id,username,email,password_hash,verified,is_admin) VALUES (:uid,:name,:email,:password,true,:admin)'),
                      {'uid':uid,'name':name,'email':name+'@example.test','password':hash_password(password),'admin':admin})
            c.execute(text('INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (:uid,:ref)'), {'uid':uid,'ref':refs[uid]})
        assert reconcile(c, {'backend':2,'backend_copy':None}, apply=True)[0]['status'] == 'applied'
        c.execute(text("INSERT INTO booking_slots(id,course_id,calendar_owner_id,slot_date,start_time,duration_minutes) VALUES (10,'backend',2,'2020-01-01','10:00',60)"))
        c.execute(text("""INSERT INTO booking_history(course_id,slot_id,student_id,owner_at_booking_id,status,starts_at,ends_at)
            VALUES ('backend',10,3,2,'completed','2020-01-01T07:00:00Z','2020-01-01T08:00:00Z')"""))
        c.execute(text("INSERT INTO user_lesson_progress(user_id,lesson_id,course_id,completed_at) VALUES (3,'member','backend',now()),(4,'outside','backend',now()),(3,'foreign','backend_copy',now())"))
        c.execute(text("INSERT INTO user_activity_days(user_id,activity_date,visited_at) VALUES (3,(now() AT TIME ZONE 'Europe/Moscow')::date,now()),(4,(now() AT TIME ZONE 'Europe/Moscow')::date,now())"))
    app = create_app()
    try:
        async with app.router.lifespan_context(app):
            clients = {i:httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=(uuid4().hex,1234)),base_url='http://test') for i in (1,2,3)}
            try:
                for uid,name in [(1,'admin'),(2,'leader'),(3,'member')]:
                    r = await clients[uid].post('/api/user/login',json={'email':name+'@example.test','password':password},headers={'Idempotency-Key':str(uuid4())})
                    assert r.status_code == 200, (uid,r.status_code,r.text)
                async def get(uid,path,params=None,status=200):
                    r = await clients[uid].get(path,params=params)
                    assert r.status_code == status, (path,r.status_code,r.text)
                    return r.json()
                global_overview = await get(1,'/api/admin-platform/overview')
                assert global_overview['activity_today']['unique_users'] == 2
                assert global_overview['course_id'] is None and global_overview['timezone'] == 'Europe/Moscow'
                selected = await get(1,'/api/admin-platform/overview',{'course_id':'backend'})
                assert selected['activity_today']['unique_users'] is None and selected['activity_today']['coverage_since'] is None
                assert selected['dashboard']['daily_active_students'] is None
                assert selected['dashboard']['total_students'] == 1 and selected['dashboard']['lessons_completed_7d'] == 1
                course = await get(2,'/api/admin/courses/backend/overview',{'year':2026,'month':11})
                assert course['roster_count'] == 1 and course['dashboard']['total_students'] == 1
                assert course['dashboard']['lessons_completed_7d'] == 1
                assert course['course_trends']['completions']['status'] == 'available'
                assert len(course['course_trends']['completions']['daily']) == 30
                await get(1,'/api/admin-platform/overview',{'course_id':'not_found'},404)
                await get(2,'/api/admin/courses/backend_copy/overview',{'year':2026,'month':11},403)
                await get(3,'/api/admin/courses/backend/overview',{'year':2026,'month':11},403)

                catalog = default_runtime().snapshot('backend').catalog
                countable = {item for track in catalog.get_tracks_summary() for item in track['countable_lesson_ids']}
                lesson_id = next(lesson['id'] for lesson in catalog.LESSONS
                                 if lesson['id'] in countable and not is_overview_lesson(lesson)
                                 and (True, ()) in lesson_completion_requirements(lesson))
                with engine.begin() as c:
                    c.execute(text("UPDATE users SET subscription_until=now()+interval '1 day' WHERE id=3"))
                    c.execute(text("""INSERT INTO user_lesson_progress(user_id,lesson_id,course_id,completed_at)
                        VALUES (3,:lesson,'backend',now()),(4,:lesson,'backend',now()),(4,:lesson,'backend_copy',now())"""),
                              {'lesson': lesson_id})

                async def legacy_completion_result(_request, _response, _principal, baseline, course_id, **_period):
                    assert course_id == 'backend' and baseline['completed_lessons'] is None
                    return {**baseline, 'completed_lessons': 1,
                            'dashboard': {**baseline['dashboard'], 'lessons_completed_7d': 1}}

                with (patch.dict(os.environ, {'PYMENTOR_ANALYTICS_ADMIN_COMPLETIONS_ENABLED': '1'}),
                      patch.object(app.state, 'analytics_legacy_read_mode', 'analytics'),
                      patch.object(legacy_bridge, 'apply_course_completions', legacy_completion_result)):
                    for uid in (1, 2):
                        offloaded = await get(uid,'/api/admin/courses/backend/overview',{'year':2026,'month':11})
                        assert offloaded['completed_lessons'] == offloaded['dashboard']['lessons_completed_7d'] == 1
                        completions = offloaded['course_trends']['completions']
                        assert completions['status'] == 'available' and completions['quality'] == 'complete'
                        assert len(completions['daily']) == 30
                        for cohort in ('with_subscription', 'without_subscription'):
                            assert sum(day[cohort] for day in completions['daily']) == 1

                context = await get(1,'/api/admin/courses/backend/calendar-context')
                assert len(context['owners']) == 1 and context['owners'][0]['id'] == 2
                for path in ['/api/admin-platform/courses/backend/assignment','/api/admin-platform/courses/backend/assignment/revoke','/api/admin/courses/backend/slot-transfers']:
                    response = await clients[1].post(path,json={},headers={'Idempotency-Key':str(uuid4())})
                    assert response.status_code in (404,405), (path,response.status_code,response.text)
                with engine.connect() as c:
                    assert c.execute(text("SELECT user_id FROM course_leadership WHERE course_id='backend' AND revoked_at IS NULL")).scalar_one() == 2
                    assert c.execute(text('SELECT count(*) FROM booking_slots')).scalar_one() == 1
                assert all(path not in app.openapi()['paths'] for path in [
                    '/api/admin-platform/courses/{course_id}/assignment',
                    '/api/admin-platform/courses/{course_id}/assignment/revoke',
                    '/api/admin/courses/{course_id}/slot-transfers'])
                # File changes cannot alter authority of an already running process.
                manifest = Path(os.environ['PYMENTOR_COURSES_ROOT'])/'backend'/'course.yaml'
                changed = json.loads(manifest.read_text(encoding='utf-8'))
                changed['leader_user_id'] = 3
                manifest.write_text(json.dumps(changed, ensure_ascii=False, indent=2), encoding='utf-8')
                context = await get(1,'/api/admin/courses/backend/calendar-context')
                assert context['owners'][0]['id'] == 2
                from pymentor_core.adapters.course_local.runtime import CourseRuntime, default_runtime
                from pymentor_core.adapters.sql import course_leader_config
                from sqlalchemy.orm import Session
                assert default_runtime().snapshot('backend').manifest.leader_user_id == 2
                restarted = CourseRuntime(os.environ['PYMENTOR_COURSES_ROOT'])
                assert restarted.leader_user_ids['backend'] == 3
                old_runtime = course_leader_config.default_runtime
                try:
                    course_leader_config.default_runtime = lambda: restarted
                    with Session(engine) as db:
                        assert course_leader_config.state(db, 'backend') == 'configured_owner_changed'
                finally:
                    course_leader_config.default_runtime = old_runtime
            finally:
                for client in clients.values(): await client.aclose()
                store = app.state.local_async_identity.redis
                keys = [key async for key in store._client.scan_iter(match=store._prefix+'*')]
                if keys: await store._client.delete(*keys)
    finally:
        engine.dispose()


if __name__ == '__main__':
    asyncio.run(exercise())
