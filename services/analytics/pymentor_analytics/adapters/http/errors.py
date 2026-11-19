"""Sanitized transport errors shared by independently mounted routers."""
from uuid import uuid4

from fastapi.responses import JSONResponse

from pymentor_analytics.contracts.v2 import ErrorResponse


def error_response(status: int, code: str, message: str, headers: dict[str, str] | None = None):
    body = ErrorResponse(code=code, message=message, correlation_id=uuid4())
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"), headers=headers)
