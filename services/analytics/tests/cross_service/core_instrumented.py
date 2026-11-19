"""Synthetic Core HTTP entrypoint with opt-in SQL guard for individual requests."""
from contextvars import ContextVar
from sqlalchemy import event
from sqlalchemy.engine import Engine

_guarded = ContextVar("cross_service_no_legacy_activity_sql", default=False)
_completions_guarded = ContextVar("cross_service_no_completion_sql", default=False)


@event.listens_for(Engine, "before_cursor_execute")
def reject_legacy_activity_sql(conn, cursor, statement, parameters, context, executemany):
    query = statement.lower()
    if _guarded.get() and "user_activity_days" in query:
        raise RuntimeError("synthetic request attempted legacy activity SQL")
    if _completions_guarded.get() and "user_lesson_progress" in query:
        raise RuntimeError("synthetic request attempted legacy completion SQL")


class Guard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        headers = scope.get("headers", ()) if scope["type"] == "http" else ()
        enabled = any(key.lower() == b"x-synthetic-no-legacy-sql" and value == b"1" for key, value in headers)
        token = _guarded.set(enabled)
        completion_token = _completions_guarded.set(
            any(key.lower() == b"x-synthetic-no-completion-sql" and value == b"1" for key, value in headers)
        )
        try:
            await self.app(scope, receive, send)
        finally:
            _completions_guarded.reset(completion_token)
            _guarded.reset(token)


def create_app():
    from pymentor_core.app.asgi import create_app as core_app
    return Guard(core_app())
