"""Owned PostgreSQL/Redis ASGI checks; configure the multicourse lab before pytest."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dev"))
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from sqlalchemy import event, update

from core_async_audit import seed
from pymentor_core.app.asgi import create_app
from pymentor_core.app.database import engine
from pymentor_core.adapters.sql.models import Session as LoginSession


def test_login_me_logout_and_expiry_use_no_sync_request_sql():
    email, password = seed()
    observed = []
    def capture(*args):
        observed.append(1)
    event.listen(engine, "before_cursor_execute", capture)

    async def exercise():
        app = create_app()
        async with app.router.lifespan_context(app):
            database = app.state.local_async_identity.database
            observed.clear()  # Exclude the preserved startup schema precheck.
            transport = httpx.ASGITransport(app=app, client=(uuid4().hex, 1234))
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                assert (await client.get('/api/user/me')).status_code == 401
                missing_operation = await client.post('/api/user/login', json={'email':email,'password':password})
                assert missing_operation.status_code == 400
                invalid = await client.post('/api/user/login', json={'email':email,'password':'wrong-password'}, headers={'Idempotency-Key':str(uuid4())})
                assert invalid.status_code == 401
                operation = str(uuid4())
                login = await client.post('/api/user/login', json={'email': email, 'password': password}, headers={'Idempotency-Key': operation})
                assert login.status_code == 200
                user_id = login.json()['user']['id']
                assert (await client.get('/user/me')).json()['user']['id'] == user_id
                created_sessions = []
                factory = database._factory
                def tracked_factory():
                    session = factory()
                    created_sessions.append(session)
                    return session
                database._factory = tracked_factory
                try:
                    results = await asyncio.gather(*(client.get('/api/user/me') for _ in range(8)))
                    assert all(result.status_code == 200 for result in results)
                    assert len(created_sessions) >= 8
                    assert len({id(session) for session in created_sessions}) == len(created_sessions)
                finally:
                    database._factory = factory
                profile = await client.patch('/api/user/profile', json={'username': 'Synthetic Changed'})
                assert profile.status_code == 200 and profile.json()['user']['username'] == 'Synthetic Changed'
                assert (await client.get('/api/user/me')).json()['user']['username'] == 'Synthetic Changed'
                visit = await client.post('/api/user/activity/visit', json={'activity_date': '2026-11-14'})
                assert visit.status_code == 200
                calendar = await client.get('/api/user/activity', params={'year': 2026, 'month': 11})
                assert '2026-11-14' in calendar.json()['days']
                assert (await client.get('/api/user/activity/summary')).status_code == 200
                assert (await client.post('/api/user/logout')).status_code == 200
                assert (await client.get('/api/user/me')).status_code == 401
                login = await client.post('/user/login', json={'email': email, 'password': password}, headers={'Idempotency-Key': str(uuid4())})
                assert login.status_code == 200
                async with database.unit_of_work() as uow:
                    await uow.session.execute(update(LoginSession).where(LoginSession.user_id == user_id).values(created_at=datetime.now(timezone.utc)-timedelta(days=31)))
                    await uow.commit()
                assert (await client.get('/api/user/me')).status_code == 401
                assert database.engine.pool.checkedout() == 0
                assert observed == []
    try:
        asyncio.run(exercise())
    finally:
        event.remove(engine, 'before_cursor_execute', capture)


def test_request_uow_rolls_back_and_returns_connection_after_cancellation():
    from sqlalchemy import select
    from pymentor_core.adapters.sql.models import User
    email, _password = seed()

    async def exercise():
        app = create_app()
        async with app.router.lifespan_context(app):
            database = app.state.local_async_identity.database
            changed = asyncio.Event()
            async def mutate_then_wait():
                async with database.unit_of_work() as uow:
                    user = await uow.session.scalar(select(User).where(User.email == email))
                    user.username = 'Never committed'
                    await uow.session.flush()
                    changed.set()
                    await asyncio.Event().wait()
            task = asyncio.create_task(mutate_then_wait())
            await changed.wait()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError('cancellation was swallowed')
            async with database.unit_of_work() as uow:
                user = await uow.session.scalar(select(User).where(User.email == email))
                assert user.username == 'MC195 synthetic'
            assert database.engine.pool.checkedout() == 0
    asyncio.run(exercise())


def test_registration_profile_email_change_and_password_reset(monkeypatch):
    from threading import get_ident
    from pymentor_core.adapters.sql import email_auth
    from pymentor_core.modules.identity.api import router as identity_router

    monkeypatch.setenv('PYMENTOR_EMAIL_TOKEN_SECRET', 'local-synthetic-secret-' * 3)
    monkeypatch.setattr(identity_router, '_send_registration_verification', lambda *args: None)
    email, password = seed()
    target = 'mc196-' + uuid4().hex + '@example.invalid'
    outgoing = []
    async_loop_thread = []
    def send(config, recipient, purpose, token, **kwargs):
        assert get_ident() != async_loop_thread[0]
        outgoing.append((recipient, purpose, token, kwargs))
    monkeypatch.setattr(email_auth, 'send_account_email', send)
    observed = []
    def capture(*args): observed.append(1)
    event.listen(engine, 'before_cursor_execute', capture)

    async def exercise():
        async_loop_thread.append(get_ident())
        app = create_app()
        async with app.router.lifespan_context(app):
            database = app.state.local_async_identity.database
            observed.clear()  # Exclude the preserved startup schema precheck.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(uuid4().hex, 1234)), base_url='http://test') as client:
                new_email = 'mc196-' + uuid4().hex + '@example.invalid'
                created = await client.post('/api/user/register', json={'username':'synthetic', 'email':new_email, 'password':'synthetic-test-password'})
                assert created.status_code == 200
                assert (await client.post('/api/user/login', json={'email':email,'password':password}, headers={'Idempotency-Key':str(uuid4())})).status_code == 200
                response = await client.post('/api/user/email-change/request', json={'email':target})
                assert response.status_code == 200
                proofs = [row[2] for row in outgoing if row[1] == 'change_email_dual']
                assert len(proofs) == 2
                first = await client.post('/api/user/email-change/confirm', json={'token':proofs[0]})
                assert first.status_code == 200 and first.json()['completed'] is False
                assert (await client.get('/api/user/me')).json()['user']['email'] == email
                second = await client.post('/api/user/email-change/confirm', json={'token':proofs[1]})
                assert second.status_code == 200 and second.json()['completed'] is True
                assert (await client.get('/api/user/me')).json()['user']['email'] == target
                reset = await client.post('/api/user/password-reset/request', json={'email':target})
                assert reset.status_code == 202
                reset_token = next(row[2] for row in outgoing if row[1] == 'reset_password')
                confirm = await client.post('/api/user/password-reset/confirm', json={'token':reset_token, 'new_password':'synthetic-new-password'})
                assert confirm.status_code == 200
                assert (await client.post('/api/user/logout')).status_code == 200
                assert (await client.post('/api/user/login', json={'email':target,'password':'synthetic-new-password'}, headers={'Idempotency-Key':str(uuid4())})).status_code == 200
                assert database.engine.pool.checkedout() == 0
                assert observed == []
    try:
        asyncio.run(exercise())
    finally:
        event.remove(engine, 'before_cursor_execute', capture)


def test_email_delivery_failure_does_not_activate_confirmation(monkeypatch):
    from pymentor_core.adapters.sql import email_auth
    monkeypatch.setenv('PYMENTOR_EMAIL_TOKEN_SECRET', 'local-synthetic-secret-' * 3)
    email, password = seed()
    target = 'mc196-' + uuid4().hex + '@example.invalid'
    captured = []
    database_ref = []
    def fail_second(config, recipient, purpose, token, **kwargs):
        assert database_ref[0].engine.pool.checkedout() == 0
        captured.append(token)
        if len(captured) == 2:
            raise OSError('synthetic delivery failure')
    monkeypatch.setattr(email_auth, 'send_account_email', fail_second)
    observed = []
    def capture(*args): observed.append(1)
    event.listen(engine, 'before_cursor_execute', capture)
    async def exercise():
        app = create_app()
        async with app.router.lifespan_context(app):
            database_ref.append(app.state.local_async_identity.database)
            observed.clear()  # Only business requests are observed after startup.
            transport = httpx.ASGITransport(app=app, client=(uuid4().hex, 1234))
            async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
                login = await client.post('/api/user/login', json={'email':email,'password':password}, headers={'Idempotency-Key':str(uuid4())})
                assert login.status_code == 200
                failed = await client.post('/api/user/email-change/request', json={'email':target})
                assert failed.status_code == 503
                assert (await client.post('/api/user/email-change/confirm', json={'token':captured[0]})).status_code == 409
                assert (await client.get('/api/user/me')).json()['user']['email'] == email
                assert database_ref[0].engine.pool.checkedout() == 0
                assert observed == []
    try:
        asyncio.run(exercise())
    finally:
        event.remove(engine, 'before_cursor_execute', capture)


def test_email_verification_password_change_and_idempotent_login(monkeypatch):
    from pymentor_core.adapters.sql import email_auth
    monkeypatch.setenv('PYMENTOR_EMAIL_TOKEN_SECRET', 'local-synthetic-secret-' * 3)
    sent = []
    monkeypatch.setattr(email_auth, 'send_account_email', lambda config, recipient, purpose, token, **kwargs: sent.append((purpose, token)))
    email = 'mc196-' + uuid4().hex + '@example.invalid'
    password = 'synthetic-previous-password'
    observed = []
    def capture(*args): observed.append(1)
    event.listen(engine, 'before_cursor_execute', capture)
    async def exercise():
        app = create_app()
        async with app.router.lifespan_context(app):
            database = app.state.local_async_identity.database
            observed.clear()  # Exclude the preserved startup schema precheck.
            transport = httpx.ASGITransport(app=app, client=(uuid4().hex, 1234))
            async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
                registered = await client.post('/api/user/register', json={'email':email,'username':'synthetic','password':password})
                assert registered.status_code == 200 and not registered.json()['user']['verified']
                op = str(uuid4())
                first = await client.post('/api/user/login', json={'email':email,'password':password}, headers={'Idempotency-Key':op})
                assert first.status_code == 200
                first_cookie = client.cookies.get('session')
                retry = await client.post('/api/user/login', json={'email':email,'password':password}, headers={'Idempotency-Key':op})
                assert retry.status_code == 200 and client.cookies.get('session') == first_cookie
                verify = await client.post('/api/user/email-verification/request')
                assert verify.status_code == 200
                token = next(t for p,t in reversed(sent) if p == 'verify_email')
                assert (await client.post('/api/user/email-verification/confirm', json={'token':token})).status_code == 200
                assert (await client.get('/api/user/me')).json()['user']['verified'] is True
                assert (await client.post('/api/user/password-change/request')).status_code == 200
                change_token = next(t for p,t in reversed(sent) if p == 'change_password')
                assert (await client.post('/api/user/password-reset/confirm', json={'token':change_token,'new_password':'synthetic-next-password'})).status_code == 200
                assert (await client.get('/api/user/me')).status_code == 200
                assert database.engine.pool.checkedout() == 0
                assert observed == []
    try:
        asyncio.run(exercise())
    finally:
        event.remove(engine, 'before_cursor_execute', capture)
