from typing import Literal
from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import Query
from fastapi import Request, Response
from sqlalchemy.orm import Session
from pymentor_core.app.context import RequestContext
from pymentor_core.app.dependencies import get_db
from pymentor_core.app.dependencies import get_request_context
from pymentor_core.shared.api_contracts import api_contract_responses
from pymentor_core.modules.bookings.api.schemas import SlotCreate
from pymentor_core.modules.administration.api.schemas import AdminAccountsResponse
from pymentor_core.modules.administration.api.schemas import AdminOverviewResponse
from pymentor_core.modules.administration.api.schemas import AdminSlotEnvelope
from pymentor_core.modules.administration.api.schemas import AdminSlotsResponse
from pymentor_core.modules.administration.api.schemas import AdminUserEnvelope
from pymentor_core.modules.administration.api.schemas import DeletedIdResponse
from pymentor_core.modules.administration.api.schemas import QuotaChange
from pymentor_core.modules.administration.api.schemas import StudentProfileEnvelope
from pymentor_core.modules.administration.api.schemas import SubscriptionChange
from pymentor_core.modules.administration.api import service
from pymentor_core.modules.landings.api.admin import router as landing_admin_router

admin_router = APIRouter()
admin_router.include_router(
    landing_admin_router, prefix="/landings", tags=["landing-admin"]
)


async def _admin_sql(request, operation, *, with_principal=False):
    from pymentor_core.app.async_request import request_principal, run_request_sql
    from pymentor_core.modules.identity.api.sessions import _local_async_identity
    identity = _local_async_identity(request)
    actor = await request_principal(request) if identity else None
    result = await run_request_sql(request, lambda db: operation(db, actor), identity=identity)
    return (result, actor) if with_principal else result


async def _admin_content_sql(request, operation, *, with_principal=False):
    from pymentor_core.app.async_request import request_principal, run_request_sql
    from pymentor_core.modules.identity.api.sessions import _local_async_identity
    from pymentor_core.modules.identity.principal import require_admin
    from pymentor_core.modules.learning.api.service import prepare_request_content
    from starlette.concurrency import run_in_threadpool
    identity = _local_async_identity(request)
    if identity is None:
        result = await run_request_sql(request, lambda db: operation(db, None, None))
        return (result, None) if with_principal else result
    actor = require_admin(await request_principal(request))
    content = await run_in_threadpool(prepare_request_content, "tracks")
    result = await run_request_sql(request, lambda db: operation(db, actor, content), identity=identity)
    return (result, actor) if with_principal else result


@admin_router.get(
    "/overview", responses=api_contract_responses(AdminOverviewResponse, 401, 403)
)
async def admin_overview(request: Request, response: Response):
    "Краткая сводка по активности учеников, подпискам и загрузке расписания."
    operation = lambda db, actor: service.admin_overview(request=request, db=db, actor=actor)
    if getattr(request.app.state, "analytics_legacy_read_mode", "off") == "off":
        return await _admin_sql(request, operation)
    baseline, principal = await _admin_sql(request, operation, with_principal=True)
    from pymentor_core.modules.analytics_delivery.legacy_bridge import apply
    return await apply(request, response, principal, "admin", baseline)


@admin_router.get(
    "/users", responses=api_contract_responses(AdminAccountsResponse, 401, 403)
)
async def list_users(request: Request, search: str = Query(default="", max_length=100),
    subscription: Literal["all", "active", "inactive"] = Query(default="all"),
    lessons: Literal["all", "none", "low", "many"] = Query(default="all"),
    year: int | None = Query(default=None, ge=2000, le=2100),
    month: int | None = Query(default=None, ge=1, le=12)):
    return await _admin_sql(request, lambda db, actor: service.list_users(request=request, search=search,
        subscription=subscription, lessons=lessons, year=year, month=month, db=db, actor=actor))


@admin_router.delete(
    "/users/{user_id}",
    responses=api_contract_responses(DeletedIdResponse, 400, 401, 403, 404, 409),
)
async def delete_user(user_id: int, request: Request,
    expected_account_ref: str | None = Header(default=None, alias="X-Account-Ref")):
    result = await _admin_sql(request, lambda db, actor: service.delete_user(user_id=user_id,
        request=request, db=db, expected_account_ref=expected_account_ref, actor=actor))
    state = result.get("_analytics_deletion_state") if isinstance(result, dict) else None
    if state == "pending":
        from fastapi.responses import JSONResponse
        return JSONResponse({"status": "pending"}, status_code=202, headers={"Retry-After": "2", "Cache-Control": "no-store"})
    if state == "complete":
        from fastapi import Response
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    return result


@admin_router.patch(
    "/users/{user_id}/subscription",
    responses=api_contract_responses(AdminUserEnvelope, 401, 403, 404),
)
async def change_subscription(user_id: int, payload: SubscriptionChange, request: Request):
    return await _admin_sql(request, lambda db, actor: service.change_subscription(user_id=user_id,
        payload=payload, request=request, db=db, uow=request.app.state.composition_root.for_request(db).uow, actor=actor))


@admin_router.get(
    "/slots", responses=api_contract_responses(AdminSlotsResponse, 401, 403)
)
async def admin_list_slots(request: Request, year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12), include_past: bool = Query(default=False)):
    return await _admin_sql(request, lambda db, actor: service.admin_list_slots(request=request,
        year=year, month=month, include_past=include_past, db=db, actor=actor))


@admin_router.post(
    "/slots",
    responses=api_contract_responses(AdminSlotEnvelope, 400, 401, 403, 409),
)
async def admin_create_slot(payload: SlotCreate, request: Request):
    return await _admin_sql(request, lambda db, actor: service.admin_create_slot(payload=payload, request=request, db=db, actor=actor))


@admin_router.delete(
    "/slots/{slot_id}",
    responses=api_contract_responses(DeletedIdResponse, 401, 403, 404),
)
async def admin_delete_slot(slot_id: int, request: Request):
    return await _admin_sql(request, lambda db, actor: service.admin_delete_slot(slot_id=slot_id, request=request, db=db, actor=actor))


@admin_router.get(
    "/users/{user_id}/profile",
    responses=api_contract_responses(StudentProfileEnvelope, 401, 403, 404),
)
async def student_profile(user_id: int, request: Request, response: Response, year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12)):
    if getattr(request.app.state, "analytics_legacy_read_mode", "off") != "analytics":
        return await _admin_content_sql(request, lambda db, actor, content: service.student_profile(user_id=user_id,
            request=request, year=year, month=month, db=db, actor=actor, content=content))
    baseline, principal = await _admin_content_sql(request, lambda db, actor, content: service.student_profile(user_id=user_id,
        request=request, year=year, month=month, db=db, actor=actor, content=content, analytics_activity={}), with_principal=True)
    from pymentor_core.modules.analytics_delivery.legacy_bridge import read_profile
    data, _ = await read_profile(request, response, principal, user_id, year, month,
                                 expected_ref=baseline["profile"]["account_ref"])
    return {**baseline, "profile": {**baseline["profile"], **data}}


@admin_router.post(
    "/users/{user_id}/quota",
    responses=api_contract_responses(StudentProfileEnvelope, 401, 403, 404),
)
async def change_quota(user_id: int, payload: QuotaChange, request: Request, response: Response):
    if getattr(request.app.state, "analytics_legacy_read_mode", "off") != "analytics":
        return await _admin_content_sql(request, lambda db, actor, content: service.change_quota(user_id=user_id,
            payload=payload, request=request, db=db, actor=actor, content=content))
    from pymentor_core.app.async_request import request_principal
    from pymentor_core.modules.identity.principal import require_admin
    from pymentor_core.modules.analytics_delivery.legacy_bridge import read_profile
    principal = require_admin(await request_principal(request))
    data, reference = await read_profile(request, response, principal, user_id, payload.year, payload.month)
    return await _admin_content_sql(request, lambda db, actor, content: service.change_quota(user_id=user_id,
        payload=payload, request=request, db=db, actor=actor, content=content,
        analytics_activity=data, expected_account_ref=reference))
