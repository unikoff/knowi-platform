"""Offline checks only; the PostgreSQL ALTER is exercised in the synthetic run."""
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from pymentor_core.adapters.sql.analytics_delivery import checkpoint, enqueue, metadata, next_revision, outbox, revisions
from pymentor_core.modules.analytics_delivery.contracts import activity
from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_sql import barriers, proofs


def test_bigint_roundtrip_and_postgresql_upgrade_plan(synthetic_engine):
    large = 2**31 + 1
    ref = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    engine = synthetic_engine
    metadata.create_all(engine)
    with Session(engine) as db:
        db.execute(revisions.insert().values(subject_ref=str(ref), revision=large - 1))
        assert next_revision(db, ref) == large
        event = activity(ref, date(2026, 11, 17), None, revision=large)
        enqueue(db, event, datetime.now(timezone.utc))
        db.execute(checkpoint.insert().values(stream="activity", high_watermark=large, cursor=large))
        db.commit()
        assert db.execute(select(revisions.c.revision)).scalar_one() == large
        assert db.execute(select(outbox.c.revision)).scalar_one() == large
        assert db.execute(select(checkpoint.c.high_watermark, checkpoint.c.cursor)).one() == (large, large)
    engine.dispose()
    fields = {outbox: ("revision",), revisions: ("revision",), checkpoint: ("high_watermark", "cursor"),
              proofs: ("cut_revision",), barriers: ("high_watermark",)}
    for table, names in fields.items():
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        for name in names:
            assert isinstance(table.c[name].type, BigInteger)
            assert f"{name} BIGINT" in ddl
    sql = (Path(__file__).resolve().parents[2] / "tools/analytics_transfer/002_snapshot_proof.sql").read_text()
    for table, names in ((outbox, ("revision",)), (revisions, ("revision",)),
                         (checkpoint, ("high_watermark", "cursor"))):
        for name in names:
            assert f"ALTER TABLE {table.name} ALTER COLUMN {name} TYPE BIGINT;" in sql
