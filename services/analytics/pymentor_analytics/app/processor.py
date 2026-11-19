"""Bounded synthetic processing loop, independent of HTTP request handlers."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
import random
from uuid import uuid4

from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.probes import ReadinessPort
from pymentor_analytics.contracts.processing_v1 import MAX_BATCH, ClaimedBatch, StaleClaim
from pymentor_analytics.contracts.work_v1 import SYNTHETIC_SUBSCRIPTIONS
from pymentor_analytics.modules.analytics.activity.synthetic import calculate
from pymentor_analytics.modules.analytics.ports import ProcessorStore

LEASE_SECONDS = 30


class ProcessorRuntime:
    """One lane per configured consumer; bounded shared DB and CPU capacity."""

    def __init__(self, store: ProcessorStore, readiness: ReadinessPort, settings: Settings, *, handler=calculate):
        self.store = store
        self.readiness = readiness
        self.settings = settings
        self.handler = handler
        self.worker_id = uuid4()  # lease owner, never consumer identity
        requested = set(settings.processor_consumers.split(","))
        self.subscriptions = tuple(s for s in SYNTHETIC_SUBSCRIPTIONS if s.consumer in requested)
        capacity = min(settings.processor_max_inflight, settings.pool_size + settings.pool_overflow)
        self._inflight = asyncio.Semaphore(capacity)
        self._compute_slots = asyncio.Semaphore(capacity)
        self._consumer_slots = {sub.consumer: asyncio.Semaphore(1) for sub in self.subscriptions}
        self._executor = ThreadPoolExecutor(max_workers=capacity, thread_name_prefix="analytics-synthetic")
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        if self._tasks or self._stop.is_set():
            raise RuntimeError("processor already started or stopped")
        self._tasks = [asyncio.create_task(self._consumer(sub.consumer, sub.version), name=f"processor:{sub.consumer}")
                       for sub in self.subscriptions]

    async def stop(self) -> None:
        """Stop claims, drain within deadline, then cancel and let leases expire."""
        self._stop.set()
        try:
            if self._tasks:
                done, pending = await asyncio.wait(self._tasks, timeout=self.settings.shutdown_timeout_seconds)
                for task in pending:
                    task.cancel()
                if pending:
                    finished, pending = await asyncio.wait(pending, timeout=1)
                    done |= finished
                await asyncio.gather(*done, return_exceptions=True)
                if pending:
                    raise RuntimeError("processor shutdown timed out")
                self._tasks.clear()
        finally:
            # A Python thread cannot be force-cancelled; bounded pure handlers
            # finish on their own, and no new work is submitted after stop.
            self._executor.shutdown(wait=False, cancel_futures=True)

    async def _is_ready(self) -> bool:
        try:
            return await asyncio.wait_for(self.readiness.is_ready(), timeout=self.settings.db_timeout_seconds) is True
        except Exception:
            return False

    async def _pause(self, seconds: float) -> None:
        delay = min(self.settings.processor_poll_max_seconds, max(0.05, seconds * random.uniform(0.8, 1.2)))
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=delay)
        except TimeoutError:
            pass

    async def _consumer(self, consumer: str, version: int) -> None:
        delay = self.settings.processor_poll_min_seconds
        while not self._stop.is_set():
            if self._consumer_slots[consumer].locked():
                await self._pause(delay)
                delay = min(delay * 2, self.settings.processor_poll_max_seconds)
                continue
            if not await self._is_ready():
                await self._pause(delay)
                delay = min(delay * 2, self.settings.processor_poll_max_seconds)
                continue
            async with self._inflight:
                if self._stop.is_set():
                    break
                outcome = await self._cycle(consumer, version)
            if outcome == "worked":
                delay = self.settings.processor_poll_min_seconds
            else:
                await self._pause(delay)
                delay = min(delay * 2, self.settings.processor_poll_max_seconds)

    async def _compute(self, batch: ClaimedBatch):
        # A cancelled await must not free a slot while the real thread runs.
        consumer_slot = self._consumer_slots[batch.consumer]
        await consumer_slot.acquire()
        try:
            await self._compute_slots.acquire()
        except BaseException:
            consumer_slot.release()
            raise
        loop = asyncio.get_running_loop()
        try:
            future = self._executor.submit(self.handler, batch)
        except BaseException:
            self._compute_slots.release()
            consumer_slot.release()
            raise
        def release_when_finished(_):
            try:
                def release_slots():
                    self._compute_slots.release()
                    consumer_slot.release()
                loop.call_soon_threadsafe(release_slots)
            except RuntimeError:
                pass  # loop already closed after bounded shutdown
        future.add_done_callback(release_when_finished)
        return await asyncio.shield(asyncio.wrap_future(future))

    async def _heartbeat(self, batch: ClaimedBatch, lost: asyncio.Event) -> None:
        while True:
            await asyncio.sleep(LEASE_SECONDS / 3)
            try:
                if not await asyncio.wait_for(self.store.renew(batch, LEASE_SECONDS), timeout=self.settings.db_timeout_seconds):
                    lost.set()
                    return
            except Exception:
                lost.set()
                return

    async def _cleanup(self, batch: ClaimedBatch, *, failed: bool) -> None:
        try:
            method = self.store.fail if failed else self.store.release
            await asyncio.wait_for(method(batch), timeout=min(1, self.settings.db_timeout_seconds))
        except Exception:
            pass  # lease expiry is the recovery boundary; no ACK is inferred

    async def _cycle(self, consumer: str, version: int) -> str:
        batch = None
        heartbeat = None
        commit_started = False
        try:
            async with asyncio.timeout(self.settings.processor_job_timeout_seconds):
                batch = await self.store.claim(consumer, version, self.worker_id, MAX_BATCH, LEASE_SECONDS)
                if batch is None:
                    return "idle"
                lost = asyncio.Event()
                heartbeat = asyncio.create_task(self._heartbeat(batch, lost))
                effect = await self._compute(batch)
                if lost.is_set() or not await self._is_ready():
                    await self._cleanup(batch, failed=False)
                    return "failed"
                # The store fences the transaction even if a lease expires here.
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
                heartbeat = None
                commit_started = True
                await self.store.commit(batch, effect)
                return "worked"
        except asyncio.CancelledError:
            if batch is not None and not commit_started:
                await self._cleanup(batch, failed=False)
            raise
        except TimeoutError:
            if batch is not None and not commit_started:
                await self._cleanup(batch, failed=True)
            # A commit timeout is an unknown outcome: do not ACK or release it.
            return "failed"
        except StaleClaim:
            return "failed"
        except Exception:
            if batch is not None and not commit_started:
                await self._cleanup(batch, failed=True)
            return "failed"
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
