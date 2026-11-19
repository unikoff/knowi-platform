import asyncio
from collections import deque
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.factory import create_app
from pymentor_analytics.app.processor import ProcessorRuntime
from pymentor_analytics.contracts.processing_v1 import ClaimedBatch, ClaimedFact, StaleClaim, synthetic_delta
from pymentor_analytics.contracts.v2 import AcceptedEvent, BrowserEvent

URL = "postgresql://analytics_user@localhost:57392/analytics_test"
FIXTURE = Path(__file__).resolve().parents[2] / "contracts/fixtures/browser-batch.json"
SUBJECT = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def batch(consumer, *, already_applied=False):
    item = json.loads(FIXTURE.read_text(encoding="utf-8"))["events"][0]
    item["event_id"] = str(uuid4())
    event = BrowserEvent.model_validate_json(json.dumps(item).encode())
    record = AcceptedEvent(producer_namespace=f"browser:{SUBJECT}", subject_ref=SUBJECT, source="browser", event=event, received_at=datetime.now(timezone.utc))
    return ClaimedBatch(consumer=consumer, version=1, subject_ref=SUBJECT, subject_epoch=1,
                        worker=uuid4(), generation=1, batch_id=uuid4(), facts=(ClaimedFact(record, already_applied),))


def config(**kwargs):
    return Settings(database_url=URL, processor_poll_min_seconds=0.05, processor_poll_max_seconds=0.1,
                    processor_job_timeout_seconds=0.2, db_timeout_seconds=0.1, shutdown_timeout_seconds=1, **kwargs)


class Ready:
    def __init__(self, value=True): self.value = value
    async def is_ready(self): return self.value


class FakeStore:
    """In-memory loop probe, never evidence of DB durability."""
    def __init__(self, jobs=()):
        self.jobs = {name: deque(x for x in jobs if x.consumer == name) for name in ("synthetic.activity", "synthetic.independent")}
        self.claims = []; self.commits = []; self.failures = []; self.releases = []; self.renews = []
        self.changed = asyncio.Event()
    async def claim(self, consumer, version, worker, limit, lease_seconds):
        self.claims.append((consumer, version, worker, limit, lease_seconds))
        return replace(self.jobs[consumer].popleft(), worker=worker) if self.jobs[consumer] else None
    async def commit(self, work, effect):
        self.commits.append((work, effect)); self.changed.set()
    async def fail(self, work):
        self.failures.append(work); self.changed.set(); return "retry"
    async def release(self, work):
        self.releases.append(work); self.changed.set(); return True
    async def renew(self, work, seconds):
        self.renews.append(work); return True


async def until(predicate, timeout=2):
    async with asyncio.timeout(timeout):
        while not predicate(): await asyncio.sleep(0.01)


def test_synthetic_delta_is_deterministic_bounded_and_not_a_time_kpi():
    first = batch("synthetic.activity")
    ignored = batch("synthetic.activity", already_applied=True)
    combined = first.facts + ignored.facts
    one = synthetic_delta(combined)
    two = synthetic_delta(tuple(reversed(combined)))
    assert one == two and one.course_counts == {"backend": 1} and len(one.fingerprint_xor) == 64
    assert synthetic_delta(ignored.facts).course_counts == {}


def test_closed_privacy_gate_blocks_claim_and_only_processor_has_loop():
    class CountingStore:
        async def claim(self, *args): raise AssertionError("closed readiness must not claim")
    store = CountingStore()
    for role in ("collector", "processor", "query"):
        app = create_app(role, config(), processor_store=store)
        with TestClient(app) as client:
            assert client.get("/live").status_code == 200 and client.get("/ready").status_code == 503
            assert hasattr(app.state, "processor_runtime") == (role == "processor")
            time.sleep(0.1)
        assert not hasattr(app.state, "processor_runtime")


def test_two_independent_consumers_and_slow_failure_isolated():
    async def scenario():
        jobs = (batch("synthetic.activity"), batch("synthetic.independent"))
        store = FakeStore(jobs)
        def handler(work):
            assert threading.current_thread().name.startswith("analytics-synthetic")
            if work.consumer == "synthetic.activity": raise ValueError("poison")
            return synthetic_delta(work.facts)
        runtime = ProcessorRuntime(store, Ready(), config(), handler=handler)
        runtime.start()
        try:
            await until(lambda: bool(store.commits) and bool(store.failures))
            assert [x.consumer for x, _ in store.commits] == ["synthetic.independent"]
            assert store.failures[0].consumer == "synthetic.activity"
            assert all(version == 1 and limit == 100 and lease == 30 for _, version, _, limit, lease in store.claims)
        finally:
            await runtime.stop()
    asyncio.run(scenario())


def test_readiness_change_before_commit_releases_without_ack():
    async def scenario():
        ready, store = Ready(), FakeStore((batch("synthetic.activity"),))
        def handler(work):
            ready.value = False
            return synthetic_delta(work.facts)
        runtime = ProcessorRuntime(store, ready, config(), handler=handler)
        runtime.start()
        try:
            await until(lambda: bool(store.releases))
            assert not store.commits and not store.failures
        finally:
            await runtime.stop()
    asyncio.run(scenario())


def test_stale_commit_never_becomes_success_or_release():
    class StaleStore(FakeStore):
        async def commit(self, work, effect):
            self.changed.set(); raise StaleClaim()
    async def scenario():
        store = StaleStore((batch("synthetic.activity"),))
        runtime = ProcessorRuntime(store, Ready(), config())
        runtime.start()
        try:
            await asyncio.wait_for(store.changed.wait(), 2)
            await asyncio.sleep(0.05)
            assert not store.commits and not store.releases and not store.failures
        finally:
            await runtime.stop()
    asyncio.run(scenario())


def test_cancel_during_commit_leaves_unknown_outcome_for_lease_expiry():
    class LostReplyStore(FakeStore):
        def __init__(self, jobs): super().__init__(jobs); self.started = asyncio.Event()
        async def commit(self, work, effect):
            self.started.set()
            await asyncio.Event().wait()
    async def scenario():
        store = LostReplyStore((batch("synthetic.activity"),))
        runtime = ProcessorRuntime(store, Ready(), config())
        runtime.start()
        await asyncio.wait_for(store.started.wait(), 2)
        await asyncio.sleep(0.3)  # job deadline cancels ambiguous commit
        await runtime.stop()
        assert not store.releases and not store.failures and not store.commits
    asyncio.run(scenario())


def test_timed_out_thread_keeps_capacity_and_other_consumer_can_progress():
    class RepeatStore(FakeStore):
        async def claim(self, consumer, version, worker, limit, lease_seconds):
            if consumer == "synthetic.activity":
                self.claims.append((consumer, version, worker, limit, lease_seconds))
                return replace(batch(consumer), worker=worker)
            return await super().claim(consumer, version, worker, limit, lease_seconds)
    async def scenario():
        store = RepeatStore((batch("synthetic.independent"),))
        lock = threading.Lock(); current = 0; peak = 0
        def handler(work):
            nonlocal current, peak
            if work.consumer == "synthetic.activity":
                with lock:
                    current += 1; peak = max(peak, current)
                try: time.sleep(0.35)
                finally:
                    with lock: current -= 1
            return synthetic_delta(work.facts)
        runtime = ProcessorRuntime(store, Ready(), config(), handler=handler)
        runtime.start()
        try:
            await until(lambda: bool(store.failures) and bool(store.commits))
            await asyncio.sleep(0.1)
            assert peak == 1 and any(work.consumer == "synthetic.independent" for work, _ in store.commits)
        finally:
            await runtime.stop()
        await asyncio.sleep(0.2)  # bounded test handler can finish its thread
    asyncio.run(scenario())


def test_cancel_during_compute_releases_known_claim_without_ack():
    async def scenario():
        store = FakeStore((batch("synthetic.activity"),))
        started = threading.Event(); finish = threading.Event()
        def handler(work):
            started.set(); finish.wait(1)
            return synthetic_delta(work.facts)
        runtime = ProcessorRuntime(store, Ready(), config(), handler=handler)
        runtime.start()
        try:
            await until(started.is_set)
            task = runtime._tasks[0]
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert len(store.releases) == 1 and not store.commits
        finally:
            finish.set()
            await runtime.stop()
    asyncio.run(scenario())


def test_lease_loss_during_compute_prevents_commit(monkeypatch):
    import pymentor_analytics.app.processor as processor
    monkeypatch.setattr(processor, "LEASE_SECONDS", 0.06)
    class LostLease(FakeStore):
        async def renew(self, work, seconds):
            self.renews.append(work)
            return False
    async def scenario():
        store = LostLease((batch("synthetic.activity"),))
        def handler(work):
            time.sleep(0.12)
            return synthetic_delta(work.facts)
        runtime = ProcessorRuntime(store, Ready(), config(), handler=handler)
        runtime.start()
        try:
            await until(lambda: bool(store.releases))
            assert store.renews and not store.commits
        finally:
            await runtime.stop()
    asyncio.run(scenario())


def test_shutdown_drains_inflight_commit_without_starting_new_work():
    class DrainStore(FakeStore):
        def __init__(self, jobs):
            super().__init__(jobs); self.started = asyncio.Event(); self.finish = asyncio.Event()
        async def commit(self, work, effect):
            self.started.set(); await self.finish.wait()
            await super().commit(work, effect)
    async def scenario():
        store = DrainStore((batch("synthetic.activity"),))
        runtime = ProcessorRuntime(store, Ready(), config())
        runtime.start()
        await asyncio.wait_for(store.started.wait(), 2)
        stopping = asyncio.create_task(runtime.stop())
        await asyncio.sleep(0.02)
        claim_count = len(store.claims)
        store.finish.set()
        await asyncio.wait_for(stopping, 2)
        assert len(store.commits) == 1 and len(store.claims) == claim_count
    asyncio.run(scenario())


def test_uncooperative_port_has_bounded_shutdown_and_no_false_ack():
    class StuckStore(FakeStore):
        def __init__(self, jobs):
            super().__init__(jobs); self.started = asyncio.Event(); self.finish = asyncio.Event()
        async def commit(self, work, effect):
            self.started.set()
            while not self.finish.is_set():
                try:
                    await self.finish.wait()
                except asyncio.CancelledError:
                    pass  # deliberately broken test adapter
    async def scenario():
        store = StuckStore((batch("synthetic.activity"),))
        runtime = ProcessorRuntime(store, Ready(), config())
        runtime.start()
        tasks = list(runtime._tasks)
        await asyncio.wait_for(store.started.wait(), 2)
        before = time.monotonic()
        try:
            try:
                await runtime.stop()
                raise AssertionError("uncooperative port must not appear to drain")
            except RuntimeError as exc:
                assert str(exc) == "processor shutdown timed out"
            assert time.monotonic() - before < 2.7
            assert not store.commits and not store.releases
        finally:
            store.finish.set()
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 1)
    asyncio.run(scenario())
