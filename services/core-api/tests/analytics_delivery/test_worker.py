import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from pymentor_core.adapters.sql.analytics_delivery import metadata, outbox, enqueue, claim
from pymentor_core.modules.analytics_delivery.contracts import activity, deletion
from pymentor_core.modules.analytics_delivery.relay import Receipt
from pymentor_core.adapters.sql.analytics_delivery_ops import worker

SUBJECT = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def test_worker_reconciles_lost_reply_without_resending(synthetic_engine, monkeypatch):
    engine = synthetic_engine
    metadata.create_all(engine)
    sessions = sessionmaker(engine)
    event = activity(SUBJECT, date(2026, 11, 17), None, revision=0)
    with sessions() as db:
        enqueue(db, event, datetime.now(timezone.utc))
        db.commit()
    monkeypatch.setattr(worker, "retry_delay", lambda *a, **kw: 0)
    class Transport:
        sends = 0
        async def send(self, event, deadline):
            self.sends += 1
            return Receipt.UNKNOWN
        async def lookup(self, event_id, fingerprint, deadline): return Receipt.ACCEPTED
    transport = Transport()
    assert asyncio.run(worker.run_once(sessions, transport)) == 1
    with sessions() as db:
        assert db.execute(select(outbox.c.status, outbox.c.uncertain)).one() == ("pending", True)
    assert asyncio.run(worker.run_once(sessions, transport)) == 1
    with sessions() as db:
        assert db.execute(select(outbox.c.status)).scalar_one() == "delivered"
    assert transport.sends == 1
    engine.dispose()


def test_delete_ack_requires_journal_and_applied_purge(synthetic_engine, monkeypatch):
    engine = synthetic_engine
    metadata.create_all(engine)
    sessions = sessionmaker(engine)
    with sessions() as db:
        enqueue(db, deletion(SUBJECT, revision=1), datetime.now(timezone.utc))
        db.commit()
    monkeypatch.setattr(worker, "retry_delay", lambda *a, **kw: 0)
    class Authority:
        async def append(self, event, created_at, timeout_seconds): return 1
    class Transport:
        status = Receipt.ABSENT
        async def deletion_status(self, event, deadline): return self.status
        async def send(self, *args): raise AssertionError("deletion must never use ordinary transfer")
    transport = Transport()
    asyncio.run(worker.run_once(sessions, transport, authority=Authority()))
    with sessions() as db:
        assert db.execute(select(outbox.c.status)).scalar_one() == "pending"
    transport.status = Receipt.ACCEPTED
    asyncio.run(worker.run_once(sessions, transport, authority=Authority()))
    with sessions() as db:
        assert db.execute(select(outbox.c.status)).scalar_one() == "delivered"
    engine.dispose()


def test_cancelled_worker_leaves_uncertain_lease_recoverable(synthetic_engine):
    async def scenario():
        engine = synthetic_engine
        metadata.create_all(engine)
        sessions = sessionmaker(engine)
        event = activity(SUBJECT, date(2026, 11, 17), None, revision=0)
        with sessions() as db:
            enqueue(db, event, datetime.now(timezone.utc))
            db.commit()
        started = asyncio.Event()
        class Transport:
            async def send(self, event, deadline):
                started.set()
                await asyncio.Event().wait()
        task = asyncio.create_task(worker.run_once(sessions, Transport(), deadline_seconds=1, lease_seconds=2))
        await started.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        with sessions() as db:
            assert db.execute(select(outbox.c.status, outbox.c.uncertain)).one() == ("claimed", True)
            recovered = claim(db, datetime.now(timezone.utc) + timedelta(seconds=3), limit=1, lease_seconds=2, max_attempts=5)
            assert recovered[0]["event_id"] == event["event_id"] and recovered[0]["uncertain"] is True
        engine.dispose()
    asyncio.run(scenario())
