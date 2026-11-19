"""Synthetic, in-process proof of cookie-bound marker; no database or server."""
import hashlib
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault('DATABASE_URL', 'postgresql+psycopg://synthetic@127.0.0.1:1/isolated')

from pymentor_core.modules.identity.api import router as identity_router
from pymentor_core.modules.analytics_delivery import router as grant_router
from pymentor_core.modules.identity.contracts import InvalidSession
from pymentor_core.adapters.postgres.identity_sessions import AsyncIdentitySessions

COOKIE_A = '11111111-1111-4111-8111-111111111111'
COOKIE_B = '22222222-2222-4222-8222-222222222222'
ACCOUNT = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


@pytest.fixture
def client(monkeypatch):
    class Session:
        async def run_sync(self, callback): return callback(object())
    @asynccontextmanager
    async def uow(): yield SimpleNamespace(session=Session())
    class Identity:
        database = SimpleNamespace(unit_of_work=uow)
        async def authenticate(self, token):
            if token not in (COOKIE_A, COOKIE_B): raise InvalidSession()
            return SimpleNamespace(user_id=1, account_ref=ACCOUNT)
    class Signer:
        def grant(self, **claims): return 'synthetic-non-cookie-grant'
    async def run_sql(request, op):
        return {'subject': ACCOUNT, 'role': 'user', 'scope': 'report:read',
                'courses': ['backend'], 'subjects': [], 'global_read': False}
    monkeypatch.setattr(identity_router, 'identity_repository', lambda db: SimpleNamespace(user_by_id=lambda ident: object()))
    monkeypatch.setattr(identity_router, 'role_projection', lambda db, user: {'account_ref': ACCOUNT})
    monkeypatch.setattr(grant_router, 'run_request_sql', run_sql)
    app = FastAPI()
    app.state.identity_sessions = Identity()
    app.state.analytics_grant_signer = Signer()
    app.state.calendar_session_marker_key = b'X' * 32
    app.include_router(identity_router.user_router, prefix='/user')
    app.include_router(grant_router.router)
    with TestClient(app) as c:
        yield c, app


def get_pair(client, token):
    headers = {'Cookie': 'session=' + token}
    me = client.get('/user/me', headers=headers)
    grant = client.post('/analytics/grants/report', headers=headers, json={'course_id': 'backend'})
    return me, grant


def test_marker_is_bound_to_verified_cookie_and_shared_between_responses(client):
    c, _ = client
    me_a, grant_a = get_pair(c, COOKIE_A)
    me_b, grant_b = get_pair(c, COOKIE_B)
    assert me_a.status_code == grant_a.status_code == me_b.status_code == grant_b.status_code == 200
    a = me_a.headers['X-Calendar-Session-Marker']
    b = me_b.headers['X-Calendar-Session-Marker']
    assert len(a) == 64 and a == grant_a.headers['X-Calendar-Session-Marker']
    assert b == grant_b.headers['X-Calendar-Session-Marker'] and a != b
    assert a != COOKIE_A and a != hashlib.sha256(COOKIE_A.encode()).hexdigest()
    assert a != grant_a.json()['grant'] and AsyncIdentitySessions._token_identifiers(a) is None
    assert 'no-store' in me_a.headers['cache-control'] and 'no-store' in grant_a.headers['cache-control']


def test_invalid_cookie_or_missing_key_never_yields_marker(client):
    c, app = client
    me, grant = get_pair(c, '33333333-3333-4333-8333-333333333333')
    assert me.status_code == grant.status_code == 401
    assert 'X-Calendar-Session-Marker' not in me.headers and 'X-Calendar-Session-Marker' not in grant.headers
    app.state.calendar_session_marker_key = None
    me, grant = get_pair(c, COOKIE_A)
    assert me.status_code == grant.status_code == 200
    assert not me.headers.get('X-Calendar-Session-Marker') and not grant.headers.get('X-Calendar-Session-Marker')


def test_process_key_change_and_old_grant_cannot_match_current_session(client):
    c, app = client
    old_me, old_grant = get_pair(c, COOKIE_A)
    old = old_grant.headers['X-Calendar-Session-Marker']
    new_me, new_grant = get_pair(c, COOKIE_B)
    assert new_me.headers['X-Calendar-Session-Marker'] == new_grant.headers['X-Calendar-Session-Marker']
    assert old != new_me.headers['X-Calendar-Session-Marker']
    app.state.calendar_session_marker_key = b'Y' * 32
    rotated_me, rotated_grant = get_pair(c, COOKIE_A)
    assert rotated_me.headers['X-Calendar-Session-Marker'] != old
    assert rotated_me.headers['X-Calendar-Session-Marker'] == rotated_grant.headers['X-Calendar-Session-Marker']
    assert old_me.headers['X-Calendar-Session-Marker'] == old


def test_targeted_and_unauthorized_grants_do_not_reveal_marker(client, monkeypatch):
    from fastapi import HTTPException
    c, _ = client
    targeted = c.post('/analytics/grants/report', headers={'Cookie': 'session=' + COOKIE_A},
                      json={'course_id':'backend', 'user_id':2})
    assert targeted.status_code == 200 and 'X-Calendar-Session-Marker' not in targeted.headers
    async def forbidden(request, op): raise HTTPException(403, 'denied')
    monkeypatch.setattr(grant_router, 'run_request_sql', forbidden)
    denied = c.post('/analytics/grants/report', headers={'Cookie': 'session=' + COOKIE_A},
                    json={'course_id':'backend'})
    assert denied.status_code == 403 and 'X-Calendar-Session-Marker' not in denied.headers


def test_randomness_failure_disables_only_marker_and_auth_remains_available(client, monkeypatch):
    import secrets
    from pymentor_core.modules.identity.cache_session_marker import new_marker_key
    c, app = client
    def unavailable(size):
        raise OSError('synthetic random provider unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(secrets, 'token_bytes', unavailable)
        app.state.calendar_session_marker_key = new_marker_key()
    me, grant = get_pair(c, COOKIE_A)
    assert me.status_code == grant.status_code == 200
    assert not me.headers.get('X-Calendar-Session-Marker') and not grant.headers.get('X-Calendar-Session-Marker')
