from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from fastapi import Request
from fastapi import Response
from sqlalchemy.orm import Session
from pymentor_core.app.dependencies import get_db
from pymentor_core.app.dependencies import get_request_context
from pymentor_core.app.context import RequestContext
from pymentor_core.modules.bookings.api.schemas import StudentSlotEnvelope
from pymentor_core.modules.bookings.api.schemas import StudentSlotsResponse
from pymentor_core.shared.api_contracts import api_contract_responses
import logging
from time import perf_counter
from pymentor_core.modules.identity import public as accounts
from pymentor_core.modules.bookings.api import service

booking_router = APIRouter()
logger = logging.getLogger("pymentor.booking")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


@booking_router.get(
    "/slots",
    responses=api_contract_responses(StudentSlotsResponse, 401),
)
async def get_bookable_slots(request: Request, year: int = Query(..., ge=2000, le=2100),
                             month: int = Query(..., ge=1, le=12)):
    def operation(db, user):
        context = request.app.state.composition_root.for_request(db)
        slots = service.list_bookable_slots(year=year, month=month, user_id=user.id, db=db, clock=context.clock)
        quota = service.get_quota_status(user_id=user.id, year=year, month=month, db=db, uow=context.uow)
        return {"slots": slots, "quota": quota}
    return await _booking_sql(request, operation)


async def _booking_sql(request, operation):
    from pymentor_core.app.async_request import request_principal, run_request_sql
    from pymentor_core.modules.identity.api.sessions import _local_async_identity
    identity = _local_async_identity(request)
    if identity is None:
        return await run_request_sql(request, lambda db: operation(db, accounts.get_current_principal(request=request, db=db)))
    principal = await request_principal(request)
    return await run_request_sql(request, lambda db: operation(db, principal), identity=identity)


@booking_router.post(
    "/slots/{slot_id}/book",
    responses=api_contract_responses(StudentSlotEnvelope, 400, 401, 403, 404, 409),
)
async def book_slot(slot_id: int, request: Request):
    started = perf_counter()
    def operation(db, user):
        slot = service.book_slot(slot_id=slot_id, user_id=user.id, db=db,
                                 notification_username=user.username, notification_email=user.email)
        logger.info("booking_saved slot_id=%s duration_ms=%.1f", slot.id, (perf_counter() - started) * 1000)
        return {"slot": service.serialize_slot_student(slot, user.id)}
    return await _booking_sql(request, operation)


@booking_router.post(
    "/slots/{slot_id}/cancel",
    responses=api_contract_responses(StudentSlotEnvelope, 401, 403, 404, 409),
)
async def cancel_slot(slot_id: int, request: Request):
    started = perf_counter()
    def operation(db, user):
        slot = service.cancel_booking(slot_id=slot_id, user_id=user.id, db=db,
                                      notification_username=user.username, notification_email=user.email)
        logger.info("booking_cancelled slot_id=%s duration_ms=%.1f", slot.id, (perf_counter() - started) * 1000)
        return {"slot": service.serialize_slot_student(slot, user.id)}
    return await _booking_sql(request, operation)
