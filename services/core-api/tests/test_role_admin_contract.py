"""Offline RA1 contract checks; these do not assert live API enforcement."""
import copy
import json
import runpy
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic.json_schema import models_json_schema

CONTRACT = Path(__file__).resolve().parents[1] / 'contracts'
DTO = runpy.run_path(str(CONTRACT / 'role-admin-v1.py'))
FIX = json.loads((CONTRACT / 'role-admin-v1.fixtures.json').read_text(encoding='utf-8'))


def parse(model, payload):
    return DTO[model].model_validate_json(json.dumps(payload))


@pytest.mark.parametrize('role', ['user', 'leader', 'admin'])
def test_golden_identity_and_capabilities(role):
    user = parse('Me', FIX['me'][role]).user
    course_caps = {'course.read', 'course.roster', 'course.search_grant',
                   'course.quota.grant', 'course.subscription.extend', 'course.calendar.manage'}
    expected = set() if role == 'user' else course_caps
    if role == 'admin':
        expected |= {'platform.read', 'platform.manage'}
    assert set(user.capabilities) == expected
    assert user.is_admin == (role == 'admin')
    assert str(user.account_ref) == FIX['me'][role]['user']['account_ref']
    assert user.course_id == ('backend' if role == 'leader' else None)


def test_invalid_identity_and_untrusted_fields_rejected():
    leader = copy.deepcopy(FIX['me']['leader'])
    leader['user']['is_admin'] = True
    with pytest.raises(ValidationError):
        parse('Me', leader)
    leader = copy.deepcopy(FIX['me']['leader'])
    leader['user']['course_id'] = None
    with pytest.raises(ValidationError):
        parse('Me', leader)
    grant = {'year': 2026, 'month': 11, 'add': 3, 'reason': 'manual', 'is_admin': True}
    with pytest.raises(ValidationError):
        parse('QuotaGrant', grant)


def test_fixture_privacy_and_course_isolation():
    roster = parse('Roster', FIX['roster'])
    result = parse('SearchResult', FIX['search'])
    quota = parse('Quota', FIX['quota'])
    progress = parse('Progress', FIX['progress'])
    booking = parse('Booking', FIX['booking'])
    assert {person.last_booking_status for person in roster.users} == {'booked', 'completed', 'cancelled', None}
    assert result.users[0].id not in {person.id for person in roster.users}
    assert roster.course_id == result.course_id == quota.course_id == progress.course_id == booking.course_id
    assert quota.remaining == 3
    assert parse('Subscription', FIX['subscription']).scope == 'platform'
    assert 'student_id' not in DTO['StudentSlot'].model_fields
    assert set(DTO['Person'].model_fields) == {'id', 'account_ref', 'username', 'masked_email'}
    assert set(DTO['Progress'].model_fields).isdisjoint({'code', 'solution', 'tests', 'email'})
    context = parse('BookingContext', {'course_id': 'backend', 'active_owner_id': None,
                                       'active_owner_name': None, 'my_upcoming_bookings': []})
    assert context.active_owner_id is None


def test_search_result_can_read_course_balance_before_grant():
    person = parse('SearchResult', FIX['search']).users[0]
    balance = parse('QuotaEnvelope', FIX['quota_before_grant']).quota
    assert str(person.account_ref) == FIX['quota_read_headers']['X-Account-Ref']
    assert balance.user_id == person.id
    assert (balance.course_id, balance.year, balance.month, balance.remaining) == ('backend', 2026, 11, 0)
    assert person.id not in {item['id'] for item in FIX['roster']['users']}


@pytest.mark.parametrize('model,payload', [
    ('QuotaGrant', {'year': 2026, 'month': 11, 'add': -1, 'reason': 'x'}),
    ('QuotaGrant', {'year': 2026, 'month': 13, 'add': 3, 'reason': 'x'}),
    ('SubscriptionExtend', {'days': 0, 'reason': 'x'}),
    ('SubscriptionReset', {'confirmed': False, 'reason': 'x'}),
    ('Quota', {'course_id': 'backend', 'user_id': 7, 'year': 2026, 'month': 11, 'granted': 1, 'used': 2, 'remaining': 0}),
    ('Subscription', {'scope': 'course', 'user_id': 7, 'subscription_until': None, 'subscription_plan': None}),
    ('Subscription', {'scope': 'platform', 'user_id': 7, 'subscription_until': '2026-12-01T10:00:00', 'subscription_plan': None}),
])
def test_invalid_commands_and_balances(model, payload):
    with pytest.raises(ValidationError):
        parse(model, payload)


def test_access_fixture_exercises_essential_denials():
    cases = {(c['actor'], c['operation'], c['course']): c['allowed'] for c in FIX['access_cases']}
    assert cases['leader', 'course_overview', 'backend']
    assert not cases['leader', 'course_overview', 'backend_copy']
    assert not cases['leader', 'legacy_admin', None]
    assert not cases['leader', 'subscription_reset', None]
    assert cases['admin', 'platform_read', None]


def test_generated_schema_is_current_and_all_refs_resolve():
    _, expected = models_json_schema([(m, 'validation') for m in DTO['MODELS']], title='Role admin v1 DTOs')
    expected['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    generated = json.loads((CONTRACT / 'role-admin-v1.schema.json').read_text(encoding='utf-8'))
    assert generated == expected

    def walk(value):
        if isinstance(value, dict):
            if '$ref' in value:
                assert value['$ref'].startswith('#/$defs/')
                assert value['$ref'].split('/')[-1] in generated['$defs']
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(generated)


def test_openapi_bindings_and_generation():
    generate = runpy.run_path(str(CONTRACT / 'role-admin-v1.openapi.py'))['generate']
    api = json.loads((CONTRACT / 'role-admin-v1.openapi.json').read_text(encoding='utf-8'))
    schema = json.loads((CONTRACT / 'role-admin-v1.schema.json').read_text(encoding='utf-8'))
    assert api == generate()
    assert api['security'] == [{'SessionCookie': []}]
    assert '/api/courses/{course_id}/booking/context' in api['paths']
    seen = set()
    for methods in api['paths'].values():
        for method, op in methods.items():
            assert op['operationId'] not in seen
            seen.add(op['operationId'])
            headers = {p['name'] for p in op['parameters'] if p['in'] == 'header' and p['required']}
            if method == 'post':
                assert 'Idempotency-Key' in headers
            if op['operationId'] in {'studentStatistics', 'studentProfileDetails', 'courseUserQuota', 'grantQuota', 'courseExtendSubscription', 'courseExtendAndGrant', 'resetSubscription', 'platformExtendSubscription'}:
                assert 'X-Account-Ref' in headers
            if op['operationId'] in {'studentStatistics', 'studentProfileDetails', 'courseStudyTime', 'courseExtendAndGrant'}:
                assert 'X-Actor-Ref' in headers
            if op['operationId'] == 'courseStudyTime':
                assert 'X-Auth-Revision' in headers
            ref = op['responses']['200']['content']['application/json']['schema']['$ref']
            assert ref.split('/')[-1] in schema['$defs']
    assert len(seen) == 27
    assert '/api/admin-platform/users/{user_id}/admin-role' not in api['paths']
    assert '/api/admin-platform/courses/{course_id}/assignment' not in api['paths']
    assert '/api/admin-platform/courses/{course_id}/assignment/revoke' not in api['paths']
    assert '/api/admin/courses/{course_id}/slot-transfers' not in api['paths']
    quota_read = api['paths']['/api/admin/courses/{course_id}/users/{user_id}/quota']['get']
    assert quota_read['responses']['200']['content']['application/json']['schema']['$ref'].endswith('/QuotaEnvelope')
    assert {p['name'] for p in quota_read['parameters'] if p['required']} >= {'course_id', 'user_id', 'year', 'month', 'X-Account-Ref'}


def test_staff_calendar_context_names_owners_without_exposing_directory():
    context = parse('CalendarContext', {'course_id': 'backend', 'owners': [
        {'id': 12, 'username': 'Current teacher', 'active': True, 'pending_slots': 0},
    ]})
    assert len(context.owners) == 1 and context.owners[0].active
    with pytest.raises(ValidationError):
        parse('CalendarContext', {'course_id': 'backend', 'owners': [
            {'id': 12, 'username': 'Current', 'active': True, 'pending_slots': 0},
            {'id': 13, 'username': 'Former', 'active': False, 'pending_slots': 2}]})
    assert set(DTO['CalendarOwner'].model_fields) == {'id', 'username', 'active', 'pending_slots'}
    assert parse('CalendarContext', {'course_id': 'backend_copy', 'owners': []}).owners == []


def test_legacy_booking_creation_time_is_unknown_without_inventing_history():
    legacy = dict(FIX['booking'], created_at=None, legacy_coverage=True)
    assert parse('Booking', legacy).created_at is None
    with pytest.raises(ValidationError):
        parse('Booking', dict(legacy, legacy_coverage=False))
    assert parse('Booking', dict(legacy, owner_at_booking_id=None)).owner_at_booking_id is None
