import asyncio

import pytest

from pymentor_analytics.app.probes import SingleflightReadiness


def test_singleflight_overlaps_cancellation_next_check_and_shutdown():
    async def scenario():
        class Probe:
            def __init__(self):
                self.calls = 0
                self.entered = asyncio.Event()
                self.release = asyncio.Event()
            async def is_ready(self):
                self.calls += 1
                self.entered.set()
                await self.release.wait()
                return self.calls == 1

        inner = Probe()
        probe = SingleflightReadiness(inner, 1)
        first = asyncio.create_task(probe.is_ready())
        await inner.entered.wait()
        second = asyncio.create_task(probe.is_ready())
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        inner.release.set()
        assert await second is True
        assert inner.calls == 1
        assert await probe.is_ready() is False
        assert inner.calls == 2
        inner.release.clear()
        inner.entered.clear()
        pending = asyncio.create_task(probe.is_ready())
        await inner.entered.wait()
        await probe.close()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert await probe.is_ready() is False

    asyncio.run(scenario())


def test_singleflight_failure_and_timeout_do_not_poison_later_calls():
    async def scenario():
        class Probe:
            calls = 0
            async def is_ready(self):
                self.calls += 1
                if self.calls == 1:
                    raise ValueError("failure")
                if self.calls == 2:
                    await asyncio.Event().wait()
                return False
        inner = Probe()
        probe = SingleflightReadiness(inner, .01)
        with pytest.raises(ValueError):
            await probe.is_ready()
        with pytest.raises(TimeoutError):
            await probe.is_ready()
        assert await probe.is_ready() is False
        assert inner.calls == 3
        await probe.close()
    asyncio.run(scenario())
