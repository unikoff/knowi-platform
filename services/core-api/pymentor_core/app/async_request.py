"""Short-lived asyncpg unit of work for ASGI request phases."""
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from sqlalchemy.exc import SQLAlchemyError

from pymentor_core.app.dependencies import get_db
from pymentor_core.modules.identity.api.sessions import _local_async_identity
from pymentor_core.modules.identity.api.sessions import async_current_principal


async def request_principal(request, *, optional=False):
    try:
        return await async_current_principal(request)
    except HTTPException as exc:
        if optional and exc.status_code == 401:
            return None
        raise


async def run_request_sql(request, operation, *, identity=None):
    """Run an existing SQL use case with an exclusive session and guaranteed cleanup."""
    identity = identity if identity is not None else _local_async_identity(request)
    if identity is None:
        def legacy():
            import inspect
            dependency = request.app.dependency_overrides.get(get_db, get_db)
            supplied = dependency()
            if not inspect.isgenerator(supplied):
                return operation(supplied)
            try:
                return operation(next(supplied))
            finally:
                supplied.close()
        return await run_in_threadpool(legacy)
    try:
        async with identity.database.unit_of_work() as uow:
            return await uow.session.run_sync(operation)
    except (SQLAlchemyError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
