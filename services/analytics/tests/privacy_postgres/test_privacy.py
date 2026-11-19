"""Local journal application; authority authentication is tested separately."""
import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from alembic import command
from alembic.config import Config

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.intake import PostgresIntake
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore, PostgresSubjectFence
from pymentor_analytics.adapters.postgres.processor import PostgresProcessor, PostgresSyntheticCounts
from pymentor_analytics.adapters.postgres.replay import PostgresReplay
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.privacy_control_v1 import JournalEntry, deletion_hash
from pymentor_analytics.contracts.privacy_storage_v1 import PrivacyAuthorityMismatch, PrivacyConflict, PrivacySequenceGap
from pymentor_analytics.contracts.processing_v1 import ProjectionUnavailable, StaleClaim, synthetic_delta
from pymentor_analytics.contracts.replay_v1 import ReplayForbidden
from pymentor_analytics.contracts.v2 import AcceptedEvent, DeletionFence, parse_browser_item
from pymentor_analytics.modules.collector.ports import IntakeForbidden
from tools.bootstrap_postgres import cleanup, config, connect, create

ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = "synthetic-authority-1"
CONSUMER = "synthetic.activity"
OTHER = "synthetic.independent"


@pytest.fixture
def target():
    if os.environ.get("ANALYTICS_RUN_SYNTHETIC_PRIVACY") != "1":
        pytest.skip("explicit synthetic privacy runner required")
    data = config(ROOT / ".local" / "postgres-connection.json")
    name, token, urls = create(data)
    try:
        upgrade(Settings(database_url=urls["migrate"]), target_revision="a006")
        yield data, name, urls
    finally:
        cleanup(data, name, token)


def database(url):
    return Database(Settings(database_url=url, pool_size=2, pool_overflow=0, db_timeout_seconds=5))


def journal(seq, subject, *, control_id=None, epoch=1, producer="synthetic-producer"):
    now = datetime.now(timezone.utc)
    control = DeletionFence(schema_version=2, kind="deletion_fence", control_id=control_id or uuid4(),
                            subject_ref=subject, epoch=epoch, occurred_at=now)
    return JournalEntry(seq=seq, producer=producer, control=control, received_at=now,
                        payload_hash=deletion_hash(producer, control))


def activity(subject, course="backend"):
    raw = json.loads((ROOT / "contracts/fixtures/browser-batch.json").read_text(encoding="utf-8"))["events"][0]
    now = datetime.now(timezone.utc)
    raw.update(event_id=str(uuid4()), course_id=course, started_at=now.isoformat(),
               ended_at=now.isoformat(), last_action_at=now.isoformat())
    return AcceptedEvent(producer_namespace="synthetic", subject_ref=subject, source="browser",
                         event=parse_browser_item(json.dumps(raw).encode()), received_at=now)


async def seed(writer, *events):
    for event in events:
        assert await writer.accept(event, deadline=time.monotonic() + 4) == "accepted"


async def drain(store, consumer):
    for _ in range(20):
        batch = await store.claim(consumer, 1, uuid4(), 100, 30)
        if batch is None: return
        await store.commit(batch, synthetic_delta(batch.facts))
    raise AssertionError("bounded processor drain exceeded")


def scalar(target, statement, args=()):
    data, name, _ = target
    with connect(data, name) as connection:
        return connection.execute(statement, args).fetchone()[0]


def test_deletion_before_first_event_and_new_lifecycle(target):
    _, _, urls = target
    async def run():
        collector, privacy = database(urls["collector"]), database(urls["privacy"])
        old, new = uuid4(), uuid4()
        try:
            store, writer = PostgresPrivacyStore(privacy), PostgresIntake(collector)
            assert (await store.cursor(AUTHORITY)).applied_through == 0
            result = await store.apply(AUTHORITY, journal(1, old), limit=1)
            assert result.complete and result.applied_through == 1
            assert not await PostgresSubjectFence(collector).permits(old)
            with pytest.raises(IntakeForbidden): await writer.accept(activity(old), deadline=time.monotonic() + 3)
            assert await PostgresSubjectFence(collector).permits(new)
            assert await writer.accept(activity(new), deadline=time.monotonic() + 3) == "accepted"
        finally:
            await collector.close(); await privacy.close()
    asyncio.run(run())
    assert scalar(target, "SELECT count(*) FROM analytics.subject_fences WHERE deleted") == 1
    assert scalar(target, "SELECT count(*) FROM analytics.privacy_receipts WHERE phase='complete'") == 1


def test_contiguous_prefix_same_id_conflict_and_immutable_authority(target):
    _, _, urls = target
    async def run():
        privacy, query = database(urls["privacy"]), database(urls["query"])
        subject = uuid4()
        try:
            store = PostgresPrivacyStore(privacy)
            assert (await store.cursor(AUTHORITY)).applied_through == 0
            with pytest.raises(PrivacySequenceGap): await store.apply(AUTHORITY, journal(2, subject))
            base = journal(1, subject)
            with pytest.raises(PrivacyConflict):
                await store.apply(AUTHORITY, base.model_copy(update={"seq": 2**63}))
            oversized_control = base.control.model_copy(update={"epoch": 2**63})
            oversized = base.model_copy(update={"control": oversized_control,
                                                "payload_hash": deletion_hash(base.producer, oversized_control)})
            with pytest.raises(PrivacyConflict): await store.apply(AUTHORITY, oversized)
            first = journal(1, subject, epoch=3)
            assert (await store.apply(AUTHORITY, first)).complete
            same = first.model_copy(update={"received_at": first.received_at + timedelta(seconds=10)})
            assert (await store.apply(AUTHORITY, same)).applied_through == 1
            altered_control = first.control.model_copy(update={"epoch": 4})
            altered = JournalEntry(seq=2, producer=first.producer, control=altered_control,
                                   received_at=first.received_at, payload_hash=deletion_hash(first.producer, altered_control))
            with pytest.raises(PrivacyConflict): await store.apply(AUTHORITY, altered)
            with pytest.raises(PrivacyConflict): await store.apply(AUTHORITY, first.model_copy(update={"seq": 2}))
            with pytest.raises(PrivacyConflict): await store.apply(AUTHORITY, first.model_copy(update={"payload_hash": "0" * 64}))
            with pytest.raises(PrivacyConflict): await store.apply(AUTHORITY, journal(1, uuid4()))
            with pytest.raises(PrivacySequenceGap): await store.apply(AUTHORITY, journal(3, uuid4()))
            assert (await store.apply(AUTHORITY, journal(2, subject, epoch=2))).complete
            assert (await store.cursor(AUTHORITY)).applied_through == 2
            for statement in ("UPDATE analytics.subject_fences SET deleted=false",
                              "UPDATE analytics.subject_fences SET epoch=1",
                              "UPDATE analytics.subject_fences SET subject_ref=gen_random_uuid()",
                              "DELETE FROM analytics.subject_fences"):
                with pytest.raises(DBAPIError):
                    async with privacy.transaction() as session:
                        await session.execute(text(statement))
            with pytest.raises(PrivacyAuthorityMismatch): await store.cursor("other-authority")
            with pytest.raises(DBAPIError):
                async with privacy.transaction() as session:
                    await session.execute(text("UPDATE analytics.privacy_state SET authority_id='other'"))
            async with query.transaction() as session:
                row = (await session.execute(text("SELECT authority_id,applied_through FROM analytics.privacy_state WHERE singleton=true"))).one()
                assert tuple(row) == (AUTHORITY, 2)
            with pytest.raises(DBAPIError):
                async with query.transaction() as session:
                    await session.execute(text("SELECT * FROM analytics.privacy_receipts"))
        finally:
            await privacy.close(); await query.close()
    asyncio.run(run())
    assert scalar(target, "SELECT epoch FROM analytics.subject_fences") == 3


def test_crash_after_fence_partial_purge_resumes_and_ack_is_last(target):
    data, name, urls = target
    async def run():
        collector, processor, privacy = (database(urls[r]) for r in ("collector", "processor", "privacy"))
        subject = uuid4()
        class LostReply(PostgresPrivacyStore):
            async def _after_commit(self): raise RuntimeError("synthetic lost reply")
        try:
            await seed(PostgresIntake(collector), *(activity(subject, "a" if i % 2 else "b") for i in range(5)))
            worker = PostgresProcessor(processor)
            await drain(worker, CONSUMER); await drain(worker, OTHER)
            with connect(data, name) as connection:
                for generation in range(1, 5):
                    connection.execute("""INSERT INTO analytics.processing_replays
                        (consumer,consumer_version,subject_ref,request_id,generation,subject_epoch,phase)
                        VALUES (%s,1,%s,%s,%s,0,'complete')""", (CONSUMER, subject, uuid4(), generation))
            await PostgresReplay(processor).begin(CONSUMER, 1, subject, uuid4())
            store = PostgresPrivacyStore(privacy)
            await store.cursor(AUTHORITY)
            item = journal(1, subject, epoch=2)
            started = time.monotonic()
            with pytest.raises(RuntimeError): await LostReply(privacy).apply(AUTHORITY, item, 2)
            assert scalar(target, "SELECT count(*) FROM analytics.activity_facts WHERE subject_ref=%s", (subject,)) == 3
            assert scalar(target, "SELECT applied_through FROM analytics.privacy_state") == 0
            assert scalar(target, "SELECT count(*) FROM analytics.processing_replays WHERE subject_ref=%s", (subject,)) == 3
            assert scalar(target, "SELECT deleted FROM analytics.subject_fences WHERE subject_ref=%s", (subject,)) is True
            assert scalar(target, "SELECT phase FROM analytics.privacy_receipts WHERE seq=1") == "purging"
            with pytest.raises(IntakeForbidden): await PostgresIntake(collector).accept(activity(subject), deadline=time.monotonic() + 3)
            # New pool/store simulates coordinator restart; no in-memory checkpoint is reused.
            await privacy.close()
            privacy = database(urls["privacy"])
            store = PostgresPrivacyStore(privacy)
            for _ in range(20):
                progress = await store.apply(AUTHORITY, item, 2)
                assert max(progress.removed_facts, progress.removed_results, progress.removed_leases,
                           progress.removed_replays, progress.removed_windows) <= 2
                if progress.complete: break
            else:
                raise AssertionError("bounded deletion did not complete")
            elapsed = time.monotonic() - started
            print(f"synthetic_delete_elapsed_seconds={elapsed:.3f}")
            assert elapsed < 60
            assert progress.applied_through == 1 and progress.epoch == 2
            for table in ("activity_facts", "processing_jobs", "processing_receipts", "processing_results",
                          "processing_leases", "processing_replays", "admission_subject_windows"):
                assert scalar(target, f"SELECT count(*) FROM analytics.{table} WHERE subject_ref=%s", (subject,)) == 0
            assert scalar(target, "SELECT count(*) FROM analytics.privacy_receipts WHERE phase='complete'") == 1
            assert (await store.apply(AUTHORITY, item, 2)).complete
        finally:
            for db in (collector, processor, privacy): await db.close()
    asyncio.run(run())


def test_claim_and_replay_cannot_publish_after_deletion(target):
    _, _, urls = target
    async def run():
        collector, processor, privacy = (database(urls[r]) for r in ("collector", "processor", "privacy"))
        subject = uuid4()
        try:
            await seed(PostgresIntake(collector), activity(subject))
            worker, replay, store = PostgresProcessor(processor), PostgresReplay(processor), PostgresPrivacyStore(privacy)
            claim = await worker.claim(CONSUMER, 1, uuid4(), 1, 30)
            assert claim
            item = journal(1, subject)
            assert (await store.apply(AUTHORITY, item)).complete
            with pytest.raises(StaleClaim): await worker.commit(claim, synthetic_delta(claim.facts))
            with pytest.raises(ReplayForbidden): await replay.begin(CONSUMER, 1, subject, uuid4())
            assert await worker.claim(CONSUMER, 1, uuid4(), 1, 30) is None
            for table in ("activity_facts", "processing_jobs", "processing_receipts", "processing_results", "processing_leases"):
                assert scalar(target, f"SELECT count(*) FROM analytics.{table} WHERE subject_ref=%s", (subject,)) == 0
        finally:
            for db in (collector, processor, privacy): await db.close()
    asyncio.run(run())


def test_existing_replay_handle_forbidden_after_deletion(target):
    _, _, urls = target
    async def run():
        collector, processor, privacy = (database(urls[r]) for r in ("collector", "processor", "privacy"))
        subject = uuid4()
        try:
            await seed(PostgresIntake(collector), activity(subject))
            replay, store = PostgresReplay(processor), PostgresPrivacyStore(privacy)
            state = await replay.begin(CONSUMER, 1, subject, uuid4())
            assert (await store.apply(AUTHORITY, journal(1, subject))).complete
            with pytest.raises(ReplayForbidden): await replay.step(state.handle)
            with pytest.raises(ReplayForbidden): await replay.finish(state.handle)
            assert scalar(target, "SELECT count(*) FROM analytics.processing_replays WHERE subject_ref=%s", (subject,)) == 0
        finally:
            for db in (collector, processor, privacy): await db.close()
    asyncio.run(run())


@pytest.mark.parametrize("deleted_identity", ["actor", "target"])
def test_read_lock_drains_before_delete_ack(target, deleted_identity):
    _, _, urls = target
    async def run():
        collector, processor, query, privacy = (database(urls[r]) for r in ("collector", "processor", "query", "privacy"))
        actor, subject = uuid4(), uuid4()
        reached, release = asyncio.Event(), asyncio.Event()
        class GatedReader(PostgresSyntheticCounts):
            async def _before_materialize(self):
                reached.set()
                await release.wait()
        try:
            await seed(PostgresIntake(collector), activity(subject))
            await drain(PostgresProcessor(processor), CONSUMER)
            store = PostgresPrivacyStore(privacy)
            await store.cursor(AUTHORITY)
            reader = GatedReader(query)
            reading = asyncio.create_task(reader.read_counts(CONSUMER, 1, actor, subject, ("backend",)))
            await asyncio.wait_for(reached.wait(), 2)
            item = journal(1, actor if deleted_identity == "actor" else subject)
            deleting = asyncio.create_task(store.apply(AUTHORITY, item))
            await asyncio.sleep(.1)
            assert not deleting.done()
            assert scalar(target, "SELECT applied_through FROM analytics.privacy_state") == 0
            release.set()
            value = await reading
            assert value and value.rows[0].event_count == 1
            assert (await deleting).complete
            if deleted_identity == "actor":
                with pytest.raises(ProjectionUnavailable): await reader.read_counts(CONSUMER, 1, actor, subject, ("backend",))
            else:
                assert await reader.read_counts(CONSUMER, 1, actor, subject, ("backend",)) is None
        finally:
            release.set()
            for db in (collector, processor, query, privacy): await db.close()
    asyncio.run(run())


def test_before_commit_failure_does_not_advance_or_create_fence(target):
    _, _, urls = target
    async def run():
        privacy = database(urls["privacy"])
        subject = uuid4()
        class Crash(PostgresPrivacyStore):
            async def _before_commit(self): raise RuntimeError("synthetic crash")
        try:
            store = PostgresPrivacyStore(privacy)
            await store.cursor(AUTHORITY)
            with pytest.raises(RuntimeError): await Crash(privacy).apply(AUTHORITY, journal(1, subject))
            assert (await store.cursor(AUTHORITY)).applied_through == 0
            assert scalar(target, "SELECT count(*) FROM analytics.subject_fences WHERE subject_ref=%s", (subject,)) == 0
            assert scalar(target, "SELECT count(*) FROM analytics.privacy_receipts") == 0
        finally:
            await privacy.close()
    asyncio.run(run())


def test_global_control_order_serializes_two_inflight_entries(target):
    _, _, urls = target
    async def run():
        first_db, second_db, holder_db = (database(urls["privacy"]) for _ in range(3))
        subject1, subject2 = uuid4(), uuid4()
        held, release, control_locked = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class ObservableStore(PostgresPrivacyStore):
            async def _locked_state(self, session, authority_id):
                state = await super()._locked_state(session, authority_id)
                control_locked.set()
                return state
        async def hold_subject():
            from pymentor_analytics.contracts.locking_v1 import subject_lock_key
            async with holder_db.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject1)})
                held.set()
                await release.wait()
        holder = None
        try:
            await PostgresPrivacyStore(first_db).cursor(AUTHORITY)
            holder = asyncio.create_task(hold_subject())
            await asyncio.wait_for(held.wait(), 2)
            first = asyncio.create_task(ObservableStore(first_db).apply(AUTHORITY, journal(1, subject1)))
            await asyncio.wait_for(control_locked.wait(), 2)
            second = asyncio.create_task(PostgresPrivacyStore(second_db).apply(AUTHORITY, journal(2, subject2)))
            await asyncio.sleep(.1)
            assert not first.done() and not second.done()
            assert scalar(target, "SELECT applied_through FROM analytics.privacy_state") == 0
            release.set()
            await holder
            assert (await first).applied_through == 1
            assert (await second).applied_through == 2
        finally:
            release.set()
            if holder and not holder.done(): await holder
            for db in (first_db, second_db, holder_db): await db.close()
    asyncio.run(run())


def test_final_commit_lost_reply_reconciles_by_same_control(target):
    _, _, urls = target
    async def run():
        privacy = database(urls["privacy"])
        class LostReply(PostgresPrivacyStore):
            async def _after_commit(self): raise RuntimeError("synthetic lost final response")
        try:
            item = journal(1, uuid4())
            with pytest.raises(RuntimeError): await LostReply(privacy).apply(AUTHORITY, item)
            store = PostgresPrivacyStore(privacy)
            assert (await store.cursor(AUTHORITY)).applied_through == 1
            assert (await store.apply(AUTHORITY, item)).complete
            assert (await store.apply(AUTHORITY, journal(2, uuid4()))).applied_through == 2
            assert scalar(target, "SELECT count(*) FROM analytics.privacy_receipts WHERE phase='complete'") == 2
        finally:
            await privacy.close()
    asyncio.run(run())


def test_runtime_roles_cannot_write_privacy_cursor_or_apply_deletion(target):
    _, _, urls = target
    async def run():
        privacy = database(urls["privacy"])
        runtime = [database(urls[role]) for role in ("collector", "processor", "query")]
        try:
            await PostgresPrivacyStore(privacy).cursor(AUTHORITY)
            for db in runtime:
                async with db.transaction() as session:
                    assert (await session.execute(text("SELECT authority_id,applied_through FROM analytics.privacy_state WHERE singleton=true"))).one() == (AUTHORITY, 0)
                with pytest.raises(DBAPIError):
                    async with db.transaction() as session:
                        await session.execute(text("UPDATE analytics.privacy_state SET applied_through=999"))
                with pytest.raises(DBAPIError):
                    async with db.transaction() as session:
                        await session.execute(text("SELECT * FROM analytics.privacy_receipts"))
            with pytest.raises(DBAPIError):
                await PostgresPrivacyStore(runtime[0]).apply(AUTHORITY, journal(1, uuid4()))
        finally:
            await privacy.close()
            for db in runtime: await db.close()
    asyncio.run(run())


def test_a005_to_a006_preserves_existing_state_and_starts_unbound():
    if os.environ.get("ANALYTICS_RUN_SYNTHETIC_PRIVACY") != "1":
        pytest.skip("explicit synthetic privacy runner required")
    data = config(ROOT / ".local" / "postgres-connection.json")
    name, token, urls = create(data)
    subject, previous_tombstone = uuid4(), uuid4()
    try:
        engine = create_engine(urls["migrate"].replace("postgresql://", "postgresql+psycopg://", 1))
        try:
            with engine.begin() as connection:
                cfg = Config(str(ROOT / "alembic.ini"))
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "a005")
        finally:
            engine.dispose()
        async def seed_old():
            collector, processor, privacy = (database(urls[r]) for r in ("collector", "processor", "privacy"))
            try:
                await seed(PostgresIntake(collector), activity(subject))
                await drain(PostgresProcessor(processor), CONSUMER)
                await PostgresReplay(processor).begin(CONSUMER, 1, subject, uuid4())
                async with privacy.transaction() as session:
                    await session.execute(text("INSERT INTO analytics.subject_fences VALUES (:subject,4,true,:control)"),
                                          {"subject": previous_tombstone, "control": uuid4()})
            finally:
                for db in (collector, processor, privacy): await db.close()
        asyncio.run(seed_old())
        with connect(data, name) as connection:
            before = tuple(connection.execute(f"SELECT count(*) FROM analytics.{table}").fetchone()[0]
                           for table in ("activity_facts", "processing_jobs", "processing_receipts", "processing_results",
                                         "processing_leases", "processing_replays", "subject_fences"))
            assert before == (1, 2, 1, 1, 1, 1, 1)
        upgrade(Settings(database_url=urls["migrate"]), target_revision="a006")
        with connect(data, name) as connection:
            after = tuple(connection.execute(f"SELECT count(*) FROM analytics.{table}").fetchone()[0]
                          for table in ("activity_facts", "processing_jobs", "processing_receipts", "processing_results",
                                        "processing_leases", "processing_replays", "subject_fences"))
            assert after == before
            assert connection.execute("SELECT count(*) FROM analytics.privacy_state").fetchone() == (0,)
            assert connection.execute("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=%s", (previous_tombstone,)).fetchone() == (4, True)
    finally:
        cleanup(data, name, token)
