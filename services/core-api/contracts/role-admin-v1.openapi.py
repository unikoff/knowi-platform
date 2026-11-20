"""Generate the proposed RA1 OpenAPI document; it is not the live app schema."""
import json
import re
from pathlib import Path

P = '/api/admin/courses/{course_id}'
B = '/api/courses/{course_id}/booking'
A = '/api/admin-platform'
# method, path, operationId, query names, input DTO, output DTO, authority
operations = [
    ('get', '/api/user/me', 'getMe', '', None, 'Me', 'authenticated'),
    ('get', '/api/user/booking-mode', 'bookingMode', '', None, 'BookingMode', 'authenticated'),
    ('get', P+'/overview', 'courseOverview', 'year month', None, 'CourseOverview', 'course staff'),
    ('get', P+'/study-time', 'courseStudyTime', '', None, 'CourseStudyTime', 'course staff, matching actor lifecycle and authority revision'),
    ('get', P+'/roster', 'courseRoster', 'cursor limit search year? month?', None, 'Roster', 'course staff'),
    ('get', P+'/roster/{user_id}/progress', 'rosterProgress', '', None, 'Progress', 'course staff, roster member'),
    ('get', P+'/users/{user_id}/statistics', 'studentStatistics', '', None, 'StudentStatistics', 'course staff, matching actor and target lifecycles'),
    ('get', P+'/users/{user_id}/profile-details', 'studentProfileDetails', 'year month cursor profile_limit', None, 'StudentProfileDetails', 'course staff, matching actor and target lifecycles'),
    ('get', P+'/users/{user_id}/quota', 'courseUserQuota', 'year month', None, 'QuotaEnvelope', 'course staff, matching target lifecycle'),
    ('get', P+'/grant-search', 'grantSearch', 'q cursor search_limit', None, 'SearchResult', 'course staff'),
    ('post', P+'/users/{user_id}/quota-grants', 'grantQuota', '', 'QuotaGrant', 'QuotaEnvelope', 'course staff'),
    ('post', P+'/users/{user_id}/subscription-extensions', 'courseExtendSubscription', '', 'SubscriptionExtend', 'Subscription', 'course staff'),
    ('post', P+'/users/{user_id}/subscription-and-quota', 'courseExtendAndGrant', '', 'SubscriptionQuotaGrant', 'SubscriptionQuotaResult', 'course staff, matching actor and target lifecycles'),
    ('get', P+'/slots', 'courseSlots', 'owner_id year month', None, 'Slots', 'course staff'),
    ('get', P+'/calendar-context', 'courseCalendarContext', '', None, 'CalendarContext', 'course staff'),
    ('post', P+'/slots', 'createSlot', '', 'SlotCreate', 'SlotEnvelope', 'course staff'),
    ('post', P+'/slots/{slot_id}/archive', 'archiveSlot', '', 'ReasonCommand', 'SlotEnvelope', 'course staff'),
    ('get', B+'/context', 'studentBookingContext', '', None, 'BookingContext', 'authenticated'),
    ('get', B+'/slots', 'studentSlots', 'owner_id year month', None, 'StudentSlots', 'authenticated'),
    ('post', B+'/slots/{slot_id}/book', 'bookSlot', '', None, 'BookingEnvelope', 'authenticated'),
    ('post', B+'/bookings/{booking_id}/cancel', 'cancelBooking', '', None, 'BookingEnvelope', 'booking student'),
    ('get', A+'/overview', 'platformOverview', 'course_id', None, 'PlatformOverview', 'admin'),
    ('get', A+'/courses', 'platformCourses', 'cursor limit', None, 'Courses', 'admin'),
    ('get', A+'/users', 'platformUsers', 'cursor limit search', None, 'PlatformUsers', 'admin'),
    ('get', A+'/users/{user_id}', 'platformUser', '', None, 'PlatformUser', 'admin'),
    ('post', A+'/users/{user_id}/subscription-extensions', 'platformExtendSubscription', '', 'SubscriptionExtend', 'Subscription', 'admin'),
    ('post', A+'/users/{user_id}/subscription-reset', 'resetSubscription', '', 'SubscriptionReset', 'Subscription', 'admin'),
    ('get', A+'/operations', 'platformOperations', 'cursor limit course_id actor_id', None, 'Operations', 'admin'),
]


def schema_ref(name):
    return {'$ref': './role-admin-v1.schema.json#/$defs/' + name}


def parameter(name, location, required=False, schema=None):
    return {'name': name, 'in': location, 'required': required, 'schema': schema or {'type': 'string'}}


def query_parameter(name):
    optional = name.endswith('?')
    name = name.removesuffix('?')
    schemas = {
        'year': {'type': 'integer', 'minimum': 2000, 'maximum': 2100},
        'month': {'type': 'integer', 'minimum': 1, 'maximum': 12},
        'owner_id': {'type': 'integer', 'minimum': 1},
        'actor_id': {'type': 'integer', 'minimum': 1},
        'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 50},
        'profile_limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20},
        'search_limit': {'type': 'integer', 'minimum': 1, 'maximum': 10, 'default': 10},
        'q': {'type': 'string', 'minLength': 3, 'maxLength': 100},
        'search': {'type': 'string', 'maxLength': 100},
    }
    return parameter('limit' if name in {'profile_limit', 'search_limit'} else name, 'query',
                     not optional and name in {'year', 'month', 'owner_id', 'q'}, schemas.get(name))


def generate():
    document = {'openapi': '3.1.0', 'info': {'title': 'Role admin v1 proposed contract', 'version': '1.0.0',
                'description': 'Design contract only. See role-admin-v1.md for authorization and transactions.'},
                'security': [{'SessionCookie': []}], 'paths': {},
                'components': {'securitySchemes': {'SessionCookie': {'type': 'apiKey', 'in': 'cookie', 'name': 'session'}}}}
    for method, path, operation_id, query, request, response, authority in operations:
        params = []
        for name in re.findall(r'\{([^}]+)\}', path):
            params.append(parameter(name, 'path', True, {'type': 'string', 'minLength': 1, 'maxLength': 100} if name == 'course_id' else {'type': 'integer', 'minimum': 1}))
        params.extend(query_parameter(q) for q in query.split())
        if method != 'get':
            params.append(parameter('Idempotency-Key', 'header', True, {'type': 'string', 'format': 'uuid'}))
        if operation_id in {'studentStatistics', 'studentProfileDetails', 'courseUserQuota', 'grantQuota', 'courseExtendSubscription', 'courseExtendAndGrant', 'platformExtendSubscription', 'resetSubscription'}:
            params.append(parameter('X-Account-Ref', 'header', True, {'type': 'string', 'format': 'uuid'}))
        if operation_id in {'studentStatistics', 'studentProfileDetails', 'courseStudyTime', 'courseExtendAndGrant'}:
            params.append(parameter('X-Actor-Ref', 'header', True, {'type': 'string', 'format': 'uuid'}))
        if operation_id == 'courseStudyTime':
            params.append(parameter('X-Auth-Revision', 'header', True, {'type': 'integer', 'minimum': 0}))
        op = {'operationId': operation_id, 'description': 'Authority: ' + authority + '. See role-admin-v1.md.',
              'parameters': params, 'responses': {'200': {'description': 'Success', 'content': {'application/json': {'schema': schema_ref(response)}}}}}
        if request:
            op['requestBody'] = {'required': True, 'content': {'application/json': {'schema': schema_ref(request)}}}
        # /user/me has the legacy error envelope; new endpoints use Error.
        if path != '/api/user/me':
            for status in (400, 401, 403, 404, 409, 422, 429, 503):
                op['responses'][str(status)] = {'description': 'See error semantics in role-admin-v1.md', 'content': {'application/json': {'schema': schema_ref('Error')}}}
        else:
            op['responses']['401'] = {'description': 'Existing legacy detail envelope'}
        document['paths'].setdefault(path, {})[method] = op
    return document


if __name__ == '__main__':
    Path(__file__).with_name('role-admin-v1.openapi.json').write_text(
        json.dumps(generate(), indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
