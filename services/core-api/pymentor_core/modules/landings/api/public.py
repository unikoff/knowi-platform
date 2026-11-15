"""Public, allowlisted event endpoints used by landing-page forms."""

from datetime import datetime
from datetime import timezone
from uuid import UUID

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Header
from fastapi import HTTPException
from fastapi import Response
from fastapi import Request
from fastapi import status
from sqlalchemy.orm import Session

from pymentor_core.app.dependencies import get_db
from pymentor_core.shared.bindings import get_bindings
from pymentor_core.modules.landings.contracts import LandingError
from pymentor_core.modules.landings.contracts import course_landing_slugs
from pymentor_core.modules.landings.api.schemas import LandingConsultationRequest

router = APIRouter()


@router.post("/{slug}/consultations", status_code=status.HTTP_202_ACCEPTED)
async def submit_consultation_request(
    slug: str,
    payload: LandingConsultationRequest,
    response: Response,
    request: Request,
    idempotency_key: str = Header(
        ..., alias="Idempotency-Key", min_length=36, max_length=36
    ),
):
    from pymentor_core.app.async_request import run_request_sql
    try:
        event_id = str(UUID(idempotency_key))
    except ValueError:
        raise HTTPException(
            status_code=422, detail="Idempotency-Key должен быть UUID"
        ) from None

    event_payload = payload.model_dump(exclude={"consent", "consent_version"})
    event_payload["consented_at"] = datetime.now(timezone.utc).isoformat()
    event_payload["consent_version"] = payload.consent_version
    try:
        def submit(db):
            bindings = get_bindings()
            if slug in course_landing_slugs(include_disabled=True).values():
                event, duplicate = bindings.landings.submit_course_consultation(
                    db, slug, event_id, event_payload
                )
            else:
                event, duplicate = bindings.landing_requests.submit_consultation(
                    db, slug, event_id, event_payload
                )
            return event.event_id, duplicate
        returned_event_id, duplicate = await run_request_sql(request, submit)
    except LandingError as exc:
        raise HTTPException(
            status_code=exc.status_code, detail=exc.detail, headers=exc.headers
        ) from exc
    if duplicate:
        response.status_code = status.HTTP_200_OK
    return {"accepted": True, "duplicate": duplicate, "event_id": returned_event_id}
