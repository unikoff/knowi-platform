import asyncio
import threading
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from pymentor_analytics.app.activity_day_projection_runtime import ActivityDayProjectionRuntime
from pymentor_analytics.modules.analytics.activity.day_ports import DayClaim, DaySourceNotReady, StaleDayClaim


class Ready:
    def __init__(self, ready=True): self.ready = ready
    async def is_ready(self): return self.ready


class Store:
    def __init__(self, error=None):
        self.claimed = DayClaim(uuid4(), date(2026, 1, 1), 1, None, uuid4(), uuid4(), uuid4())
        self.error, self.claims, self.failures, self.commits = error, 0, [], []
    async def backfill_step(self, *, deadline): return "draining"
    async def claim(self, owner, *, deadline): self.claims += 1; return self.claimed
    async def load(self, claim, *, deadline):
        if self.error in ("not_ready", "stale"):
            raise DaySourceNotReady() if self.error == "not_ready" else StaleDayClaim()
        return replace(claim, subject_epoch=0), ()
    async def commit(self, claim, projection, *, deadline):
        if self.error == "lost": raise RuntimeError("lost reply")
        self.commits.append(projection)
    async def fail(self, claim, reason, *, deadline): self.failures.append(reason)


@pytest.mark.parametrize("error", [None, "not_ready", "stale", "lost"])
def test_day_cycle_lease_recovery_and_no_penalty(error):
    async def run():
        store = Store(error)
        runtime = ActivityDayProjectionRuntime(store, Ready(), enabled=True)
        try:
            assert await runtime._cycle() is (error is None)
            assert runtime._normal_idle is (error in {"not_ready", "stale"})
            assert store.failures == []
            assert len(store.commits) == (error is None)
            assert runtime.failures == (error == "lost")
        finally: await runtime.stop()
    asyncio.run(run())


def test_disabled_and_not_ready_do_not_claim():
    async def run():
        store = Store()
        runtime = ActivityDayProjectionRuntime(store, Ready(False))
        runtime.start()
        await asyncio.sleep(.01)
        assert store.claims == 0
        assert await runtime._cycle() is False
        assert store.claims == 0
        await runtime.stop()
    asyncio.run(run())


def test_cancel_after_commit_started_does_not_fail_claim():
    async def run():
        store = Store()
        entered = asyncio.Event()
        async def commit(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        store.commit = commit
        runtime = ActivityDayProjectionRuntime(store, Ready(), enabled=True)
        task = asyncio.create_task(runtime._cycle())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert store.failures == []
        await runtime.stop()
    asyncio.run(run())


def test_compute_runs_one_at_a_time():
    async def run():
        runtime = ActivityDayProjectionRuntime(Store(), Ready())
        active = 0
        peak = 0
        lock = threading.Lock()
        release = threading.Event()
        started = threading.Event()
        def compute(claim, records):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                started.set()
            release.wait(2)
            with lock: active -= 1
            return {}
        runtime._normalize = compute
        first = asyncio.create_task(runtime._compute(None, ()))
        await asyncio.to_thread(started.wait, 1)
        second = asyncio.create_task(runtime._compute(None, ()))
        await asyncio.sleep(.02)
        release.set()
        await asyncio.gather(first, second)
        assert peak == 1
        await runtime.stop()
    asyncio.run(run())


def test_cancel_before_commit_releases_claim():
    async def run():
        store = Store()
        entered = asyncio.Event()
        async def load(claim, *, deadline):
            entered.set()
            await asyncio.Event().wait()
        store.load = load
        runtime = ActivityDayProjectionRuntime(store, Ready(), enabled=True)
        task = asyncio.create_task(runtime._cycle())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert store.failures == ["transient"]
        assert store.commits == []
        await runtime.stop()
    asyncio.run(run())


def test_backfill_failure_counted_without_blocking_drain(caplog):
    async def run():
        store = Store()
        async def unavailable(*, deadline): raise RuntimeError("sensitive detail")
        store.backfill_step = unavailable
        runtime = ActivityDayProjectionRuntime(store, Ready(), enabled=True)
        try:
            assert await runtime._cycle() is True
            assert await runtime._cycle() is True
            assert runtime.backfill_failures == 2
            assert len(store.commits) == 2
            assert len([r for r in caplog.records if "backfill temporarily unavailable" in r.message]) == 1
            assert "sensitive detail" not in caplog.text
        finally: await runtime.stop()
    asyncio.run(run())


def test_course_backfill_is_idle_only_and_requires_readiness():
    async def run():
        store, ready, calls = Store(), Ready(), []
        async def course(*, deadline):
            calls.append(deadline)
            return "bucket"
        runtime = ActivityDayProjectionRuntime(store, ready, enabled=True, course_backfill=course)
        try:
            assert await runtime._cycle() is True
            assert calls == []  # a day claim takes precedence
            async def no_claim(*args, **kwargs): return None
            store.claim = no_claim
            assert await runtime._cycle() is True
            assert len(calls) == 1
            ready.ready = False
            assert await runtime._cycle() is False
            assert len(calls) == 1
        finally:
            await runtime.stop()
    asyncio.run(run())


def test_deferred_day_does_not_run_course_sidecar_and_sidecar_errors_backoff():
    async def run():
        store, calls = Store("not_ready"), []
        async def course(*, deadline):
            calls.append(True)
            raise RuntimeError("unavailable")
        runtime = ActivityDayProjectionRuntime(store, Ready(), enabled=True, course_backfill=course)
        try:
            assert await runtime._cycle() is False and runtime._normal_idle
            assert calls == []
            async def no_claim(*args, **kwargs): return None
            store.claim = no_claim
            assert await runtime._cycle() is False and not runtime._normal_idle
            assert calls == [True]
        finally: await runtime.stop()
    asyncio.run(run())
