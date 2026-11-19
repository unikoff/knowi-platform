"""Real transactions on fresh generated databases; never the retained runtime DB."""
import asyncio
import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from pymentor_analytics.adapters.postgres.activity import ActivityStorage
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.intake import PostgresIntake
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.metadata_v1 import MetadataRejected
from pymentor_analytics.contracts.v2 import AcceptedEvent, canonical_hash, parse_browser_item
from pymentor_analytics.contracts.work_v1 import SYNTHETIC_SUBSCRIPTIONS
from pymentor_analytics.modules.collector.ports import IntakeForbidden, IntakeUnavailable
from tools.bootstrap_postgres import cleanup, config, connect, create

ROOT = Path(__file__).resolve().parents[2]


def record():
    raw = json.loads((ROOT / "contracts/fixtures/browser-batch.json").read_text(encoding="utf-8"))["events"][0]
    raw["event_id"] = str(uuid4())
    now = datetime.now(timezone.utc)
    raw.update(started_at=now.isoformat(), ended_at=now.isoformat(), last_action_at=now.isoformat())
    return AcceptedEvent(producer_namespace="synthetic", subject_ref=uuid4(), source="browser",
                         event=parse_browser_item(json.dumps(raw).encode()), received_at=now)


def settings(url):
    return Settings(database_url=url, pool_size=2, pool_overflow=0, db_timeout_seconds=3)


def upgrade_a001(url):
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.begin() as connection:
            cfg = Config(str(ROOT / "alembic.ini"))
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "a001")
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def target():
    if os.environ.get("ANALYTICS_RUN_SYNTHETIC_INTAKE") != "1":
        pytest.skip("explicit synthetic intake runner required")
    data = config(ROOT / ".local" / "postgres-connection.json")
    name, token, urls = create(data)
    old = record()
    try:
        upgrade_a001(urls["migrate"])
        async def seed():
            database = Database(settings(urls["collector"]))
            try:
                async with database.transaction() as session:
                    assert await ActivityStorage(session).insert(old) == "inserted"
            finally:
                await database.close()
        asyncio.run(seed())
        upgrade(settings(urls["migrate"]), target_revision="a006")
        yield data, name, urls, old
    finally:
        cleanup(data, name, token)


def counts(data, name, item):
    with connect(data, name) as connection:
        fact = connection.execute("SELECT canonical_hash FROM analytics.activity_facts WHERE producer_namespace=%s AND event_id=%s",
                                  (item.producer_namespace, item.event.event_id)).fetchall()
        jobs = connection.execute("SELECT consumer, consumer_version, subject_ref, status FROM analytics.processing_jobs WHERE producer_namespace=%s AND event_id=%s ORDER BY consumer",
                                  (item.producer_namespace, item.event.event_id)).fetchall()
    return fact, jobs


def assert_fact_and_jobs(data, name, item):
    fact, jobs = counts(data, name, item)
    assert fact == [(canonical_hash(item),)]
    assert jobs == [(sub.consumer, sub.version, item.subject_ref, "pending") for sub in SYNTHETIC_SUBSCRIPTIONS]


def test_upgrade_preserves_a001_fact_and_backfills_jobs(target):
    data, name, urls, old = target
    assert_fact_and_jobs(data, name, old)
    async def run():
        db = Database(settings(urls["collector"]))
        try:
            assert await PostgresIntake(db).accept(old, deadline=time.monotonic() + 2) == "duplicate"
        finally:
            await db.close()
    asyncio.run(run())
    assert_fact_and_jobs(data, name, old)


def test_duplicate_repairs_jobs_of_legacy_write(target):
    data, name, urls, _ = target
    item = record()
    async def run():
        db = Database(settings(urls["collector"]))
        try:
            async with db.transaction() as session:
                assert await ActivityStorage(session).insert(item) == "inserted"
            assert counts(data, name, item)[1] == []
            assert await PostgresIntake(db).accept(item, deadline=time.monotonic() + 2) == "duplicate"
        finally:
            await db.close()
    asyncio.run(run())
    assert_fact_and_jobs(data, name, item)


@pytest.mark.parametrize("offset,reason", [(timedelta(seconds=121), "future_clock"), (timedelta(days=-8), "too_late")])
def test_invalid_clock_rolls_back_new_fact_and_jobs(target, offset, reason):
    data, name, urls, _ = target
    item = record()
    invalid = item.model_copy(update={"received_at": item.event.ended_at - offset})
    async def run():
        db = Database(settings(urls["collector"]))
        try:
            with pytest.raises(MetadataRejected) as exc:
                await PostgresIntake(db).accept(invalid, deadline=time.monotonic() + 2)
            assert exc.value.reason == reason
        finally:
            await db.close()
    asyncio.run(run())
    assert counts(data, name, item) == ([], [])


def test_retries_past_window_remain_duplicate_or_conflict(target):
    data, name, urls, _ = target
    item = record()
    later = item.model_copy(update={"received_at": item.received_at + timedelta(days=8)})
    conflict = later.model_copy(update={"event": later.event.model_copy(update={"course_id": "different"})})
    async def run():
        db = Database(settings(urls["collector"]))
        try:
            intake = PostgresIntake(db)
            assert await intake.accept(item, deadline=time.monotonic() + 2) == "accepted"
            assert await intake.accept(later, deadline=time.monotonic() + 2) == "duplicate"
            assert await intake.accept(conflict, deadline=time.monotonic() + 2) == "conflict"
        finally:
            await db.close()
    asyncio.run(run())
    assert_fact_and_jobs(data, name, item)


@pytest.mark.parametrize("mode", ["same", "different_payload", "different_subject"])
def test_two_collectors_race_same_key(target, mode):
    data, name, urls, _ = target
    item = record()
    if mode == "different_payload":
        other = item.model_copy(update={"event": item.event.model_copy(update={"course_id": "different"})})
    elif mode == "different_subject":
        other = item.model_copy(update={"subject_ref": uuid4()})
    else:
        other = item
    async def run():
        first_db = Database(settings(urls["collector"]))
        second_db = Database(settings(urls["collector"]))
        reached, release = asyncio.Event(), asyncio.Event()
        class Gate(PostgresIntake):
            async def _after_fact(self):
                reached.set()
                await release.wait()
        try:
            first = asyncio.create_task(Gate(first_db).accept(item, deadline=time.monotonic() + 3))
            await asyncio.wait_for(reached.wait(), 2)
            second = asyncio.create_task(PostgresIntake(second_db).accept(other, deadline=time.monotonic() + 3))
            await asyncio.sleep(.1)
            assert not second.done()
            release.set()
            assert await asyncio.gather(first, second) == ["accepted", "duplicate" if mode == "same" else "conflict"]
        finally:
            await first_db.close()
            await second_db.close()
    asyncio.run(run())
    assert_fact_and_jobs(data, name, item)


@pytest.mark.parametrize("point", ["_after_fact", "_before_commit"])
def test_injected_failure_rolls_back_fact_and_jobs(target, point):
    data, name, urls, _ = target
    item = record()
    async def failure(self):
        raise IntakeUnavailable("injected failure")
    async def run():
        db = Database(settings(urls["collector"]))
        broken = type("Broken", (PostgresIntake,), {point: failure})(db)
        try:
            with pytest.raises(IntakeUnavailable):
                await broken.accept(item, deadline=time.monotonic() + 2)
        finally:
            await db.close()
    asyncio.run(run())
    assert counts(data, name, item) == ([], [])


@pytest.mark.parametrize("mode", ["cancel", "deadline", "lost_response"])
def test_cancellation_timeout_and_lost_reply_reconcile(target, mode):
    data, name, urls, _ = target
    item = record()
    async def run():
        db = Database(settings(urls["collector"]))
        reached = asyncio.Event()
        async def wait_here(self):
            reached.set()
            await asyncio.Event().wait()
        hook = "_after_commit" if mode == "lost_response" else "_after_fact"
        subject = type("Paused", (PostgresIntake,), {hook: wait_here})(db)
        try:
            task = asyncio.create_task(subject.accept(item, deadline=time.monotonic() + (.5 if mode == "deadline" else 3)))
            await asyncio.wait_for(reached.wait(), 2)
            if mode == "deadline":
                with pytest.raises(IntakeUnavailable):
                    await task
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            expected = "duplicate" if mode == "lost_response" else "accepted"
            assert await PostgresIntake(db).accept(item, deadline=time.monotonic() + 2) == expected
        finally:
            await db.close()
    asyncio.run(run())
    assert_fact_and_jobs(data, name, item)


def test_privacy_lock_tombstone_and_cascade(target):
    data, name, urls, _ = target
    item = record()
    async def run():
        collector = Database(settings(urls["collector"]))
        privacy = Database(settings(urls["privacy"]))
        entered, release, privacy_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class Gate(PostgresIntake):
            async def _after_fact(self):
                entered.set()
                await release.wait()
        async def delete():
            async with privacy.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(item.subject_ref)})
                privacy_entered.set()
                await session.execute(text("INSERT INTO analytics.subject_fences(subject_ref,epoch,deleted,control_id) VALUES (:subject,1,true,:control)"),
                                      {"subject": item.subject_ref, "control": uuid4()})
                await session.execute(text("DELETE FROM analytics.activity_facts WHERE subject_ref=:subject"), {"subject": item.subject_ref})
        try:
            initial = asyncio.create_task(Gate(collector).accept(item, deadline=time.monotonic() + 3))
            await asyncio.wait_for(entered.wait(), 2)
            deletion = asyncio.create_task(delete())
            await asyncio.sleep(.1)
            assert not privacy_entered.is_set()
            release.set()
            assert await initial == "accepted"
            await deletion
            with pytest.raises(IntakeForbidden):
                await PostgresIntake(collector).accept(item, deadline=time.monotonic() + 2)
        finally:
            await collector.close()
            await privacy.close()
    asyncio.run(run())
    assert counts(data, name, item) == ([], [])


def test_tombstone_precedes_duplicate_and_conflict_checks(target):
    data, name, urls, _ = target
    item = record()
    changed = item.model_copy(update={"event": item.event.model_copy(update={"course_id": "different"})})
    async def run():
        collector = Database(settings(urls["collector"]))
        privacy = Database(settings(urls["privacy"]))
        try:
            assert await PostgresIntake(collector).accept(item, deadline=time.monotonic() + 2) == "accepted"
            async with privacy.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(item.subject_ref)})
                await session.execute(text("INSERT INTO analytics.subject_fences(subject_ref,epoch,deleted,control_id) VALUES (:subject,1,true,:control)"),
                                      {"subject": item.subject_ref, "control": uuid4()})
            for candidate in (item, changed):
                with pytest.raises(IntakeForbidden):
                    await PostgresIntake(collector).accept(candidate, deadline=time.monotonic() + 2)
        finally:
            await collector.close()
            await privacy.close()
    asyncio.run(run())
    assert_fact_and_jobs(data, name, item)


def test_job_grants_and_stable_lock_key(target):
    data, name, urls, _ = target
    assert subject_lock_key(UUID("00000000-0000-0000-0000-000000000000")) == -1453335421131774274
    async def run():
        collector = Database(settings(urls["collector"]))
        processor = Database(settings(urls["processor"]))
        query = Database(settings(urls["query"]))
        try:
            with pytest.raises(DBAPIError):
                async with collector.transaction() as session:
                    await session.execute(text("UPDATE analytics.processing_jobs SET status='wrong'"))
            async with processor.transaction() as session:
                await session.execute(text("UPDATE analytics.processing_jobs SET status='pending' WHERE false"))
            with pytest.raises(DBAPIError):
                async with processor.transaction() as session:
                    await session.execute(text("UPDATE analytics.activity_facts SET course_id='wrong'"))
            with pytest.raises(DBAPIError):
                async with query.transaction() as session:
                    await session.execute(text("SELECT * FROM analytics.processing_jobs"))
        finally:
            for db in (collector, processor, query):
                await db.close()
    asyncio.run(run())


def test_new_image_upgrades_a001_and_old_scaffold_starts():
    if (os.environ.get("ANALYTICS_RUN_SYNTHETIC_INTAKE") != "1"
            or os.environ.get("ANALYTICS_INTAKE_TEST_IMAGE") != "knowi-analytics:corp78-synthetic"):
        pytest.skip("explicit synthetic image check required")
    data = config(ROOT / ".local" / "postgres-connection.json")
    name, token, urls = create(data)
    old = record()
    try:
        upgrade_a001(urls["migrate"])
        async def seed():
            db = Database(settings(urls["collector"]))
            try:
                async with db.transaction() as session:
                    assert await ActivityStorage(session).insert(old) == "inserted"
            finally:
                await db.close()
        asyncio.run(seed())
        env = os.environ.copy()
        env["ANALYTICS_MIGRATE_DATABASE_URL"] = urls["migrate"].replace("@127.0.0.1:57392/", "@analytics-postgres:5432/")
        result = subprocess.run(["docker", "run", "--rm", "--network", "knowi-analytics-01a1df75-net",
                                 "--env", "ANALYTICS_MIGRATE_DATABASE_URL", "knowi-analytics:corp78-synthetic", "migrate"],
                                cwd=ROOT, env=env, capture_output=True, timeout=45)
        assert result.returncode == 0, result.stderr.decode()
        assert_fact_and_jobs(data, name, old)
        from tools.check_image import PROBE
        env["ANALYTICS_QUERY_DATABASE_URL"] = urls["query"].replace("@127.0.0.1:57392/", "@analytics-postgres:5432/")
        previous = PROBE.replace(
            'env[f"ANALYTICS_{role.upper()}_DATABASE_URL"] = "postgresql://synthetic:synthetic@unreachable:5432/analytics_smoke"', "")
        baseline = subprocess.run(["docker", "run", "--rm", "--network", "knowi-analytics-01a1df75-net",
                                   "--env", "ANALYTICS_QUERY_DATABASE_URL", "--entrypoint", "python",
                                   "knowi-analytics:an1-local", "-B", "-c", previous, "query"],
                                  cwd=ROOT, env=env, capture_output=True, timeout=45)
        assert baseline.returncode == 0, baseline.stderr.decode()
        assert json.loads(baseline.stdout)["ready"] == 503
    finally:
        cleanup(data, name, token)
