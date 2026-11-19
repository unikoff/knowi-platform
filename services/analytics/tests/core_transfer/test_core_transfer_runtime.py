import asyncio

from pymentor_analytics.app.core_transfer_runtime import CoreTransferRuntime
from pymentor_analytics.adapters.postgres.core_transfer import has_core_schema
from pymentor_analytics.modules.collector.ports import IntakeUnavailable
import pytest


def test_default_off_and_shutdown_after_work():
    async def scenario():
        class Ready:
            async def is_ready(self): return True
        class Store:
            def __init__(self): self.calls = 0
            async def project_one(self, *, deadline):
                self.calls += 1
                return self.calls == 1
        store = Store()
        disabled = CoreTransferRuntime(store, Ready())
        disabled.start()
        await asyncio.sleep(.06)
        await disabled.stop()
        assert store.calls == 0
        worker = CoreTransferRuntime(store, Ready(), enabled=True, poll_seconds=.05)
        worker.start()
        await asyncio.sleep(.12)
        await worker.stop()
        assert store.calls >= 2 and worker.processed == 1 and worker.failures == 0
    asyncio.run(scenario())


def test_closed_readiness_never_projects():
    async def scenario():
        class Closed:
            async def is_ready(self): return False
        class Store:
            async def project_one(self, *, deadline): raise AssertionError("closed barrier must block work")
        worker = CoreTransferRuntime(Store(), Closed(), enabled=True, poll_seconds=.05)
        worker.start()
        await asyncio.sleep(.12)
        await worker.stop()
        assert worker.processed == worker.failures == 0
    asyncio.run(scenario())


def test_error_is_retried_after_bounded_delay_and_cancellation():
    async def scenario():
        class Ready:
            async def is_ready(self): return True
        class Failed:
            async def project_one(self, *, deadline): raise RuntimeError("secret test marker")
        worker = CoreTransferRuntime(Failed(), Ready(), enabled=True, poll_seconds=.05, max_failures=1, cooldown_seconds=1)
        worker.start()
        await asyncio.sleep(.06)
        assert worker.failures == 1
        await asyncio.wait_for(worker.stop(), timeout=.2)
    asyncio.run(scenario())


def test_schema_detection_is_dynamic_and_fails_closed_on_partial_expansion():
    async def scenario():
        class Result:
            def __init__(self, row): self.row = row
            def one(self): return self.row
        class Session:
            def __init__(self): self.rows = iter([(False, False, False), (True, True, True), (True, True, False)])
            async def execute(self, statement): return Result(next(self.rows))
        session = Session()
        assert await has_core_schema(session) is False
        assert await has_core_schema(session) is True
        with pytest.raises(IntakeUnavailable): await has_core_schema(session)
    asyncio.run(scenario())


def test_projection_and_proof_workers_alternate():
    async def scenario():
        class Ready:
            async def is_ready(self): return True
        class Store:
            def __init__(self): self.calls = 0
            async def project_one(self, *, deadline):
                self.calls += 1
                if projection.calls and proofs.calls:
                    both.set()
                return True
        both = asyncio.Event()
        projection, proofs = Store(), Store()
        worker = CoreTransferRuntime(projection, Ready(), proof_store=proofs, enabled=True, poll_seconds=.05)
        worker.start()
        await asyncio.wait_for(both.wait(), timeout=1)
        await worker.stop()
        assert projection.calls >= 1 and proofs.calls >= 1
    asyncio.run(scenario())
