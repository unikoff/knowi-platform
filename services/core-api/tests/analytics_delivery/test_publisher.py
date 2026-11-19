import asyncio
import os
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://synthetic@127.0.0.1:1/isolated")

from pymentor_core.app import analytics_publisher as publisher
from pymentor_core.adapters.sql import models
from pymentor_core.adapters.sql.analytics_delivery import metadata, write_gate
from pymentor_core.adapters.sql.analytics_delivery_ops import export, integration, publisher_scan
from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_sql import metadata as proof_metadata, proofs

REF = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
REPLACEMENT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def env():
    return {"PYMENTOR_ANALYTICS_PUBLISHER_ENABLED": "1", "PYMENTOR_ANALYTICS_DELIVERY_ENABLED": "1",
            "PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED": "1", "PYMENTOR_ANALYTICS_PUBLISHER_BACKFILL_COMPLETE": "1",
            "DATABASE_URL": "postgresql+psycopg://publisher@localhost/fixture",
            "PYMENTOR_ANALYTICS_PUBLISHER_EXPECTED_DATABASE": "fixture",
            "PYMENTOR_ANALYTICS_PUBLISHER_EXPECTED_USER": "publisher"}


def test_disabled_and_wrong_identity_fail_before_engine(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("engine must not be created")
    import sqlalchemy
    monkeypatch.setattr(sqlalchemy, "create_engine", forbidden)
    for remove in ("PYMENTOR_ANALYTICS_PUBLISHER_ENABLED", "PYMENTOR_ANALYTICS_DELIVERY_ENABLED",
                   "PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED", "PYMENTOR_ANALYTICS_PUBLISHER_BACKFILL_COMPLETE"):
        current = env()
        del current[remove]
        with pytest.raises(ValueError):
            publisher.config_from_env(current)
    current = env()
    current["PYMENTOR_ANALYTICS_PUBLISHER_EXPECTED_DATABASE"] = "other"
    with pytest.raises(ValueError):
        publisher.config_from_env(current)
    current = env()
    current["PYMENTOR_ANALYTICS_PUBLISHER_EXPECTED_USER"] = "other"
    with pytest.raises(ValueError):
        publisher.config_from_env(current)
    assert publisher.config_from_env(env()).expected_database == "fixture"


def test_database_server_identity_checked_before_schema(monkeypatch):
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query):
            assert "current_database" in str(query)
            return self
        def one(self): return ("different", "publisher")
    class Engine:
        def connect(self): return Connection()
    with pytest.raises(ValueError, match="identity mismatch"):
        publisher.validate_database(Engine(), publisher.config_from_env(env()))


@pytest.fixture
def sessions(synthetic_engine, monkeypatch):
    monkeypatch.setenv("PYMENTOR_ANALYTICS_DELIVERY_ENABLED", "1")
    monkeypatch.setenv("PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED", "1")
    monkeypatch.delenv("PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE", raising=False)
    from uuid import uuid4
    engine = synthetic_engine
    for model in (models.User, models.UserActivityDay, models.UserLessonProgress):
        model.__table__.create(engine)
    metadata.create_all(engine)
    proof_metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE account_lifecycles(user_id INTEGER PRIMARY KEY, lifecycle_id VARCHAR(36) UNIQUE NOT NULL)"))
        conn.execute(text("INSERT INTO users(id,username,email,password_hash,is_admin,verified) VALUES (1,'u1','u1@example.test','hash',false,true),(2,'u2','u2@example.test','hash',false,true)"))
        conn.execute(text("INSERT INTO account_lifecycles VALUES (1,:ref)"), {"ref": REF})
        conn.execute(write_gate.insert().values(singleton=1, mode="mirror"))
    factory = sessionmaker(engine)
    with factory() as db:
        for stream in ("activity", "progress"):
            export.initialize(db, stream)
            export.export_page(db, stream)
            export.seal_backfill(db, stream)
        db.commit()
    yield factory
    engine.dispose()


def test_new_empty_user_revision_refresh_and_reused_lifecycle(sessions):
    first = publisher_scan.scan_page(sessions, 0, stage_limit=2)
    assert (first.scanned, first.staged, first.blocked) == (2, 2, 0)
    with sessions() as db:
        first_refs = dict(db.execute(text("SELECT user_id,lifecycle_id FROM account_lifecycles")).all())
        assert first_refs[1] == REF and first_refs[2] != REF
        assert db.execute(select(proofs.c.cut_revision)).scalars().all() == [0, 0]
        db.execute(proofs.update().values(status="verified", accepted=True))
        db.commit()
    assert publisher_scan.scan_page(sessions, 0).staged == 0
    with sessions() as db:
        integration.record_visit(db, 1, date(2026, 11, 17), datetime(2026, 11, 17, tzinfo=timezone.utc), REF)
        db.commit()
    assert publisher_scan.scan_page(sessions, 0).staged == 1
    with sessions() as db:
        assert db.execute(select(proofs.c.cut_revision).where(proofs.c.subject_ref == REF).order_by(proofs.c.cut_revision)).scalars().all() == [0, 1]
        db.execute(text("UPDATE account_lifecycles SET lifecycle_id=:new WHERE user_id=1"), {"new": REPLACEMENT})
        db.commit()
    assert publisher_scan.scan_page(sessions, 0).blocked == 1
    with sessions() as db:
        # Replacement has old user's legacy rows in this artificial direct swap;
        # guard must reject them as uncaptured, not publish an invalid proof.
        assert db.execute(select(proofs.c.cut_revision).where(proofs.c.subject_ref == REPLACEMENT)).scalars().all() == []
        db.query(models.UserActivityDay).filter_by(user_id=1).delete()
        db.commit()
    assert publisher_scan.scan_page(sessions, 0).staged == 1
    with sessions() as db:
        assert db.execute(select(proofs.c.cut_revision).where(proofs.c.subject_ref == REPLACEMENT)).scalar_one() == 0


def test_page_limit_retries_and_quarantine_are_bounded(sessions, monkeypatch):
    real_stage = publisher_scan.stage_subject
    attempts = []
    def failing(db, uid, ref, **kwargs):
        attempts.append(uid)
        raise ValueError("guard")
    monkeypatch.setattr(publisher_scan, "stage_subject", failing)
    result = publisher_scan.scan_page(sessions, 0, stage_limit=1)
    assert (result.cursor, result.scanned, result.blocked, attempts) == (1, 1, 1, [1])
    # User 2 is not skipped just because user 1 failed.
    result = publisher_scan.scan_page(sessions, result.cursor, stage_limit=1)
    assert (result.cursor, attempts) == (2, [1, 2])
    with sessions() as db:
        assert db.execute(text("SELECT count(*) FROM account_lifecycles WHERE user_id=2")).scalar_one() == 0
    monkeypatch.setattr(publisher_scan, "stage_subject", real_stage)
    assert publisher_scan.scan_page(sessions, 2).cursor == 0
    assert publisher_scan.scan_page(sessions, 0).staged == 2
    with sessions() as db:
        db.execute(proofs.update().where(proofs.c.subject_ref == REF).values(status="quarantined"))
        db.commit()
    result = publisher_scan.scan_page(sessions, 0)
    assert result.staged == 0 and result.blocked == 1
    with sessions() as db:
        integration.record_visit(db, 1, date(2026, 11, 17), datetime(2026, 11, 17, tzinfo=timezone.utc), REF)
        db.commit()
    result = publisher_scan.scan_page(sessions, 0)
    assert result.staged == 0 and result.blocked == 1
    with sessions() as db:
        assert db.execute(select(proofs.c.cut_revision).where(proofs.c.subject_ref == REF)).scalars().all() == [0]


def test_competing_stale_scans_leave_one_proof(sessions, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2, timeout=5)
    real_stage = publisher_scan.stage_subject
    def concurrent_stage(db, uid, ref, **kwargs):
        barrier.wait()
        return real_stage(db, uid, ref, **kwargs)
    monkeypatch.setattr(publisher_scan, "stage_subject", concurrent_stage)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publisher_scan.scan_page, sessions, 0, page_size=1, stage_limit=1) for _ in range(2)]
        results = [f.result(timeout=10) for f in futures]
    monkeypatch.setattr(publisher_scan, "stage_subject", real_stage)
    # SQLite can abort both competing writers; the next bounded sweep recovers.
    if not sum(item.staged for item in results):
        assert sum(item.blocked for item in results) == 2
        assert publisher_scan.scan_page(sessions, 0, page_size=1).staged == 1
    with sessions() as db:
        assert len(db.execute(select(proofs).where(proofs.c.subject_ref == REF)).all()) == 1
    assert publisher_scan.scan_page(sessions, 0, page_size=1).staged == 0


def test_delayed_coverage_serializes_revisions_and_other_users_progress(sessions):
    from pymentor_core.adapters.sql.analytics_delivery import outbox
    from pymentor_core.adapters.sql.analytics_delivery_ops import snapshot_relay
    from pymentor_core.modules.analytics_delivery.relay import Receipt

    class Receiver:
        def __init__(self):
            self.coverage = "pending"
            self.sent = []
        async def send_proof(self, proof, timeout):
            self.sent.append(proof["cut_revision"])
            return Receipt.ACCEPTED
        async def lookup_proof(self, proof_id, fingerprint, timeout):
            return Receipt.ACCEPTED, self.coverage

    receiver = Receiver()
    assert publisher_scan.scan_page(sessions, 0, page_size=1).staged == 1
    asyncio.run(snapshot_relay.run_once(sessions, receiver))
    with sessions() as db:
        integration.record_visit(db, 1, date(2026, 11, 17), datetime(2026, 11, 17, tzinfo=timezone.utc), REF)
        db.execute(outbox.update().values(status="delivered"))
        db.execute(proofs.update().values(next_attempt_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
        db.commit()
    # A pending old cut does not consume the stage budget of the next user.
    assert publisher_scan.scan_page(sessions, 0, stage_limit=1).staged == 1
    asyncio.run(snapshot_relay.run_once(sessions, receiver))
    with sessions() as db:
        assert db.execute(select(proofs.c.cut_revision).where(proofs.c.subject_ref == REF)).scalars().all() == [0]
        db.execute(proofs.update().values(next_attempt_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
        db.commit()
    receiver.coverage = "verified"
    asyncio.run(snapshot_relay.run_once(sessions, receiver))
    assert publisher_scan.scan_page(sessions, 0).staged == 1
    asyncio.run(snapshot_relay.run_once(sessions, receiver))
    with sessions() as db:
        integration.record_visit(db, 1, date(2026, 11, 18), datetime(2026, 11, 18, tzinfo=timezone.utc), REF)
        db.execute(outbox.update().values(status="delivered"))
        db.execute(proofs.update().values(next_attempt_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
        db.commit()
    assert publisher_scan.scan_page(sessions, 0).staged == 0
    asyncio.run(snapshot_relay.run_once(sessions, receiver))
    assert publisher_scan.scan_page(sessions, 0).staged == 1
    asyncio.run(snapshot_relay.run_once(sessions, receiver))
    with sessions() as db:
        db.execute(proofs.update().values(next_attempt_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))
        db.commit()
    asyncio.run(snapshot_relay.run_once(sessions, receiver))
    with sessions() as db:
        rows = db.execute(select(proofs.c.cut_revision, proofs.c.status).where(proofs.c.subject_ref == REF)
                          .order_by(proofs.c.cut_revision)).all()
        assert rows == [(0, "verified"), (1, "verified"), (2, "verified")]
    assert receiver.sent.count(0) >= 1 and 1 in receiver.sent and 2 in receiver.sent


def test_rechecks_after_stale_page_read_under_gate_and_user_lock(sessions, monkeypatch):
    original_gate = publisher_scan.require_write_gate
    original_guard = publisher_scan.guard_lifecycle
    original_stage = publisher_scan.stage_subject
    order = []
    raced = False
    def gate(db, mode):
        nonlocal raced
        if not raced:
            raced = True
            # Another publisher stages cut 0 after the page read; then a write
            # advances the business revision before this publisher gets its lock.
            with sessions() as other:
                original_stage(other, 1, REF, deadline_seconds=3)
                other.commit()
            with sessions() as other:
                integration.record_visit(other, 1, date(2026, 11, 17), datetime(2026, 11, 17, tzinfo=timezone.utc), REF)
        order.append("gate")
        return original_gate(db, mode)
    def guard(db, uid, ref):
        order.append("user")
        return original_guard(db, uid, ref)
    def forbidden(*args, **kwargs):
        pytest.fail("stale scan staged over an unverified proof")
    monkeypatch.setattr(publisher_scan, "require_write_gate", gate)
    monkeypatch.setattr(publisher_scan, "guard_lifecycle", guard)
    monkeypatch.setattr(publisher_scan, "stage_subject", forbidden)
    assert publisher_scan.scan_page(sessions, 0, page_size=1).staged == 0
    assert order == ["gate", "user"]
    with sessions() as db:
        assert db.execute(select(proofs.c.cut_revision).where(proofs.c.subject_ref == REF)).scalars().all() == [0]


def test_new_lifecycle_locks_gate_before_user(sessions, monkeypatch):
    order = []
    gate, lifecycle = publisher_scan.require_write_gate, publisher_scan.lifecycle
    def checked_gate(db, mode):
        order.append("gate")
        return gate(db, mode)
    def checked_lifecycle(db, uid):
        order.append("user")
        return lifecycle(db, uid)
    monkeypatch.setattr(publisher_scan, "require_write_gate", checked_gate)
    monkeypatch.setattr(publisher_scan, "lifecycle", checked_lifecycle)
    assert publisher_scan.scan_page(sessions, 1, page_size=1).staged == 1
    assert order == ["gate", "user"]


def test_direct_staging_defers_then_retries_after_verified_coverage(sessions):
    # This is the same stage_subject entry point used by the manual CLI.
    from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_export import SnapshotDeferred, stage_subject
    with sessions() as db:
        first = stage_subject(db, 1, REF)
        db.commit()
        assert stage_subject(db, 1, REF) == first  # same-cut retry remains idempotent
        db.commit()
        integration.record_visit(db, 1, date(2026, 11, 17), datetime(2026, 11, 17, tzinfo=timezone.utc), REF)
        with pytest.raises(SnapshotDeferred):
            stage_subject(db, 1, REF)
        db.rollback()
        assert db.execute(select(proofs.c.cut_revision).where(proofs.c.subject_ref == REF)).scalars().all() == [0]
        db.execute(proofs.update().where(proofs.c.subject_ref == REF).values(status="verified", accepted=True))
        db.commit()
        assert stage_subject(db, 1, REF)["cut_revision"] == 1
        db.commit()


@pytest.mark.parametrize("quarantined", ["proof", "outbox"])
def test_direct_staging_preserves_quarantine(sessions, quarantined):
    from pymentor_core.adapters.sql.analytics_delivery import outbox
    from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_export import SnapshotConflict, stage_subject
    with sessions() as db:
        stage_subject(db, 1, REF)
        db.commit()
        integration.record_visit(db, 1, date(2026, 11, 17), datetime(2026, 11, 17, tzinfo=timezone.utc), REF)
        if quarantined == "proof":
            db.execute(proofs.update().values(status="quarantined"))
        else:
            db.execute(proofs.update().values(status="verified", accepted=True))
            db.execute(outbox.update().values(status="quarantined"))
        db.commit()
        with pytest.raises(SnapshotConflict):
            stage_subject(db, 1, REF)
        db.rollback()
        assert db.execute(select(proofs.c.cut_revision).where(proofs.c.subject_ref == REF)).scalars().all() == [0]


@pytest.mark.parametrize("detail_unavailable", [False, True])
def test_loop_orders_delivery_scan_proof_and_stops(monkeypatch, caplog, detail_unavailable):
    from sqlalchemy.exc import SQLAlchemyError
    from pymentor_core.adapters.sql.analytics_delivery_ops import worker, snapshot_relay
    calls = []
    stop = asyncio.Event()
    monkeypatch.setattr(publisher, "check_gate", lambda _: None)
    def backlog(_):
        if detail_unavailable:
            raise SQLAlchemyError("diagnostic query timed out")
        return {f"{kind}_{key}": 0 for kind in ("fact", "proof")
                for key in ("blocked", "pending", "claimed", "quarantined", "eligible_sample", "truncated")}
    def fallback(_):
        calls.append("terminal_flags")
        return True, False
    monkeypatch.setattr(publisher, "backlog_snapshot", backlog)
    monkeypatch.setattr(publisher, "blocked_backlog", fallback)
    async def facts(*args, **kwargs):
        calls.append("facts")
        return 1
    def scan(*args, **kwargs):
        calls.append("scan")
        return publisher_scan.ScanResult(1, 1, 1, 0)
    async def proof(*args, **kwargs):
        calls.append("proof")
        stop.set()
        return 1
    monkeypatch.setattr(worker, "run_once", facts)
    monkeypatch.setattr(snapshot_relay, "run_once", proof)
    monkeypatch.setattr(publisher_scan, "scan_page", scan)
    with caplog.at_level("INFO", logger=publisher.LOG.name):
        asyncio.run(publisher.run(None, None, None, stop, interval=.01))
    assert calls == ["facts", "scan", "proof"] + (["terminal_flags"] if detail_unavailable else [])
    assert "publisher_tick" in caplog.text
    assert "publisher_cycle_failed" not in caplog.text
    if detail_unavailable:
        assert "publisher_backlog_detail_unavailable" in caplog.text
        assert "fact_blocked=1 proof_blocked=0" in caplog.text
    else:
        assert "backlog_cap=1000 fact_pending=0" in caplog.text


def test_backlog_snapshot_is_bounded_and_respects_proof_dependency(sessions, monkeypatch):
    from pymentor_core.adapters.sql.analytics_delivery import outbox
    monkeypatch.setattr(publisher, "BACKLOG_CAP", 1)
    publisher_scan.scan_page(sessions, 0, stage_limit=2)
    with sessions() as db:
        integration.record_visit(db, 1, date(2026, 11, 17), datetime(2026, 11, 17, tzinfo=timezone.utc), REF)
        # The existing proof cut precedes this fact; make it depend on the fact.
        db.execute(proofs.update().where(proofs.c.subject_ref == REF).values(cut_revision=1))
        db.commit()
    snapshot = publisher.backlog_snapshot(sessions)
    assert snapshot["fact_pending"] == 1
    assert snapshot["fact_eligible_sample"] == 1
    assert snapshot["proof_pending"] == 1 and snapshot["proof_truncated"]
    assert snapshot["proof_eligible_sample"] == 1
    assert not snapshot["fact_blocked"] and not snapshot["proof_blocked"]
    with sessions() as db:
        db.execute(outbox.update().values(attempts=20))
        db.execute(proofs.update().where(proofs.c.subject_ref == REF).values(status="quarantined"))
        db.commit()
    snapshot = publisher.backlog_snapshot(sessions)
    assert snapshot["fact_blocked"] and snapshot["fact_eligible_sample"] == 0
    assert snapshot["proof_blocked"] and snapshot["proof_quarantined"] == 1
    with sessions() as db:
        db.execute(outbox.update().values(status="claimed", attempts=1,
                    lease_until=datetime.now(timezone.utc) + timedelta(minutes=1)))
        db.commit()
    assert publisher.backlog_snapshot(sessions)["fact_eligible_sample"] == 0
    with sessions() as db:
        db.execute(outbox.update().values(lease_until=datetime.now(timezone.utc) - timedelta(minutes=1)))
        db.commit()
    assert publisher.backlog_snapshot(sessions)["fact_eligible_sample"] == 1


def test_loop_does_not_advance_after_relay_error(monkeypatch):
    from pymentor_core.adapters.sql.analytics_delivery_ops import worker, snapshot_relay
    calls = []
    stop = asyncio.Event()
    monkeypatch.setattr(publisher, "check_gate", lambda _: None)
    async def facts(*args, **kwargs):
        calls.append("facts")
        if len(calls) == 1:
            raise RuntimeError("transient")
        stop.set()
        return 0
    def forbidden(*args, **kwargs):
        pytest.fail("scan or proof ran after relay failure")
    monkeypatch.setattr(worker, "run_once", facts)
    monkeypatch.setattr(publisher_scan, "scan_page", forbidden)
    monkeypatch.setattr(snapshot_relay, "run_once", forbidden)
    asyncio.run(publisher.run(None, None, None, stop, interval=.001))
    assert calls == ["facts", "facts"]


def test_changed_gate_blocks_delivery(monkeypatch):
    from pymentor_core.adapters.sql.analytics_delivery_ops import worker
    stop = asyncio.Event()
    calls = []
    def gate(_):
        calls.append("gate")
        stop.set()
        raise ValueError("changed gate")
    async def forbidden(*args, **kwargs):
        pytest.fail("delivery must not run after gate refusal")
    monkeypatch.setattr(publisher, "check_gate", gate)
    monkeypatch.setattr(worker, "run_once", forbidden)
    asyncio.run(publisher.run(None, None, None, stop, interval=.001))
    assert calls == ["gate"]
