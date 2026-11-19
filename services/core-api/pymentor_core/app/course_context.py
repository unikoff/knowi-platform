"""Resolve the product once for the complete async request, including Runner."""
from fastapi import HTTPException, Query, Request
from pymentor_core.adapters.course_local.runtime import default_runtime
from pymentor_core.modules.learning.course_context import course_scope
from pymentor_core.adapters.course_local.packages import CourseNotFoundError, CoursePackageError


def enabled_course_ids(course_id: str | None = None) -> list[str]:
    try:
        registry = default_runtime().registry
        if course_id is None:
            return [entry.course_id for entry in registry.entries.values() if entry.enabled]
        registry.require_enabled(course_id)
    except CourseNotFoundError:
        raise HTTPException(status_code=404, detail="Course not found") from None
    return [course_id]


async def course_request_scope(request: Request, course_id: str | None = Query(default=None, max_length=80)):
    if len(request.query_params.getlist('course_id')) > 1:
        raise HTTPException(status_code=400, detail='Ambiguous course_id')
    body_course = None
    if request.method == 'POST' and await request.body():
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail='Invalid JSON') from None
        if isinstance(body, dict) and 'course_id' in body:
            body_course = body['course_id']
            if body_course is not None and (not isinstance(body_course, str) or not 1 <= len(body_course) <= 80):
                raise HTTPException(status_code=400, detail='Invalid course_id')
    if course_id is not None and body_course is not None and course_id != body_course:
        raise HTTPException(status_code=400, detail='Conflicting course_id')
    selected = course_id if course_id is not None else body_course if body_course is not None else 'backend'
    try:
        default_runtime().registry.require_enabled(selected)
    except CourseNotFoundError:
        raise HTTPException(status_code=404, detail='Course not found') from None
    with course_scope(selected):
        try:
            yield selected
        except (CoursePackageError, OSError):
            # File/snapshot failure must not reveal server paths or pick another course.
            raise HTTPException(status_code=503, detail='Course content unavailable') from None
