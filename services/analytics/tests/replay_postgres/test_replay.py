"""Real replay, fencing and budget tests on fresh disposable databases."""
import asyncio
import hashlib
import json
import os
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError
from alembic import command
from alembic.config import Config

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.intake import PostgresIntake
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.adapters.postgres.processor import PostgresProcessor, PostgresSyntheticCounts
from pymentor_analytics.adapters.postgres.replay import PostgresReplay
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.processing_v1 import ProjectionUnavailable, StaleClaim, synthetic_delta
from pymentor_analytics.contracts.replay_v1 import ReplayBusy, ReplayForbidden, ReplayStale
from pymentor_analytics.contracts.v2 import AcceptedEvent, parse_browser_item
from tools.bootstrap_postgres import cleanup, config, connect, create

ROOT = Path(__file__).resolve().parents[2]
CONSUMER, OTHER = "synthetic.activity", "synthetic.independent"


@pytest.fixture
def target():
    if os.environ.get("ANALYTICS_RUN_SYNTHETIC_REPLAY") != "1":
        pytest.skip("explicit synthetic replay runner required")
    data = config(ROOT / ".local" / "postgres-connection.json")
    name, token, urls = create(data)
    try:
        upgrade(Settings(database_url=urls["migrate"]), target_revision="a006")
        yield data, name, urls
    finally:
        cleanup(data, name, token)


def database(url):
    return Database(Settings(database_url=url, pool_size=2, pool_overflow=0, db_timeout_seconds=5))


def upgrade_to(url, revision):
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.begin() as connection:
            cfg = Config(str(ROOT / "alembic.ini"))
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, revision)
    finally:
        engine.dispose()


def event(subject, course="a", event_id=None):
    raw = json.loads((ROOT / "contracts/fixtures/browser-batch.json").read_text(encoding="utf-8"))["events"][0]
    now = datetime.now(timezone.utc)
    raw.update(event_id=str(event_id or uuid4()), course_id=course, started_at=now.isoformat(),
               ended_at=now.isoformat(), last_action_at=now.isoformat())
    return AcceptedEvent(producer_namespace="synthetic", subject_ref=subject, source="browser",
                         event=parse_browser_item(json.dumps(raw).encode()), received_at=now)


async def seed(writer, *records):
    for record in records:
        assert await writer.accept(record, deadline=time.monotonic() + 4) == "accepted"


async def drain(store, consumer=CONSUMER):
    for _ in range(100):
        batch = await store.claim(consumer, 1, uuid4(), 100, 30)
        if batch is None:
            return
        await store.commit(batch, synthetic_delta(batch.facts))
    raise AssertionError("bounded drain did not settle")


def projection(target, consumer, subject):
    data, name, _ = target
    with connect(data, name) as connection:
        return connection.execute("""SELECT courses,covered_count,fingerprint_xor FROM analytics.processing_results
            WHERE consumer=%s AND consumer_version=1 AND subject_ref=%s""", (consumer, subject)).fetchone()


def fingerprint(records):
    value = 0
    for record in records:
        canonical = json.dumps(record.model_dump(mode="json", exclude={"received_at"}),
                               ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        namespace = record.producer_namespace.encode()
        raw = len(namespace).to_bytes(4, "big") + namespace + record.event.event_id.bytes + hashlib.sha256(canonical).hexdigest().encode()
        value ^= int.from_bytes(hashlib.sha256(raw).digest(), "big")
    return f"{value:064x}"


async def advance_to(replay, state, phase, limit=100):
    for _ in range(100):
        if state.phase == phase:
            return state
        state = await replay.step(state.handle, limit)
    raise AssertionError("bounded replay did not reach requested phase")


def test_rebuild_corrupt_projection_old_claim_fenced_and_other_consumer_unchanged(target):
    data, name, urls = target
    async def run():
        collector, processor, query = (database(urls[r]) for r in ("collector", "processor", "query"))
        subject = uuid4()
        try:
            writer, store, replay, reader = PostgresIntake(collector), PostgresProcessor(processor), PostgresReplay(processor), PostgresSyntheticCounts(query)
            records = (event(subject, "a"), event(subject, "a"), event(subject, "b"))
            await seed(writer, *records)
            await drain(store, CONSUMER)
            await drain(store, OTHER)
            baseline = projection(target, CONSUMER, subject)
            independent = projection(target, OTHER, subject)
            assert baseline[:2] == ({"a": 2, "b": 1}, 3)
            pending = event(subject, "a")
            await seed(writer, pending)
            old = await store.claim(CONSUMER, 1, uuid4(), 1, 30)
            assert old
            with connect(data, name) as connection:
                connection.execute("UPDATE analytics.processing_results SET courses='{}'::jsonb WHERE consumer=%s AND subject_ref=%s", (CONSUMER, subject))
            request_id = uuid4()
            state = await replay.begin(CONSUMER, 1, subject, request_id)
            assert state.phase == "cleanup" and (await replay.begin(CONSUMER, 1, subject, request_id)).handle == state.handle
            with pytest.raises(ReplayBusy): await replay.begin(CONSUMER, 1, subject, uuid4())
            with pytest.raises(StaleClaim): await store.commit(old, synthetic_delta(old.facts))
            with pytest.raises(ProjectionUnavailable): await reader.read_counts(CONSUMER, 1, subject, subject, ("a",))
            assert await reader.read_counts(CONSUMER, 1, subject, subject, ("unrelated",)) is None
            assert await store.claim(CONSUMER, 1, uuid4(), 100, 30) is None
            state = await advance_to(replay, state, "scan", 2)
            for _ in range(20):
                if state.phase == "drain": break
                state = await replay.step(state.handle, 2)
                await drain(store)
            assert state.phase == "drain"
            await drain(store)
            state = await replay.finish(state.handle)
            assert state.phase == "complete"
            assert projection(target, CONSUMER, subject)[:2] == ({"a": 3, "b": 1}, 4)
            assert projection(target, CONSUMER, subject)[2] == fingerprint((*records, pending))
            assert projection(target, OTHER, subject) == independent
            assert (await replay.finish(state.handle)).phase == "complete"
            with pytest.raises(ReplayStale):
                newer = await replay.begin(CONSUMER, 1, subject, uuid4())
                assert newer.handle.generation > state.handle.generation
                assert (await replay.begin(CONSUMER, 1, subject, request_id)).phase == "complete"
                await replay.step(state.handle)
        finally:
            for d in (collector, processor, query): await d.close()
    asyncio.run(run())


def test_page_rollback_and_lost_reply_resume_same_request(target):
    data, name, urls = target
    async def run():
        collector, processor = database(urls["collector"]), database(urls["processor"])
        subject = uuid4()
        class BeforeCrash(PostgresReplay):
            async def _before_commit(self): raise RuntimeError("before commit")
        class AfterCrash(PostgresReplay):
            async def _after_commit(self): raise RuntimeError("lost reply")
        try:
            await seed(PostgresIntake(collector), *(event(subject, event_id=UUID(int=i)) for i in range(1, 5)))
            await drain(PostgresProcessor(processor))
            replay = PostgresReplay(processor)
            state = await replay.begin(CONSUMER, 1, subject, uuid4())
            state = await advance_to(replay, state, "scan")
            with pytest.raises(RuntimeError): await BeforeCrash(processor).step(state.handle, 1)
            assert (await replay.begin(CONSUMER, 1, subject, state.handle.request_id)).cursor_event_id is None
            with connect(data, name) as connection:
                assert connection.execute("SELECT status FROM analytics.processing_jobs WHERE consumer=%s AND event_id=%s", (CONSUMER, UUID(int=1))).fetchone() == ("completed",)
            with pytest.raises(RuntimeError): await AfterCrash(processor).step(state.handle, 1)
            resumed = await replay.begin(CONSUMER, 1, subject, state.handle.request_id)
            assert resumed.cursor_event_id == UUID(int=1) and resumed.handle == state.handle
            with connect(data, name) as connection:
                assert connection.execute("SELECT status,work_class FROM analytics.processing_jobs WHERE consumer=%s AND event_id=%s", (CONSUMER, UUID(int=1))).fetchone() == ("pending", "replay")
            resumed = await replay.step(resumed.handle, 1)
            assert resumed.cursor_event_id == UUID(int=2)
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_cleanup_and_scan_pages_are_bounded(target):
    data, name, urls = target
    async def run():
        collector, processor = database(urls["collector"]), database(urls["processor"])
        subject = uuid4()
        try:
            await seed(PostgresIntake(collector), *(event(subject, event_id=UUID(int=i)) for i in range(1, 21)))
            store, replay = PostgresProcessor(processor), PostgresReplay(processor)
            await drain(store)
            state = await replay.begin(CONSUMER, 1, subject, uuid4())
            state = await replay.step(state.handle, 2)
            assert state.phase == "cleanup"
            with connect(data, name) as connection:
                assert connection.execute("SELECT count(*) FROM analytics.processing_receipts WHERE consumer=%s AND subject_ref=%s", (CONSUMER, subject)).fetchone() == (18,)
            assert await store.claim(CONSUMER, 1, uuid4(), 100, 30) is None
            state = await advance_to(replay, state, "scan", 2)
            state = await replay.step(state.handle, 100)
            assert state.phase == "scan" and state.blocked and state.cursor_event_id == UUID(int=16)
            with connect(data, name) as connection:
                assert connection.execute("SELECT count(*) FROM analytics.processing_jobs WHERE consumer=%s AND subject_ref=%s AND status='pending' AND work_class='replay'", (CONSUMER, subject)).fetchone() == (16,)
            await drain(store)
            state = await advance_to(replay, state, "drain")
            await drain(store)
            assert (await replay.finish(state.handle)).phase == "complete"
            assert projection(target, CONSUMER, subject)[:2] == ({"a": 20}, 20)
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_live_event_behind_cursor_and_during_step_is_covered(target):
    _, _, urls = target
    async def run():
        collector, processor = database(urls["collector"]), database(urls["processor"])
        subject, reached, release = uuid4(), asyncio.Event(), asyncio.Event()
        class Gate(PostgresReplay):
            async def _before_commit(self):
                reached.set()
                await release.wait()
        try:
            writer, replay, store = PostgresIntake(collector), PostgresReplay(processor), PostgresProcessor(processor)
            await seed(writer, event(subject, "a", UUID(int=2)), event(subject, "b", UUID(int=9)))
            state = await replay.begin(CONSUMER, 1, subject, uuid4())
            state = await advance_to(replay, state, "scan")
            task = asyncio.create_task(Gate(processor).step(state.handle, 1))
            await asyncio.wait_for(reached.wait(), 2)
            late = event(subject, "a", UUID(int=1))
            arrival = asyncio.create_task(writer.accept(late, deadline=time.monotonic() + 4))
            await asyncio.sleep(.1)
            assert not arrival.done()
            release.set()
            state = await task
            assert state.cursor_event_id == UUID(int=2)
            assert await arrival == "accepted"
            state = await advance_to(replay, state, "drain", 1)
            assert (await replay.finish(state.handle)).phase == "drain"
            await drain(store)
            assert (await replay.finish(state.handle)).phase == "complete"
            assert projection(target, CONSUMER, subject)[:2] == ({"a": 2, "b": 1}, 3)
        finally:
            release.set()
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_replay_capacity_reserves_live_slots_and_recovers_after_drain(target):
    data, name, urls = target
    async def run():
        collector, processor = database(urls["collector"]), database(urls["processor"])
        subject, live_subject = uuid4(), uuid4()
        try:
            writer, store, replay = PostgresIntake(collector), PostgresProcessor(processor), PostgresReplay(processor)
            await seed(writer, *(event(subject, event_id=UUID(int=i)) for i in range(1, 5)))
            await drain(store, CONSUMER)
            await drain(store, OTHER)
            with connect(data, name) as connection:
                connection.execute("UPDATE analytics.admission_budget SET max_outstanding_jobs=6")
            state = await replay.begin(CONSUMER, 1, subject, uuid4())
            state = await advance_to(replay, state, "scan")
            state = await replay.step(state.handle)
            assert state.blocked and state.phase == "scan"
            with connect(data, name) as connection:
                assert connection.execute("SELECT count(*) FROM analytics.processing_jobs WHERE work_class='replay' AND status<>'completed'").fetchone() == (3,)
            # The reserved half still admits a live fact and its two jobs.
            await seed(writer, event(live_subject, "live"))
            with connect(data, name) as connection:
                assert connection.execute("SELECT count(*) FROM analytics.processing_jobs WHERE status<>'completed'").fetchone() == (5,)
            await drain(store, CONSUMER)
            state = await advance_to(replay, state, "drain")
            assert (await replay.finish(state.handle)).phase == "drain"
            await drain(store, CONSUMER)
            assert (await replay.finish(state.handle)).phase == "complete"
            assert projection(target, CONSUMER, subject)[:2] == ({"a": 4}, 4)
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_critical_storage_rejects_new_replay_but_allows_same_id_reconciliation(target):
    data, name, urls = target
    async def run():
        processor = database(urls["processor"])
        subject = uuid4()
        try:
            replay = PostgresReplay(processor)
            state = await replay.begin(CONSUMER, 1, subject, uuid4())
            with connect(data, name) as connection:
                connection.execute("UPDATE analytics.admission_budget SET max_database_bytes=1")
            assert (await replay.begin(CONSUMER, 1, subject, state.handle.request_id)).handle == state.handle
            with pytest.raises(ReplayBusy):
                await replay.begin(CONSUMER, 1, uuid4(), uuid4())
            with connect(data, name) as connection:
                assert connection.execute("SELECT count(*) FROM analytics.processing_replays").fetchone() == (1,)
        finally:
            await processor.close()
    asyncio.run(run())


def test_cleanup_function_rejects_direct_delete_inactive_stale_or_tombstone(target):
    data, name, urls = target
    async def run():
        collector, processor, query, privacy = (database(urls[r]) for r in ("collector", "processor", "query", "privacy"))
        subject = uuid4()
        call = text("SELECT analytics.replay_cleanup(:consumer,1,:subject,:request,:generation,:epoch,:limit)")
        try:
            await seed(PostgresIntake(collector), event(subject))
            await drain(PostgresProcessor(processor))
            with pytest.raises(DBAPIError):
                async with processor.transaction() as session:
                    await session.execute(text("DELETE FROM analytics.processing_receipts"))
            bad = {"consumer": CONSUMER, "subject": subject, "request": uuid4(), "generation": 1, "epoch": 0, "limit": 100}
            with pytest.raises(DBAPIError):
                async with processor.transaction() as session: await session.execute(call, bad)
            replay = PostgresReplay(processor)
            state = await replay.begin(CONSUMER, 1, subject, uuid4())
            args = {**bad, "request": state.handle.request_id, "generation": state.handle.generation}
            for change in ({"request": uuid4()}, {"generation": state.handle.generation + 1}, {"epoch": 1}, {"limit": 101}):
                with pytest.raises(DBAPIError):
                    async with processor.transaction() as session: await session.execute(call, {**args, **change})
            with pytest.raises(DBAPIError):
                async with query.transaction() as session: await session.execute(call, args)
            with connect(data, name) as connection:
                assert connection.execute("SELECT count(*) FROM analytics.processing_receipts WHERE consumer=%s AND subject_ref=%s", (CONSUMER, subject)).fetchone() == (1,)
            async with privacy.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
                await session.execute(text("INSERT INTO analytics.subject_fences VALUES (:subject,1,true,:control)"),
                                      {"subject": subject, "control": uuid4()})
            with pytest.raises(DBAPIError):
                async with processor.transaction() as session: await session.execute(call, args)
            with pytest.raises(ReplayForbidden): await replay.begin(CONSUMER, 1, subject, uuid4())
            with pytest.raises(ReplayForbidden): await replay.step(state.handle)
            with pytest.raises(ReplayForbidden): await replay.finish(state.handle)
            with connect(data, name) as connection:
                assert connection.execute("SELECT count(*) FROM analytics.processing_receipts WHERE consumer=%s AND subject_ref=%s", (CONSUMER, subject)).fetchone() == (1,)
        finally:
            for d in (collector, processor, query, privacy): await d.close()
    asyncio.run(run())


def test_cleanup_function_takes_exact_subject_lock(target):
    _, _, urls = target
    async def run():
        collector, processor, privacy = (database(urls[r]) for r in ("collector", "processor", "privacy"))
        subject, locked, release = uuid4(), asyncio.Event(), asyncio.Event()
        holder = None
        async def hold():
            async with privacy.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
                locked.set()
                await release.wait()
        try:
            await seed(PostgresIntake(collector), event(subject))
            await drain(PostgresProcessor(processor))
            replay = PostgresReplay(processor)
            state = await replay.begin(CONSUMER, 1, subject, uuid4())
            holder = asyncio.create_task(hold())
            await asyncio.wait_for(locked.wait(), 2)
            async def direct_cleanup():
                async with processor.transaction() as session:
                    return (await session.execute(text("""SELECT analytics.replay_cleanup(:consumer,1,:subject,
                        :request,:generation,:epoch,100)"""), {"consumer": CONSUMER, "subject": subject,
                        "request": state.handle.request_id, "generation": state.handle.generation,
                        "epoch": state.handle.subject_epoch})).scalar_one()
            task = asyncio.create_task(direct_cleanup())
            await asyncio.sleep(.1)
            assert not task.done()
            release.set()
            await holder
            assert await task == 1
        finally:
            release.set()
            if holder and not holder.done(): await holder
            for d in (collector, processor, privacy): await d.close()
    asyncio.run(run())


def test_privacy_subject_delete_cascades_composite_fks_without_resurrection(target):
    data, name, urls = target
    async def run():
        collector, processor, privacy = (database(urls[r]) for r in ("collector", "processor", "privacy"))
        subject = uuid4()
        try:
            await seed(PostgresIntake(collector), event(subject))
            await drain(PostgresProcessor(processor))
            replay = PostgresReplay(processor)
            state = await replay.begin(CONSUMER, 1, subject, uuid4())
            async with privacy.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
                await session.execute(text("INSERT INTO analytics.subject_fences VALUES (:subject,1,true,:control)"),
                                      {"subject": subject, "control": uuid4()})
                for table in ("processing_replays", "processing_leases", "processing_results", "activity_facts"):
                    await session.execute(text(f"DELETE FROM analytics.{table} WHERE subject_ref=:subject"), {"subject": subject})
            with pytest.raises(ReplayForbidden): await replay.step(state.handle)
            with pytest.raises(ReplayForbidden): await replay.finish(state.handle)
            with connect(data, name) as connection:
                for table in ("activity_facts", "processing_jobs", "processing_receipts", "processing_results", "processing_replays"):
                    assert connection.execute(f"SELECT count(*) FROM analytics.{table} WHERE subject_ref=%s", (subject,)).fetchone() == (0,)
        finally:
            for d in (collector, processor, privacy): await d.close()
    asyncio.run(run())


async def quarantine(store, target, subject, consumer=CONSUMER):
    data, name, _ = target
    for attempt in (1, 2, 3):
        claim = await store.claim(consumer, 1, uuid4(), 1, 30)
        assert claim and claim.subject_ref == subject
        assert await store.fail(claim) == ("quarantined" if attempt == 3 else "retry")
        if attempt < 3:
            with connect(data, name) as connection:
                connection.execute("UPDATE analytics.processing_jobs SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE consumer=%s AND subject_ref=%s", (consumer, subject))


def test_preexisting_quarantine_reopens_with_no_global_capacity_growth(target):
    data, name, urls = target
    async def run():
        collector, processor = database(urls["collector"]), database(urls["processor"])
        subject = uuid4()
        try:
            await seed(PostgresIntake(collector), event(subject))
            store, replay = PostgresProcessor(processor), PostgresReplay(processor)
            await quarantine(store, target, subject)
            # One quarantined live + the independent consumer's live job fill 2.
            with connect(data, name) as connection:
                connection.execute("UPDATE analytics.admission_budget SET max_outstanding_jobs=2")
            state = await replay.begin(CONSUMER, 1, subject, uuid4())
            state = await advance_to(replay, state, "scan")
            state = await replay.step(state.handle)
            assert state.phase == "drain" and not state.blocked
            with connect(data, name) as connection:
                assert connection.execute("SELECT status,work_class,attempts FROM analytics.processing_jobs WHERE consumer=%s AND subject_ref=%s", (CONSUMER, subject)).fetchone() == ("pending", "replay", 0)
                assert connection.execute("SELECT count(*) FROM analytics.processing_jobs WHERE status<>'completed'").fetchone() == (2,)
            await drain(store, CONSUMER)
            assert (await replay.finish(state.handle)).phase == "complete"
            assert projection(target, CONSUMER, subject)[:2] == ({"a": 1}, 1)
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_poison_during_current_generation_stays_quarantined_and_dirty(target):
    data, name, urls = target
    async def run():
        collector, processor, query = (database(urls[r]) for r in ("collector", "processor", "query"))
        subject = uuid4()
        try:
            await seed(PostgresIntake(collector), event(subject))
            store, replay = PostgresProcessor(processor), PostgresReplay(processor)
            state = await replay.begin(CONSUMER, 1, subject, uuid4())
            state = await advance_to(replay, state, "scan")
            # This job fails after begin but before the scanner sees its key.
            await quarantine(store, target, subject)
            with connect(data, name) as connection:
                assert connection.execute("SELECT status,failed_replay_id FROM analytics.processing_jobs WHERE consumer=%s AND subject_ref=%s", (CONSUMER, subject)).fetchone() == ("quarantined", state.handle.request_id)
            state = await replay.step(state.handle)
            assert state.phase == "drain" and (await replay.finish(state.handle)).phase == "drain"
            with connect(data, name) as connection:
                assert connection.execute("SELECT status,attempts FROM analytics.processing_jobs WHERE consumer=%s AND subject_ref=%s", (CONSUMER, subject)).fetchone() == ("quarantined", 3)
            with pytest.raises(ProjectionUnavailable):
                await PostgresSyntheticCounts(query).read_counts(CONSUMER, 1, subject, subject, ("a",))
            # The independent consumer is not blocked by this dirty generation.
            await drain(store, OTHER)
            assert projection(target, OTHER, subject)[:2] == ({"a": 1}, 1)
        finally:
            for d in (collector, processor, query): await d.close()
    asyncio.run(run())


def test_quarantined_replay_counts_against_reserved_capacity(target):
    data, name, urls = target
    async def run():
        collector, processor = database(urls["collector"]), database(urls["processor"])
        subject = uuid4()
        try:
            writer, store, replay = PostgresIntake(collector), PostgresProcessor(processor), PostgresReplay(processor)
            await seed(writer, *(event(subject, event_id=UUID(int=i)) for i in range(1, 4)))
            await drain(store, CONSUMER)
            await drain(store, OTHER)
            with connect(data, name) as connection:
                connection.execute("UPDATE analytics.admission_budget SET max_outstanding_jobs=4")
            state = await advance_to(replay, await replay.begin(CONSUMER, 1, subject, uuid4()), "scan")
            state = await replay.step(state.handle)
            assert state.blocked and state.cursor_event_id == UUID(int=2)
            with connect(data, name) as connection:
                connection.execute("UPDATE analytics.processing_jobs SET next_attempt_at=clock_timestamp()+interval '60 seconds' WHERE consumer=%s AND event_id=%s", (CONSUMER, UUID(int=2)))
            await quarantine(store, target, subject)
            with connect(data, name) as connection:
                assert connection.execute("SELECT status,work_class FROM analytics.processing_jobs WHERE consumer=%s AND event_id=%s", (CONSUMER, UUID(int=1))).fetchone() == ("quarantined", "replay")
            still_blocked = await replay.step(state.handle)
            assert still_blocked.blocked and still_blocked.cursor_event_id == UUID(int=2)
            await seed(writer, event(uuid4(), "live"))
            with connect(data, name) as connection:
                assert connection.execute("SELECT count(*) FROM analytics.processing_jobs WHERE status<>'completed'").fetchone() == (4,)
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_dirty_other_course_remains_indistinguishable_from_no_data(target):
    _, _, urls = target
    async def run():
        collector, processor, query = (database(urls[r]) for r in ("collector", "processor", "query"))
        subject, no_data_subject = uuid4(), uuid4()
        try:
            writer, store, replay, reader = PostgresIntake(collector), PostgresProcessor(processor), PostgresReplay(processor), PostgresSyntheticCounts(query)
            assert await reader.read_counts(CONSUMER, 1, no_data_subject, no_data_subject, ("a",)) is None
            await seed(writer, event(subject, "b"))
            await drain(store)
            assert await reader.read_counts(CONSUMER, 1, subject, subject, ("a",)) is None
            state = await replay.begin(CONSUMER, 1, subject, uuid4())
            assert await reader.read_counts(CONSUMER, 1, subject, subject, ("a",)) is None
            with pytest.raises(ProjectionUnavailable):
                await reader.read_counts(CONSUMER, 1, subject, subject, ("b",))
            state = await advance_to(replay, state, "drain")
            await drain(store)
            assert (await replay.finish(state.handle)).phase == "complete"
        finally:
            for d in (collector, processor, query): await d.close()
    asyncio.run(run())


def test_live_priority_has_bounded_fairness_for_replay_jobs(target):
    data, name, urls = target
    async def run():
        collector, processor = database(urls["collector"]), database(urls["processor"])
        subject = uuid4()
        try:
            writer, store, replay = PostgresIntake(collector), PostgresProcessor(processor), PostgresReplay(processor)
            original = event(subject, "rebuild", UUID(int=1))
            await seed(writer, original)
            await drain(store, CONSUMER)
            state = await replay.begin(CONSUMER, 1, subject, uuid4())
            state = await advance_to(replay, state, "scan")
            state = await replay.step(state.handle)
            assert state.phase == "drain"
            await seed(writer, *(event(subject, "live", UUID(int=i)) for i in range(2, 7)))
            classes = []
            for _ in range(4):
                claim = await store.claim(CONSUMER, 1, uuid4(), 1, 30)
                assert claim
                with connect(data, name) as connection:
                    kind = connection.execute("SELECT work_class FROM analytics.processing_jobs WHERE consumer=%s AND event_id=%s", (CONSUMER, claim.facts[0].record.event.event_id)).fetchone()[0]
                    classes.append(kind)
                await store.commit(claim, synthetic_delta(claim.facts))
            assert classes[0] == "live" and "replay" in classes
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_upgrade_from_a004_preserves_fact_receipt_result_and_budget():
    if os.environ.get("ANALYTICS_RUN_SYNTHETIC_REPLAY") != "1":
        pytest.skip("explicit synthetic replay runner required")
    data = config(ROOT / ".local" / "postgres-connection.json")
    name, token, urls = create(data)
    subject, item = uuid4(), None
    try:
        engine = create_engine(urls["migrate"].replace("postgresql://", "postgresql+psycopg://", 1))
        try:
            with engine.begin() as connection:
                cfg = Config(str(ROOT / "alembic.ini"))
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "a004")
        finally:
            engine.dispose()
        item = event(subject)
        async def seed_old():
            collector = database(urls["collector"])
            try: await seed(PostgresIntake(collector), item)
            finally: await collector.close()
        asyncio.run(seed_old())
        with connect(data, name) as connection:
            connection.execute("INSERT INTO analytics.processing_receipts VALUES (%s,1,%s,%s,%s)",
                               (CONSUMER, item.producer_namespace, item.event.event_id, subject))
            connection.execute("""INSERT INTO analytics.processing_results
                (consumer,consumer_version,subject_ref,subject_epoch,courses,fingerprint_xor,covered_count)
                VALUES (%s,1,%s,0,'{"a":1}'::jsonb,%s,1)""", (CONSUMER, subject, "0" * 64))
            connection.execute("UPDATE analytics.processing_jobs SET status='completed' WHERE consumer=%s", (CONSUMER,))
        upgrade_to(urls["migrate"], "a005")
        with connect(data, name) as connection:
            assert connection.execute("SELECT version_num FROM public.alembic_version").fetchone() == ("a005",)
            assert connection.execute("SELECT count(*) FROM analytics.activity_facts").fetchone() == (1,)
            assert connection.execute("SELECT count(*) FROM analytics.processing_receipts").fetchone() == (1,)
            assert connection.execute("SELECT courses,covered_count FROM analytics.processing_results").fetchone() == ({"a": 1}, 1)
            assert connection.execute("SELECT accepted_count FROM analytics.admission_budget").fetchone() == (1,)
            assert connection.execute("SELECT work_class FROM analytics.processing_jobs WHERE consumer=%s", (CONSUMER,)).fetchone() == ("live",)
    finally:
        cleanup(data, name, token)


@pytest.mark.parametrize("corruption", ["receipt_a_as_b", "receipt_b_as_a", "job_a_as_b"])
def test_a004_mismatched_ownership_blocks_upgrade_without_deleting_data(corruption):
    if os.environ.get("ANALYTICS_RUN_SYNTHETIC_REPLAY") != "1":
        pytest.skip("explicit synthetic replay runner required")
    data = config(ROOT / ".local" / "postgres-connection.json")
    name, token, urls = create(data)
    try:
        engine = create_engine(urls["migrate"].replace("postgresql://", "postgresql+psycopg://", 1))
        try:
            with engine.begin() as connection:
                cfg = Config(str(ROOT / "alembic.ini"))
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "a004")
        finally:
            engine.dispose()
        a, b = uuid4(), uuid4()
        fact_a, fact_b = event(a), event(b)
        async def seed_old():
            collector = database(urls["collector"])
            try: await seed(PostgresIntake(collector), fact_a, fact_b)
            finally: await collector.close()
        asyncio.run(seed_old())
        with connect(data, name) as connection:
            if corruption == "job_a_as_b":
                connection.execute("UPDATE analytics.processing_jobs SET subject_ref=%s WHERE consumer=%s AND event_id=%s", (b, CONSUMER, fact_a.event.event_id))
            else:
                wrong_fact, wrong_owner, good_fact, good_owner = ((fact_a, b, fact_b, b) if corruption == "receipt_a_as_b" else (fact_b, a, fact_a, a))
                for fact, owner in ((wrong_fact, wrong_owner), (good_fact, good_owner)):
                    connection.execute("INSERT INTO analytics.processing_receipts VALUES (%s,1,%s,%s,%s)",
                                       (CONSUMER, fact.producer_namespace, fact.event.event_id, owner))
            before = connection.execute("SELECT producer_namespace,event_id,subject_ref FROM analytics.processing_receipts ORDER BY event_id").fetchall()
        with pytest.raises(DBAPIError):
            upgrade_to(urls["migrate"], "a005")
        with connect(data, name) as connection:
            assert connection.execute("SELECT version_num FROM public.alembic_version").fetchone() == ("a004",)
            assert connection.execute("SELECT count(*) FROM analytics.activity_facts").fetchone() == (2,)
            assert connection.execute("SELECT count(*) FROM analytics.processing_jobs").fetchone() == (4,)
            assert connection.execute("SELECT producer_namespace,event_id,subject_ref FROM analytics.processing_receipts ORDER BY event_id").fetchall() == before
            if corruption == "job_a_as_b":
                assert connection.execute("SELECT subject_ref FROM analytics.processing_jobs WHERE consumer=%s AND event_id=%s", (CONSUMER, fact_a.event.event_id)).fetchone() == (b,)
    finally:
        cleanup(data, name, token)


def test_new_mismatched_receipt_and_job_are_rejected(target):
    _, _, urls = target
    async def run():
        collector, processor = database(urls["collector"]), database(urls["processor"])
        item, wrong = event(uuid4()), uuid4()
        try:
            await seed(PostgresIntake(collector), item)
            with pytest.raises(DBAPIError):
                async with processor.transaction() as session:
                    await session.execute(text("""INSERT INTO analytics.processing_receipts
                        (consumer,consumer_version,producer_namespace,event_id,subject_ref)
                        VALUES (:consumer,1,:producer,:event,:wrong)"""),
                        {"consumer": CONSUMER, "producer": item.producer_namespace, "event": item.event.event_id, "wrong": wrong})
            with pytest.raises(DBAPIError):
                async with processor.transaction() as session:
                    await session.execute(text("""INSERT INTO analytics.processing_jobs
                        (producer_namespace,event_id,consumer,consumer_version,subject_ref,status,work_class)
                        VALUES (:producer,:event,:consumer,2,:wrong,'pending','replay')"""),
                        {"consumer": CONSUMER, "producer": item.producer_namespace, "event": item.event.event_id, "wrong": wrong})
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())
