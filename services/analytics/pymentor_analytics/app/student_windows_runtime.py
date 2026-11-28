"""Lifecycle hook for the Analytics student-window reconciliation worker."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
import logging
from typing import Protocol

from sqlalchemy.exc import DBAPIError

from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.windows_v1 import WindowKey
from pymentor_analytics.modules.analytics.activity.student_windows_reconcile import CoverageEvidence
from pymentor_analytics.modules.analytics.activity.student_windows_worker import WindowReconcileWorker, WorkerBatch

logger = logging.getLogger(__name__)


class Readiness(Protocol):
    async def is_ready(self) -> bool: ...


@dataclass(slots=True)
class ReconciliationMetrics:
    keys: int = 0
    coverage_unknown: int = 0
    mismatch_before: int = 0
    mismatch_after: int = 0
    repairs: int = 0
    failures: int = 0
    pending_age_ms: int = 0


class StudentWindowsReconciliationRuntime:
    def __init__(
        self, *, worker: WindowReconcileWorker, readiness: Readiness, enabled: bool = False,
        poll_seconds: float = 1.0, trusted_evidence: Mapping[WindowKey, CoverageEvidence] | None = None,
    ) -> None:
        if type(enabled) is not bool or not 0.1 <= poll_seconds <= 60:
            raise ValueError("invalid student window reconciliation runtime")
        self.worker = worker
        self.readiness = readiness
        self.enabled = enabled
        self.poll_seconds = poll_seconds
        self._trusted_evidence = dict(trusted_evidence or {})
        if any(not evidence.managed_scenario or evidence.manifest_expected is None
               or not evidence.v2_source_complete or evidence.old_web_only
               for evidence in self._trusted_evidence.values()):
            raise ValueError('runtime requires independent trusted coverage evidence')
        self.metrics = ReconciliationMetrics()
        self._task: asyncio.Task[None] | None = None
        self._started = False

    def start(self) -> None:
        if self._started:
            raise RuntimeError("student window reconciliation runtime already started")
        self._started = True
        if self.enabled:
            self._task = asyncio.create_task(self._run(), name="analytics-student-windows")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def _count(self, batch: WorkerBatch) -> None:
        self.metrics.pending_age_ms = max((item.age_ms for item in batch.windows), default=0)
        self.metrics.failures += int(batch.timed_out or batch.queue_unavailable)
        for item in batch.windows:
            self.metrics.keys += 1
            self.metrics.failures += int(item.status in {"unavailable", "overflow"})
            comparison = item.comparison
            if comparison is None or comparison.decision.expected_stats is None:
                self.metrics.coverage_unknown += 1
            if comparison is not None and comparison.decision.expected_stats is not None:
                self.metrics.mismatch_before += int(bool(comparison.before.differences))
                if item.after is not None:
                    self.metrics.mismatch_after += int(bool(item.after.differences))
                    self.metrics.repairs += int(item.after.revision != comparison.before.revision)

    async def _run(self) -> None:
        checked = False
        while True:
            try:
                if await self.readiness.is_ready() is True:
                    self._count(await self.worker.run_once(repair=True, evidence=self._trusted_evidence))
                    checked = False
            except (DBAPIError, ConnectionError, TimeoutError, PrivacyUnavailable):
                self.metrics.failures += 1
                if not checked:
                    logger.warning("Student window reconciliation temporarily unavailable")
                    checked = True
            await asyncio.sleep(self.poll_seconds)
