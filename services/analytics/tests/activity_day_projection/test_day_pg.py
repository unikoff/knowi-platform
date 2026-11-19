"""Disposable database only; independent explicit PostgreSQL GO required."""
import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pymentor_analytics.adapters.postgres.activity_day_projection import PostgresActivityDayProjection
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.modules.analytics.activity.day import normalize_day
from pymentor_analytics.modules.analytics.activity.day_ports import DaySourceNotReady, StaleDayClaim

pytestmark = pytest.mark.skipif(os.environ.get('ANALYTICS_RUN_ACTIVITY_DAY_PROJECTION_SYNTHETIC') != '1',
                               reason='separate disposable a011 PostgreSQL GO required')


def test_trigger_claim_defer_publish_and_invalidation(activity_day_projection_target):
    _, _, urls, run_id, _ = activity_day_projection_target
    async def run():
        migrate, processor = [Database(Settings(database_url=urls[role], pool_size=2, pool_overflow=0, db_timeout_seconds=5))
                              for role in ('migrate', 'processor')]
        store = PostgresActivityDayProjection(processor, expected_run_id=run_id)
        subject = uuid4(); start = datetime(2026, 1, 1, 21, tzinfo=timezone.utc)
        deadline = lambda: time.monotonic() + 5
        try:
            async with migrate.transaction() as session:
                await session.execute(text("UPDATE analytics.activity_projection_backfill SET phase='complete' WHERE singleton=true"))
                await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': subject_lock_key(subject)})
                await session.execute(text("""INSERT INTO analytics.activity_projection_buckets(subject_ref,bucket_start,metric_version,generation,state)
                    VALUES(:subject,:start,'activity-v1',1,'dirty')"""), {'subject': subject, 'start': start})
            assert await store.backfill_step(deadline=deadline()) == 'draining'
            assert await store.claim(uuid4(), deadline=deadline()) is None  # dirty source
            async with migrate.transaction() as session:
                row = (await session.execute(text('SELECT state,attempts,generation FROM analytics.activity_projection_day_sources'))).one()
                assert row == ('dirty', 0, 1)
                # Claiming a non-ready bucket does not invalidate the day.
                await session.execute(text("""UPDATE analytics.activity_projection_buckets SET state='claimed', claim_id=:claim, owner=:owner,
                  claim_generation=1,claim_run_id=:run,lease_expires_at=clock_timestamp()+interval '30 seconds'"""),
                  {'claim': uuid4(), 'owner': uuid4(), 'run': run_id})
                assert (await session.execute(text('SELECT generation FROM analytics.activity_projection_day_sources'))).scalar_one() == 1
            assert await store.claim(uuid4(), deadline=deadline()) is None  # claimed source
            async with migrate.transaction() as session:
                # Expired day leases still recover regardless of source state;
                # load retains its defensive deferred-source check.
                await session.execute(text("""UPDATE analytics.activity_projection_day_sources SET state='claimed',
                  claim_id=:claim,owner=:owner,claim_generation=generation,claim_run_id=:run,
                  lease_expires_at=clock_timestamp()-interval '1 second'"""),
                  {'claim': uuid4(), 'owner': uuid4(), 'run': run_id})
            claim = await store.claim(uuid4(), deadline=deadline())
            assert claim.local_day.isoformat() == '2026-01-02'
            with pytest.raises(DaySourceNotReady): await store.load(claim, deadline=deadline())
            async with migrate.transaction() as session:
                row = (await session.execute(text('SELECT state,attempts FROM analytics.activity_projection_day_sources'))).one()
                assert row == ('dirty', 1)
                await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': subject_lock_key(subject)})
                await session.execute(text("""UPDATE analytics.activity_projection_buckets SET state='ready',projection_generation=1,subject_epoch=0,
                  winner_intervals='[]',presence_intervals='[]',presence_points='[]',contributions='[]',presence_labels='[]',
                  compact_item_count=0,edge_item_count=0,has_presence=false"""))
                # The ready transition's trigger resets the deferred deadline.
                assert (await session.execute(text("SELECT next_attempt_at<=clock_timestamp() FROM analytics.activity_projection_day_sources"))).scalar_one()
            claim = await store.claim(uuid4(), deadline=deadline())
            claim, records = await store.load(claim, deadline=deadline())
            await store.commit(claim, normalize_day(records), deadline=deadline())
            assert await store.backfill_step(deadline=deadline()) == 'complete'
            async with migrate.transaction() as session:
                await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': subject_lock_key(subject)})
                await session.execute(text("UPDATE analytics.activity_projection_buckets SET generation=generation+1,state='dirty'"))
                row = (await session.execute(text('SELECT state,generation FROM analytics.activity_projection_day_sources'))).one()
                assert row == ('dirty', 3)
            with pytest.raises(StaleDayClaim): await store.commit(claim, normalize_day(records), deadline=deadline())
            async with processor.transaction() as session:
                with pytest.raises(DBAPIError):
                    await session.execute(text("UPDATE analytics.activity_projection_buckets SET bucket_start=bucket_start+interval '15 minutes'"))
        finally:
            await migrate.close(); await processor.close()
    asyncio.run(run())


def test_trigger_permissions_and_owner_cannot_move_keys(activity_day_projection_target):
    _, name, urls, _, _ = activity_day_projection_target
    async def run():
        migrate, processor, collector = [Database(Settings(database_url=urls[role], pool_size=2, pool_overflow=0, db_timeout_seconds=5))
                                         for role in ('migrate', 'processor', 'collector')]
        subject = uuid4(); start = datetime(2026, 2, 1, tzinfo=timezone.utc)
        try:
            async with migrate.transaction() as session:
                owner, security_definer, public_exec = (await session.execute(text("""SELECT pg_get_userbyid(p.proowner),p.prosecdef,
                  EXISTS(SELECT 1 FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a
                         WHERE a.grantee=0 AND a.privilege_type='EXECUTE')
                  FROM pg_proc p WHERE p.oid='analytics.activity_projection_bucket_changed()'::regprocedure"""))).one()
                assert (owner, security_definer, public_exec) == (name + '_migrate', True, False)
                await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': subject_lock_key(subject)})
                await session.execute(text("""INSERT INTO analytics.activity_projection_buckets(subject_ref,bucket_start,metric_version,generation,state)
                  VALUES(:subject,:start,'activity-v1',1,'dirty')"""), {'subject': subject, 'start': start})
            with pytest.raises(DBAPIError):
                async with migrate.transaction() as session:
                    await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': subject_lock_key(subject)})
                    await session.execute(text("UPDATE analytics.activity_projection_buckets SET bucket_start=bucket_start+interval '15 minutes'"))
            with pytest.raises(DBAPIError):
                async with collector.transaction() as session:
                    await session.execute(text('SELECT * FROM analytics.activity_projection_day_sources'))
            with pytest.raises(DBAPIError):
                async with processor.transaction() as session:
                    await session.execute(text("UPDATE analytics.activity_projection_day_sources SET generation=generation+1"))
        finally:
            await migrate.close(); await processor.close(); await collector.close()
    asyncio.run(run())


def test_quarantined_bucket_is_not_hidden_from_day_worker(activity_day_projection_target):
    _, _, urls, run_id, _ = activity_day_projection_target
    async def run():
        migrate, processor = [Database(Settings(database_url=urls[role], pool_size=2, pool_overflow=0, db_timeout_seconds=5))
                              for role in ('migrate', 'processor')]
        store = PostgresActivityDayProjection(processor, expected_run_id=run_id)
        subject, start = uuid4(), datetime(2026, 1, 1, 21, tzinfo=timezone.utc)
        try:
            async with migrate.transaction() as session:
                await session.execute(text("UPDATE analytics.activity_projection_backfill SET phase='complete' WHERE singleton=true"))
                await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': subject_lock_key(subject)})
                await session.execute(text("""INSERT INTO analytics.activity_projection_buckets
                    (subject_ref,bucket_start,metric_version,generation,state)
                    VALUES(:subject,:start,'activity-v1',1,'quarantined')"""), {'subject': subject, 'start': start})
            assert await store.backfill_step(deadline=time.monotonic() + 5) == 'draining'
            claim = await store.claim(uuid4(), deadline=time.monotonic() + 5)
            assert claim is not None
            with pytest.raises(ValueError, match='quarantined source bucket'):
                await store.load(claim, deadline=time.monotonic() + 5)
        finally:
            await migrate.close(); await processor.close()
    asyncio.run(run())


# All concurrency cases use fresh disposable databases and separately bounded
# transactions. Events control ordering; pg_blocking_pids proves the intended
# wait actually occurred before a blocking transaction is released.
def _database(url):
    return Database(Settings(database_url=url, pool_size=3, pool_overflow=0, db_timeout_seconds=8))


def _deadline():
    return time.monotonic() + 7


async def _blocked_by(observer, blocker_pid):
    async with asyncio.timeout(3):
        while True:
            async with observer.transaction() as session:
                blocked = (await session.execute(text("""SELECT EXISTS(SELECT 1 FROM pg_stat_activity
                    WHERE :blocker = ANY(pg_blocking_pids(pid)))"""), {'blocker': blocker_pid})).scalar_one()
            if blocked:
                return
            await asyncio.sleep(0.01)


async def _insert_bucket(session, subject, start, generation=1, ready=False):
    await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': subject_lock_key(subject)})
    await session.execute(text("""INSERT INTO analytics.activity_projection_buckets
        (subject_ref,bucket_start,metric_version,generation,state)
        VALUES(:subject,:start,'activity-v1',:generation,'dirty')"""),
        {'subject': subject, 'start': start, 'generation': generation})
    if ready:
        await session.execute(text("""UPDATE analytics.activity_projection_buckets SET state='ready',
            projection_generation=generation,subject_epoch=0,winner_intervals='[]',presence_intervals='[]',
            presence_points='[]',contributions='[]',presence_labels='[]',compact_item_count=0,
            edge_item_count=0,has_presence=false WHERE subject_ref=:subject AND bucket_start=:start"""),
            {'subject': subject, 'start': start})


async def _initialize(store, migrate):
    async with migrate.transaction() as session:
        await session.execute(text("UPDATE analytics.activity_projection_backfill SET phase='complete' WHERE singleton=true"))
    assert await store.backfill_step(deadline=_deadline()) in ('scanning', 'draining')


@pytest.mark.parametrize('operation', ['load', 'commit'])
def test_writer_holds_subject_before_bucket_and_day_worker_rejects_old_claim(activity_day_projection_target, operation):
    _, _, urls, run_id, _ = activity_day_projection_target
    async def run():
        migrate, processor, observer = [_database(urls[role]) for role in ('migrate', 'processor', 'migrate')]
        store = PostgresActivityDayProjection(processor, expected_run_id=run_id)
        subject, start = uuid4(), datetime(2026, 2, 1, tzinfo=timezone.utc)
        locked, release = asyncio.Event(), asyncio.Event()
        tasks = []
        try:
            async with migrate.transaction() as session:
                await _insert_bucket(session, subject, start, ready=True)
            await _initialize(store, migrate)
            claim = await store.claim(uuid4(), deadline=_deadline())
            if operation == 'commit':
                claim, records = await store.load(claim, deadline=_deadline())
                projection = normalize_day(records)
            blocker = {}
            async def writer():
                async with migrate.transaction() as session:
                    blocker['pid'] = (await session.execute(text('SELECT pg_backend_pid()'))).scalar_one()
                    await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': subject_lock_key(subject)})
                    await session.execute(text("""UPDATE analytics.activity_projection_buckets SET
                        state='dirty',generation=generation+1 WHERE subject_ref=:subject AND bucket_start=:start"""),
                        {'subject': subject, 'start': start})
                    locked.set()
                    await asyncio.wait_for(release.wait(), 4)
            tasks.append(asyncio.create_task(writer()))
            await asyncio.wait_for(locked.wait(), 3)
            async def day_operation():
                if operation == 'load':
                    return await store.load(claim, deadline=_deadline())
                return await store.commit(claim, projection, deadline=_deadline())
            tasks.append(asyncio.create_task(day_operation()))
            await _blocked_by(observer, blocker['pid'])
            release.set()
            await asyncio.wait_for(tasks[0], 4)
            with pytest.raises(StaleDayClaim):
                await asyncio.wait_for(tasks[1], 4)
            async with observer.transaction() as session:
                assert (await session.execute(text('SELECT state FROM analytics.activity_projection_day_sources'))).scalar_one() == 'dirty'
                assert (await session.execute(text('SELECT count(*) FROM analytics.activity_projection_days'))).scalar_one() == 0
        finally:
            release.set()
            for task in tasks:
                if not task.done(): task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await migrate.close(); await processor.close(); await observer.close()
    asyncio.run(run())


@pytest.mark.parametrize('mutation', ['new_bucket', 'lower_generation'])
def test_bucket_changes_after_load_invalidate_even_below_max_generation(activity_day_projection_target, mutation):
    _, _, urls, run_id, _ = activity_day_projection_target
    async def run():
        migrate, processor = [_database(urls[role]) for role in ('migrate', 'processor')]
        store = PostgresActivityDayProjection(processor, expected_run_id=run_id)
        subject, start = uuid4(), datetime(2026, 3, 1, tzinfo=timezone.utc)
        try:
            async with migrate.transaction() as session:
                await _insert_bucket(session, subject, start, generation=10, ready=True)
                await _insert_bucket(session, subject, start + timedelta(minutes=15), generation=1, ready=True)
            await _initialize(store, migrate)
            claim = await store.claim(uuid4(), deadline=_deadline())
            claim, records = await store.load(claim, deadline=_deadline())
            async with migrate.transaction() as session:
                await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': subject_lock_key(subject)})
                if mutation == 'new_bucket':
                    await _insert_bucket(session, subject, start + timedelta(minutes=30))
                else:
                    await session.execute(text("""UPDATE analytics.activity_projection_buckets SET generation=generation+1,state='dirty'
                        WHERE subject_ref=:subject AND bucket_start=:start"""),
                        {'subject': subject, 'start': start + timedelta(minutes=15)})
                assert (await session.execute(text('SELECT max(generation) FROM analytics.activity_projection_buckets'))).scalar_one() == 10
            with pytest.raises(StaleDayClaim):
                await store.commit(claim, normalize_day(records), deadline=_deadline())
            async with migrate.transaction() as session:
                assert (await session.execute(text('SELECT count(*) FROM analytics.activity_projection_days'))).scalar_one() == 0
        finally:
            await migrate.close(); await processor.close()
    asyncio.run(run())


def test_competing_claims_skip_locked_expiry_and_lost_commit_reply(activity_day_projection_target):
    _, _, urls, run_id, _ = activity_day_projection_target
    async def run():
        migrate, processor1, processor2 = [_database(urls[role]) for role in ('migrate', 'processor', 'processor')]
        first, second = [PostgresActivityDayProjection(db, expected_run_id=run_id) for db in (processor1, processor2)]
        subject, start = uuid4(), datetime(2026, 4, 1, tzinfo=timezone.utc)
        locked, release = asyncio.Event(), asyncio.Event()
        holder = None
        try:
            async with migrate.transaction() as session:
                await _insert_bucket(session, subject, start, ready=True)
                await _insert_bucket(session, subject, start + timedelta(days=1), ready=True)
            await _initialize(first, migrate)
            async def hold_first_day():
                async with migrate.transaction() as session:
                    await session.execute(text("""SELECT subject_ref FROM analytics.activity_projection_day_sources
                        WHERE local_day=:day FOR UPDATE"""), {'day': start.date()})
                    locked.set()
                    await asyncio.wait_for(release.wait(), 4)
            holder = asyncio.create_task(hold_first_day())
            await asyncio.wait_for(locked.wait(), 3)
            other = await first.claim(uuid4(), deadline=_deadline())
            assert other is not None and other.local_day == (start + timedelta(days=1)).date()
            assert await second.claim(uuid4(), deadline=_deadline()) is None
            release.set()
            await asyncio.wait_for(holder, 4)
            original = await second.claim(uuid4(), deadline=_deadline())
            assert original is not None and original.local_day == start.date() and original.claim_id != other.claim_id
            original, records = await second.load(original, deadline=_deadline())
            async with migrate.transaction() as session:
                await session.execute(text("""UPDATE analytics.activity_projection_day_sources
                    SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE local_day=:day"""), {'day': start.date()})
            replacement = await first.claim(uuid4(), deadline=_deadline())
            assert replacement and replacement.claim_id != original.claim_id
            with pytest.raises(StaleDayClaim):
                await second.commit(original, normalize_day(records), deadline=_deadline())
            replacement, fresh = await first.load(replacement, deadline=_deadline())
            payload = normalize_day(fresh)
            await first.commit(replacement, payload, deadline=_deadline())
            # The commit succeeded but its response was hypothetically lost.
            with pytest.raises(StaleDayClaim):
                await first.commit(replacement, payload, deadline=_deadline())
            async with migrate.transaction() as session:
                row = (await session.execute(text("""SELECT s.state,s.generation,d.generation,d.run_id,
                    (SELECT count(*) FROM analytics.activity_projection_days)
                    FROM analytics.activity_projection_day_sources s JOIN analytics.activity_projection_days d
                    USING(subject_ref,local_day,metric_version) WHERE s.local_day=:day"""), {'day': start.date()})).one()
                assert row[0] == 'ready' and row[1] == row[2] and row[3] == run_id and row[4] == 1
        finally:
            release.set()
            if holder is not None:
                if not holder.done(): holder.cancel()
                await asyncio.gather(holder, return_exceptions=True)
            await migrate.close(); await processor1.close(); await processor2.close()
    asyncio.run(run())


def test_nonready_claim_updates_do_not_wait_for_day_held_by_writer(activity_day_projection_target):
    _, _, urls, run_id, _ = activity_day_projection_target
    async def run():
        writer_db, claim_db, observer = [_database(urls[role]) for role in ('migrate', 'processor', 'migrate')]
        subject, first = uuid4(), datetime(2026, 5, 1, tzinfo=timezone.utc)
        second = first + timedelta(minutes=15)
        bucket_locked, day_locked, release_claim = asyncio.Event(), asyncio.Event(), asyncio.Event()
        tasks, pids = [], {}
        try:
            async with writer_db.transaction() as session:
                await _insert_bucket(session, subject, first)
                await _insert_bucket(session, subject, second)
            async def bucket_claim():
                async with claim_db.transaction() as session:
                    pids['claim'] = (await session.execute(text('SELECT pg_backend_pid()'))).scalar_one()
                    await session.execute(text("""SELECT subject_ref FROM analytics.activity_projection_buckets
                        WHERE subject_ref=:subject AND bucket_start=:start FOR UPDATE"""), {'subject': subject, 'start': second})
                    bucket_locked.set()
                    await asyncio.wait_for(release_claim.wait(), 4)
                    await session.execute(text("""UPDATE analytics.activity_projection_buckets SET state='claimed',claim_id=:claim,
                        owner=:owner,claim_generation=generation,claim_run_id=:run,lease_expires_at=clock_timestamp()+interval '30 seconds'
                        WHERE subject_ref=:subject AND bucket_start=:start"""),
                        {'subject': subject, 'start': second, 'claim': uuid4(), 'owner': uuid4(), 'run': run_id})
                    await session.execute(text("""UPDATE analytics.activity_projection_buckets SET state='quarantined',
                        claim_id=NULL,owner=NULL,claim_generation=NULL,claim_run_id=NULL,lease_expires_at=NULL
                        WHERE subject_ref=:subject AND bucket_start=:start"""), {'subject': subject, 'start': second})
            async def writer():
                await asyncio.wait_for(bucket_locked.wait(), 3)
                async with writer_db.transaction() as session:
                    await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': subject_lock_key(subject)})
                    # This bucket change locks the day row through the trigger.
                    await session.execute(text("""UPDATE analytics.activity_projection_buckets SET generation=generation+1
                        WHERE subject_ref=:subject AND bucket_start=:start"""), {'subject': subject, 'start': first})
                    day_locked.set()
                    # The second bucket is held by claim_db. A trigger that also
                    # locks the day for claim/quarantine would create a cycle.
                    await session.execute(text("""UPDATE analytics.activity_projection_buckets SET generation=generation+1,state='dirty'
                        WHERE subject_ref=:subject AND bucket_start=:start"""), {'subject': subject, 'start': second})
            tasks.extend((asyncio.create_task(bucket_claim()), asyncio.create_task(writer())))
            await asyncio.wait_for(day_locked.wait(), 3)
            await _blocked_by(observer, pids['claim'])
            release_claim.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 4)
            async with observer.transaction() as session:
                source = (await session.execute(text('SELECT generation,state FROM analytics.activity_projection_day_sources'))).one()
                assert source == (4, 'dirty')
        finally:
            release_claim.set()
            for task in tasks:
                if not task.done(): task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await writer_db.close(); await claim_db.close(); await observer.close()
    asyncio.run(run())


def test_scan_registers_legacy_and_concurrent_insert_before_cursor(activity_day_projection_target):
    _, _, urls, run_id, _ = activity_day_projection_target
    async def run():
        migrate, processor, observer, early_writer = [_database(urls[role]) for role in ('migrate', 'processor', 'migrate', 'migrate')]
        store = PostgresActivityDayProjection(processor, expected_run_id=run_id)
        legacy, early = UUID(int=2), UUID(int=1)
        start = datetime(2026, 6, 1, tzinfo=timezone.utc)
        locked, release = asyncio.Event(), asyncio.Event()
        tasks, blocker = [], {}
        try:
            async with migrate.transaction() as session:
                await session.execute(text("UPDATE analytics.activity_projection_backfill SET phase='complete' WHERE singleton=true"))
                # Insert 101 old buckets, then remove their synthetic sources to
                # model a database whose buckets predate trigger installation.
                await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': subject_lock_key(legacy)})
                await session.execute(text("""INSERT INTO analytics.activity_projection_buckets(subject_ref,bucket_start,metric_version,generation,state)
                    SELECT :subject, CAST(:start AS timestamptz) + n * interval '15 minutes','activity-v1',1,'dirty'
                    FROM generate_series(0,100) n"""), {'subject': legacy, 'start': start})
                await session.execute(text('DELETE FROM analytics.activity_projection_day_sources'))
            async def hold_legacy_subject():
                async with migrate.transaction() as session:
                    blocker['pid'] = (await session.execute(text('SELECT pg_backend_pid()'))).scalar_one()
                    await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': subject_lock_key(legacy)})
                    locked.set()
                    await asyncio.wait_for(release.wait(), 4)
            tasks.append(asyncio.create_task(hold_legacy_subject()))
            await asyncio.wait_for(locked.wait(), 3)
            tasks.append(asyncio.create_task(store.backfill_step(deadline=_deadline())))
            # The scanner selected its first page and now waits on legacy's
            # subject lock; the new subject sorts before its pending cursor.
            await _blocked_by(observer, blocker['pid'])
            async with early_writer.transaction() as session:
                await _insert_bucket(session, early, start)
            release.set()
            await asyncio.wait_for(tasks[0], 4)
            assert await asyncio.wait_for(tasks[1], 4) == 'scanning'
            async with observer.transaction() as session:
                cursor = (await session.execute(text('SELECT cursor_subject,cursor_bucket FROM analytics.activity_projection_day_backfill'))).one()
                assert cursor.cursor_subject == legacy and cursor.cursor_bucket == start + timedelta(minutes=15 * 99)
            assert await store.backfill_step(deadline=_deadline()) == 'draining'
            async with observer.transaction() as session:
                missing = (await session.execute(text("""SELECT count(*) FROM analytics.activity_projection_buckets b
                    LEFT JOIN analytics.activity_projection_day_sources s ON s.subject_ref=b.subject_ref AND
                      s.local_day=(b.bucket_start AT TIME ZONE 'UTC'+interval '3 hours')::date AND s.metric_version=b.metric_version
                    WHERE s.subject_ref IS NULL"""))).scalar_one()
                assert missing == 0
                assert (await session.execute(text("""SELECT count(*) FROM analytics.activity_projection_day_sources
                    WHERE subject_ref=:subject"""), {'subject': early})).scalar_one() == 1
        finally:
            release.set()
            for task in tasks:
                if not task.done(): task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await migrate.close(); await processor.close(); await observer.close(); await early_writer.close()
    asyncio.run(run())
