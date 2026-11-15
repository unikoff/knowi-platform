from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from fastapi import Request
from sqlalchemy.orm import Session
from pymentor_core.app.context import RequestContext
from pymentor_core.app.dependencies import get_db
from pymentor_core.app.dependencies import get_request_context
from pymentor_core.shared.api_contracts import api_contract_responses
from pymentor_core.shared.api_contracts import legacy_error_responses
from pymentor_core.modules.learning.api import service
from pymentor_core.modules.learning.api.schemas import CodeRunResponse
from pymentor_core.modules.learning.api.schemas import CodeSubmitRequest
from pymentor_core.modules.learning.api.schemas import CodeSubmitResponse
from pymentor_core.modules.learning.api.schemas import InterpreterRunRequest
from pymentor_core.modules.learning.api.schemas import LearningCatalogResponse
from pymentor_core.modules.learning.api.schemas import LessonCompletionResponse
from pymentor_core.modules.learning.api.schemas import LessonMaterialResponse
from pymentor_core.modules.learning.api.schemas import LessonTheoryResponse
from pymentor_core.modules.learning.api.schemas import LessonsListResponse
from pymentor_core.modules.learning.api.schemas import ManualPracticeAnswerResponse
from pymentor_core.modules.learning.api.schemas import TracksResponse
from pymentor_core.app.course_context import course_request_scope

learning_router = APIRouter(dependencies=[Depends(course_request_scope)], responses=legacy_error_responses(400,404,503))


@learning_router.get('/products')
async def list_products():
    from pymentor_core.shared.bindings import get_bindings
    from starlette.concurrency import run_in_threadpool
    return {'products': await run_in_threadpool(get_bindings().content.get_products)}


async def _learning_sql(request, operation, *, optional=True, kind=None, **content_args):
    from pymentor_core.app.async_request import request_principal, run_request_sql
    from pymentor_core.modules.identity.api.sessions import _local_async_identity
    identity = _local_async_identity(request)
    if identity is None:
        return await run_request_sql(request, lambda db: operation(db, service._PRINCIPAL_UNSET, None))
    principal = await request_principal(request, optional=optional)
    from starlette.concurrency import run_in_threadpool
    content = await run_in_threadpool(service.prepare_request_content, kind, **content_args)
    return await run_request_sql(request, lambda db: operation(db, principal, content), identity=identity)



@learning_router.get("/tracks", responses=api_contract_responses(TracksResponse))
async def list_tracks(request: Request):
    "Треки с прогрессом пользователя — для переключателя курса."
    return await _learning_sql(request, lambda db, principal, content: service.list_tracks(request=request, db=db, principal=principal, content=content), kind="tracks")


@learning_router.get(
    "/lessons", responses=api_contract_responses(LessonsListResponse, 404)
)
async def list_lessons(request: Request, track: str | None = Query(default=None, max_length=160)):
    return await _learning_sql(request, lambda db, principal, content: service.list_lessons(request=request, track=track, db=db, principal=principal, content=content), kind="lessons", track=track)


@learning_router.get(
    "/catalog", responses=api_contract_responses(LearningCatalogResponse)
)
async def learning_catalog(request: Request):
    "Complete navigation in one request, without lesson bodies or task text."
    return await _learning_sql(request, lambda db, principal, content: service.learning_catalog(request=request, db=db, principal=principal, content=content), kind="catalog")


@learning_router.get(
    "/lessons/{lesson_id}",
    responses=api_contract_responses(LessonMaterialResponse, 403, 404),
)
async def lesson_material(lesson_id: str, request: Request):
    return await _learning_sql(request, lambda db, principal, content: service.lesson_material(lesson_id=lesson_id, request=request, db=db, principal=principal, content=content), kind="material", lesson_id=lesson_id)


@learning_router.get(
    "/lessons/{lesson_id}/manual-practice/{exercise_index}/tasks/{task_index}/answer",
    response_model=ManualPracticeAnswerResponse,
    responses=legacy_error_responses(403, 404),
)
async def manual_practice_answer(lesson_id: str, exercise_index: int, task_index: int, request: Request):
    return await _learning_sql(request, lambda db, principal, content: service.manual_practice_answer(
        lesson_id=lesson_id, exercise_index=exercise_index, task_index=task_index,
        request=request, db=db, principal=principal, content=content), kind="manual", lesson_id=lesson_id)


@learning_router.post(
    "/lessons/{lesson_id}/complete",
    responses=api_contract_responses(LessonCompletionResponse, 400, 401, 403, 404),
)
async def complete_lesson(lesson_id: str, request: Request):
    "Ручная отметка «Выполнено» — только для уроков с self_check."
    def complete(db, principal, content):
        context = request.app.state.composition_root.for_request(db)
        return service.complete_lesson(lesson_id=lesson_id, request=request, db=db,
                                       uow=context.uow, principal=principal, content=content)
    return await _learning_sql(request, complete, optional=False, kind="completion", lesson_id=lesson_id)


@learning_router.get(
    "/lessons/{lesson_id}/theory",
    response_model=LessonTheoryResponse,
    responses=legacy_error_responses(403, 404),
)
async def lesson_theory(lesson_id: str, request: Request):
    return await _learning_sql(request, lambda db, principal, content: service.lesson_theory(
        lesson_id=lesson_id, request=request, db=db, principal=principal, content=content), kind="theory", lesson_id=lesson_id)


@learning_router.post(
    "/tasks/{task_id}/submit",
    response_model=CodeSubmitResponse,
    responses=legacy_error_responses(403, 404, 409, 503),
)
async def submit_task(task_id: str, payload: CodeSubmitRequest, request: Request):
    return await service.submit_task(task_id=task_id, payload=payload, request=request)


@learning_router.post(
    "/tasks/{task_id}/run",
    response_model=CodeRunResponse,
    responses=legacy_error_responses(403, 404, 409, 503),
)
async def run_task_code(task_id: str, payload: CodeSubmitRequest, request: Request):
    "Запускает одну демонстрационную проверку без зачёта урока."
    return await service.run_task_code(
        task_id=task_id, payload=payload, request=request
    )


@learning_router.post(
    "/interpreter/run",
    response_model=CodeRunResponse,
    responses=legacy_error_responses(503),
)
async def run_interpreter_code(payload: InterpreterRunRequest):
    "Запускает самостоятельный фрагмент Python без проверки и зачёта урока."
    return await service.run_interpreter_code(payload=payload)
