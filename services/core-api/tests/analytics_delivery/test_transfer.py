import asyncio
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import Session

from pymentor_core.adapters.sql.analytics_delivery import (
    metadata, checkpoint, outbox, enqueue, start_checkpoint, stage_page, scan_page,
    claim, mark_sending, finish, next_revision,
)
from pymentor_core.modules.analytics_delivery.contracts import activity, progress, deletion, fingerprint
from pymentor_core.modules.analytics_delivery.relay import deliver, Receipt, retry_delay
from pymentor_core.modules.identity.activity_policy import summarize_activity, serialize_activity_timestamp

SUBJECT = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)


@pytest.fixture
def db(synthetic_engine):
    engine = synthetic_engine
    metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_dto_lifecycle_and_conflict(db):
    first = activity(SUBJECT, date(2026, 11, 17), None, revision=0)
    assert first == activity(SUBJECT, date(2026, 11, 17), None, revision=0)
    assert first["payload"] == {"activity_date": "2026-11-17", "visited_at": None, "provenance": "legacy_global"}
    assert first["event_id"] != activity(UUID(int=2), date(2026, 11, 17), None, revision=0)["event_id"]
    assert enqueue(db, first, NOW)
    assert not enqueue(db, first, NOW)
    with pytest.raises(ValueError, match="revision conflict"):
        enqueue(db, activity(SUBJECT, date(2026, 11, 17), NOW, revision=0), NOW)
    assert enqueue(db, activity(SUBJECT, date(2026, 11, 17), NOW, revision=1), NOW)
    assert progress(SUBJECT, "backend", "lesson-1", None, revision=2)["payload"]["completed_at"] is None
    assert deletion(SUBJECT, revision=3)["payload"] == {}
    with pytest.raises(TypeError):
        activity(8, date(2026, 11, 17), None, revision=0)
    forged = dict(first, event_id=str(UUID(int=1)))
    with pytest.raises(ValueError, match="invalid transfer event"):
        enqueue(db, forged, NOW)
    assert next_revision(db, SUBJECT) == 1
    assert next_revision(db, SUBJECT) == 2


def test_atomic_business_rollback_and_bounded_checkpoint(db):
    db.execute(text("CREATE TABLE business (id INTEGER PRIMARY KEY)"))
    db.commit()
    db.execute(text("INSERT INTO business VALUES (1)"))
    event = activity(SUBJECT, date(2026, 11, 17), None, revision=0)
    enqueue(db, event, NOW)
    db.rollback()
    assert db.execute(text("SELECT count(*) FROM business")).scalar_one() == 0
    assert db.execute(select(outbox.c.event_id)).all() == []
    start_checkpoint(db, "activity", 10)
    with pytest.raises(ValueError):
        stage_page(db, "activity", [(4, event), (2, event)], NOW, limit=2)
    assert stage_page(db, "activity", [(4, event)], NOW, limit=2) == 4
    assert db.execute(select(checkpoint.c.cursor)).scalar_one() == 4
    db.rollback()
    assert db.execute(select(checkpoint)).all() == []
    assert db.execute(select(outbox)).all() == []


def test_keyset_read_fails_closed_without_lifecycle(db):
    db.execute(text("CREATE TABLE account_lifecycles(user_id INTEGER, lifecycle_id TEXT)"))
    db.execute(text("CREATE TABLE user_activity_days(id INTEGER, user_id INTEGER, activity_date DATE, visited_at TIMESTAMP WITH TIME ZONE)"))
    db.execute(text("INSERT INTO account_lifecycles VALUES (1, :ref)"), {"ref": str(SUBJECT)})
    db.execute(text("INSERT INTO user_activity_days VALUES (1,1,'2026-11-16',NULL), (2,2,'2026-11-17',NULL)"))
    first = scan_page(db, "activity", 0, 2, limit=1)
    assert first[0][1]["subject_ref"] == str(SUBJECT)
    with pytest.raises(ValueError, match="mapping missing"):
        scan_page(db, "activity", 1, 2, limit=1)


def test_lost_reply_reconciliation_and_expired_lease(db):
    event = activity(SUBJECT, date(2026, 11, 17), None, revision=0)
    enqueue(db, event, NOW)
    db.commit()
    first = claim(db, NOW, limit=1, lease_seconds=5, max_attempts=5)[0]
    mark_sending(db, event["event_id"], first["claim_token"])
    db.commit()  # crash after send; only this uncertainty flag survives
    second = claim(db, NOW + timedelta(seconds=6), limit=1, lease_seconds=5, max_attempts=5)[0]
    assert second["uncertain"] is True
    with pytest.raises(ValueError, match="stale claim"):
        finish(db, event["event_id"], first["claim_token"], "ack", NOW)

    class Receiver:
        async def lookup(self, event_id, digest, deadline_seconds):
            assert (event_id, digest) == (event["event_id"], fingerprint(event))
            return Receipt.ACCEPTED
        async def send(self, event, deadline_seconds):
            raise AssertionError("accepted event must not be sent again")

    decision = asyncio.run(deliver(Receiver(), event, second["fingerprint"], uncertain=True, deadline_seconds=1))
    finish(db, event["event_id"], second["claim_token"], decision.action, NOW)
    db.commit()
    assert db.execute(select(outbox.c.status)).scalar_one() == "delivered"


def test_unknown_reconciliation_never_resends():
    event = deletion(SUBJECT, revision=1)
    class Receiver:
        async def lookup(self, *args): return Receipt.UNKNOWN
        async def send(self, *args): raise AssertionError("unknown outcome cannot be replayed")
    assert asyncio.run(deliver(Receiver(), event, fingerprint(event), uncertain=True, deadline_seconds=1)).action == "reconcile"
    assert retry_delay(10, 0.5, cap_seconds=60) == 30


def test_single_deadline_and_conflict_stop_replay():
    event = activity(SUBJECT, date(2026, 11, 17), None, revision=1)
    class Receiver:
        async def lookup(self, *args): return Receipt.ABSENT
        async def send(self, *args): raise AssertionError("overall deadline expired")
    ticks = iter([0.0, 0.1, 2.0])
    assert asyncio.run(deliver(Receiver(), event, fingerprint(event), uncertain=True, deadline_seconds=1,
                               clock=lambda: next(ticks))).action == "retry"
    class Conflict(Receiver):
        async def lookup(self, *args): return Receipt.CONFLICT
    assert asyncio.run(deliver(Conflict(), event, fingerprint(event), uncertain=True, deadline_seconds=1)).action == "quarantine"


def test_revision_allocation_rolls_back_with_source(db):
    db.execute(text("CREATE TABLE business (id INTEGER PRIMARY KEY)"))
    db.commit()
    db.execute(text("INSERT INTO business VALUES (1)"))
    assert next_revision(db, SUBJECT) == 1
    enqueue(db, activity(SUBJECT, date(2026, 11, 17), NOW, revision=1), NOW)
    db.rollback()
    assert next_revision(db, SUBJECT) == 1
    assert db.execute(select(outbox.c.event_id)).all() == []


def test_core_summary_parity_fixture():
    fixture = json.loads((Path(__file__).parents[2] / "contracts/analytics-transfer-parity-fixtures.json").read_text(encoding="utf-8"))
    today = date.fromisoformat(fixture["today"])
    visits = {date.fromisoformat(row["activity_date"]) for row in fixture["subject_visits"]}
    completions = [(datetime.fromisoformat(value),) for value in fixture["completed_at"]]
    result = summarize_activity(today, visits, completions)
    assert result == fixture["summary"]
    calendar = {"days": [r["activity_date"] for r in fixture["subject_visits"]], "visits": [
        {"activity_date": r["activity_date"], "visited_at": serialize_activity_timestamp(
            datetime.fromisoformat(r["visited_at"]) if r["visited_at"] else None)} for r in fixture["subject_visits"]]}
    assert calendar == fixture["calendar"]
    # Admin fixture records the two distinct Core policies, and awaits integration parity.
    case = fixture["admin_case"]
    assert len({r["subject"] for r in case["subjects"]}) == case["platform_activity_today"]["unique_users"]
    assert sum(not r["is_admin"] for r in case["subjects"]) == case["legacy_dashboard_active_students_7d"]
