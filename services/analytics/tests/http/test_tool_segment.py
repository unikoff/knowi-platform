import json
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_intake import context, app_for, MemoryIntake, Catalog, Fence, auth, event
from pymentor_analytics.contracts.v2 import BrowserEvent


def tool(**changes):
    return {**event(), "type":"tool_segment", "lesson_id":None, "section":None, "catalog_version":"platform-tools-v1", "payload":{"tool_id":"account"}, "ended_at":"2026-11-16T10:00:01Z", "active_ms":1000, **changes}


def test_tool_intake_dedup_auth_catalog_and_fence(context):
    store=MemoryIntake()
    app=app_for(context, intake=store, catalog=Catalog(), fence=Fence())
    with TestClient(app) as client:
        path="/api/analytics/events/batch"
        item=tool()
        result=client.post(path, headers=auth(context[0]), json={"events":[item,item]})
        assert [x["status"] for x in result.json()["items"]] == ["accepted","duplicate"]
        denied=tool(course_id="platform-tools", event_id=str(uuid4()))
        assert client.post(path, headers=auth(context[0]), json={"events":[denied]}).status_code == 403
        assert client.post(path, headers=auth(context[0], course_ids=["platform-tools"]), json={"events":[denied]}).json()["items"][0]["status"] == "accepted"
    app=app_for(context, intake=MemoryIntake(), catalog=Catalog(), fence=Fence(False))
    with TestClient(app) as client:
        assert client.post(path, headers=auth(context[0]), json={"events":[tool()]}).status_code == 403


def test_retired_history_tab_is_rejected_before_storage(context):
    store = MemoryIntake()
    history = tool(payload={"tool_id": "account-history"})
    statistics = tool(event_id=str(uuid4()), payload={"tool_id": "account-statistics"})
    with TestClient(app_for(context, intake=store, catalog=Catalog(), fence=Fence())) as client:
        result = client.post("/api/analytics/events/batch", headers=auth(context[0]),
                             json={"events": [history, statistics]})

    assert result.status_code == 200
    assert [(item["status"], item["code"]) for item in result.json()["items"]] == [
        ("rejected", "invalid"), ("accepted", "committed"),
    ]
    assert len(store.records) == 1


@pytest.mark.parametrize("changes", [
    {"payload":{}}, {"payload":{"tool_id":"https://example.com"}}, {"payload":{"tool_id":"account","text":"secret"}},
    {"lesson_id":"lesson-1"}, {"section":"theory"}, {"catalog_version":"other"},
    {"active_ms":0}, {"type":"activity_segment", "payload":{"tool_id":"account"}},
])
def test_invalid_tools(changes):
    obj=tool(); obj.update(changes)
    with pytest.raises(ValidationError): BrowserEvent.model_validate_json(json.dumps(obj))


def test_reserved_course_for_legacy():
    obj=event(course_id="platform-tools")
    with pytest.raises(ValidationError): BrowserEvent.model_validate_json(json.dumps(obj))

def test_storage_tool_insert_and_duplicate_never_schedule_jobs():
    import asyncio, time
    from contextlib import asynccontextmanager
    from datetime import datetime, timezone, timedelta
    from uuid import uuid4
    from pymentor_analytics.adapters.postgres.intake import PostgresIntake
    from pymentor_analytics.contracts.v2 import AcceptedEvent, canonical_hash
    now=datetime.now(timezone.utc).replace(microsecond=0)
    raw=tool(started_at=now.isoformat(), ended_at=(now+timedelta(seconds=1)).isoformat(), last_action_at=now.isoformat(), event_id=str(uuid4()))
    record=AcceptedEvent(producer_namespace="browser", subject_ref=uuid4(), source="browser", event=BrowserEvent.model_validate_json(json.dumps(raw)), received_at=now)
    class Result:
        def __init__(self, value=None): self.value=value
        def scalar_one_or_none(self): return self.value
    class Session:
        prior=None
        inserts=[]
        async def execute(self, statement, params=None):
            sql=str(statement)
            if "SELECT canonical_hash" in sql: return Result(self.prior)
            if "INSERT INTO analytics.activity_facts" in sql:
                self.inserts.append(sql); self.prior=canonical_hash(record); return Result(self.prior)
            assert "processing_jobs" not in sql
            return Result()
    class DB:
        def __init__(self): self.session=Session()
        @asynccontextmanager
        async def transaction(self): yield self.session
    class Backpressure:
        async def check(self, session, **kwargs): assert kwargs["new_jobs"] == 0
    async def run():
        db=DB(); intake=PostgresIntake(db); intake.backpressure=Backpressure()
        assert await intake.accept(record, deadline=time.monotonic()+3) == "accepted"
        assert await intake.accept(record, deadline=time.monotonic()+3) == "duplicate"
        assert len(db.session.inserts)==1 and "browser_tool_v1" in db.session.inserts[0]
    asyncio.run(run())
