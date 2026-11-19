"""Offline guard and HTTP-shape checks; no live PostgreSQL or Redis is exercised."""
from pathlib import Path
import json
import runpy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from pymentor_core.adapters.sql import role_admin_reads as reads
from pymentor_core.adapters.sql import role_identity
from pymentor_core.modules.administration.api import role_admin_router
from pymentor_core.modules.identity.principal import Principal, require_admin
from pymentor_core.modules.administration.api import role_admin_schemas
from pymentor_core.modules.identity.api.schemas import CurrentUserPublic


@pytest.fixture(autouse=True)
def configured_projection_for_fake_databases(monkeypatch):
    # These cases exercise authority with a fake DB; projection itself has PG tests.
    from pymentor_core.adapters.sql import course_leader_config
    monkeypatch.setattr(course_leader_config, 'require_projection', lambda db, course: 'ready')


class Result:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return self

    def one_or_none(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


class ActorDB:
    def __init__(self, row):
        self.row = row
        self.queries = []

    def execute(self, statement, params=None):
        self.queries.append((str(statement), params))
        return Result(self.row)


def principal(ref=None, admin=False):
    user = SimpleNamespace(id=2, username='Leader', email='leader@example.test', is_admin=admin, subscription_until=None)
    return Principal.from_user(user, account_ref=ref)


def test_course_authority_is_live_and_checked_before_catalog(monkeypatch):
    seen = []
    monkeypatch.setattr(reads, '_enabled', lambda course: seen.append(course))
    ref = str(uuid4())
    db = ActorDB({'id': 2, 'is_admin': False, 'course_id': 'backend', 'revision': 3})
    assert reads.authorize(db, principal(ref), 'backend')['course_id'] == 'backend'
    assert seen == ['backend']
    seen.clear()
    with pytest.raises(HTTPException) as denied:
        reads.authorize(db, principal(ref), 'backend_copy')
    assert denied.value.status_code == 403 and seen == []
    with pytest.raises(HTTPException) as platform:
        reads.authorize(db, principal(ref))
    assert platform.value.status_code == 403
    assert 'a.lifecycle_id=:ref' in db.queries[0][0]
    with pytest.raises(HTTPException) as old_lifecycle:
        reads.authorize(ActorDB(None), principal(ref), 'backend')
    assert old_lifecycle.value.status_code == 401
    with pytest.raises(HTTPException):
        require_admin(principal(ref))


def test_cursor_is_bound_to_actor_revision_scope_and_filters():
    actor = {'ref': str(uuid4()), 'revision': 4}
    cursor = reads._next(0, 1, [1, 2], actor=actor, scope='roster:backend', filters={'search': ''})
    assert reads._cursor(cursor, actor=actor, scope='roster:backend', filters={'search': ''}) == 1
    for other, scope, filters in [({**actor, 'revision': 5}, 'roster:backend', {'search': ''}),
                                   (actor, 'roster:backend_copy', {'search': ''}),
                                   (actor, 'roster:backend', {'search': 'alice'})]:
        with pytest.raises(HTTPException) as invalid:
            reads._cursor(cursor, actor=other, scope=scope, filters=filters)
        assert invalid.value.status_code == 400


def test_role_projection_is_server_owned(monkeypatch):
    user = SimpleNamespace(id=2, username='A', email='a@example.test', is_admin=False,
                           subscription_until=None, subscription_plan=None, verified=True)
    monkeypatch.setattr(role_identity, 'account_ref', lambda db, uid: '00000000-0000-4000-8000-000000000002')
    leader = role_identity.project(ActorDB({'is_admin': False, 'course_id': 'backend', 'revision': 5}), user)
    assert leader['role'] == 'leader' and not leader['is_admin'] and leader['course_id'] == 'backend'
    admin = role_identity.project(ActorDB({'is_admin': True, 'course_id': None, 'revision': 6}), user)
    assert admin['role'] == 'admin' and admin['is_admin'] and admin['course_id'] is None
    assert 'platform.manage' in admin['capabilities'] and 'platform.manage' not in leader['capabilities']


def test_unmapped_paid_data_blocks_course_reads():
    db = ActorDB(True)
    with pytest.raises(HTTPException) as unavailable:
        reads._require_reconciled(db)
    assert unavailable.value.status_code == 503
    sql = db.queries[0][0]
    assert 'course_id IS NULL' in sql and 'calendar_owner_id IS NULL' in sql
    assert 'granted_slots>0' in sql


def test_quota_query_checks_target_lifecycle_and_prevents_negative_balance():
    class QuotaResult(Result):
        def one_or_none(self):
            return self.value
    class QuotaDB(ActorDB):
        def execute(self, statement, params):
            self.queries.append((str(statement), params))
            return QuotaResult(self.row)
    ref = str(uuid4())
    db = QuotaDB({'granted': 3, 'used': 2})
    result = reads.quota_data(db, 'backend', 7, 2026, 11, ref)
    assert result['remaining'] == 1 and db.queries[0][1]['ref'] == ref
    assert 'course_id=:course' in db.queries[0][0] and 'lifecycle_id=:ref' in db.queries[0][0]
    with pytest.raises(HTTPException) as missing:
        reads.quota_data(QuotaDB(None), 'backend', 7, 2026, 11, ref)
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as inconsistent:
        reads.quota_data(QuotaDB({'granted': 1, 'used': 2}), 'backend', 7, 2026, 11, ref)
    assert inconsistent.value.status_code == 503


def test_new_route_validation_and_error_shape_with_stubbed_adapter(monkeypatch):
    """Only routing/serialization is exercised, not authentication or SQL authorization."""
    app = FastAPI()
    app.include_router(role_admin_router.router, prefix='/api')
    fixture = json.loads((Path(__file__).resolve().parents[1] / 'contracts' / 'role-admin-v1.fixtures.json').read_text(encoding='utf-8'))

    async def fake_read(request, operation, *, limited=False, selection_course=None, with_principal=False):
        if request.url.path.endswith('/grant-search'):
            return fixture['search']
        raise HTTPException(status_code=403, detail='Course access denied')

    monkeypatch.setattr(role_admin_router, '_read', fake_read)
    with TestClient(app) as client:
        good = client.get('/api/admin/courses/backend/grant-search', params={'q': 'alice'})
        assert good.status_code == 200 and good.json() == fixture['search']
        invalid = client.get('/api/admin/courses/backend/grant-search', params={'q': 'a'})
        assert invalid.status_code == 422 and invalid.json()['code'] == 'invalid_request'
        denied = client.get('/api/admin/courses/backend_copy/overview', params={'year': 2026, 'month': 11})
        assert denied.status_code == 403 and denied.json()['code'] == 'forbidden'


def test_runtime_read_dto_fields_match_accepted_contract():
    design = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'contracts' / 'role-admin-v1.py'))
    names = ['Quota', 'QuotaEnvelope', 'Person', 'RosterPerson', 'Roster', 'SearchResult',
             'Progress', 'CourseOverview', 'CalendarContext', 'PlatformOverview', 'Courses',
             'PlatformUser', 'PlatformUsers', 'Operation', 'Operations']
    for name in names:
        actual = getattr(role_admin_schemas, name)
        assert set(actual.model_fields) == set(design[name].model_fields), name
        assert {key for key, field in actual.model_fields.items() if field.is_required()} == {
            key for key, field in design[name].model_fields.items() if field.is_required()
        }, name
    assert set(CurrentUserPublic.model_fields) == set(design['User'].model_fields)
    assert all(field.is_required() for field in CurrentUserPublic.model_fields.values())
