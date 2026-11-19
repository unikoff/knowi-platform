"""Offline command invariants; fake DB/Redis do not prove PostgreSQL transactions."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import runpy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from pymentor_core.adapters.redis.sessions import RedisSessionStore
from pymentor_core.adapters.sql import role_admin_commands as commands
from pymentor_core.modules.administration.api import role_admin_schemas as dto
from pymentor_core.modules.administration.api import role_admin_router


@pytest.fixture(autouse=True)
def configured_projection_for_fake_databases(monkeypatch):
    # Real projection and lifecycle checks are covered by the PG integration suite.
    from pymentor_core.adapters.sql import course_leader_config
    monkeypatch.setattr(course_leader_config, 'require_projection', lambda db, course: 'ready')


def example():
    actor = SimpleNamespace(user_id=2, account_ref=str(uuid4()))
    cmd = commands.Command(operation='quota.grant', path='/api/admin/courses/backend/users/7/quota-grants',
                           key=str(uuid4()), payload={'year': 2026, 'month': 11, 'add': 3, 'reason': 'manual'},
                           course='backend', target_id=7, target_ref=str(uuid4()))
    return actor, cmd


def test_hash_binds_operation_payload_path_scope_and_target():
    _, command = example()
    base = commands.request_hash(command)
    assert base == commands.request_hash(replace(command, payload=dict(reversed(list(command.payload.items())))))
    for other in (replace(command, path=command.path + '/other'), replace(command, course='backend_copy'),
                  replace(command, target_ref=str(uuid4())), replace(command, payload={**command.payload, 'add': 4})):
        assert commands.request_hash(other) != base


def test_public_command_entry_fails_closed_before_any_write(monkeypatch):
    actor, command = example()
    observed = []
    monkeypatch.setattr(commands.reads, 'authorize', lambda db, principal, course: observed.append(course))
    def disabled(db, *, scoped):
        assert scoped
        raise HTTPException(status_code=503, detail='Scoped writes unavailable until cutover')
    monkeypatch.setattr(commands, 'require_mode', disabled)
    with pytest.raises(HTTPException) as blocked:
        commands.guarded_execute(object(), actor, command)
    assert blocked.value.status_code == 503 and observed == ['backend']


class Result:
    def __init__(self, value):
        self.value = value
    def mappings(self):
        return self
    def one_or_none(self):
        return self.value
    def all(self):
        return self.value
    def scalar_one_or_none(self):
        return self.value
    def scalar_one(self):
        return self.value


class FakeDB:
    def __init__(self, actor, command, prior=None, assignment=True, roster=False):
        self.actor, self.command, self.prior = actor, command, prior
        self.assignment, self.roster = assignment, roster
        self.calls, self.commits = [], 0
    def execute(self, statement, params=None):
        query = str(statement)
        self.calls.append(query)
        if 'FROM role_admin_commands' in query:
            return Result(self.prior)
        if 'FROM users WHERE id IN' in query:
            return Result([{'id': self.actor.user_id, 'is_admin': False}, {'id': self.command.target_id, 'is_admin': False}])
        if 'FROM account_lifecycles' in query:
            return Result(self.actor.account_ref if params['uid'] == self.actor.user_id else self.command.target_ref)
        if 'FROM course_leadership' in query:
            return Result({'id': 1, 'course_id': self.command.course, 'user_id': self.actor.user_id,
                           'account_ref': self.actor.account_ref, 'revision': 5} if self.assignment else None)
        if 'role_admin_revisions' in query:
            return Result(5)
        if 'FROM booking_history' in query:
            return Result(self.roster)
        if 'pg_advisory_xact_lock' in query:
            return Result(None)
        raise AssertionError(query)
    def commit(self):
        self.commits += 1


def prior_for(command):
    return {'request_hash': commands.request_hash(command), 'response_status': 200,
            'response_body': {'quota': {'granted': 3}}, 'redacted': False,
            'target_user_id': command.target_id, 'target_account_ref': command.target_ref}


def test_replay_rechecks_authority_and_does_not_require_expired_selection(monkeypatch):
    actor, command = example()
    monkeypatch.setattr(commands.reads, '_enabled', lambda course: None)
    db = FakeDB(actor, command, prior_for(command))
    assert commands._execute(db, actor, command) == {'quota': {'granted': 3}}
    assert db.commits == 0
    assert next(i for i, q in enumerate(db.calls) if 'FROM users WHERE id IN' in q) < next(i for i, q in enumerate(db.calls) if 'FROM course_leadership' in q)
    with pytest.raises(HTTPException) as revoked:
        commands._execute(FakeDB(actor, command, prior_for(command), assignment=False), actor, command)
    assert revoked.value.status_code == 403
    changed = replace(command, payload={**command.payload, 'add': 4})
    with pytest.raises(HTTPException) as conflict:
        commands._execute(FakeDB(actor, changed, prior_for(command)), actor, changed)
    assert conflict.value.status_code == 409
    redacted = {**prior_for(command), 'redacted': True, 'target_user_id': None, 'target_account_ref': None}
    with pytest.raises(HTTPException) as tombstone:
        commands._execute(FakeDB(actor, command, redacted), actor, command)
    assert tombstone.value.status_code == 409


def test_new_outside_roster_grant_needs_matching_search_revision(monkeypatch):
    actor, command = example()
    monkeypatch.setattr(commands.reads, '_enabled', lambda course: None)
    with pytest.raises(HTTPException) as denied:
        commands._execute(FakeDB(actor, command), actor, command)
    assert denied.value.status_code == 403
    effects = []
    monkeypatch.setattr(commands, '_apply', lambda *args: ({'quota': {}}, None, {}, 'course'))
    monkeypatch.setattr(commands, '_audit', lambda *args, **kwargs: effects.append('audit'))
    monkeypatch.setattr(commands, '_store', lambda *args, **kwargs: effects.append('dedup'))
    db = FakeDB(actor, command)
    assert commands._execute(db, actor, replace(command, selection_revision=5)) == {'quota': {}}
    assert effects == ['audit', 'dedup'] and db.commits == 1


@pytest.mark.parametrize('model,payload', [
    (dto.QuotaGrant, {'year': 2026, 'month': 11, 'add': 0, 'reason': 'x'}),
    (dto.QuotaGrant, {'year': 2026, 'month': 11, 'add': '3', 'reason': 'x'}),
    (dto.SubscriptionExtend, {'days': -1, 'reason': 'x'}),
    (dto.SubscriptionReset, {'confirmed': 1, 'reason': 'x'}),
    (dto.AdminRoleCommand, {'is_admin': True, 'confirmed': False, 'reason': 'x'}),
])
def test_invalid_mutations_rejected(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_search_selection_uses_expiring_hashed_redis_key():
    async def check():
        store = object.__new__(RedisSessionStore)
        store._prefix = 'test:'
        store._client = SimpleNamespace(set=AsyncMock(), get=AsyncMock(return_value=b'7'))
        actor, target = str(uuid4()), str(uuid4())
        await store.store_role_selection(actor, 'backend', target, 7)
        key, value = store._client.set.await_args.args
        assert actor not in key and target not in key and 'backend' not in key
        assert value == '7' and store._client.set.await_args.kwargs['ex'] == 900
        assert await store.get_role_selection(actor, 'backend', target) == 7
    asyncio.run(check())


def test_mutation_route_stays_closed_at_cutover_barrier_with_stubs(monkeypatch):
    actor, command = example()
    identity = SimpleNamespace(redis=SimpleNamespace(get_role_selection=AsyncMock(return_value=5)))
    monkeypatch.setattr(role_admin_router, '_local_async_identity', lambda request: identity)
    async def get_actor(request):
        return actor
    async def run_sql(request, operation, *, identity):
        return operation(object())
    monkeypatch.setattr(role_admin_router, 'request_principal', get_actor)
    monkeypatch.setattr(role_admin_router, 'run_request_sql', run_sql)
    monkeypatch.setattr(commands.reads, 'authorize', lambda db, principal, course: None)
    def disabled(db, *, scoped):
        raise HTTPException(status_code=503, detail='Scoped writes unavailable until cutover')
    monkeypatch.setattr(commands, 'require_mode', disabled)
    monkeypatch.setattr(commands, '_execute', lambda *args: pytest.fail('barrier was bypassed'))
    app = FastAPI()
    app.include_router(role_admin_router.router, prefix='/api')
    with TestClient(app) as client:
        blocked = client.post(command.path, json=command.payload,
                              headers={'Idempotency-Key': command.key, 'X-Account-Ref': command.target_ref})
        assert blocked.status_code == 503 and blocked.json()['code'] == 'unavailable'
        missing_key = client.post(command.path, json=command.payload, headers={'X-Account-Ref': command.target_ref})
        assert missing_key.status_code == 422 and missing_key.json()['code'] == 'invalid_request'


def test_subscription_extension_preserves_existing_paid_time():
    actor, cmd = example()
    cmd = replace(cmd, operation='subscription.extend.course', payload={'days': 3, 'reason': 'x'})
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    until = now + timedelta(days=30)
    class DB:
        def __init__(self):
            self.updated = None
        def execute(self, statement, params=None):
            if 'clock_timestamp' in str(statement):
                return Result(now)
            assert 'UPDATE users SET subscription_until' in str(statement)
            self.updated = params
            return Result(None)
    db = DB()
    response, before, after, scope = commands._apply(db, actor,
        {'subscription_until': until, 'subscription_plan': 'pro'}, cmd, 7, cmd.target_ref, None)
    assert scope == 'platform' and before['subscription_until'] == until
    assert after['subscription_until'] == until + timedelta(days=3)
    assert response['subscription_until'] == db.updated['until'] == after['subscription_until']


def test_mutation_dto_fields_match_accepted_contract():
    design = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'contracts' / 'role-admin-v1.py'))
    for name in ['QuotaGrant', 'SubscriptionExtend', 'SubscriptionReset', 'AdminRoleCommand',
                 'AdminRoleResult', 'AssignmentCommand', 'ReasonCommand', 'Assignment', 'AssignmentEnvelope']:
        actual = getattr(dto, name)
        assert set(actual.model_fields) == set(design[name].model_fields), name
        assert all(field.is_required() for field in actual.model_fields.values()), name
