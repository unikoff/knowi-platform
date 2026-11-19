import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from uuid import uuid4
import pytest

from pymentor_analytics.adapters.postgres.intake import PostgresIntake
from pymentor_analytics.contracts.v2 import AcceptedEvent, parse_browser_item
from pymentor_analytics.contracts.work_v1 import SYNTHETIC_SUBSCRIPTIONS


def item():
    root = Path(__file__).resolve().parents[2]
    raw = json.loads((root / "contracts/fixtures/browser-batch.json").read_text(encoding="utf-8"))["events"][0]
    now = datetime.now(timezone.utc)
    raw.update(event_id=str(uuid4()), started_at=now.isoformat(), ended_at=now.isoformat(), last_action_at=now.isoformat())
    return AcceptedEvent(producer_namespace="browser", subject_ref=uuid4(), source="browser",
        event=parse_browser_item(json.dumps(raw).encode()), received_at=now)


@pytest.mark.parametrize("local", [False, True])
def test_intake_one_fact_with_mode_specific_job_counts(local):
    class Result:
        def __init__(self, value=None): self.value = value
        def scalar_one_or_none(self): return self.value
    class Session:
        facts = 0
        jobs = []
        async def execute(self, statement, params=None):
            sql = str(statement)
            if "INSERT INTO analytics.activity_facts" in sql:
                self.facts += 1
                return Result("inserted-hash")
            if "INSERT INTO analytics.processing_jobs" in sql:
                self.jobs.append((params["consumer"], params["version"]))
            return Result()
    class Database:
        def __init__(self): self.session = Session()
        @asynccontextmanager
        async def transaction(self): yield self.session
    class Backpressure:
        new_jobs = None
        async def check(self, session, **kwargs): self.new_jobs = kwargs["new_jobs"]
    async def run():
        db = Database()
        intake = PostgresIntake(db, subscriptions=()) if local else PostgresIntake(db)
        intake.backpressure = Backpressure()
        assert await intake.accept(item(), deadline=time.monotonic() + 3) == "accepted"
        assert db.session.facts == 1
        assert intake.backpressure.new_jobs == (0 if local else 2)
        assert db.session.jobs == ([] if local else [(s.consumer, s.version) for s in SYNTHETIC_SUBSCRIPTIONS])
    asyncio.run(run())


def test_intake_rejects_unknown_or_duplicate_subscriptions():
    with pytest.raises(ValueError): PostgresIntake(object(), subscriptions=(SYNTHETIC_SUBSCRIPTIONS[0],) * 2)
    with pytest.raises(ValueError): PostgresIntake(object(), subscriptions=("unknown",))
