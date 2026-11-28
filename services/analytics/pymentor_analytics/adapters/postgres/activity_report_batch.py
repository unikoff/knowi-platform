"""Group only concurrently waiting self reports; completed results are never reused."""
import asyncio
from collections import deque
from dataclasses import dataclass

from pymentor_analytics.contracts.auth_v1 import VerifiedGrant
from pymentor_analytics.contracts.reports_v1 import ActivityQuery
from pymentor_analytics.modules.analytics.reports.activity_scope import ReportScope

_MAX_BATCH = 8
_MAX_QUEUED = 64
READ_SINGLE = object()


@dataclass(slots=True)
class SelfReportRead:
    grant: VerifiedGrant
    query: ActivityQuery
    scope: ReportScope
    deadline: float
    future: asyncio.Future

    @property
    def key(self):
        query = self.query
        return (query.from_time, query.to_time, query.course_id, query.timezone)


class SelfReportQueue:
    def __init__(self, read, *, concurrency: int):
        self.read = read
        self.concurrency = concurrency
        self.pending = deque()
        self.running = {}
        self.scheduled = None
        self.closed = False

    async def submit(self, grant, query, scope, deadline):
        if self.closed:
            raise TimeoutError()
        if len(self.pending) >= _MAX_QUEUED:
            return READ_SINGLE
        future = asyncio.get_running_loop().create_future()
        job = SelfReportRead(grant, query, scope, deadline, future)
        self.pending.append(job)
        self._schedule()
        try:
            result = await future
            if isinstance(result, Exception):
                raise result
            return result
        finally:
            if future.cancelled():
                self._cancel_waiter(job)

    def _cancel_waiter(self, job):
        self.pending = deque(item for item in self.pending if item is not job)
        for task, jobs in self.running.items():
            if any(item is job for item in jobs) and all(item.future.done() for item in jobs):
                task.cancel()
                break

    def _schedule(self):
        if (not self.closed and self.scheduled is None and self.pending
                and len(self.running) < self.concurrency):
            self.scheduled = asyncio.get_running_loop().call_soon(self._dispatch)

    def _dispatch(self):
        self.scheduled = None
        while self.pending and len(self.running) < self.concurrency:
            first = self.pending.popleft()
            if first.future.done():
                continue
            jobs = [first]
            outstanding = deque()
            for job in self.pending:
                if job.future.done():
                    continue
                if len(jobs) < _MAX_BATCH and job.key == first.key:
                    jobs.append(job)
                else:
                    outstanding.append(job)
            self.pending = outstanding
            # Membership cannot grow once this operation is scheduled.
            batch = tuple(jobs)
            task = asyncio.create_task(self.read(batch))
            self.running[task] = batch
            task.add_done_callback(self._finished)

    def _finished(self, task):
        jobs = self.running.pop(task)
        failure = TimeoutError() if task.cancelled() else task.exception()
        results = (failure,) * len(jobs) if failure is not None else task.result()
        for job, result in zip(jobs, results, strict=True):
            if not job.future.done():
                job.future.set_result(result)
        self._schedule()

    async def close(self):
        self.closed = True
        if self.scheduled is not None:
            self.scheduled.cancel()
            self.scheduled = None
        while self.pending:
            job = self.pending.popleft()
            if not job.future.done():
                job.future.set_result(TimeoutError())
        running = tuple(self.running)
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)

