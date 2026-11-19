import asyncio
import importlib.util
import json
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace

import pytest

from pymentor_analytics.adapters.postgres.activity_projection import _valid_projection
from pymentor_analytics.adapters.postgres.core_transfer import PostgresCoreTransfer
from pymentor_analytics.app.activity_projection_runtime import ActivityProjectionRuntime
from pymentor_analytics.contracts.auth_v1 import VerifiedGrant
from pymentor_analytics.modules.analytics.activity.bucket import normalize_subject_bucket
from pymentor_analytics.modules.analytics.core_transfer.legacy_service import LegacyService
from pymentor_analytics.modules.analytics.activity.projection_ports import BucketClaim, StaleBucketClaim
from pymentor_analytics.modules.collector.ports import IntakeUnavailable

spec = importlib.util.spec_from_file_location("activity_fixture", Path(__file__).parents[1] / "reference/test_activity_v1.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def test_storage_checks_compact_and_edge_metadata_against_normalizer():
    subject = fixture.SUBJECT
    start = fixture.BASE
    facts = [fixture.event(start, start + timedelta(seconds=20), number=1, subject=subject),
             fixture.event(start + timedelta(seconds=5), start + timedelta(seconds=15), number=2,
                           course="another-course", subject=subject),
             fixture.event(start, start, number=3, subject=subject, opened=True)]
    claim = BucketClaim(subject, start, 1, 0, uuid4(), uuid4(), uuid4())
    output = normalize_subject_bucket(facts, subject_ref=subject, bucket_start=start)
    raw, metadata = _valid_projection(claim, output)
    decoded = json.loads(raw)
    compact = ("contributions", "presence_labels")
    edges = ("winner_intervals", "presence_intervals", "presence_points")
    expected_bytes = lambda keys: sum(len(json.dumps(decoded[k], ensure_ascii=False, sort_keys=True,
                                                     separators=(",", ":")).encode()) for k in keys)
    assert metadata == {"compact_count": sum(len(decoded[k]) for k in compact),
                        "edge_count": sum(len(decoded[k]) for k in edges),
                        "compact_bytes": expected_bytes(compact), "edge_bytes": expected_bytes(edges)}
    decoded["contributions"][0]["active_ms"] += 1
    with pytest.raises(ValueError, match="summary mismatch"):
        _valid_projection(claim, decoded)


class Ready:
    async def is_ready(self): return True


class Store:
    def __init__(self, *, fail_backfill=False, commit_error=None, invalid=False):
        self.claimed = BucketClaim(fixture.SUBJECT, fixture.BASE, 1, None, uuid4(), uuid4(), uuid4())
        self.fail_backfill, self.commit_error, self.invalid = fail_backfill, commit_error, invalid
        self.commits, self.failures = [], []

    async def backfill_step(self, *, deadline):
        if self.fail_backfill: raise RuntimeError("capacity")
        return "scanning"

    async def claim(self, owner, *, deadline): return self.claimed

    async def load(self, claim, *, deadline):
        if self.invalid: raise ValueError("invalid stored fact")
        return replace(claim, subject_epoch=0), (fixture.event(fixture.BASE, fixture.BASE + timedelta(seconds=15)),)

    async def commit(self, claim, projection, *, deadline):
        if self.commit_error: raise self.commit_error
        self.commits.append((claim, projection))

    async def fail(self, claim, reason, *, deadline): self.failures.append(reason)


@pytest.mark.parametrize("mode", ["success", "backfill_busy", "stale", "lost_reply", "invalid"])
def test_runtime_reconciles_leases_and_keeps_draining(mode):
    async def run():
        store = Store(fail_backfill=mode == "backfill_busy", invalid=mode == "invalid",
                      commit_error=StaleBucketClaim() if mode == "stale" else RuntimeError("unknown commit") if mode == "lost_reply" else None)
        runtime = ActivityProjectionRuntime(store, Ready(), enabled=True, operation_timeout=5)
        try:
            result = await runtime._cycle()
            assert result is (mode in {"success", "backfill_busy"})
            assert len(store.commits) == int(mode in {"success", "backfill_busy"})
            assert store.failures == (["invalid_fact"] if mode == "invalid" else [])
        finally:
            await runtime.stop()
    asyncio.run(run())


def test_idle_poll_is_bounded_after_300_seconds_and_failure_backoff_resets(monkeypatch):
    async def run():
        runtime = ActivityProjectionRuntime(Store(), Ready(), enabled=True)
        pauses = []
        outcomes = ["idle"] * 600 + ["work", "failure", "failure", "idle", "failure"]
        async def cycle():
            outcome = outcomes.pop(0)
            runtime._normal_idle = outcome == "idle"
            if not outcomes: runtime._stop.set()
            return outcome == "work"
        async def pause(seconds): pauses.append(seconds)
        runtime._cycle, runtime._pause = cycle, pause
        monkeypatch.setattr("pymentor_analytics.app.activity_projection_runtime.random.uniform", lambda a, b: 1)
        try:
            await runtime._run()
            assert pauses[:600] == [0.5] * 600
            assert sum(pauses[:600]) == 300
            assert pauses[600:] == [0.5, 1.0, 0.5, 0.5]
        finally:
            await runtime.stop()
    asyncio.run(run())


def test_stale_is_normal_idle_but_unready_and_backfill_errors_are_failures():
    async def run():
        store = Store(commit_error=StaleBucketClaim())
        runtime = ActivityProjectionRuntime(store, Ready())
        try:
            assert await runtime._cycle() is False and runtime._normal_idle
            async def no_claim(*args, **kwargs): return None
            store.claim = no_claim
            store.fail_backfill = True
            assert await runtime._cycle() is False and not runtime._normal_idle
            async def unready(): return False
            runtime.readiness.is_ready = unready
            assert await runtime._cycle() is False and not runtime._normal_idle
        finally: await runtime.stop()
    asyncio.run(run())


def test_legacy_bridge_exposes_browser_quality_separately_from_core_coverage():
    async def run():
        subject, admin = uuid4(), uuid4()
        now = fixture.BASE.isoformat()
        snapshot = {"subject_ref": str(subject), "coverage_through_revision": 4, "source_snapshot_at": now}
        class Store:
            database = SimpleNamespace(transaction_guard=object())
            browser_run_id = uuid4()
            async def read_subject(self, namespace, ref, today, **kwargs):
                assert ref == subject and kwargs["require_coverage"]
                return ([{"activity_date": today.isoformat(), "visited_at": None}], [], snapshot, now)
            async def read_admin(self, namespace, subjects, today, **kwargs):
                assert kwargs["required_revisions"] == {subject: 4, admin: 2}
                return ([{"subject_ref": str(subject), "activity_date": today.isoformat()}],
                        [snapshot, {"subject_ref": str(admin), "coverage_through_revision": 2, "source_snapshot_at": now}], now)
        service = LegacyService(Store(), "core", None, require_coverage=True)
        user = VerifiedGrant(sub=subject, role="user", scope="report:read", course_ids=("backend",), subject_refs=(),
                             global_read=False, expires_at=int(time.time())+60)
        operator = VerifiedGrant(sub=admin, role="admin", scope="report:read", course_ids=(), subject_refs=(),
                                 global_read=True, expires_at=int(time.time())+60,
                                 legacy_admin_roster_v1=((subject, 4, True), (admin, 2, False)))
        result = await service.read_summary(user, deadline=time.monotonic()+2)
        assert result["data"]["visited_today"] and result["coverage"] == "complete"
        assert result["browser_quality"] == "unknown" and result["browser_as_of"] == now
        result = await service.read_admin(operator, deadline=time.monotonic()+2)
        assert result["data"]["dashboard_activity"]["active_students_7d"] == 1
        assert result["coverage_subjects"][0]["coverage_through_revision"] == 4 and result["browser_as_of"] == now
    asyncio.run(run())


@pytest.mark.parametrize("case", ["ready", "wrong_run", "dirty", "overflow"])
def test_browser_days_requires_fresh_guard_generation_and_bounded_ready_snapshot(case):
    async def run():
        guards = []
        async def guard(_): guards.append(True)
        class Result:
            def __init__(self, value): self.value = value
            def scalar_one(self): return self.value
            def one(self): return self.value
        class Session:
            async def execute(self, statement, params):
                if "lock_activity_projection_run" in str(statement): return Result(case != "wrong_run")
                return Result(SimpleNamespace(n=300001 if case == "overflow" else 1,
                    bad=case == "dirty", day_count=1, days=[{"subject_ref": str(fixture.SUBJECT), "day": "2026-01-01"}],
                    edge_items=0, edge_bytes=0, as_of=fixture.BASE))
        adapter = PostgresCoreTransfer(SimpleNamespace(transaction_guard=guard), browser_run_id=uuid4())
        if case == "ready":
            result = await adapter._browser_days(Session(), [fixture.SUBJECT], end=fixture.BASE.date())
            assert result["days"][0]["day"] == "2026-01-01" and len(guards) == 2
        else:
            with pytest.raises(IntakeUnavailable):
                await adapter._browser_days(Session(), [fixture.SUBJECT], end=fixture.BASE.date())
            assert len(guards) == (1 if case == "wrong_run" else 2)
    asyncio.run(run())
