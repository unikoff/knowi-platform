"""Owner-operated a011 recovery; never replay an unknown outcome."""
import argparse
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import re
import sys
import time
from uuid import UUID, uuid4

from sqlalchemy import URL, text

import synthetic_backup as backup
import unseal_a010 as old

from pymentor_analytics.adapters.postgres.activity_day_projection import PostgresActivityDayProjection, _payload
from pymentor_analytics.adapters.postgres.activity_projection import PostgresActivityProjection, begin_activity_projection_backfill
from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
from pymentor_analytics.app.activity_day_projection_runtime import ActivityDayProjectionRuntime
from pymentor_analytics.app.activity_projection_runtime import ActivityProjectionRuntime
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.privacy import PrivacyRuntime
from pymentor_analytics.contracts.auth_v1 import VerifiedGrant
from pymentor_analytics.contracts.reports_v1 import ActivityQuery
from pymentor_analytics.modules.analytics.activity.day import MAX_BUCKETS, MAX_BYTES, MAX_ITEMS, normalize_day

ROLES = old.ROLES


def validate(record, run_id):
    if (record.get("kind") != "restore" or record.get("state") != "parity_verified"
            or record.get("history", {}).get("head") != "a011"
            or record.get("parity_verified") is not True or record.get("privacy_opened") is not False
            or record.get("projection_rebuild_required") is not True
            or record.get("application_access") != "revoked" or "unseal_run_id" in record
            or not isinstance(run_id, UUID) or run_id.int == 0):
        raise ValueError("restore is not eligible for a011 recovery")
    name, nonce = record.get("database", ""), record.get("nonce", "")
    if not backup.db.PATTERN.fullmatch(name) or not re.fullmatch(r"[0-9a-f]{64}", nonce):
        raise ValueError("invalid restore identity")
    return name


async def _closed(session, name):
    for role in ROLES:
        if (await session.execute(text("SELECT has_database_privilege(:role,current_database(),'CONNECT')"),
                                  {"role": name + "_" + role})).scalar_one() is not False:
            raise ValueError("application quarantine changed")
    if (await session.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                                   "AND usename=ANY(:roles)"),
                              {"roles": [name + "_" + role for role in ROLES]})).scalar_one() != 0:
        raise ValueError("application sessions remain")


async def _ready_state(session, run_id, name=None):
    if name is not None:
        privacy_sessions = (await session.execute(text("SELECT count(*) FROM pg_stat_activity "
            "WHERE datname=current_database() AND usename=:role"), {"role": name + "_privacy"})).scalar_one()
        if privacy_sessions != 1:
            raise ValueError("privacy session ownership changed")
    for table in ("activity_projection_backfill", "activity_projection_day_backfill"):
        row = (await session.execute(text(f"SELECT run_id,phase FROM analytics.{table} WHERE singleton=true FOR SHARE"))).one()
        if tuple(row) != (run_id, "complete"):
            raise ValueError("projection rebuild incomplete")
    for table in ("activity_projection_buckets", "activity_projection_day_sources"):
        if (await session.execute(text(f"SELECT count(*) FROM analytics.{table} WHERE state<>'ready'"))).scalar_one():
            raise ValueError("projection work remains")
    stale_bucket = (await session.execute(text("""SELECT EXISTS(SELECT 1 FROM analytics.activity_projection_buckets b
        LEFT JOIN analytics.subject_fences f USING(subject_ref)
        WHERE b.projection_generation IS DISTINCT FROM b.generation
           OR b.subject_epoch IS DISTINCT FROM COALESCE(f.epoch,0) OR COALESCE(f.deleted,false))"""))).scalar_one()
    if stale_bucket:
        raise ValueError("bucket projection is stale")
    # A completed prefix means the privacy worker finished its paginated purge;
    # independently refuse any physically present row for a deleted fence.
    tables = ("activity_facts", "activity_projection_buckets", "activity_projection_day_sources", "activity_projection_days")
    for table in tables:
        found = (await session.execute(text(f"SELECT EXISTS(SELECT 1 FROM analytics.{table} t "
                            "JOIN analytics.subject_fences f USING(subject_ref) WHERE f.deleted)"))).scalar_one()
        if found:
            raise ValueError("deleted subject data remains")
    if (await session.execute(text("SELECT EXISTS(SELECT 1 FROM analytics.privacy_receipts WHERE phase<>'complete')"))).scalar_one():
        raise ValueError("privacy purge remains")


async def invalidate_restored_buckets(database, *, deadline):
    """Invalidate every restored bucket, including buckets without current facts."""
    cursor = None
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("bucket invalidation deadline")
        async with database.transaction() as session:
            predicate = "WHERE (subject_ref,bucket_start,metric_version)>(:subject,:bucket,:metric)" if cursor else ""
            args = {} if cursor is None else {"subject": cursor[0], "bucket": cursor[1], "metric": cursor[2]}
            rows = (await session.execute(text("""WITH selected AS MATERIALIZED (
                SELECT subject_ref,bucket_start,metric_version FROM analytics.activity_projection_buckets """ + predicate +
                """ ORDER BY subject_ref,bucket_start,metric_version LIMIT 100 FOR UPDATE), changed AS (
                UPDATE analytics.activity_projection_buckets b SET generation=b.generation+1,state='dirty',
                    claim_id=NULL,owner=NULL,lease_expires_at=NULL,claim_generation=NULL,claim_epoch=NULL,claim_run_id=NULL,
                    attempts=0,next_attempt_at=clock_timestamp(),last_failure=NULL
                FROM selected s WHERE (b.subject_ref,b.bucket_start,b.metric_version)=(s.subject_ref,s.bucket_start,s.metric_version)
                RETURNING b.subject_ref,b.bucket_start,b.metric_version)
                SELECT subject_ref,bucket_start,metric_version FROM changed ORDER BY subject_ref,bucket_start,metric_version"""), args)).all()
            cursor = (rows[-1].subject_ref, rows[-1].bucket_start, rows[-1].metric_version) if len(rows) == 100 else None
        if cursor is None:
            return


async def reconcile_days(database, store, run_id, *, deadline):
    """Check every bucket source and every day payload in bounded pages."""
    cursor = None
    while True:
        cursor, missing = await store.reconciliation_step(after=cursor, deadline=deadline)
        if missing:
            raise ValueError("day source reconciliation failed")
        if cursor is None:
            break
    cursor = None
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("day reconciliation deadline")
        async with database.transaction() as session:
            orphan = (await session.execute(text("""SELECT EXISTS(SELECT 1 FROM analytics.activity_projection_days d
                LEFT JOIN analytics.activity_projection_day_sources s USING(subject_ref,local_day,metric_version)
                WHERE s.subject_ref IS NULL)"""))).scalar_one()
            if orphan:
                raise ValueError("orphan day payload")
            predicate = "WHERE (s.subject_ref,s.local_day,s.metric_version)>(:subject,:day,:metric)" if cursor else ""
            args = {"run": run_id}
            if cursor:
                args.update(subject=cursor[0], day=cursor[1], metric=cursor[2])
            rows = (await session.execute(text("""SELECT s.subject_ref,s.local_day,s.metric_version,s.state,
                s.generation,d.generation AS payload_generation,d.run_id,d.subject_epoch,
                COALESCE(f.epoch,0) AS current_epoch,COALESCE(f.deleted,false) AS deleted,
                d.source_bucket_count,d.source_compact_items,d.source_compact_bytes,
                (SELECT count(*) FROM analytics.activity_projection_buckets b WHERE b.subject_ref=s.subject_ref
                 AND (b.bucket_start AT TIME ZONE 'UTC' + interval '3 hours')::date=s.local_day AND b.metric_version=s.metric_version) AS bucket_count,
                (SELECT COALESCE(sum(b.compact_item_count),0) FROM analytics.activity_projection_buckets b WHERE b.subject_ref=s.subject_ref
                 AND (b.bucket_start AT TIME ZONE 'UTC' + interval '3 hours')::date=s.local_day AND b.metric_version=s.metric_version) AS bucket_items,
                (SELECT COALESCE(sum(b.compact_json_bytes),0) FROM analytics.activity_projection_buckets b WHERE b.subject_ref=s.subject_ref
                 AND (b.bucket_start AT TIME ZONE 'UTC' + interval '3 hours')::date=s.local_day AND b.metric_version=s.metric_version) AS bucket_bytes
                FROM analytics.activity_projection_day_sources s
                LEFT JOIN analytics.activity_projection_days d USING(subject_ref,local_day,metric_version)
                LEFT JOIN analytics.subject_fences f USING(subject_ref) """ + predicate +
                " ORDER BY s.subject_ref,s.local_day,s.metric_version LIMIT 100"), args)).all()
            for row in rows:
                if (row.state != "ready" or row.deleted or row.run_id != run_id
                        or row.generation != row.payload_generation or row.subject_epoch != row.current_epoch
                        or row.source_bucket_count != row.bucket_count or row.source_compact_items != row.bucket_items
                        or row.source_compact_bytes != row.bucket_bytes or row.bucket_count > MAX_BUCKETS
                        or row.bucket_items > MAX_ITEMS or row.bucket_bytes > MAX_BYTES):
                    raise ValueError("day payload reconciliation failed")
                key = {"subject": row.subject_ref, "day": row.local_day, "metric": row.metric_version}
                day = (await session.execute(text("""SELECT contributions,presence_labels,source_bucket_count,
                    source_compact_items,source_compact_bytes FROM analytics.activity_projection_days
                    WHERE subject_ref=:subject AND local_day=:day AND metric_version=:metric"""), key)).mappings().one()
                buckets = (await session.execute(text("""SELECT contributions,presence_labels,compact_item_count,compact_json_bytes
                    FROM analytics.activity_projection_buckets WHERE subject_ref=:subject AND metric_version=:metric
                    AND (bucket_start AT TIME ZONE 'UTC' + interval '3 hours')::date=:day ORDER BY bucket_start LIMIT 97"""), key)).mappings().all()
                payload = dict(day)
                _payload(payload)
                if normalize_day([dict(bucket) for bucket in buckets]) != payload:
                    raise ValueError("day aggregate reconciliation failed")
            cursor = (rows[-1].subject_ref, rows[-1].local_day, rows[-1].metric_version) if len(rows) == 100 else None
        if cursor is None:
            break


class _QueryRoleDatabase:
    """Run the actual reader as query role without granting database CONNECT."""
    def __init__(self, database, gate, role):
        self.database, self.transaction_guard, self.role = database, gate.check_in_transaction, role

    @asynccontextmanager
    async def transaction(self):
        async with self.database.transaction() as session:
            await session.execute(text(f'SET LOCAL ROLE "{self.role}"'))
            actual = (await session.execute(text("SELECT current_user"))).scalar_one()
            if actual != self.role:
                raise ValueError("reader role changed")
            await self.transaction_guard(session)
            yield session
            await self.transaction_guard(session)


async def guarded_reader_check(config, name, gate, run_id):
    # Bootstrap is limited to this validated disposable target; SET ROLE makes
    # the real report execute with query privileges while CONNECT stays revoked.
    url = URL.create("postgresql", username=config["user"], password=config["password"],
                     host=config["host"], port=config["port"], database=name).render_as_string(hide_password=False)
    bootstrap = Database(Settings(database_url=url, pool_size=1, pool_overflow=0))
    try:
        reader = _QueryRoleDatabase(bootstrap, gate, name + "_query")
        actor = uuid4()
        grant = VerifiedGrant(sub=actor, role="admin", scope="report:read", course_ids=(), subject_refs=(),
                              global_read=True, expires_at=int(time.time()) + 60)
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=2)).replace(hour=21, minute=0, second=0, microsecond=0)
        query = ActivityQuery(from_time=start, to_time=start + timedelta(days=1), course_id=None, subject_ref=actor)
        await PostgresProjectionActivityReport(reader, None, expected_run_id=run_id,
                                             day_projection_enabled=True).read_activity(grant, query)
    finally:
        await bootstrap.close()


async def _grant_after_verified_rebuild(identity, owner, gate, config, name, run_id):
    """Exclusive locked final step for an explicitly reconciled ready record."""
    with old.operation_lock(identity):
        record = backup.read_record(identity, "restore", "unseal_grant_ready")
        await _grant_locked(identity, record, owner, gate, config, name, run_id)


async def _grant_locked(identity, record, owner, gate, config, name, run_id):
    """A lost commit response remains grant-pending for reconciliation."""
    if (record.get("state") != "unseal_grant_ready" or record.get("unseal_run_id") != str(run_id)
            or record.get("application_access") != "revoked" or record.get("database") != name
            or record.get("kind") != "restore" or record.get("parity_verified") is not True
            or record.get("privacy_opened") is not False or record.get("projection_rebuild_required") is not True
            or not backup.db.PATTERN.fullmatch(name) or not isinstance(run_id, UUID) or run_id.int == 0
            or owner.transaction_guard != gate.check_in_transaction):
        raise ValueError("restore is not eligible for final grant")
    backup.reject_retained(record)
    expected = backup.history_for_revision("a011")
    if record.get("history") != expected:
        raise ValueError("restore history differs")
    backup.current(record, config, expected)
    with backup.db.connect(config, name) as conn:
        backup.assert_a011_privileges(conn, name)
    await gate.tick()
    if not await gate.is_ready():
        raise ValueError("privacy authority not current")
    await reconcile_days(owner, PostgresActivityDayProjection(owner, expected_run_id=run_id), run_id,
                         deadline=time.monotonic() + 30)
    await guarded_reader_check(config, name, gate, run_id)
    await gate.tick()
    if not await gate.is_ready():
        raise ValueError("privacy authority not current")
    record["state"] = "unseal_grant_pending"
    backup.write_record(identity, record)
    async with owner.transaction() as session:
        actual = (await session.execute(text("SELECT current_database(),current_user"))).one()
        if tuple(actual) != (name, name + "_migrate"):
            raise ValueError("restore owner identity changed")
        await _closed(session, name)
        await _ready_state(session, run_id, name)
        for role in ROLES:
            await session.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO "{name}_{role}"'))
    async with owner.transaction() as session:
        for role in ROLES:
            if (await session.execute(text("SELECT has_database_privilege(:role,current_database(),'CONNECT')"),
                                      {"role": name + "_" + role})).scalar_one() is not True:
                raise ValueError("post-grant privileges unverified")
    record.update(state="unsealed", application_access="granted", projection_rebuild_required=False,
                  privacy_opened=False)
    backup.write_record(identity, record)


async def recover(identity, authority, run_id, *, timeout=120, resource_check=None):
    if not isinstance(authority, AuthorityConfig) or not 1 <= timeout <= 300:
        raise ValueError("invalid recovery configuration")
    with old.operation_lock(identity):
        if resource_check is not None: resource_check()
        record = backup.read_record(identity, "restore", "parity_verified")
        name = validate(record, run_id)
        backup.reject_retained(record)
        config = backup.connection_config()
        expected = backup.history_for_revision("a011")
        if record.get("history") != expected: raise ValueError("restore history differs")
        backup.current(record, config, expected)
        with backup.db.connect(config, name) as conn:
            backup.assert_a011_privileges(conn, name)
        owner = Database(Settings(database_url=record["urls"]["migrate"], pool_size=1, pool_overflow=0))
        privacy_db = Database(Settings(database_url=record["urls"]["privacy"], pool_size=1, pool_overflow=0))
        gate = PrivacyRuntime(AuthorityClient(authority), privacy_db, store=PostgresPrivacyStore(privacy_db))
        worker = None
        try:
            async with asyncio.timeout(timeout):
                async with owner.transaction() as session:
                    actual = (await session.execute(text("SELECT current_database(),current_user"))).one()
                    if tuple(actual) != (name, name + "_migrate"): raise ValueError("restore owner identity changed")
                    await _closed(session, name)
                    privacy_connect = (await session.execute(text("SELECT has_database_privilege(:role,current_database(),'CONNECT')"),
                                                            {"role": name + "_privacy"})).scalar_one()
                    privacy_active = (await session.execute(text("SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname=current_database() AND usename=:role"), {"role": name + "_privacy"})).scalar_one()
                    if privacy_connect is not False or privacy_active:
                        raise ValueError("privacy quarantine changed")
                    previous = []
                    for table in ("activity_projection_backfill", "activity_projection_day_backfill"):
                        previous.append((await session.execute(text(f"SELECT run_id FROM analytics.{table} WHERE singleton=true"))).scalar_one())
                    if run_id in previous: raise ValueError("new dataset identity required")
                record.update(state="unseal_pending", unseal_run_id=str(run_id),
                              unseal_old_run_id=str(previous[0]) if previous[0] else None)
                backup.write_record(identity, record)
                # Permit only the dedicated purge role after durable intent.
                # A lost grant response leaves unseal_pending for reconciliation.
                async with owner.transaction() as session:
                    await _closed(session, name)
                    await session.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO "{name}_privacy"'))
                while True:
                    if resource_check is not None: resource_check()
                    await gate.tick()
                    if await gate.is_ready(): break
                    await asyncio.sleep(.1)
                owner.transaction_guard = gate.check_in_transaction
                await begin_activity_projection_backfill(owner, expected_run_id=previous[0], new_run_id=run_id,
                                                         deadline=time.monotonic() + 10)
                await invalidate_restored_buckets(owner, deadline=time.monotonic() + 30)
                worker = ActivityProjectionRuntime(PostgresActivityProjection(owner, expected_run_id=run_id), gate,
                                                   enabled=True, operation_timeout=5)
                worker.start()
                for table, phase in (("activity_projection_backfill", "bucket"), ("activity_projection_day_backfill", "day")):
                    if phase == "day":
                        await worker.stop()
                        day_store = PostgresActivityDayProjection(owner, expected_run_id=run_id)
                        worker = ActivityDayProjectionRuntime(day_store, gate, enabled=True, operation_timeout=5)
                        worker.start()
                    while True:
                        if resource_check is not None: resource_check()
                        await gate.tick()
                        async with owner.transaction() as session:
                            row = (await session.execute(text(f"SELECT run_id,phase FROM analytics.{table} WHERE singleton=true"))).one()
                            work_table = "activity_projection_buckets" if phase == "bucket" else "activity_projection_day_sources"
                            pending = (await session.execute(text(f"SELECT count(*) FROM analytics.{work_table} WHERE state<>'ready'"))).scalar_one()
                        if tuple(row) == (run_id, "complete") and pending == 0 and await gate.is_ready(): break
                        await asyncio.sleep(.1)
                await worker.stop()
                worker = None
                await reconcile_days(owner, day_store, run_id, deadline=time.monotonic() + 30)
                await gate.tick()
                if not await gate.is_ready(): raise ValueError("privacy authority not current")
                async with owner.transaction() as session:
                    await _closed(session, name)
                    await _ready_state(session, run_id, name)
                await guarded_reader_check(config, name, gate, run_id)
                record.update(state="unseal_grant_ready", application_access="revoked")
                backup.write_record(identity, record)
                if resource_check is not None: resource_check()
                # The outer operation lock remains held throughout the final
                # durable intent and grant; never reacquire it recursively.
                await _grant_locked(identity, record, owner, gate, config, name, run_id)
                return {"status": "unsealed", "run_id": str(run_id), "privacy_opened": False}
        finally:
            if worker is not None: await worker.stop()
            await gate.stop()
            await owner.close()
            await privacy_db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore", required=True, type=backup.identifier)
    parser.add_argument("--authority", required=True, type=backup.identifier)
    parser.add_argument("--run-id", required=True, type=backup.identifier)
    args = parser.parse_args()
    backup.tree()
    path = backup.path_for("keys", args.authority, ".authority.json")
    if path.stat().st_size > 128 * 1024 or path.stat().st_nlink != 1: raise ValueError("unsafe authority config")
    authority = AuthorityConfig.from_json(path.read_bytes())
    print(json.dumps(asyncio.run(recover(args.restore, authority, UUID(args.run_id))), separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Recovery stopped or outcome unknown; preserve state and reconcile.", file=sys.stderr)
        raise SystemExit(2) from None
