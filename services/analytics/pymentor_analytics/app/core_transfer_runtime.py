"""Default-off bounded worker for the transaction-only Core projection."""
import asyncio
from contextlib import suppress
import logging
import random
import time

logger = logging.getLogger(__name__)


class CoreTransferRuntime:
    def __init__(self, store, readiness, *, proof_store=None, enabled=False, poll_seconds=0.5,
                 operation_timeout=3.0, max_failures=3, cooldown_seconds=30.0):
        if (type(enabled) is not bool or not 0.05 <= poll_seconds <= 5 or not 0.1 <= operation_timeout <= 30
                or type(max_failures) is not int or not 1 <= max_failures <= 10 or not 1 <= cooldown_seconds <= 300):
            raise ValueError("invalid transfer runtime configuration")
        self.store, self.readiness, self.enabled = store, readiness, enabled
        self.proof_store, self._proof_first = proof_store, False
        self.poll_seconds, self.operation_timeout = poll_seconds, operation_timeout
        self.max_failures, self.cooldown_seconds = max_failures, cooldown_seconds
        self._stop, self._task = asyncio.Event(), None
        self._started = False
        self.processed = self.failures = 0

    def start(self):
        if self._stop.is_set() or self._started:
            raise RuntimeError("transfer runtime already started or stopped")
        self._started = True
        if self.enabled:
            self._task = asyncio.create_task(self._run(), name="analytics-core-transfer")

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _pause(self, seconds):
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _run(self):
        failures = 0
        while not self._stop.is_set():
            try:
                deadline = time.monotonic() + self.operation_timeout
                async with asyncio.timeout(self.operation_timeout):
                    ready = await self.readiness.is_ready()
                    worked = False
                    if ready is True:
                        if self.proof_store is None:
                            worked = await self.store.project_one(deadline=deadline)
                        else:
                            first, second = ((self.proof_store, self.store) if self._proof_first else (self.store, self.proof_store))
                            self._proof_first = not self._proof_first
                            worked = await first.project_one(deadline=deadline)
                            if not worked:
                                worked = await second.project_one(deadline=deadline)
                failures = 0
                if worked:
                    self.processed += 1
                    # Yield between transactions to bound scheduler monopolization.
                    await asyncio.sleep(0)
                else:
                    await self._pause(self.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.failures += 1
                failures += 1
                logger.warning("Core transfer projection temporarily unavailable")
                if failures >= self.max_failures:
                    delay = self.cooldown_seconds
                    failures = 0
                else:
                    delay = min(self.cooldown_seconds, 2 ** (failures - 1) + random.uniform(0, 0.25))
                await self._pause(delay)
