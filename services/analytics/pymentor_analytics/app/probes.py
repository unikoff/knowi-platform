"""Probe interface; production readiness is closed until real adapters exist."""
import asyncio
from typing import Protocol


class ReadinessPort(Protocol):
    async def is_ready(self) -> bool:
        """True only when role-specific DB, schema and privacy checks pass."""
        ...


class UnavailableReadiness:
    async def is_ready(self) -> bool:
        return False


class SingleflightReadiness:
    """Share only an in-flight check; never reuse its completed result."""
    def __init__(self, probe: ReadinessPort, timeout: float):
        self.probe = probe
        self.timeout = timeout
        self._task: asyncio.Task | None = None
        self._closed = False

    def _finished(self, task: asyncio.Task) -> None:
        if self._task is task:
            self._task = None
        if not task.cancelled():
            task.exception()  # Consume failures even if every waiter was cancelled.

    async def is_ready(self) -> bool:
        if self._closed:
            return False
        task = self._task
        if task is None or task.done():
            task = asyncio.create_task(asyncio.wait_for(self.probe.is_ready(), self.timeout))
            self._task = task
            task.add_done_callback(self._finished)
        return await asyncio.shield(task)

    async def close(self) -> None:
        self._closed = True
        task = self._task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if self._task is task:
                self._task = None
