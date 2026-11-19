"""Bound actual body bytes, per-process concurrency and a total request deadline."""
import asyncio
from time import monotonic

from pymentor_analytics.contracts.v2 import MAX_BATCH_BYTES
from .errors import error_response

PATH = "/api/analytics/events/batch"


class BodyTooLarge(Exception):
    pass


class IntakeLimitsMiddleware:
    def __init__(self, app, *, concurrency: int, timeout_seconds: float):
        self.app = app
        self.semaphore = asyncio.Semaphore(concurrency)
        self.timeout_seconds = timeout_seconds

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != PATH or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        lengths = [value for name, value in scope["headers"] if name.lower() == b"content-length"]
        if len(lengths) > 1 or (lengths and (not lengths[0].isdigit() or len(lengths[0]) > 10)):
            await error_response(400, "invalid_request", "Invalid request")(scope, receive, send)
            return
        if lengths and int(lengths[0]) > MAX_BATCH_BYTES:
            await error_response(413, "too_large", "Batch too large")(scope, receive, send)
            return
        if self.semaphore.locked():
            await error_response(429, "rate_limited", "Retry later", {"Retry-After": "1"})(scope, receive, send)
            return
        await self.semaphore.acquire()
        seen = 0
        started = False
        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > MAX_BATCH_BYTES:
                    raise BodyTooLarge()
            return message
        async def checked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)
        scope["analytics_deadline"] = monotonic() + self.timeout_seconds
        try:
            async with asyncio.timeout(self.timeout_seconds):
                await self.app(scope, limited_receive, checked_send)
        except BodyTooLarge:
            if started:
                raise
            await error_response(413, "too_large", "Batch too large")(scope, receive, send)
        except TimeoutError:
            if started:
                raise
            await error_response(503, "unavailable", "Outcome unknown; retry the same event IDs")(scope, receive, send)
        finally:
            self.semaphore.release()
