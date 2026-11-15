import logging

from fastapi import APIRouter
from fastapi import BackgroundTasks
from fastapi import Depends
from fastapi import Header
from fastapi import Query
from fastapi import Request
from fastapi import Response
from sqlalchemy.orm import Session
from pymentor_core.app.dependencies import get_db
from pymentor_core.app.dependencies import get_request_context
from pymentor_core.app.context import RequestContext
from pymentor_core.shared.api_contracts import api_contract_responses
from pymentor_core.modules.identity.api import accounts as service
from pymentor_core.modules.identity import activity
from pymentor_core.modules.identity.api.schemas import ActivityCalendarResponse
from pymentor_core.modules.identity.api.schemas import ActivitySummaryResponse
from pymentor_core.modules.identity.api.schemas import ActivityVisit
from pymentor_core.modules.identity.api.schemas import ActivityVisitResponse
from pymentor_core.modules.identity.api.schemas import AuthResponse
from pymentor_core.modules.identity.api.schemas import CurrentUserResponse
from pymentor_core.modules.identity.api.schemas import LogoutResponse
from pymentor_core.modules.identity.api.schemas import UserCreate
from pymentor_core.modules.identity.api.schemas import UserLogin
from pymentor_core.shared.bindings import get_bindings
from starlette.concurrency import run_in_threadpool
from pymentor_core.modules.identity.api.sessions import async_login_user, async_current_principal, async_logout_user
from pymentor_core.modules.identity.api.sessions import _local_async_identity
from pymentor_core.app.wiring import identity_repository
from fastapi import HTTPException

user_router = APIRouter()
logger = logging.getLogger(__name__)


def _send_registration_verification(user_id: int, email: str, password_hash: str):
    try:
        get_bindings().email_auth.send_registration_verification(user_id, email, password_hash)
    except Exception as exc:
        logger.warning("Registration verification email failed: %s", type(exc).__name__)


@user_router.post("/register", responses=api_contract_responses(AuthResponse, 400))
async def register_user(user: UserCreate, background_tasks: BackgroundTasks, request: Request):
    identity = _local_async_identity(request)
    if identity is None:
        def legacy(db):
            created = service.register_user(user=user, db=db)
            return service._serialize_user(created), created.id, created.email, created.password
        public, user_id, email, password_hash = await _run_identity_sql(request, None, legacy)
    else:
        from pymentor_core.modules.identity.passwords import hash_password, is_bcrypt_hash
        password_hash = (user.password if is_bcrypt_hash(user.password)
                         else await run_in_threadpool(hash_password, user.password))
        prepared = user.model_copy(update={"password": password_hash})
        async with identity.database.unit_of_work() as uow:
            def register(db):
                created = service.register_user(user=prepared, db=db)
                return service._serialize_user(created), created.id, created.email, created.password
            public, user_id, email, password_hash = await uow.session.run_sync(register)
    background_tasks.add_task(_send_registration_verification, user_id, email, password_hash)
    return {"message": "Registration successful", "user": public}


@user_router.get("/login-capabilities")
def login_capabilities(request: Request):
    # The experimental async service exists only in the isolated local mode.
    from pymentor_core.modules.identity.api.sessions import _local_async_identity

    identity = _local_async_identity(request)
    return {
        "idempotent_login": bool(
            identity and getattr(identity, "supports_login_retry", False)
        )
    }


@user_router.post(
    "/login", responses=api_contract_responses(AuthResponse, 400, 401, 409, 503)
)
async def login_user(
    user: UserLogin,
    response: Response,
    request: Request,
    operation_id: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if _local_async_identity(request) is not None:
        user_db = await async_login_user(response, user, request)
    else:
        def legacy(db):
            return service._serialize_user(service.login_user(response=response, user=user, db=db, request=request))
        return {"message": "Login successful", "user": await _run_identity_sql(request, None, legacy)}
    return {"message": "Login successful", "user": service._serialize_user(user_db)}


@user_router.get("/me", responses=api_contract_responses(CurrentUserResponse, 401))
async def me(request: Request):
    identity = _local_async_identity(request)
    if identity is None:
        def legacy(db):
            context = request.app.state.composition_root.for_request(db)
            return service._serialize_user(service.get_current_user(request=request, db=db, uow=context.uow))
        return {"user": await _run_identity_sql(request, None, legacy)}
    principal = await async_current_principal(request)
    async with identity.database.unit_of_work() as uow:
        def read_user(db):
            user_db = identity_repository(db).user_by_id(principal.user_id)
            if user_db is None:
                raise HTTPException(status_code=401, detail="User not found")
            return service._serialize_user(user_db)
        return {"user": await uow.session.run_sync(read_user)}


@user_router.post(
    "/activity/visit",
    responses=api_contract_responses(ActivityVisitResponse, 401),
)
async def record_activity_visit(payload: ActivityVisit, request: Request):
    identity = _local_async_identity(request)
    if identity is None:
        def legacy(db, context):
            user = service.get_current_user(request=request, db=db)
            return user.id
        user_id, operation = None, legacy
    else:
        user_id = (await async_current_principal(request)).user_id
        operation = None
    def record(db):
        root = request.app.state.composition_root
        context = root.for_request(db)
        uid = operation(db, context) if operation else user_id
        visited_at = context.clock.now()
        activity_date = payload.activity_date or visited_at.astimezone(activity.MOSCOW_TZ).date()
        day = activity.record_activity_day(user_id=uid, activity_date=activity_date, db=db,
            visited_at=visited_at, uow=context.uow, clock=context.clock)
        return {"activity_date": day.activity_date.isoformat(),
                "visited_at": activity.serialize_activity_timestamp(day.visited_at)}
    return await _run_identity_sql(request, identity, record)


async def _run_identity_sql(request, identity, operation):
    from pymentor_core.app.async_request import run_request_sql
    return await run_request_sql(request, operation, identity=identity)


@user_router.get("/activity", responses=api_contract_responses(ActivityCalendarResponse, 401))
async def get_activity_calendar(request: Request, year: int = Query(..., ge=2000, le=2100),
                                month: int = Query(..., ge=1, le=12)):
    identity = _local_async_identity(request)
    user_id = (await async_current_principal(request)).user_id if identity else None
    def read(db):
        uid = user_id if identity else service.get_current_user(request=request, db=db).id
        records = activity.get_activity_records(user_id=uid, year=year, month=month, db=db)
        return {"days": [r.activity_date.isoformat() for r in records],
                "visits": [{"activity_date": r.activity_date.isoformat(),
                            "visited_at": activity.serialize_activity_timestamp(r.visited_at)} for r in records]}
    return await _run_identity_sql(request, identity, read)


@user_router.get("/activity/summary", responses=api_contract_responses(ActivitySummaryResponse, 401))
async def get_activity_summary(request: Request):
    identity = _local_async_identity(request)
    user_id = (await async_current_principal(request)).user_id if identity else None
    def read(db):
        uid = user_id if identity else service.get_current_user(request=request, db=db).id
        return activity.get_activity_summary(user_id=uid, db=db, clock=request.app.state.composition_root.for_request(db).clock)
    return await _run_identity_sql(request, identity, read)


@user_router.post("/logout", responses=api_contract_responses(LogoutResponse, 401))
async def logout_user(request: Request, response: Response):
    if _local_async_identity(request) is not None:
        await async_logout_user(request, response)
    else:
        await _run_identity_sql(request, None, lambda db:
            service.logout_user(request=request, response=response, db=db))
    return {"message": "Logout successful"}


from pymentor_core.modules.identity.api.email_actions import router as email_actions_router

user_router.include_router(email_actions_router)
