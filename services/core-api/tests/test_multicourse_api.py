"""Course-aware real ASGI/PG/Redis checks; no source/SQLite or real delivery."""
import asyncio
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sys
from uuid import uuid4
import httpx
from sqlalchemy import func,select,update
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'/'dev'))
from core_async_audit import seed
from pymentor_core.app.asgi import create_app
from pymentor_core.adapters.course_local.runtime import default_runtime,current_course_id
from pymentor_core.adapters.sql.models import User,UserLessonProgress,UserTaskProgress

COURSES=('backend','backend_copy')

async def login(client,email,password):
    response=await client.post('/api/user/login',json={'email':email,'password':password},headers={'Idempotency-Key':str(uuid4())})
    assert response.status_code==200,response.text
    return response.json()['user']['id']

def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=(uuid4().hex,1)),base_url='http://test')


def test_shared_access_scoped_resources_defaults_and_fail_closed(monkeypatch):
    email,password=seed()
    async def exercise():
        app=create_app()
        async with app.router.lifespan_context(app):
            db=app.state.local_async_identity.database
            runtime=default_runtime()
            lessons=runtime.snapshot('backend').catalog.LESSONS
            chosen={kind:next(l for l in lessons if l.get('access')==kind) for kind in ('free','registered','subscription')}
            async with client(app) as guest,client(app) as student:
                products=await guest.get('/api/learning/products')
                assert products.status_code==200
                assert {p['course_id'] for p in products.json()['products']}==set(COURSES)
                assert all(set(p)=={'course_id','slug','title','content_version'} for p in products.json()['products'])
                for course in COURSES:
                    for kind,lesson in chosen.items():
                        r=await guest.get('/api/learning/lessons/'+lesson['id'],params={'course_id':course})
                        assert r.status_code==(200 if kind=='free' else 403),(course,kind,r.text)
                uid=await login(student,email,password)
                # Existing active subscription opens both without separate grants.
                for course in COURSES:
                    for lesson in chosen.values():
                        assert (await student.get('/api/learning/lessons/'+lesson['id'],params={'course_id':course})).status_code==200
                for until in (None,datetime.now(timezone.utc)-timedelta(seconds=30),datetime.now(timezone.utc)+timedelta(days=30)):
                    for admin in (False,True):
                        async with db.unit_of_work() as uow:
                            await uow.session.execute(update(User).where(User.id==uid).values(subscription_until=until,is_admin=admin))
                            await uow.commit()
                        expected=200 if until is not None and until>datetime.now(timezone.utc) else 403
                        for course in COURSES:
                            r=await student.get('/api/learning/lessons/'+chosen['subscription']['id'],params={'course_id':course})
                            assert r.status_code==expected,(course,admin,until,r.text)
                            assert (await student.get('/api/learning/lessons/'+chosen['registered']['id'],params={'course_id':course})).status_code==200
                # Legacy missing course and explicit backend remain identical.
                for path in ('/api/learning/tracks','/api/learning/catalog','/learning/catalog'):
                    assert (await student.get(path)).json()==(await student.get(path,params={'course_id':'backend'})).json()
                for bad in ('unknown','', '../backend','chapter_01'):
                    assert (await student.get('/api/learning/catalog',params={'course_id':bad})).status_code==404
                assert (await student.get('/api/learning/catalog?course_id=backend&course_id=backend_copy')).status_code==400
                registry=runtime.registry
                entries=dict(registry.entries)
                entries['backend_copy']=replace(entries['backend_copy'],enabled=False)
                monkeypatch.setattr(runtime,'registry',replace(registry,entries=entries))
                assert (await student.get('/api/learning/catalog',params={'course_id':'backend_copy'})).status_code==404
                assert (await student.get('/api/learning/catalog',params={'course_id':'backend'})).status_code==200
                monkeypatch.setattr(runtime,'registry',registry)
                from pymentor_core.modules.learning.api import service
                old=service.prepare_request_content
                def unavailable(*_,**__): raise OSError('PRIVATE_SERVER_PATH_MC481')
                monkeypatch.setattr(service,'prepare_request_content',unavailable)
                response=await student.get('/api/learning/catalog',params={'course_id':'backend_copy'})
                assert response.status_code==503 and 'PRIVATE_SERVER_PATH_MC481' not in response.text
                monkeypatch.setattr(service,'prepare_request_content',old)
            assert current_course_id()=='backend'
            assert db.engine.pool.checkedout()==0
    asyncio.run(exercise())


def test_http_completion_ownership_concurrent_course_scope_and_runner():
    email,password=seed(); other_email,other_password=seed()
    async def exercise():
        app=create_app()
        async with app.router.lifespan_context(app):
            db=app.state.local_async_identity.database
            snapshot=default_runtime().snapshot('backend')
            lesson=next(l for l in snapshot.catalog.LESSONS if l['self_check'] and not l['tasks'] and not l['source_file'].startswith('chapter_01/'))
            task=next(t for l in snapshot.catalog.LESSONS for t in l['tasks'] if t.get('mode')=='script' and t.get('tests') and t['tests'][0].get('assert')=='stdout')
            async with client(app) as student,client(app) as other:
                uid=await login(student,email,password);other_id=await login(other,other_email,other_password)
                path='/api/learning/lessons/'+lesson['id']
                results=await asyncio.gather(*(student.post(path+'/complete',params={'course_id':course},json={'user_id':other_id}) for course in COURSES for _ in range(3)))
                assert all(r.status_code==200 and r.json()['completed'] for r in results),[r.text for r in results]
                for course in COURSES:
                    own=await student.get(path,params={'course_id':course}); stranger=await other.get(path,params={'course_id':course})
                    assert own.status_code==stranger.status_code==200
                    assert own.json()['lesson']['completed'] is True
                    assert stranger.json()['lesson']['completed'] is False
                payload={'code':'print('+repr(task['tests'][0]['expected'])+')','task_revision':task['revision']}
                submit='/api/learning/tasks/'+task['id']+'/submit'
                conflict=await student.post(submit,params={'course_id':'backend'},json={**payload,'course_id':'backend_copy'})
                assert conflict.status_code==400
                stale=await student.post(submit,params={'course_id':'backend_copy'},json={**payload,'task_revision':'0'*64})
                assert stale.status_code==409
                for course in COURSES:
                    # Preview/interpreter are scoped, but never award completion.
                    preview=await student.post('/api/learning/tasks/'+task['id']+'/run',params={'course_id':course},json=payload)
                    assert preview.status_code==200 and preview.json()['ok'],preview.text
                    interpreted=await student.post('/api/learning/interpreter/run',json={'course_id':course,'code':'print(481)'})
                    assert interpreted.status_code==200 and interpreted.json()['stdout'].strip()=='481'
                async with db.unit_of_work() as uow:
                    assert await uow.session.scalar(select(func.count()).select_from(UserTaskProgress).where(UserTaskProgress.user_id==uid))==0
                for course in COURSES:
                    result=await student.post(submit,params={'course_id':course},json=payload)
                    assert result.status_code==200 and result.json()['ok'],result.text
                    assert all(t['expected'] is None and t['actual'] is None for t in result.json()['tests'])
                    assert result.json()['stdout']=='' and result.json()['traceback'] is None
                    if course=='backend':
                        async with db.unit_of_work() as uow:
                            assert list((await uow.session.scalars(select(UserTaskProgress.course_id).where(UserTaskProgress.user_id==uid))).all())==['backend']
                async with db.unit_of_work() as uow:
                    rows=(await uow.session.execute(select(UserTaskProgress.course_id,UserTaskProgress.user_id).where(UserTaskProgress.user_id.in_([uid,other_id])))).all()
                    assert set(rows)=={('backend',uid),('backend_copy',uid)}
                    assert await uow.session.scalar(select(func.count()).select_from(UserLessonProgress).where(UserLessonProgress.user_id==uid,UserLessonProgress.lesson_id==lesson['id']))==2
                # Missing course still targets A only; same IDs do not collide.
                assert (await student.post(submit,json=payload)).status_code==200
            assert current_course_id()=='backend'
            assert db.engine.pool.checkedout()==0
    asyncio.run(exercise())


def test_restart_and_compatible_disable_copy_preserve_new_progress(monkeypatch):
    email,password=seed()
    async def exercise():
        app=create_app()
        runtime=default_runtime()
        lesson=next(l for l in runtime.snapshot('backend').catalog.LESSONS if l['self_check'] and not l['tasks'] and not l['source_file'].startswith('chapter_01/'))
        path='/api/learning/lessons/'+lesson['id']
        async with app.router.lifespan_context(app):
            async with client(app) as student:
                uid=await login(student,email,password)
                assert (await student.post(path+'/complete')).status_code==200
                assert (await student.get(path,params={'course_id':'backend_copy'})).json()['lesson']['completed'] is False
                assert (await student.post(path+'/complete',params={'course_id':'backend_copy'})).status_code==200
                cookies=student.cookies
        # Fresh application/resources, persistent disposable PG/Redis data.
        second=create_app()
        async with second.router.lifespan_context(second):
            db=second.state.local_async_identity.database
            async with client(second) as student:
                student.cookies.update(cookies)
                for course in COURSES:
                    assert (await student.get(path,params={'course_id':course})).json()['lesson']['completed'] is True
                assert (await student.get('/health/live')).status_code==200
                assert (await student.get('/health/ready')).status_code==200
            async with db.unit_of_work() as uow:
                before=list((await uow.session.execute(select(UserLessonProgress.id,UserLessonProgress.course_id,UserLessonProgress.completed_at).where(UserLessonProgress.user_id==uid).order_by(UserLessonProgress.id))).all())
        registry=runtime.registry
        entries=dict(registry.entries)
        entries['backend_copy']=replace(entries['backend_copy'],enabled=False)
        monkeypatch.setattr(runtime,'registry',replace(registry,entries=entries))
        third=create_app()
        async with third.router.lifespan_context(third):
            db=third.state.local_async_identity.database
            async with client(third) as student:
                student.cookies.update(cookies)
                assert (await student.get('/health/ready')).status_code==200
                assert (await student.get(path)).json()['lesson']['completed'] is True
                assert (await student.get(path,params={'course_id':'backend_copy'})).status_code==404
                async with db.unit_of_work() as uow:
                    after=list((await uow.session.execute(select(UserLessonProgress.id,UserLessonProgress.course_id,UserLessonProgress.completed_at).where(UserLessonProgress.user_id==uid).order_by(UserLessonProgress.id))).all())
                    assert before==after
                monkeypatch.setattr(runtime,'registry',registry)
                assert (await student.get(path,params={'course_id':'backend_copy'})).json()['lesson']['completed'] is True
            assert db.engine.pool.checkedout()==0
    asyncio.run(exercise())
