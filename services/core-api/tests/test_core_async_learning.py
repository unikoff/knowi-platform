"""Real multicourse lab learning request and transaction checks."""
import asyncio
import json
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timedelta, timezone
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dev"))
import httpx
from sqlalchemy import event, func, select, update
from core_async_audit import seed
from pymentor_core.app.asgi import create_app
from pymentor_core.app.database import engine
from pymentor_core.adapters.sql.models import UserLessonProgress, User
from pymentor_core.adapters.course_local.runtime import default_runtime


def test_catalog_access_and_duplicate_completion_on_asyncpg():
    email, password = seed()
    baseline = json.loads((Path(__file__).resolve().parents[1] / 'tests/fixtures/contracts/multicourse/MC2.2/runtime-check/api-baseline.json').read_text(encoding='utf-8'))['responses']
    current = Path(__file__).resolve().parents[1] / 'tests/fixtures/contracts/multicourse/main-import/main-content-before.json'
    if current.is_file():
        revision = json.loads(current.read_text(encoding='utf-8'))['content_revision']
        for entry in baseline.values():
            if isinstance(entry.get('body'), dict) and 'content_revision' in entry['body']:
                entry['body']['content_revision'] = revision
    seen = []
    def record(*args): seen.append(1)
    event.listen(engine, 'before_cursor_execute', record)
    async def exercise():
        app = create_app()
        async with app.router.lifespan_context(app):
            database = app.state.local_async_identity.database
            seen.clear()  # Exclude the preserved startup schema precheck.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(uuid4().hex, 1)), base_url='http://test') as client:
                for path in ('/api/learning/tracks', '/api/learning/catalog'):
                    response = await client.get(path)
                    assert response.status_code == baseline[path]['status']
                    assert response.json() == baseline[path]['body']
                login = await client.post('/api/user/login', json={'email':email,'password':password}, headers={'Idempotency-Key':str(uuid4())})
                assert login.status_code == 200
                user_id = login.json()['user']['id']
                catalog = await client.get('/api/learning/catalog')
                assert catalog.status_code == 200
                snapshot = await asyncio.to_thread(default_runtime().snapshot, 'backend')
                restricted = next(item for item in snapshot.catalog.LESSONS if item.get('access') == 'subscription')
                assert (await client.get(f"/api/learning/lessons/{restricted['id']}")).status_code == 200
                async with database.unit_of_work() as uow:
                    await uow.session.execute(update(User).where(User.id==user_id).values(subscription_until=None))
                    await uow.commit()
                assert (await client.get(f"/api/learning/lessons/{restricted['id']}")).status_code == 403
                async with database.unit_of_work() as uow:
                    await uow.session.execute(update(User).where(User.id==user_id).values(subscription_until=datetime.now(timezone.utc)+timedelta(days=30)))
                    await uow.commit()
                lesson = next(item for item in snapshot.catalog.LESSONS if item['self_check'] and not item['tasks'] and not item['source_file'].startswith('chapter_01/'))
                for endpoint in (f"/api/learning/lessons/{lesson['id']}", f"/api/learning/lessons/{lesson['id']}/theory"):
                    assert (await client.get(endpoint)).status_code == 200
                responses = await asyncio.gather(*(client.post(f"/api/learning/lessons/{lesson['id']}/complete") for _ in range(6)))
                assert all(r.status_code == 200 and r.json()['completed'] for r in responses)
                async with database.unit_of_work() as uow:
                    count = await uow.session.scalar(select(func.count()).select_from(UserLessonProgress).where(UserLessonProgress.user_id == user_id, UserLessonProgress.lesson_id == lesson['id']))
                    assert count == 1
                assert database.engine.pool.checkedout() == 0
                assert seen == []
    try:
        asyncio.run(exercise())
    finally:
        event.remove(engine, 'before_cursor_execute', record)


def test_actual_submit_repeats_write_once_and_runner_releases_sql():
    from pymentor_core.adapters.sql.models import UserTaskProgress
    email, password = seed()
    seen = []
    def record(*args): seen.append(1)
    event.listen(engine, 'before_cursor_execute', record)
    async def exercise():
        app = create_app()
        async with app.router.lifespan_context(app):
            database = app.state.local_async_identity.database
            seen.clear()  # Exclude the preserved startup schema precheck.
            snapshot = await asyncio.to_thread(default_runtime().snapshot, 'backend')
            task = next(t for lesson in snapshot.catalog.LESSONS for t in lesson['tasks'] if t.get('mode') == 'script' and t.get('tests') and t['tests'][0].get('assert') == 'stdout')
            code = 'print(' + repr(task['tests'][0]['expected']) + ')'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(uuid4().hex, 1)), base_url='http://test') as client:
                login = await client.post('/api/user/login', json={'email':email,'password':password}, headers={'Idempotency-Key':str(uuid4())})
                assert login.status_code == 200
                user_id = login.json()['user']['id']
                payload = {'code':code, 'task_revision':task['revision']}
                results = await asyncio.gather(*(client.post(f"/api/learning/tasks/{task['id']}/submit", json=payload) for _ in range(3)))
                assert all(r.status_code in (200, 503) for r in results)
                assert any(r.status_code == 200 and r.json()['ok'] for r in results), [(r.status_code, r.json().get('ok')) for r in results]
                repeated = await client.post(f"/api/learning/tasks/{task['id']}/submit", json=payload)
                assert repeated.status_code == 200 and repeated.json()['ok']
                async with database.unit_of_work() as uow:
                    count = await uow.session.scalar(select(func.count()).select_from(UserTaskProgress).where(UserTaskProgress.user_id == user_id, UserTaskProgress.task_id == task['id']))
                    assert count == 1
                assert database.engine.pool.checkedout() == 0
                assert seen == []
    try:
        asyncio.run(exercise())
    finally:
        event.remove(engine, 'before_cursor_execute', record)


def test_async_request_cancellation_rolls_back_sql_and_returns_pool():
    from sqlalchemy import text
    from starlette.requests import Request
    from pymentor_core.app.async_request import run_request_sql
    from pymentor_core.adapters.sql.models import User
    email, _password = seed()
    async def exercise():
        app=create_app()
        async with app.router.lifespan_context(app):
            database=app.state.local_async_identity.database
            started=asyncio.Event()
            request=Request({'type':'http','method':'GET','path':'/','headers':[], 'app':app})
            def work(db):
                user=db.query(User).filter_by(email=email).one()
                user.username='uncommitted request'
                db.flush()
                started.set()
                db.execute(text('SELECT pg_sleep(10)'))
            task=asyncio.create_task(run_request_sql(request, work))
            await started.wait()
            task.cancel()
            try: await task
            except asyncio.CancelledError: pass
            else: raise AssertionError('cancellation swallowed')
            async with database.unit_of_work() as uow:
                user=await uow.session.scalar(select(User).where(User.email==email))
                assert user.username == 'MC195 synthetic'
            assert database.engine.pool.checkedout()==0
    asyncio.run(exercise())
