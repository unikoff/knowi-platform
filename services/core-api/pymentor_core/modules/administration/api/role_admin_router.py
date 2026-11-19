"""Role-admin read endpoints; mutations are implemented in later stages."""
import hashlib
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from pymentor_core.adapters.course_local.packages import CourseNotFoundError, CoursePackageError
from pymentor_core.app.wiring import role_admin_reads, role_admin_commands, scoped_booking
from pymentor_core.app.async_request import request_principal, run_request_sql, statistics_selection_revision
from pymentor_core.modules.administration.api import role_admin_schemas as dto
from pymentor_core.modules.identity.api.sessions import _local_async_identity
from pymentor_core.modules.identity.api.sessions import session_cookie_scheme
from pymentor_core.modules.learning.api.service import prepare_request_content
from pymentor_core.modules.learning.course_context import course_scope

_CODES = {400: 'invalid_cursor', 401: 'unauthenticated', 403: 'forbidden', 404: 'not_found',
          409: 'conflict', 422: 'invalid_request', 429: 'rate_limited', 503: 'unavailable'}
CourseId = Annotated[str, Path(min_length=1, max_length=100)]
UserId = Annotated[int, Path(gt=0)]


class ContractRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def wrapped(request):
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(status_code=422, content={'detail': 'Invalid request', 'code': 'invalid_request'})
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={'detail': str(exc.detail),
                    'code': _CODES.get(exc.status_code, 'unavailable')}, headers=exc.headers)
        return wrapped


router = APIRouter(route_class=ContractRoute, dependencies=[Security(session_cookie_scheme)])
reads = role_admin_reads()
commands = role_admin_commands()
booking = scoped_booking()
_ERRORS = {code: {'model': dto.Error, 'description': name} for code, name in _CODES.items()}


async def _read(request, operation, *, limited=False, selection_course=None, with_principal=False):
    identity = _local_async_identity(request)
    principal = await request_principal(request)
    if limited:
        if not getattr(principal, 'account_ref', None) or identity.redis is None:
            raise HTTPException(status_code=503, detail='Rate limit unavailable')
        digest = hashlib.sha256(('role-admin-search\0' + principal.account_ref).encode()).hexdigest()
        try:
            allowed = await identity.redis.allow_email_request(digest, 20)
        except Exception:
            raise HTTPException(status_code=503, detail='Rate limit unavailable') from None
        if not allowed:
            raise HTTPException(status_code=429, detail='Rate limit exceeded', headers={'Retry-After': '60'})
    result = await run_request_sql(request, lambda db: operation(db, principal), identity=identity)
    if selection_course is not None:
        result, revision = result
        try:
            for person in result['users']:
                await identity.redis.store_role_selection(principal.account_ref, selection_course, person['account_ref'], revision)
        except Exception:
            raise HTTPException(status_code=503, detail='Search selection unavailable') from None
    return (result, principal) if with_principal else result


async def _write(request, operation, payload, key, *, course=None, target_id=None, target_ref=None, actor_ref=None):
    identity = _local_async_identity(request)
    principal = await request_principal(request)
    if actor_ref is not None and principal.account_ref != actor_ref:
        raise HTTPException(status_code=401, detail='Account lifecycle changed')
    selection_revision = None
    if operation in commands.COURSE_USER_OPERATIONS and target_ref is not None:
        try:
            selection_revision = await identity.redis.get_role_selection(principal.account_ref, course, target_ref)
        except Exception:
            raise HTTPException(status_code=503, detail='Search selection unavailable') from None
    command = commands.Command(operation=operation, path=request.url.path, key=str(key), payload=payload,
                               course=course, target_id=target_id, target_ref=target_ref,
                               selection_revision=selection_revision)
    return await run_request_sql(request, lambda db: commands.guarded_execute(db, principal, command), identity=identity)


async def _booking_write(request, operation, payload, key, course, resource_id=None):
    identity = _local_async_identity(request)
    principal = await request_principal(request)
    command = commands.Command(operation=operation, path=request.url.path, key=str(key), payload=payload, course=course)
    return await run_request_sql(request, lambda db: booking.execute(db, principal, command, resource_id=resource_id), identity=identity)


@router.get('/admin/courses/{course_id}/overview', response_model=dto.CourseOverview, responses=_ERRORS)
async def course_overview(request: Request, response: Response, course_id: CourseId, year: int = Query(ge=2000, le=2100), month: int = Query(ge=1, le=12)):
    from pymentor_core.modules.analytics_delivery.legacy_bridge import admin_completions_enabled, apply_course_completions
    from pymentor_core.modules.analytics_delivery.course_visitors import read_course_visits
    enabled = admin_completions_enabled(request.app.state)
    baseline, principal = await _read(request, lambda db, actor: reads.course_overview(
        db, actor, course_id, year, month, include_completions=not enabled), with_principal=True)
    if enabled:
        baseline = await apply_course_completions(request, response, principal, baseline, course_id, year=year, month=month)
    visits = await read_course_visits(request, principal, course_id)
    return {**baseline, 'course_trends': {**baseline['course_trends'], 'visits': visits}}


@router.get('/admin/courses/{course_id}/study-time', operation_id='courseStudyTime',
            response_model=dto.CourseStudyTime, responses=_ERRORS)
async def course_study_time(
    request: Request,
    course_id: CourseId,
    actor_ref: UUID = Header(alias='X-Actor-Ref'),
    auth_revision: int = Header(alias='X-Auth-Revision', ge=0),
):
    from pymentor_core.modules.analytics_delivery.course_visitors import read_course_study_time

    principal = await request_principal(request)
    if principal.account_ref != str(actor_ref):
        raise HTTPException(status_code=401, detail='Account lifecycle changed')
    return await read_course_study_time(request, principal, course_id, expected_revision=auth_revision)


@router.get('/admin/courses/{course_id}/roster', response_model=dto.Roster, response_model_exclude_unset=True, responses=_ERRORS)
async def roster(request: Request, course_id: CourseId, search: str = Query('', max_length=100),
                 cursor: str | None = None, limit: int = Query(50, ge=1, le=100),
                 year: int | None = Query(None, ge=2000, le=2100), month: int | None = Query(None, ge=1, le=12)):
    if (year is None) != (month is None):
        raise HTTPException(status_code=422, detail='Provide year and month together')
    period = (year, month) if year is not None and month is not None else None
    return await _read(request, lambda db, actor: reads.roster(db, actor, course_id, search, cursor, limit, period=period))


@router.get('/admin/courses/{course_id}/roster/{user_id}/progress', response_model=dto.Progress, responses=_ERRORS)
async def progress(request: Request, course_id: CourseId, user_id: UserId):
    return await _read(request, lambda db, actor: reads.progress(db, actor, course_id, user_id))


@router.get('/admin/courses/{course_id}/users/{user_id}/statistics',
            operation_id='studentStatistics', response_model=dto.StudentStatistics, responses=_ERRORS)
async def student_statistics(
    request: Request,
    course_id: CourseId,
    user_id: UserId,
    actor_ref: UUID = Header(alias='X-Actor-Ref'),
    reference: UUID = Header(alias='X-Account-Ref'),
):
    principal = await request_principal(request)
    if principal.account_ref != str(actor_ref):
        raise HTTPException(status_code=401, detail='Account lifecycle changed')
    # Resolve the permitted course before loading its catalog off the event loop.
    await run_request_sql(request, lambda db: reads.authorize(db, principal, course_id))
    try:
        with course_scope(course_id):
            content = await run_in_threadpool(prepare_request_content, 'catalog')
    except (CourseNotFoundError, CoursePackageError, OSError) as exc:
        raise HTTPException(status_code=503, detail='Course catalog unavailable') from exc
    selection_revision = await statistics_selection_revision(request, str(actor_ref), course_id, str(reference))
    # Recheck and lock authority and target after catalog I/O.
    return await run_request_sql(request, lambda db: reads.student_statistics(
        db, principal, course_id, user_id, str(reference), content, selection_revision,
    ))


@router.get('/admin/courses/{course_id}/users/{user_id}/profile-details', operation_id='studentProfileDetails',
            response_model=dto.StudentProfileDetails, responses=_ERRORS)
async def student_profile_details(
    request: Request,
    course_id: CourseId,
    user_id: UserId,
    year: int = Query(ge=2000, le=2100),
    month: int = Query(ge=1, le=12),
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    actor_ref: UUID = Header(alias='X-Actor-Ref'),
    reference: UUID = Header(alias='X-Account-Ref'),
):
    principal = await request_principal(request)
    if principal.account_ref != str(actor_ref):
        raise HTTPException(status_code=401, detail='Account lifecycle changed')
    selection_revision = await statistics_selection_revision(request, str(actor_ref), course_id, str(reference))
    return await run_request_sql(request, lambda db: reads.student_profile_details(
        db, principal, course_id, user_id, str(reference), year, month, cursor, limit, selection_revision,
    ))


@router.get('/admin/courses/{course_id}/users/{user_id}/quota', response_model=dto.QuotaEnvelope, responses=_ERRORS)
async def quota(request: Request, course_id: CourseId, user_id: UserId, year: int = Query(ge=2000, le=2100), month: int = Query(ge=1, le=12), reference: UUID = Header(alias='X-Account-Ref')):
    return await _read(request, lambda db, actor: reads.quota(db, actor, course_id, user_id, str(reference), year, month), limited=True)


@router.get('/admin/courses/{course_id}/grant-search', response_model=dto.SearchResult, responses=_ERRORS)
async def grant_search(request: Request, course_id: CourseId, q: str = Query(min_length=3, max_length=100), cursor: str | None = None, limit: int = Query(10, ge=1, le=10)):
    return await _read(request, lambda db, actor: reads.grant_search(db, actor, course_id, q, cursor, limit), limited=True, selection_course=course_id)


@router.get('/admin/courses/{course_id}/calendar-context', response_model=dto.CalendarContext, responses=_ERRORS)
async def calendar_context(request: Request, course_id: CourseId):
    return await _read(request, lambda db, actor: reads.calendar_context(db, actor, course_id))


@router.get('/admin-platform/overview', response_model=dto.PlatformOverview, responses=_ERRORS)
async def platform_overview(request: Request, response: Response, course_id: str | None = Query(None, min_length=1, max_length=100)):
    mode = getattr(request.app.state, "analytics_legacy_read_mode", "off")
    from pymentor_core.modules.analytics_delivery.legacy_bridge import admin_completions_enabled
    operation = lambda db, actor: reads.platform_overview(db, actor, course_id, include_activity=False, include_completions=False) if mode == "analytics" and admin_completions_enabled(request.app.state) else (reads.platform_overview(db, actor, course_id, include_activity=False) if mode == "analytics" and course_id is None else reads.platform_overview(db, actor, course_id))
    if mode == "off":
        return await _read(request, operation)
    baseline, principal = await _read(request, operation, with_principal=True)
    if course_id is not None:
        if admin_completions_enabled(request.app.state):
            from pymentor_core.modules.analytics_delivery.legacy_bridge import apply_course_completions
            return await apply_course_completions(request, response, principal, baseline, course_id)
        response.headers["X-Analytics-Source"] = "core"
        response.headers["X-Analytics-Coverage"] = "not-applicable"
        return baseline
    from pymentor_core.modules.analytics_delivery.legacy_bridge import apply
    return await apply(request, response, principal, "admin", baseline)


@router.get('/admin-platform/courses', response_model=dto.Courses, responses=_ERRORS)
async def platform_courses(request: Request, cursor: str | None = None, limit: int = Query(50, ge=1, le=100)):
    return await _read(request, lambda db, actor: reads.platform_courses(db, actor, cursor, limit))


@router.get('/admin-platform/users', response_model=dto.PlatformUsers, responses=_ERRORS)
async def platform_users(request: Request, search: str = Query('', max_length=100), cursor: str | None = None, limit: int = Query(50, ge=1, le=100)):
    return await _read(request, lambda db, actor: reads.platform_users(db, actor, search, cursor, limit))


@router.get('/admin-platform/users/{user_id}', response_model=dto.PlatformUser, responses=_ERRORS)
async def platform_user(request: Request, user_id: UserId):
    return await _read(request, lambda db, actor: reads.platform_user(db, actor, user_id))


@router.get('/admin-platform/operations', response_model=dto.Operations, responses=_ERRORS)
async def operations(request: Request, cursor: str | None = None, limit: int = Query(50, ge=1, le=100), course_id: str | None = Query(None, min_length=1, max_length=100), actor_id: int | None = Query(None, gt=0)):
    return await _read(request, lambda db, actor: reads.operations(db, actor, cursor, limit, course_id, actor_id))


@router.post('/admin/courses/{course_id}/users/{user_id}/quota-grants', response_model=dto.QuotaEnvelope, responses=_ERRORS)
async def grant_quota(request: Request, course_id: CourseId, user_id: UserId, payload: dto.QuotaGrant,
                      key: UUID = Header(alias='Idempotency-Key'), reference: UUID = Header(alias='X-Account-Ref')):
    return await _write(request, 'quota.grant', payload.model_dump(mode='json'), key, course=course_id, target_id=user_id, target_ref=str(reference))


@router.post('/admin/courses/{course_id}/users/{user_id}/subscription-extensions', response_model=dto.Subscription, responses=_ERRORS)
async def course_extend(request: Request, course_id: CourseId, user_id: UserId, payload: dto.SubscriptionExtend,
                        key: UUID = Header(alias='Idempotency-Key'), reference: UUID = Header(alias='X-Account-Ref')):
    return await _write(request, 'subscription.extend.course', payload.model_dump(mode='json'), key, course=course_id, target_id=user_id, target_ref=str(reference))


@router.post('/admin/courses/{course_id}/users/{user_id}/subscription-and-quota',
             response_model=dto.SubscriptionQuotaResult, responses=_ERRORS)
async def course_extend_and_grant(
    request: Request, course_id: CourseId, user_id: UserId, payload: dto.SubscriptionQuotaGrant,
    key: UUID = Header(alias='Idempotency-Key'), reference: UUID = Header(alias='X-Account-Ref'),
    actor_ref: UUID = Header(alias='X-Actor-Ref'),
):
    return await _write(request, commands.COMBINED_GRANT, payload.model_dump(mode='json'), key, course=course_id,
                        target_id=user_id, target_ref=str(reference), actor_ref=str(actor_ref))


@router.post('/admin-platform/users/{user_id}/subscription-extensions', response_model=dto.Subscription, responses=_ERRORS)
async def platform_extend(request: Request, user_id: UserId, payload: dto.SubscriptionExtend,
                          key: UUID = Header(alias='Idempotency-Key'), reference: UUID = Header(alias='X-Account-Ref')):
    return await _write(request, 'subscription.extend.platform', payload.model_dump(mode='json'), key, target_id=user_id, target_ref=str(reference))


@router.post('/admin-platform/users/{user_id}/subscription-reset', response_model=dto.Subscription, responses=_ERRORS)
async def reset_subscription(request: Request, user_id: UserId, payload: dto.SubscriptionReset,
                             key: UUID = Header(alias='Idempotency-Key'), reference: UUID = Header(alias='X-Account-Ref')):
    return await _write(request, 'subscription.reset', payload.model_dump(mode='json'), key, target_id=user_id, target_ref=str(reference))


@router.get('/admin/courses/{course_id}/slots', response_model=dto.Slots, responses=_ERRORS)
async def staff_slots(request: Request, course_id: CourseId, owner_id: int = Query(gt=0),
                      year: int = Query(ge=2000, le=2100), month: int = Query(ge=1, le=12)):
    return await _read(request, lambda db, actor: booking.staff_slots(db, actor, course_id, owner_id, year, month))


@router.post('/admin/courses/{course_id}/slots', response_model=dto.SlotEnvelope, responses=_ERRORS)
async def create_slot(request: Request, course_id: CourseId, payload: dto.CourseSlotCreate, key: UUID = Header(alias='Idempotency-Key')):
    return await _booking_write(request, 'slot.create', payload.model_dump(mode='json'), key, course_id)


@router.post('/admin/courses/{course_id}/slots/{slot_id}/archive', response_model=dto.SlotEnvelope, responses=_ERRORS)
async def archive_slot(request: Request, course_id: CourseId, slot_id: UserId, payload: dto.ReasonCommand, key: UUID = Header(alias='Idempotency-Key')):
    return await _booking_write(request, 'slot.archive', payload.model_dump(mode='json'), key, course_id, slot_id)


@router.get('/courses/{course_id}/booking/context', response_model=dto.BookingContext, responses=_ERRORS)
async def booking_context(request: Request, course_id: CourseId):
    return await _read(request, lambda db, actor: booking.student_context(db, actor, course_id))


@router.get('/courses/{course_id}/booking/slots', response_model=dto.StudentSlots, responses=_ERRORS)
async def student_slots(request: Request, course_id: CourseId, owner_id: int = Query(gt=0),
                        year: int = Query(ge=2000, le=2100), month: int = Query(ge=1, le=12)):
    return await _read(request, lambda db, actor: booking.student_slots(db, actor, course_id, owner_id, year, month))


@router.post('/courses/{course_id}/booking/slots/{slot_id}/book', response_model=dto.BookingEnvelope, responses=_ERRORS)
async def book_slot(request: Request, course_id: CourseId, slot_id: UserId, key: UUID = Header(alias='Idempotency-Key')):
    if await request.body():
        raise HTTPException(status_code=422, detail='Booking request must not have a body')
    return await _booking_write(request, 'booking.book', {}, key, course_id, slot_id)


@router.post('/courses/{course_id}/booking/bookings/{booking_id}/cancel', response_model=dto.BookingEnvelope, responses=_ERRORS)
async def cancel_booking(request: Request, course_id: CourseId, booking_id: UserId, key: UUID = Header(alias='Idempotency-Key')):
    if await request.body():
        raise HTTPException(status_code=422, detail='Cancellation request must not have a body')
    return await _booking_write(request, 'booking.cancel', {}, key, course_id, booking_id)
