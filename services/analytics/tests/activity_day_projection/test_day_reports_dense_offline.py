import asyncio
from uuid import uuid4

import pytest

import test_day_reports_dense_pg as sample


class Monitor:
    def __init__(self): self.calls=0
    def check(self): self.calls+=1


def state(run, phase, *, ready=0, sources=2, payloads=0):
    return {"run_id":run,"phase":phase,"cursor_subject":None,"cursor_bucket":None,"cursor_metric":None,
            "sources":sources,"ready":ready,"dirty":sources-ready,"claimed":0,"quarantined":0,"payloads":payloads}


def test_bounded_runner_observes_complete_and_stops_runtime(monkeypatch):
    run_id=uuid4(); instances=[]
    class Runtime:
        def __init__(self,*args,**kwargs):
            self.completed=2; self.failures=0; self.backfill_failures=0; self.stopped=False; instances.append(self)
        async def _cycle(self): return False
        async def stop(self): self.stopped=True
    states=iter((state(None,"scanning"),state(run_id,"complete",ready=2,payloads=2)))
    async def backlog(_): return next(states)
    monkeypatch.setattr(sample,"ActivityDayProjectionRuntime",Runtime)
    monkeypatch.setattr(sample,"_day_backlog",backlog)
    monitor=Monitor()
    result=asyncio.run(sample._build_day_projection(object(),run_id,monitor,max_cycles=4,deadline_seconds=1))
    assert result["cycles"]==1 and result["completed"]==2 and result["final"]["ready"]==2
    assert monitor.calls>=2 and instances[0].stopped


@pytest.mark.parametrize("mode", ["cycle_limit", "runtime_failure", "deadline"])
def test_bounded_runner_stops_on_failure_and_never_fabricates_complete(monkeypatch, mode):
    run_id=uuid4(); instances=[]
    class Runtime:
        def __init__(self,*args,**kwargs):
            self.completed=0; self.failures=0; self.backfill_failures=0; self.stopped=False; instances.append(self)
        async def _cycle(self):
            if mode=="runtime_failure": self.failures=1
            if mode=="deadline": await asyncio.sleep(.05)
            return True
        async def stop(self): self.stopped=True
    async def backlog(_): return state(None,"scanning")
    monkeypatch.setattr(sample,"ActivityDayProjectionRuntime",Runtime)
    monkeypatch.setattr(sample,"_day_backlog",backlog)
    error=TimeoutError if mode=="deadline" else RuntimeError
    with pytest.raises(error):
        asyncio.run(sample._build_day_projection(object(),run_id,Monitor(),max_cycles=2,
                                                 deadline_seconds=.001 if mode=="deadline" else 1))
    assert instances[0].stopped


def test_build_rejects_existing_projection_and_stops_runtime(monkeypatch):
    run_id=uuid4(); instances=[]
    class Runtime:
        def __init__(self,*args,**kwargs):
            self.completed=0; self.failures=0; self.backfill_failures=0; self.stopped=False; instances.append(self)
        async def _cycle(self): pytest.fail("must not build an already seeded day projection")
        async def stop(self): self.stopped=True
    async def backlog(_): return state(None,"scanning",ready=1,payloads=1)
    monkeypatch.setattr(sample,"ActivityDayProjectionRuntime",Runtime)
    monkeypatch.setattr(sample,"_day_backlog",backlog)
    with pytest.raises(RuntimeError,match="not fresh"):
        asyncio.run(sample._build_day_projection(object(),run_id,Monitor(),deadline_seconds=1))
    assert instances[0].stopped
