"""Owner-operated synthetic restore recovery; never infer a retry from a timeout."""
import asyncio
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sys
import json
import time
from uuid import UUID

import synthetic_backup as backup

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
from sqlalchemy import text
from pymentor_analytics.adapters.postgres.activity_projection import PostgresActivityProjection, begin_activity_projection_backfill
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
from pymentor_analytics.app.activity_projection_runtime import ActivityProjectionRuntime
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.privacy import PrivacyRuntime

ROLES = ("collector", "processor", "query", "transfer_status")


@contextmanager
def operation_lock(identity):
    path = backup.path_for("operations", identity, ".unseal.lock")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        yield
    finally:
        path.unlink()


def validate(record, run_id):
    if (record.get("kind") != "restore" or record.get("state") != "parity_verified"
            or record.get("history", {}).get("head") != "a010"
            or record.get("parity_verified") is not True or record.get("privacy_opened") is not False
            or record.get("projection_rebuild_required") is not True or record.get("application_access") != "revoked"
            or "unseal_run_id" in record or not isinstance(run_id, UUID) or run_id.int == 0):
        raise ValueError("restore is not eligible for unseal")
    name = record.get("database", "")
    if not backup.db.PATTERN.fullmatch(name) or not re.fullmatch(r"[0-9a-f]{64}", record.get("nonce", "")):
        raise ValueError("invalid restore identity")
    return name


async def recover(identity, authority, run_id, *, timeout=120, resource_check=None):
    if not isinstance(authority, AuthorityConfig) or not 1 <= timeout <= 300:
        raise ValueError("invalid recovery configuration")
    with operation_lock(identity):
        if resource_check is not None:
            resource_check()
        record = backup.read_record(identity, "restore", "parity_verified")
        name = validate(record, run_id)
        backup.reject_retained(record)
        config = backup.connection_config()
        expected = backup.history_for_revision("a010")
        if record.get("history") != expected:
            raise ValueError("restore history differs")
        backup.current(record, config, expected)
        owner = Database(Settings(database_url=record["urls"]["migrate"], pool_size=1, pool_overflow=0))
        privacy_db = Database(Settings(database_url=record["urls"]["privacy"], pool_size=1, pool_overflow=0))
        gate = PrivacyRuntime(AuthorityClient(authority), privacy_db, store=PostgresPrivacyStore(privacy_db))
        runtime = None
        try:
            async with asyncio.timeout(timeout):
                async with owner.transaction() as session:
                    actual = (await session.execute(text("SELECT current_database(),current_user"))).one()
                    if tuple(actual) != (name, name + "_migrate"):
                        raise ValueError("restore owner identity changed")
                    role_names = [name + "_" + role for role in ROLES]
                    privileges = [(await session.execute(text("SELECT has_database_privilege(:role,current_database(),'CONNECT')"),
                                                         {"role": role})).scalar_one() for role in role_names]
                    old = (await session.execute(text("SELECT run_id FROM analytics.activity_projection_backfill WHERE singleton=true"))).scalar_one()
                    if any(privileges) or old == run_id:
                        raise ValueError("restore quarantine or new dataset identity invalid")
                record.update(state="unseal_pending", unseal_run_id=str(run_id),
                              unseal_old_run_id=str(old) if old is not None else None)
                backup.write_record(identity, record)
                # An authenticated current authority observation precedes any rebuild.
                while True:
                    if resource_check is not None:
                        resource_check()
                    await gate.tick()
                    if await gate.is_ready():
                        break
                    await asyncio.sleep(.1)
                owner.transaction_guard = gate.check_in_transaction
                await begin_activity_projection_backfill(owner, expected_run_id=old, new_run_id=run_id,
                                                         deadline=time.monotonic() + 10)
                rebuild_started = time.monotonic()
                runtime = ActivityProjectionRuntime(PostgresActivityProjection(owner, expected_run_id=run_id),
                                                    gate, enabled=True, operation_timeout=5)
                runtime.start()
                while True:
                    if resource_check is not None:
                        resource_check()
                    await gate.tick()
                    async with owner.transaction() as session:
                        row = (await session.execute(text("SELECT run_id,phase FROM analytics.activity_projection_backfill WHERE singleton=true"))).one()
                        pending = (await session.execute(text("SELECT count(*) FROM analytics.activity_projection_buckets WHERE state<>'ready'"))).scalar_one()
                    if tuple(row) == (run_id, "complete") and pending == 0 and await gate.is_ready():
                        break
                    await asyncio.sleep(.1)
                rebuild_seconds = time.monotonic() - rebuild_started
                await runtime.stop()
                runtime = None
                # Refresh the signed barrier immediately before the atomic grant.
                if resource_check is not None:
                    resource_check()
                await gate.tick()
                if not await gate.is_ready():
                    raise ValueError("privacy authority not current")
                record["state"] = "unseal_grant_pending"
                backup.write_record(identity, record)
                async with owner.transaction() as session:
                    row = (await session.execute(text("SELECT run_id,phase FROM analytics.activity_projection_backfill WHERE singleton=true FOR UPDATE"))).one()
                    pending = (await session.execute(text("SELECT count(*) FROM analytics.activity_projection_buckets WHERE state<>'ready'"))).scalar_one()
                    active = (await session.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND usename=ANY(:roles)"),
                                                    {"roles": role_names})).scalar_one()
                    if tuple(row) != (run_id, "complete") or pending or active:
                        raise ValueError("rebuild or quarantine changed")
                    for role in role_names:
                        # Names are derived solely from the strictly validated synthetic DB name.
                        await session.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO "{role}"'))
                async with owner.transaction() as session:
                    privileges = [(await session.execute(text("SELECT has_database_privilege(:role,current_database(),'CONNECT')"),
                                                         {"role": role})).scalar_one() for role in role_names]
                    if not all(privileges):
                        raise ValueError("post-grant privileges unverified")
                record.update(state="unsealed", projection_rebuild_required=False, application_access="granted",
                              privacy_opened=False)
                backup.write_record(identity, record)
                return {"status": "unsealed", "run_id": str(run_id), "privacy_opened": False,
                        "rebuild_seconds": round(rebuild_seconds, 3)}
        finally:
            if runtime is not None:
                await runtime.stop()
            await gate.stop()
            await owner.close()
            await privacy_db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore", required=True, type=backup.identifier)
    parser.add_argument("--authority", required=True, type=backup.identifier,
                        help="private authority config UUID under synthetic-backup/keys")
    parser.add_argument("--run-id", required=True, type=backup.identifier)
    args = parser.parse_args()
    backup.tree()
    path = backup.path_for("keys", args.authority, ".authority.json")
    if path.stat().st_size > 128 * 1024 or path.stat().st_nlink != 1:
        raise ValueError("unsafe authority config")
    authority = AuthorityConfig.from_json(path.read_bytes())
    print(json.dumps(asyncio.run(recover(args.restore, authority, UUID(args.run_id))), separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Recovery stopped or outcome unknown; preserve state and reconcile.", file=sys.stderr)
        raise SystemExit(2) from None
