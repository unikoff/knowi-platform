import asyncio
from datetime import date, datetime, timezone
import os
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://synthetic@127.0.0.1:1/isolated")

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import sessionmaker

from pymentor_core.adapters.sql import models
from pymentor_core.adapters.sql.analytics_delivery import metadata, outbox, write_gate
from pymentor_core.adapters.sql.analytics_delivery_ops import export, snapshot_export, snapshot_relay
from pymentor_core.modules.analytics_delivery.relay import Receipt
from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_sql import metadata as proof_metadata, proofs

REF = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


@pytest.fixture
def sessions(synthetic_engine, monkeypatch):
    monkeypatch.setenv("PYMENTOR_ANALYTICS_DELIVERY_ENABLED", "1")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED", "1")
    monkeypatch.setattr(snapshot_relay, "retry_delay", lambda *args, **kwargs: 0)
    engine = synthetic_engine
    for model in (models.User, models.UserActivityDay, models.UserLessonProgress):
        model.__table__.create(engine)
    metadata.create_all(engine)
    proof_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE account_lifecycles(user_id INTEGER PRIMARY KEY,lifecycle_id VARCHAR(36) UNIQUE NOT NULL)"))
        conn.execute(text("INSERT INTO users(id,username,email,password_hash,is_admin,verified) VALUES(1,'synthetic','s@example.test','hash',false,true)"))
        conn.execute(text("INSERT INTO account_lifecycles VALUES(1,:ref)"), {"ref": REF})
        conn.execute(write_gate.insert().values(singleton=1, mode="mirror"))
    factory = sessionmaker(engine)
    with factory() as db:
        db.add(models.UserActivityDay(user_id=1, activity_date=date(2026, 11, 15), visited_at=None))
        db.commit()
        for stream in ("activity", "progress"):
            export.initialize(db, stream)
            export.export_page(db, stream)
            export.seal_backfill(db, stream)
        db.commit()
        proof = snapshot_export.stage_subject(db, 1, REF)
        db.commit()
    yield factory, proof
    engine.dispose()


def test_raw_delivery_precedes_proof_and_lost_reply_reconciles(sessions):
    factory, proof = sessions
    class Transport:
        sends = 0
        lookups = 0
        async def send_proof(self, item, remaining):
            self.sends += 1
            assert item == proof
            return Receipt.UNKNOWN
        async def lookup_proof(self, proof_id, digest, remaining):
            self.lookups += 1
            assert proof_id == proof["proof_id"]
            return Receipt.ACCEPTED, "pending" if self.lookups == 1 else "verified"
    transport = Transport()
    assert asyncio.run(snapshot_relay.run_once(factory, transport)) == 0
    with factory.begin() as db:
        db.execute(update(outbox).values(status="delivered"))
    assert asyncio.run(snapshot_relay.run_once(factory, transport)) == 1
    with factory() as db:
        assert db.execute(select(proofs.c.status, proofs.c.uncertain)).one() == ("pending", True)
    assert asyncio.run(snapshot_relay.run_once(factory, transport)) == 1
    with factory() as db:
        assert db.execute(select(proofs.c.status, proofs.c.accepted)).one() == ("pending", True)
    assert asyncio.run(snapshot_relay.run_once(factory, transport)) == 1
    with factory() as db:
        assert db.execute(select(proofs.c.status)).scalar_one() == "verified"
    assert transport.sends == 1 and transport.lookups == 2


def test_previously_accepted_missing_receipt_quarantines(sessions):
    factory, proof = sessions
    with factory.begin() as db:
        db.execute(update(outbox).values(status="delivered"))
        db.execute(update(proofs).values(accepted=True))
    class Transport:
        async def lookup_proof(self, *args): return Receipt.ABSENT, None
        async def send_proof(self, *args): raise AssertionError("do not blindly replay a lost durable receipt")
    asyncio.run(snapshot_relay.run_once(factory, Transport()))
    with factory() as db:
        assert db.execute(select(proofs.c.status)).scalar_one() == "quarantined"
