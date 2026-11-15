"""Operation-local product identity shared by ports and their adapters."""
from contextlib import contextmanager
from contextvars import ContextVar

_COURSE = ContextVar('learning_product_course', default='backend')

def current_course_id() -> str:
    return _COURSE.get()

@contextmanager
def course_scope(course_id: str):
    """Caller resolves the ID against the trusted published-course registry."""
    token = _COURSE.set(course_id)
    try:
        yield
    finally:
        _COURSE.reset(token)
