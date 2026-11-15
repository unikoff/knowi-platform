"""Admin-only API for landing configuration and connection-test events."""
from uuid import UUID
from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from starlette.concurrency import run_in_threadpool
from pymentor_core.app.async_request import request_principal, run_request_sql
from pymentor_core.modules.identity.api.sessions import _local_async_identity
from pymentor_core.modules.identity.principal import require_admin as authorize_admin
from pymentor_core.modules.administration.public import require_admin
from pymentor_core.modules.notifications.contracts import TelegramDeliveryError
from pymentor_core.modules.notifications.service import verify_telegram_destination
from pymentor_core.modules.landings.views import _landing_view, _config_view, _event_view
from pymentor_core.modules.landings.contracts import LandingError
from pymentor_core.shared.bindings import get_bindings
from pymentor_core.modules.landings.api import service
from pymentor_core.modules.landings.api.schemas import LandingCreate, LandingPatch, LandingTestEvent, TelegramConfigInput

router = APIRouter()


def _request_call(name, *args, **kwargs):
    try:
        return getattr(get_bindings().landing_requests, name)(*args, **kwargs)
    except LandingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail, headers=exc.headers) from exc


async def _admin_sql(request, operation):
    identity = _local_async_identity(request)
    actor = authorize_admin(await request_principal(request)) if identity else None
    def call(db):
        if actor is None:
            require_admin(request, db)
        return operation(db)
    return await run_request_sql(request, call, identity=identity)


@router.get("")
async def list_landings(request: Request):
    return await _admin_sql(request, lambda db: [_landing_view(item) for item in _request_call("list_landings", db)])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_landing(payload: LandingCreate, request: Request):
    return await _admin_sql(request, lambda db: _landing_view(service.create_landing(
        db, slug=payload.slug, title=payload.title.strip(), description=payload.description.strip(), is_active=payload.is_active)))


@router.patch("/{landing_id}")
async def update_landing(landing_id: str, payload: LandingPatch, request: Request):
    return await _admin_sql(request, lambda db: _landing_view(_request_call(
        "update_landing", db, landing_id, payload.model_dump(exclude_unset=True))))


@router.get("/{landing_id}/telegram")
async def get_telegram_config(landing_id: str, request: Request):
    def get_config(db):
        landing = _request_call("get_landing", db, landing_id)
        return _config_view(landing, service.current_landing_telegram_config(db, landing))
    return await _admin_sql(request, get_config)


@router.put("/{landing_id}/telegram")
async def replace_telegram_config(landing_id: str, payload: TelegramConfigInput, request: Request):
    token = await _admin_sql(request, lambda db: _request_call("prepare_telegram", db, landing_id, payload.secret_ref))
    try:
        bot_username = await run_in_threadpool(verify_telegram_destination, token, payload.chat_id)
    except TelegramDeliveryError as exc:
        code = 503 if exc.retryable else 422
        detail = "Не удалось проверить Telegram сейчас" if exc.retryable else "Не удалось проверить бота или чат Telegram"
        raise HTTPException(status_code=code, detail=detail) from None
    def finish(db):
        landing, config = _request_call("finish_telegram", db, landing_id, payload.secret_ref, payload.chat_id, bot_username)
        return _config_view(landing, config)
    return await _admin_sql(request, finish)


@router.delete("/{landing_id}/telegram")
async def disable_telegram_config(landing_id: str, request: Request):
    def disable(db):
        landing = service.disable_landing_telegram(db, landing_id)
        return _config_view(landing, service.current_landing_telegram_config(db, landing))
    return await _admin_sql(request, disable)


@router.post("/{landing_id}/events", status_code=status.HTTP_202_ACCEPTED)
async def enqueue_landing_test_event(landing_id: str, payload: LandingTestEvent, request: Request,
    response: Response, idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=36, max_length=36)):
    # Authenticate before exposing key validation, as in the original handler.
    def enqueue(db):
        try:
            event_id = str(UUID(idempotency_key))
        except ValueError:
            raise HTTPException(status_code=422, detail="Idempotency-Key должен быть UUID") from None
        event, duplicate = service.enqueue_landing_event(db, landing_id=landing_id, event_id=event_id,
            event_type=payload.event_type, payload=payload.payload)
        return {**_event_view(event), "duplicate": duplicate}
    result = await _admin_sql(request, enqueue)
    if result["duplicate"]:
        response.status_code = status.HTTP_200_OK
    return result


@router.get("/{landing_id}/events/{event_id}")
async def get_landing_event(landing_id: str, event_id: str, request: Request):
    return await _admin_sql(request, lambda db: _event_view(service.landing_event_status(db, landing_id, event_id)))


@router.post("/{landing_id}/events/{event_id}/retry")
async def retry_landing_event(landing_id: str, event_id: str, request: Request):
    return await _admin_sql(request, lambda db: _event_view(service.retry_landing_event(db, landing_id, event_id)))
