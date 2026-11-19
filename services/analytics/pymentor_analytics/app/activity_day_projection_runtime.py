"""Bounded day worker sharing the bucket runtime's lease and shutdown rules."""
import asyncio
import logging
import time
from .activity_projection_runtime import ActivityProjectionRuntime
from pymentor_analytics.modules.analytics.activity.day import normalize_day
from pymentor_analytics.modules.analytics.activity.day_ports import DaySourceNotReady, StaleDayClaim

logger = logging.getLogger(__name__)


class _ClaimTrackingStore:
    def __init__(self, store):
        self.store = store
        self.claimed = False

    def __getattr__(self, name):
        return getattr(self.store, name)

    async def claim(self, *args, **kwargs):
        claim = await self.store.claim(*args, **kwargs)
        self.claimed = claim is not None
        return claim


class ActivityDayProjectionRuntime(ActivityProjectionRuntime):
    thread_name = "analytics-activity-day"
    task_name = "analytics-activity-days"
    unavailable_message = "Activity day processing temporarily unavailable"
    # The store already reschedules a not-ready day without consuming an attempt.
    stale_exceptions = (StaleDayClaim, DaySourceNotReady)

    def __init__(self, store, readiness, *, course_backfill=None, **kwargs):
        self.course_backfill = course_backfill
        super().__init__(_ClaimTrackingStore(store) if course_backfill is not None else store, readiness, **kwargs)

    async def _cycle(self):
        if self.course_backfill is None:
            return await super()._cycle()
        self.store.claimed = False
        worked = await super()._cycle()
        if worked or self.store.claimed:
            return worked
        if await self.readiness.is_ready() is not True:
            self._normal_idle = False
            return False
        try:
            deadline = time.monotonic() + self.operation_timeout
            async with asyncio.timeout(self.operation_timeout):
                phase = await self.course_backfill(deadline=deadline)
            self._normal_idle = phase in {"complete", "draining", "idle", "retry"}
            return phase in {"bucket", "day", "window"}
        except asyncio.CancelledError:
            raise
        except Exception:
            self._normal_idle = False
            self.backfill_failures = min(self.backfill_failures + 1, 2**63 - 1)
            now = time.monotonic()
            if now >= self._next_backfill_warning:
                logger.warning("Activity course projection backfill temporarily unavailable")
                self._next_backfill_warning = now + 30
            return False

    def _normalize(self, claim, records):
        return normalize_day(records)
