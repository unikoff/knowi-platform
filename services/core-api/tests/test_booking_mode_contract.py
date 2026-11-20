"""Synthetic HTTP and SQL-adapter checks for the booking cutover indicator."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pymentor_core.modules.administration.api import role_admin_router
from pymentor_core.modules.identity.api import sessions


@pytest.mark.parametrize(('enabled', 'status', 'body'), [
    (False, 200, {'mode': 'legacy'}),
    (True, 200, {'mode': 'scoped'}),
    (None, 503, {'detail': 'Cutover state unavailable', 'code': 'unavailable'}),
])
def test_booking_mode_authenticated_and_no_store(monkeypatch, enabled, status, body):
    queries = []

    class DB:
        def execute(self, statement):
            queries.append(str(statement))
            return SimpleNamespace(scalar_one_or_none=lambda: enabled)

    identity = SimpleNamespace(authenticate=AsyncMock(return_value=SimpleNamespace(user_id=1)))
    monkeypatch.setattr(role_admin_router, '_local_async_identity', lambda request: identity)
    monkeypatch.setattr(sessions, '_local_async_identity', lambda request: identity)

    async def run_sql(request, operation, *, identity):
        return operation(DB())

    monkeypatch.setattr(role_admin_router, 'run_request_sql', run_sql)
    app = FastAPI()
    app.include_router(role_admin_router.router, prefix='/api')
    with TestClient(app) as client:
        unauthenticated = client.get('/api/user/booking-mode')
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers['cache-control'] == 'no-store'
        assert not queries
        response = client.get('/api/user/booking-mode', cookies={'session': 'synthetic'})
        assert response.status_code == status
        assert response.json() == body
        assert response.headers['cache-control'] == 'no-store'
        assert queries == ['SELECT scoped_enabled FROM role_admin_cutover WHERE id=1']
        operation = app.openapi()['paths']['/api/user/booking-mode']['get']
        assert operation['security'] == [{'SessionCookie': []}]
        assert operation['responses']['200']['content']['application/json']['schema']['$ref'].endswith('/BookingMode')
