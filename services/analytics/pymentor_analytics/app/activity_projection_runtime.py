"""Default-off bounded loop for the shared activity-v1 bucket projection."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
import logging
import random
import time
from uuid import uuid4

from pymentor_analytics.modules.analytics.activity.bucket import BucketOverflow, normalize_subject_bucket
from pymentor_analytics.modules.analytics.activity.projection_ports import ProjectionLimit, StaleBucketClaim

logger = logging.getLogger(__name__)


class ActivityProjectionRuntime:
    thread_name = "analytics-activity-bucket"
    task_name = "analytics-activity-buckets"
    unavailable_message = "Activity bucket processing temporarily unavailable"
    stale_exceptions = (StaleBucketClaim,)

    def _normalize(self, claim, records):
        return normalize_subject_bucket(records, subject_ref=claim.subject_ref, bucket_start=claim.bucket_start)

    def __init__(self, store, readiness, *, enabled=False, poll_seconds=.5, operation_timeout=20.0):
        if (type(enabled) is not bool or not .05 <= poll_seconds <= 5 or not 1 <= operation_timeout < 28):
            raise ValueError("invalid activity projection runtime")
        self.store, self.readiness, self.enabled = store, readiness, enabled
        self.poll_seconds, self.operation_timeout = poll_seconds, operation_timeout
        self.owner = uuid4()
        self._stop, self._task, self._started = asyncio.Event(), None, False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=self.thread_name)
        self._compute_slot = asyncio.Semaphore(1)
        self.completed, self.failures = 0, 0
        self._normal_idle = False
        self.backfill_failures = 0
        self._next_backfill_warning = 0.0

    def start(self):
        if self._started or self._stop.is_set():
            raise RuntimeError("activity projection runtime already started or stopped")
        self._started = True
        if self.enabled:
            self._task = asyncio.create_task(self._run(), name=self.task_name)

    async def stop(self):
        self._stop.set()
        try:
            if self._task is not None:
                self._task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._task
                self._task = None
        finally:
            self._executor.shutdown(wait=False, cancel_futures=True)

    async def _pause(self, seconds):
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def _compute(self, claim, records):
        await self._compute_slot.acquire()
        loop = asyncio.get_running_loop()
        try:
            future = self._executor.submit(self._normalize, claim, records)
        except BaseException:
            self._compute_slot.release()
            raise
        def release(_):
            try:
                loop.call_soon_threadsafe(self._compute_slot.release)
            except RuntimeError:
                pass
        future.add_done_callback(release)
        return await asyncio.shield(asyncio.wrap_future(future))

    async def _fail(self, claim, reason, deadline):
        if claim is None or time.monotonic() >= deadline:
            return
        try:
            await self.store.fail(claim, reason, deadline=deadline)
        except Exception:
            pass  # lease expiry or the newer generation remains the recovery boundary

    async def _cycle(self):
        deadline = time.monotonic() + self.operation_timeout
        claim, commit_started = None, False
        self._normal_idle = False
        try:
            async with asyncio.timeout(self.operation_timeout):
                if await self.readiness.is_ready() is not True:
                    return False
                # Backfill capacity can be full; still drain already dirty buckets.
                try:
                    phase = await self.store.backfill_step(deadline=deadline)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.backfill_failures = min(self.backfill_failures + 1, 2**63 - 1)
                    now = time.monotonic()
                    if now >= self._next_backfill_warning:
                        logger.warning("Activity projection backfill temporarily unavailable")
                        self._next_backfill_warning = now + 30
                    phase = "unavailable"
                claim = await self.store.claim(self.owner, deadline=deadline)
                if claim is None:
                    self._normal_idle = phase != "unavailable"
                    return phase == "scanning"
                claim, records = await self.store.load(claim, deadline=deadline)
                projection = await self._compute(claim, records)
                if await self.readiness.is_ready() is not True:
                    await self._fail(claim, "transient", deadline)
                    return False
                commit_started = True
                await self.store.commit(claim, projection, deadline=deadline)
                self.completed += 1
                return True
        except asyncio.CancelledError:
            # No ACK or release after an ambiguous commit; lease expiry reconciles.
            if not commit_started:
                await self._fail(claim, "transient", deadline)
            raise
        except self.stale_exceptions:
            self._normal_idle = True
            return False
        except (BucketOverflow, ProjectionLimit):
            self.failures += 1
            # ProjectionLimit from the store means its transaction rolled back;
            # unlike a lost commit response, this outcome is known.
            await self._fail(claim, "input_limit", deadline)
            return False
        except ValueError:
            self.failures += 1
            if not commit_started: await self._fail(claim, "invalid_fact", deadline)
            return False
        except TimeoutError:
            self.failures += 1
            if not commit_started: await self._fail(claim, "compute_timeout", deadline)
            return False
        except Exception:
            self.failures += 1
            if not commit_started: await self._fail(claim, "transient", deadline)
            logger.warning(self.unavailable_message)
            return False

    async def _run(self):
        delay = self.poll_seconds
        while not self._stop.is_set():
            if await self._cycle():
                delay = self.poll_seconds
                await asyncio.sleep(0)
            elif self._normal_idle:
                delay = self.poll_seconds
                await self._pause(self.poll_seconds)
            else:
                await self._pause(min(10, delay * random.uniform(.8, 1.2)))
                delay = min(10, delay * 2)
