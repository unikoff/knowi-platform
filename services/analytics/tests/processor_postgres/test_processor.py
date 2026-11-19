"""Real PostgreSQL concurrency and recovery in fresh synthetic DBs only."""
import asyncio
import hashlib
import json
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

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
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.processing_v1 import ProjectionUnavailable, StaleClaim, synthetic_delta
from pymentor_analytics.contracts.v2 import AcceptedEvent, parse_browser_item
from tools.bootstrap_postgres import cleanup, config, connect, create

ROOT = Path(__file__).resolve().parents[2]
CONSUMER = "synthetic.activity"
OTHER = "synthetic.independent"


@pytest.fixture
def target():
    if os.environ.get("ANALYTICS_RUN_SYNTHETIC_PROCESSOR") != "1":
        pytest.skip("explicit synthetic processor runner required")
    data = config(ROOT / ".local" / "postgres-connection.json")
    name, token, urls = create(data)
    try:
        upgrade(Settings(database_url=urls["migrate"]), target_revision="a006")
        yield data, name, urls
    finally:
        cleanup(data, name, token)


def database(url):
    return Database(Settings(database_url=url, pool_size=2, pool_overflow=0, db_timeout_seconds=5))


def event(subject, course="backend"):
    raw = json.loads((ROOT / "contracts/fixtures/browser-batch.json").read_text(encoding="utf-8"))["events"][0]
    now = datetime.now(timezone.utc)
    raw.update(event_id=str(uuid4()), course_id=course, started_at=now.isoformat(), ended_at=now.isoformat(), last_action_at=now.isoformat())
    return AcceptedEvent(producer_namespace="synthetic", subject_ref=subject, source="browser",
                         event=parse_browser_item(json.dumps(raw).encode()), received_at=now)


async def seed(writer, *records):
    for record in records:
        assert await writer.accept(record, deadline=time.monotonic() + 4) == "accepted"


def result(target, consumer, subject):
    data, name, _ = target
    with connect(data, name) as connection:
        return connection.execute("""SELECT courses,covered_count FROM analytics.processing_results
            WHERE consumer=%s AND consumer_version=1 AND subject_ref=%s""", (consumer, subject)).fetchone()


def expire(target):
    data, name, _ = target
    with connect(data, name) as connection:
        connection.execute("UPDATE analytics.processing_leases SET expires_at=clock_timestamp()-interval '1 second'")


def make_due(target, consumer, subject):
    data, name, _ = target
    with connect(data, name) as connection:
        connection.execute("UPDATE analytics.processing_jobs SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE consumer=%s AND subject_ref=%s", (consumer, subject))


def test_two_instances_and_independent_consumers(target):
    _, _, urls = target
    async def run():
        collector, d1, d2 = database(urls["collector"]), database(urls["processor"]), database(urls["processor"])
        subject = uuid4()
        records = (event(subject, "a"), event(subject, "a"), event(subject, "b"))
        try:
            await seed(PostgresIntake(collector), *records)
            p1, p2 = PostgresProcessor(d1), PostgresProcessor(d2)
            worker1, worker2 = uuid4(), uuid4()
            claims = await asyncio.gather(p1.claim(CONSUMER, 1, worker1, 100, 30), p2.claim(CONSUMER, 1, worker2, 100, 30))
            winner = next(c for c in claims if c is not None)
            assert sum(c is not None for c in claims) == 1 and len(winner.facts) == 3
            second = await p2.claim(OTHER, 1, worker2, 100, 30)
            assert second and len(second.facts) == 3
            await p1.commit(winner, synthetic_delta(winner.facts))
            await p2.commit(second, synthetic_delta(second.facts))
            assert result(target, CONSUMER, subject) == ({"a": 2, "b": 1}, 3)
            assert result(target, OTHER, subject) == ({"a": 2, "b": 1}, 3)
            independent_fingerprint = 0
            for record in records:
                canonical = json.dumps(record.model_dump(mode="json", exclude={"received_at"}),
                                       ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
                digest = hashlib.sha256(canonical).hexdigest().encode("ascii")
                namespace = record.producer_namespace.encode()
                encoded = len(namespace).to_bytes(4, "big") + namespace + record.event.event_id.bytes + digest
                independent_fingerprint ^= int.from_bytes(hashlib.sha256(encoded).digest(), "big")
            data, name, _ = target
            with connect(data, name) as connection:
                actual = connection.execute("SELECT fingerprint_xor FROM analytics.processing_results WHERE consumer=%s AND subject_ref=%s", (CONSUMER, subject)).fetchone()[0]
                assert actual == f"{independent_fingerprint:064x}"
        finally:
            for d in (collector, d1, d2): await d.close()
    asyncio.run(run())


def test_expiry_takeover_rejects_stale_and_renew_release(target):
    _, _, urls = target
    async def run():
        collector, d1, d2 = database(urls["collector"]), database(urls["processor"]), database(urls["processor"])
        subject = uuid4()
        try:
            await seed(PostgresIntake(collector), event(subject))
            p1, p2 = PostgresProcessor(d1), PostgresProcessor(d2)
            first = await p1.claim(CONSUMER, 1, uuid4(), 10, 30)
            assert first and await p1.renew(first, 30)
            expire(target)
            assert await p2.claim(CONSUMER, 1, uuid4(), 10, 30) is None
            make_due(target, CONSUMER, subject)
            next_claim = await p2.claim(CONSUMER, 1, uuid4(), 10, 30)
            assert next_claim and next_claim.generation > first.generation
            with pytest.raises(StaleClaim): await p1.commit(first, synthetic_delta(first.facts))
            assert not await p1.renew(first, 10) and not await p1.release(first)
            assert await p2.release(next_claim)
            final = await p1.claim(CONSUMER, 1, uuid4(), 10, 30)
            assert final and final.generation > next_claim.generation
            await p1.commit(final, synthetic_delta(final.facts))
            assert result(target, CONSUMER, subject) == ({"backend": 1}, 1)
        finally:
            for d in (collector, d1, d2): await d.close()
    asyncio.run(run())


def test_new_arrival_during_compute_not_acknowledged(target):
    data, name, urls = target
    async def run():
        collector, processor = database(urls["collector"]), database(urls["processor"])
        subject = uuid4()
        try:
            writer, store = PostgresIntake(collector), PostgresProcessor(processor)
            first, second = event(subject, "a"), event(subject, "b")
            await seed(writer, first)
            claim = await store.claim(CONSUMER, 1, uuid4(), 1, 30)
            assert claim and len(claim.facts) == 1
            await seed(writer, second)
            await store.commit(claim, synthetic_delta(claim.facts))
            with connect(data, name) as connection:
                assert connection.execute("SELECT status FROM analytics.processing_jobs WHERE consumer=%s AND event_id=%s", (CONSUMER, second.event.event_id)).fetchone() == ("pending",)
            assert result(target, CONSUMER, subject) == ({"a": 1}, 1)
            later = await store.claim(CONSUMER, 1, uuid4(), 10, 30)
            assert later and len(later.facts) == 1
            await store.commit(later, synthetic_delta(later.facts))
            assert result(target, CONSUMER, subject) == ({"a": 1, "b": 1}, 2)
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_no_partial_effect_before_commit_and_lost_reply_after_commit(target):
    data, name, urls = target
    async def run():
        collector, processor = database(urls["collector"]), database(urls["processor"])
        subject = uuid4()
        class BeforeCrash(PostgresProcessor):
            async def _before_commit(self): raise RuntimeError("synthetic before-commit crash")
        class AfterCrash(PostgresProcessor):
            async def _after_commit(self): raise RuntimeError("synthetic lost reply")
        try:
            await seed(PostgresIntake(collector), event(subject, "a"), event(subject, "b"))
            p1 = BeforeCrash(processor)
            first = await p1.claim(CONSUMER, 1, uuid4(), 100, 30)
            assert first
            with pytest.raises(RuntimeError): await p1.commit(first, synthetic_delta(first.facts))
            assert result(target, CONSUMER, subject) is None
            with connect(data, name) as connection:
                assert connection.execute("SELECT count(*) FROM analytics.processing_receipts").fetchone() == (0,)
                assert connection.execute("SELECT count(*) FROM analytics.processing_jobs WHERE consumer=%s AND status='claimed'", (CONSUMER,)).fetchone() == (2,)
            expire(target)
            p2 = AfterCrash(processor)
            assert await p2.claim(CONSUMER, 1, uuid4(), 100, 30) is None
            make_due(target, CONSUMER, subject)
            second = await p2.claim(CONSUMER, 1, uuid4(), 100, 30)
            assert second
            with pytest.raises(RuntimeError): await p2.commit(second, synthetic_delta(second.facts))
            assert result(target, CONSUMER, subject) == ({"a": 1, "b": 1}, 2)
            with pytest.raises(StaleClaim): await p2.commit(second, synthetic_delta(second.facts))
            assert await p2.claim(CONSUMER, 1, uuid4(), 100, 30) is None
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_completion_compaction_recreated_job_cannot_double_count(target):
    data, name, urls = target
    async def run():
        collector, processor, privacy = database(urls["collector"]), database(urls["processor"]), database(urls["privacy"])
        subject = uuid4()
        item = event(subject, "a")
        try:
            writer, store = PostgresIntake(collector), PostgresProcessor(processor)
            await seed(writer, item)
            claim = await store.claim(CONSUMER, 1, uuid4(), 100, 30)
            assert claim
            await store.commit(claim, synthetic_delta(claim.facts))
            async with privacy.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
                await session.execute(text("DELETE FROM analytics.processing_jobs WHERE event_id=:id AND consumer=:consumer"),
                                      {"id": item.event.event_id, "consumer": CONSUMER})
            assert await writer.accept(item, deadline=time.monotonic() + 4) == "duplicate"
            recreated = await store.claim(CONSUMER, 1, uuid4(), 100, 30)
            assert recreated and recreated.facts[0].already_applied
            assert synthetic_delta(recreated.facts).course_counts == {}
            await store.commit(recreated, synthetic_delta(recreated.facts))
            assert result(target, CONSUMER, subject) == ({"a": 1}, 1)
            with connect(data, name) as connection:
                assert connection.execute("SELECT count(*) FROM analytics.processing_receipts WHERE consumer=%s", (CONSUMER,)).fetchone() == (1,)
        finally:
            for d in (collector, processor, privacy): await d.close()
    asyncio.run(run())


def test_bounded_retries_quarantine_does_not_stop_other_subject(target):
    data, name, urls = target
    async def run():
        collector, processor = database(urls["collector"]), database(urls["processor"])
        a, b = uuid4(), uuid4()
        try:
            await seed(PostgresIntake(collector), event(a, "a"), event(b, "b"))
            store = PostgresProcessor(processor)
            first = await store.claim(CONSUMER, 1, uuid4(), 1, 30)
            assert first
            poison, other = first.subject_ref, b if first.subject_ref == a else a
            assert await store.fail(first) == "retry"
            with connect(data, name) as connection:
                assert connection.execute("SELECT attempts,next_attempt_at>clock_timestamp() FROM analytics.processing_jobs WHERE consumer=%s AND subject_ref=%s", (CONSUMER, poison)).fetchone() == (1, True)
            good = await store.claim(CONSUMER, 1, uuid4(), 1, 30)
            assert good and good.subject_ref == other
            await store.commit(good, synthetic_delta(good.facts))
            for attempt in (2, 3):
                with connect(data, name) as connection:
                    connection.execute("UPDATE analytics.processing_jobs SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE consumer=%s AND subject_ref=%s", (CONSUMER, poison))
                retry = await store.claim(CONSUMER, 1, uuid4(), 1, 30)
                assert retry and retry.subject_ref == poison
                assert await store.fail(retry) == ("retry" if attempt == 2 else "quarantined")
            assert await store.claim(CONSUMER, 1, uuid4(), 1, 30) is None
            with connect(data, name) as connection:
                assert connection.execute("SELECT status,attempts FROM analytics.processing_jobs WHERE consumer=%s AND subject_ref=%s", (CONSUMER, poison)).fetchone() == ("quarantined", 3)
            assert result(target, CONSUMER, other)[1] == 1
            for _ in range(2):
                independent = await store.claim(OTHER, 1, uuid4(), 1, 30)
                assert independent
                await store.commit(independent, synthetic_delta(independent.facts))
            assert result(target, OTHER, poison)[1] == 1
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_expiry_during_commit_rolls_back_every_effect(target):
    data, name, urls = target
    async def run():
        collector, processor = database(urls["collector"]), database(urls["processor"])
        subject = uuid4()
        class SlowCommit(PostgresProcessor):
            async def _before_commit(self): await asyncio.sleep(1.2)
        try:
            await seed(PostgresIntake(collector), event(subject))
            store = SlowCommit(processor)
            claim = await store.claim(CONSUMER, 1, uuid4(), 1, 1)
            assert claim
            with pytest.raises(StaleClaim): await store.commit(claim, synthetic_delta(claim.facts))
            assert result(target, CONSUMER, subject) is None
            with connect(data, name) as connection:
                assert connection.execute("SELECT count(*) FROM analytics.processing_receipts").fetchone() == (0,)
                assert connection.execute("SELECT status FROM analytics.processing_jobs WHERE consumer=%s", (CONSUMER,)).fetchone() == ("claimed",)
            assert await PostgresProcessor(processor).claim(CONSUMER, 1, uuid4(), 1, 30) is None
            make_due(target, CONSUMER, subject)
            takeover = await PostgresProcessor(processor).claim(CONSUMER, 1, uuid4(), 1, 30)
            assert takeover and takeover.generation > claim.generation
            await PostgresProcessor(processor).commit(takeover, synthetic_delta(takeover.facts))
            assert result(target, CONSUMER, subject) == ({"backend": 1}, 1)
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_repeated_worker_expiry_is_bounded_and_quarantined(target):
    data, name, urls = target
    async def run():
        collector, processor = database(urls["collector"]), database(urls["processor"])
        subject = uuid4()
        try:
            await seed(PostgresIntake(collector), event(subject))
            store = PostgresProcessor(processor)
            for attempt in (1, 2, 3):
                claim = await store.claim(CONSUMER, 1, uuid4(), 1, 30)
                assert claim
                expire(target)
                assert await store.claim(CONSUMER, 1, uuid4(), 1, 30) is None
                with connect(data, name) as connection:
                    row = connection.execute("SELECT attempts,status,next_attempt_at>clock_timestamp() FROM analytics.processing_jobs WHERE consumer=%s", (CONSUMER,)).fetchone()
                    assert row == (attempt, "quarantined" if attempt == 3 else "pending", True)
                if attempt < 3:
                    make_due(target, CONSUMER, subject)
            assert await store.claim(CONSUMER, 1, uuid4(), 1, 30) is None
            assert result(target, CONSUMER, subject) is None
            independent = await store.claim(OTHER, 1, uuid4(), 1, 30)
            assert independent
            await store.commit(independent, synthetic_delta(independent.facts))
            assert result(target, OTHER, subject) == ({"backend": 1}, 1)
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_claim_skips_subject_locked_by_privacy_without_holding_partition(target):
    _, _, urls = target
    async def run():
        collector, processor, privacy = (database(urls[r]) for r in ("collector", "processor", "privacy"))
        first, second = uuid4(), uuid4()
        locked, release = asyncio.Event(), asyncio.Event()
        async def hold_subject():
            async with privacy.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(first)})
                locked.set()
                await release.wait()
        try:
            await seed(PostgresIntake(collector), event(first), event(second))
            holder = asyncio.create_task(hold_subject())
            await asyncio.wait_for(locked.wait(), 2)
            store = PostgresProcessor(processor)
            claim = await store.claim(CONSUMER, 1, uuid4(), 1, 30)
            assert claim and claim.subject_ref == second
            await store.commit(claim, synthetic_delta(claim.facts))
            release.set()
            await holder
            resumed = await store.claim(CONSUMER, 1, uuid4(), 1, 30)
            assert resumed and resumed.subject_ref == first
            await store.commit(resumed, synthetic_delta(resumed.facts))
        finally:
            release.set()
            for d in (collector, processor, privacy): await d.close()
    asyncio.run(run())


def test_32_tombstones_before_limit_do_not_starve_healthy_subject(target):
    data, name, urls = target
    async def run():
        collector, processor, privacy = (database(urls[r]) for r in ("collector", "processor", "privacy"))
        blocked, healthy = [uuid4() for _ in range(32)], uuid4()
        try:
            await seed(PostgresIntake(collector), *(event(subject) for subject in (*blocked, healthy)))
            async with privacy.transaction() as session:
                for subject in blocked:
                    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
                    await session.execute(text("INSERT INTO analytics.subject_fences VALUES (:subject,1,true,:control)"),
                                          {"subject": subject, "control": uuid4()})
            store = PostgresProcessor(processor)
            claim = await store.claim(CONSUMER, 1, uuid4(), 1, 30)
            assert claim and claim.subject_ref == healthy
            await store.commit(claim, synthetic_delta(claim.facts))
            assert result(target, CONSUMER, healthy) == ({"backend": 1}, 1)
            with connect(data, name) as connection:
                assert connection.execute("SELECT count(*) FROM analytics.processing_jobs WHERE consumer=%s AND status='pending'", (CONSUMER,)).fetchone() == (32,)
        finally:
            for d in (collector, processor, privacy): await d.close()
    asyncio.run(run())


def test_cursor_reaches_healthy_subject_after_32_busy_subjects(target):
    data, name, urls = target
    async def run():
        collector, processor, privacy = (database(urls[r]) for r in ("collector", "processor", "privacy"))
        blocked, healthy = [uuid4() for _ in range(32)], uuid4()
        locked, release = asyncio.Event(), asyncio.Event()
        async def hold():
            async with privacy.transaction() as session:
                for subject in blocked:
                    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
                locked.set()
                await release.wait()
        holder = None
        try:
            await seed(PostgresIntake(collector), *(event(subject) for subject in (*blocked, healthy)))
            holder = asyncio.create_task(hold())
            await asyncio.wait_for(locked.wait(), 2)
            store = PostgresProcessor(processor)
            assert await store.claim(CONSUMER, 1, uuid4(), 1, 30) is None
            claim = await store.claim(CONSUMER, 1, uuid4(), 1, 30)
            assert claim and claim.subject_ref == healthy
            await store.commit(claim, synthetic_delta(claim.facts))
            with connect(data, name) as connection:
                assert connection.execute("SELECT count(*) FROM analytics.processing_jobs WHERE consumer=%s AND status='pending'", (CONSUMER,)).fetchone() == (32,)
            release.set()
            await holder
        finally:
            release.set()
            if holder and not holder.done(): await holder
            for d in (collector, processor, privacy): await d.close()
    asyncio.run(run())


def test_tombstone_and_exact_membership_fail_closed(target):
    _, _, urls = target
    async def run():
        collector, processor, privacy = database(urls["collector"]), database(urls["processor"]), database(urls["privacy"])
        subject = uuid4()
        try:
            await seed(PostgresIntake(collector), event(subject), event(subject))
            store = PostgresProcessor(processor)
            claim = await store.claim(CONSUMER, 1, uuid4(), 2, 30)
            assert claim and len(claim.facts) == 2
            shortened = replace(claim, facts=claim.facts[:1])
            with pytest.raises(StaleClaim): await store.commit(shortened, synthetic_delta(shortened.facts))
            assert result(target, CONSUMER, subject) is None
            async with privacy.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
                await session.execute(text("INSERT INTO analytics.subject_fences VALUES (:subject,1,true,:control)"),
                                      {"subject": subject, "control": uuid4()})
            with pytest.raises(StaleClaim): await store.commit(claim, synthetic_delta(claim.facts))
            assert not await store.renew(claim, 30) and not await store.release(claim)
            expire(target)
            assert await store.claim(CONSUMER, 1, uuid4(), 2, 30) is None
            assert result(target, CONSUMER, subject) is None
        finally:
            for d in (collector, processor, privacy): await d.close()
    asyncio.run(run())


def test_processor_cannot_mutate_raw_payload(target):
    _, _, urls = target
    async def run():
        processor = database(urls["processor"])
        try:
            with pytest.raises(DBAPIError):
                async with processor.transaction() as session:
                    await session.execute(text("UPDATE analytics.activity_facts SET course_id='incorrect'"))
            with pytest.raises(DBAPIError):
                async with processor.transaction() as session:
                    await session.execute(text("DELETE FROM analytics.processing_receipts"))
        finally:
            await processor.close()
    asyncio.run(run())


@pytest.mark.parametrize("corruption,reason", [("{}", "invalid_fact"), ('"' + 'x' * 8193 + '"', "oversized_fact")])
def test_invalid_or_oversized_fact_quarantined_without_blocking_other_subject(target, corruption, reason):
    data, name, urls = target
    async def run():
        collector, processor = database(urls["collector"]), database(urls["processor"])
        bad, good = event(uuid4()), event(uuid4())
        try:
            await seed(PostgresIntake(collector), bad, good)
            # Deliberate controlled corruption by synthetic test owner only.
            with connect(data, name) as connection:
                connection.execute("UPDATE analytics.activity_facts SET accepted_event=%s::jsonb WHERE event_id=%s", (corruption, bad.event.event_id))
            store = PostgresProcessor(processor)
            claim = await store.claim(CONSUMER, 1, uuid4(), 10, 30)
            assert claim and claim.subject_ref == good.subject_ref
            await store.commit(claim, synthetic_delta(claim.facts))
            with connect(data, name) as connection:
                assert connection.execute("SELECT status,last_failure FROM analytics.processing_jobs WHERE consumer=%s AND event_id=%s", (CONSUMER, bad.event.event_id)).fetchone() == ("quarantined", reason)
            assert result(target, CONSUMER, good.subject_ref) == ({"backend": 1}, 1)
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())


def test_upgrade_from_a003_preserves_fact_jobs_and_budget():
    if os.environ.get("ANALYTICS_RUN_SYNTHETIC_PROCESSOR") != "1":
        pytest.skip("explicit synthetic processor runner required")
    data = config(ROOT / ".local" / "postgres-connection.json")
    name, token, urls = create(data)
    try:
        engine = create_engine(urls["migrate"].replace("postgresql://", "postgresql+psycopg://", 1))
        try:
            with engine.begin() as connection:
                cfg = Config(str(ROOT / "alembic.ini"))
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "a003")
        finally:
            engine.dispose()
        async def seed_old():
            collector = database(urls["collector"])
            try: await seed(PostgresIntake(collector), event(uuid4()))
            finally: await collector.close()
        asyncio.run(seed_old())
        upgrade(Settings(database_url=urls["migrate"]), target_revision="a006")
        with connect(data, name) as connection:
            assert connection.execute("SELECT version_num FROM public.alembic_version").fetchone() == ("a006",)
            assert connection.execute("SELECT count(*) FROM analytics.activity_facts").fetchone() == (1,)
            assert connection.execute("SELECT count(*) FROM analytics.processing_jobs WHERE status='pending'").fetchone() == (2,)
            assert connection.execute("SELECT accepted_count FROM analytics.admission_budget").fetchone() == (1,)
    finally:
        cleanup(data, name, token)


def test_scoped_read_shared_actor_fence_blocks_deletion(target):
    _, _, urls = target
    async def run():
        collector, processor, query, privacy = (database(urls[r]) for r in ("collector", "processor", "query", "privacy"))
        subject, actor = uuid4(), uuid4()
        reached, release, deleted = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class GatedReader(PostgresSyntheticCounts):
            async def _before_materialize(self):
                reached.set()
                await release.wait()
        async def delete_actor():
            async with privacy.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(actor)})
                deleted.set()
                await session.execute(text("INSERT INTO analytics.subject_fences VALUES (:subject,1,true,:control)"),
                                      {"subject": actor, "control": uuid4()})
        try:
            await seed(PostgresIntake(collector), event(subject, "a"), event(subject, "b"))
            worker = PostgresProcessor(processor)
            claim = await worker.claim(CONSUMER, 1, uuid4(), 100, 30)
            assert claim
            await worker.commit(claim, synthetic_delta(claim.facts))
            reader = GatedReader(query)
            reading = asyncio.create_task(reader.read_counts(CONSUMER, 1, actor, subject, ("a",)))
            await asyncio.wait_for(reached.wait(), 2)
            deletion = asyncio.create_task(delete_actor())
            await asyncio.sleep(.1)
            assert not deleted.is_set()
            release.set()
            value = await reading
            assert value and [(row.course_id, row.event_count) for row in value.rows] == [("a", 1)]
            await deletion
            with pytest.raises(ProjectionUnavailable):
                await reader.read_counts(CONSUMER, 1, actor, subject, ("a",))
            with pytest.raises(DBAPIError):
                async with query.transaction() as session:
                    await session.execute(text("UPDATE analytics.processing_results SET covered_count=0"))
            with pytest.raises(ValueError):
                await reader.read_counts(CONSUMER, 1, subject, subject, ("a",) * 101)
        finally:
            for d in (collector, processor, query, privacy): await d.close()
    asyncio.run(run())


def test_target_epoch_change_and_tombstone_never_serve_stale_counts(target):
    _, _, urls = target
    async def run():
        collector, processor, query, privacy = (database(urls[r]) for r in ("collector", "processor", "query", "privacy"))
        subject = uuid4()
        try:
            await seed(PostgresIntake(collector), event(subject, "a"))
            store = PostgresProcessor(processor)
            claim = await store.claim(CONSUMER, 1, uuid4(), 100, 30)
            assert claim
            await store.commit(claim, synthetic_delta(claim.facts))
            reader = PostgresSyntheticCounts(query)
            assert await reader.read_counts(CONSUMER, 1, subject, subject, ("a",)) is not None
            async with privacy.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
                await session.execute(text("INSERT INTO analytics.subject_fences VALUES (:subject,1,false,:control)"),
                                      {"subject": subject, "control": uuid4()})
            with pytest.raises(ProjectionUnavailable):
                await reader.read_counts(CONSUMER, 1, subject, subject, ("a",))
            async with privacy.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
                await session.execute(text("UPDATE analytics.subject_fences SET deleted=true WHERE subject_ref=:subject"), {"subject": subject})
            with pytest.raises(ProjectionUnavailable):
                await reader.read_counts(CONSUMER, 1, subject, subject, ("a",))
            assert await reader.read_counts(CONSUMER, 1, uuid4(), subject, ("a",)) is None
        finally:
            for d in (collector, processor, query, privacy): await d.close()
    asyncio.run(run())


def test_scoped_metadata_does_not_reveal_other_course_processing(target):
    _, _, urls = target
    async def run():
        collector, processor, query = (database(urls[r]) for r in ("collector", "processor", "query"))
        subject = uuid4()
        first = event(subject, "a")
        second = event(subject, "b").model_copy(update={"received_at": first.received_at + timedelta(seconds=2)})
        try:
            writer, store, reader = PostgresIntake(collector), PostgresProcessor(processor), PostgresSyntheticCounts(query)
            await seed(writer, first)
            claim = await store.claim(CONSUMER, 1, uuid4(), 10, 30)
            assert claim
            await store.commit(claim, synthetic_delta(claim.facts))
            before = await reader.read_counts(CONSUMER, 1, subject, subject, ("a",))
            assert before and before.as_of == first.received_at
            absent = await reader.read_counts(CONSUMER, 1, subject, subject, ("unknown",))
            assert absent is None
            await seed(writer, second)
            claim = await store.claim(CONSUMER, 1, uuid4(), 10, 30)
            assert claim
            await store.commit(claim, synthetic_delta(claim.facts))
            after = await reader.read_counts(CONSUMER, 1, subject, subject, ("a",))
            assert after == before
            b_only = await reader.read_counts(CONSUMER, 1, subject, subject, ("b",))
            assert b_only and b_only.as_of == second.received_at
        finally:
            for d in (collector, processor, query): await d.close()
    asyncio.run(run())


def test_no_requested_course_indistinguishable_from_no_projection(target):
    _, _, urls = target
    async def run():
        collector, processor, query, privacy = (database(urls[r]) for r in ("collector", "processor", "query", "privacy"))
        subject = uuid4()
        try:
            reader = PostgresSyntheticCounts(query)
            no_projection = await reader.read_counts(CONSUMER, 1, subject, subject, ("a",))
            assert no_projection is None
            await seed(PostgresIntake(collector), event(subject, "b"))
            store = PostgresProcessor(processor)
            claim = await store.claim(CONSUMER, 1, uuid4(), 10, 30)
            assert claim
            await store.commit(claim, synthetic_delta(claim.facts))
            unrelated_only = await reader.read_counts(CONSUMER, 1, subject, subject, ("a",))
            assert unrelated_only is no_projection
            assert await reader.read_counts(CONSUMER, 1, subject, subject, ("b",)) is not None
            async with privacy.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
                await session.execute(text("INSERT INTO analytics.subject_fences VALUES (:subject,1,false,:control)"),
                                      {"subject": subject, "control": uuid4()})
            assert await reader.read_counts(CONSUMER, 1, subject, subject, ("a",)) is None
            with pytest.raises(ProjectionUnavailable):
                await reader.read_counts(CONSUMER, 1, subject, subject, ("b",))
        finally:
            for d in (collector, processor, query, privacy): await d.close()
    asyncio.run(run())
