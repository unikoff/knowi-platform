"""Plan, resume and independently verify the CORP-608 historical windows transfer."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime
from functools import partial
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from uuid import UUID

import psycopg
from pydantic import SecretStr, ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from pymentor_analytics.adapters.auth.config import validate_authority_key_separation
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.student_windows_backfill import BackfillError, LiveProof, StudentWindowsBackfill
from pymentor_analytics.adapters.postgres.student_windows_backfill_verify import observe_core, verify
from pymentor_analytics.adapters.postgres.student_windows_backfill_coverage import CoverageWitness
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
from pymentor_analytics.app.config import Settings, load_settings, local_deletion_config
from pymentor_analytics.app.recovery_permit import host_boot_permitted
from pymentor_analytics.adapters.core_privacy.client import CoreDeletionClient
from pymentor_analytics.adapters.core_privacy.guard import CoreDeletionGuard
from pymentor_analytics.app.privacy import PrivacyRuntime
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.windows_v1 import WindowKey, WindowStat, canonical_stats

VERSION = "608.M/2"
SCHEMA = "a016"
LOCAL_NAME = re.compile(r"analytics_test_[0-9a-f]{32}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")


def aware_clock(raw):
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("expected ISO-8601 date and time") from None
    if value.utcoffset() is None:
        raise argparse.ArgumentTypeError("T0 must include a UTC offset")
    return value


def positive_count(raw):
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("expected integer") from None
    if not 1 <= value <= 1000:
        raise argparse.ArgumentTypeError("value must be between 1 and 1000")
    return value


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--target", choices=("local", "release"), required=True)
    result.add_argument("--database", required=True)
    result.add_argument("--sentinel-sha256")
    result.add_argument("--run-id", type=UUID, required=True)
    result.add_argument("--t0", type=aware_clock, required=True)
    result.add_argument("--mode", choices=("plan", "apply", "barrier", "close-v2", "verify"), required=True)
    result.add_argument("--batch-size", type=positive_count, default=100)
    result.add_argument("--max-batches", type=positive_count, default=100)
    result.add_argument("--proof", type=Path)
    result.add_argument("--coverage", type=Path, help="independent read-only source witness for verify")
    result.add_argument("--legacy-input-drained", action="store_true")
    result.add_argument("--details", type=Path, help="new private discrepancy file (verify only)")
    return result


def required_env(name):
    value = os.environ.get(name)
    if not value:
        raise BackfillError("missing required environment setting " + name)
    return value


def connection_identity(dsn):
    url = make_url(dsn)
    if url.drivername not in {"postgresql", "postgresql+psycopg"} or not url.host or not url.password:
        raise BackfillError("explicit PostgreSQL target and credentials required")
    return url


def verify_target(args, processor_dsn, inspector_dsn):
    processor = connection_identity(processor_dsn)
    inspector = connection_identity(inspector_dsn) if inspector_dsn is not None else processor
    if (processor.host, processor.port, processor.database) != (inspector.host, inspector.port, inspector.database):
        raise BackfillError("inspector and processor must address the same database")
    if (processor.database != args.database or processor.username != f"{args.database}_processor"
            or not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", args.database)):
        raise BackfillError("database identity or restricted Analytics roles disagree")
    if args.target == "local":
        if (processor.host != "127.0.0.1" or processor.port is None or not LOCAL_NAME.fullmatch(args.database)
                or inspector.username != f"{args.database}_migrate"
                or args.sentinel_sha256 is None or not HEX.fullmatch(args.sentinel_sha256)):
            raise BackfillError("local target requires a loopback disposable database and its sentinel digest")
    elif required_env("ANALYTICS_BACKFILL_RELEASE_GO") != args.database:
        raise BackfillError("release approvals must explicitly identify this Analytics database")
    with psycopg.connect(inspector.set(drivername="postgresql").render_as_string(hide_password=False),
                         connect_timeout=5) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        owner = connection.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone()
        if owner != (f"{args.database}_migrate",):
            raise BackfillError("database owner mismatch")
        if args.target == "local":
            if connection.execute("SELECT version_num FROM public.alembic_version").fetchall() != [(SCHEMA,)]:
                raise BackfillError("local migration revision mismatch")
            sentinels = connection.execute("SELECT token FROM public.analytics_test_sentinel").fetchall()
            if len(sentinels) != 1 or sha256(sentinels[0][0].encode()).hexdigest() != args.sentinel_sha256:
                raise BackfillError("local target sentinel mismatch")


def load_proof(path, run_id, t0):
    if path is None:
        return {}, None
    with path.open("rb") as stream:
        raw = stream.read(32 * 1024 * 1024 + 1)
    if len(raw) > 32 * 1024 * 1024:
        raise BackfillError("managed proof exceeds the size bound")
    document = json.loads(raw)
    if (set(document) != {"version", "run_id", "t0", "source_attestation_sha256", "windows"}
            or document["version"] != 1 or not HEX.fullmatch(document["source_attestation_sha256"])
            or UUID(document["run_id"]) != run_id or aware_clock(document["t0"]) != t0):
        raise BackfillError("managed proof must identify the exact run and T0")
    proof = {}
    for row in document["windows"]:
        if (set(row) != {"key", "expected_stats", "live_sha256", "source_generation", "change_generation", "post_t0_source_sha256"}
                or any(not HEX.fullmatch(row[name]) for name in ("live_sha256", "post_t0_source_sha256"))
                or any(type(row[name]) is not int or row[name] < 0 for name in ("source_generation", "change_generation"))):
            raise BackfillError("invalid managed complete-window proof")
        key = WindowKey.model_validate_json(json.dumps(row["key"]))
        if key in proof:
            raise BackfillError("duplicate proof key")
        stats = canonical_stats(tuple(WindowStat.model_validate(item) for item in row["expected_stats"]))
        proof[key] = LiveProof(stats, row["live_sha256"], row["source_generation"],
                               row["change_generation"], row["post_t0_source_sha256"])
    return proof, sha256(raw).hexdigest()


async def apply(backfill, *, size, limit):
    step = 0
    while step < limit:
        if not await backfill.scan(size):
            break
        step += 1
    checkpoint = await backfill.barrier()
    while step < limit:
        if not await backfill.catch_up(size):
            break
        step += 1
    await backfill.requeue_unresolved(size)
    results = Counter()
    while step < limit:
        step += 1
        candidates = await backfill.candidates(size)
        if not candidates:
            break
        for key in candidates:
            results[await backfill.apply_key(key)] += 1
    return dict(status="CHECKPOINT", checkpoint=await backfill.plan(), work=dict(results),
                steps=step, step_budget=limit, observed_barrier=checkpoint["accepted_position"])


async def run(args, settings, authority, core_url, witness):
    database = Database(settings)
    recovery_permit = host_boot_permitted if settings.host_boot_recovery_gate else None
    runtime = PrivacyRuntime(AuthorityClient(authority), database, recovery_permit=recovery_permit)
    source = local_deletion_config(settings, "processor")
    status_database = None
    if source is not None:
        status_database = Database(settings.model_copy(update={
            "database_url": settings.core_deletion_status_database_url,
            "pool_size": 1, "pool_overflow": 0, "db_timeout_seconds": min(settings.db_timeout_seconds, 5),
        }))
        source_guard = CoreDeletionGuard(CoreDeletionClient(source), status_database, "core-local")

        async def guard(session):
            await runtime.check_in_transaction(session)
            await source_guard.check(session)
            await runtime.check_in_transaction(session)
    else:
        guard = runtime.check_in_transaction
    database.transaction_guard = database.transaction_exit_guard = guard
    runtime.start()
    try:
        async with asyncio.timeout(15):
            while not await runtime.is_ready():
                await asyncio.sleep(.25)
            async with database.transaction() as session:
                if not (await session.execute(text("""SELECT
                    to_regclass('analytics.window_backfill_scan_cursor') IS NOT NULL
                    AND to_regclass('analytics.window_backfill_run') IS NOT NULL
                    AND to_regclass('analytics.window_backfill_journal') IS NOT NULL"""))).scalar_one():
                    raise BackfillError("required historical schema capability is absent")
        core_reader = (partial(observe_core, psycopg, core_url, witness.core_target, require_tls=args.target == "release")
                       if core_url and witness else None)
        proof, proof_sha256 = load_proof(args.proof, args.run_id, args.t0)
        backfill = StudentWindowsBackfill(database, run_id=args.run_id, t0=args.t0, proof=proof)
        if args.mode == "plan":
            result = dict(status="CAPTURING", checkpoint=await backfill.plan())
        elif args.mode == "apply":
            result = await apply(backfill, size=args.batch_size, limit=args.max_batches)
        elif args.mode in {"barrier", "close-v2"}:
            result = dict(status="CHECKPOINT", checkpoint=await backfill.barrier(close=args.mode == "close-v2"))
        elif args.details is not None:
            with args.details.open("x", encoding="utf-8") as details:
                result = await verify(backfill, core_reader=core_reader, witness=witness, details=details)
        else:
            result = await verify(backfill, core_reader=core_reader, witness=witness)
        return dict(version=VERSION, schema=SCHEMA, mode=args.mode, proof_sha256=proof_sha256, **result)
    finally:
        await runtime.stop()
        await database.close()
        if status_database is not None:
            await status_database.close()


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    try:
        if args.mode == "close-v2" and not args.legacy_input_drained:
            raise BackfillError("close-v2 requires the operator's old input/drain evidence")
        if (args.details is not None or args.coverage is not None) and args.mode != "verify":
            raise BackfillError("coverage and private details are available only for verify")
        witness = None
        if args.mode == "verify":
            if args.coverage is None:
                raise BackfillError("verify requires an independently produced source coverage witness")
            with args.coverage.open("rb") as stream:
                raw = stream.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise BackfillError("source coverage witness exceeds the size bound")
            witness = CoverageWitness.model_validate_json(raw)
            if (witness.run_id != args.run_id or witness.t0 != args.t0
                    or (args.target == "release" and witness.core_target.standby)):
                raise BackfillError("source witness must match the exact run, R1 observation start and primary Core")
        if args.target == "release":
            settings = load_settings("processor")
            if settings.issuer_mode != "production":
                raise BackfillError("release requires the deployed production processor profile")
            processor_url = settings.database_url.get_secret_value()
            verify_target(args, processor_url, None)
        else:
            processor_url = required_env("ANALYTICS_DATABASE_URL")
            verify_target(args, processor_url, required_env("ANALYTICS_BACKFILL_INSPECT_URL"))
            # Only after the exact isolated owner, loopback and sentinel check.
            settings = Settings.model_construct(database_url=SecretStr(processor_url), db_timeout_seconds=10)
        core_url = os.environ.get("ANALYTICS_BACKFILL_CORE_READ_URL") if args.mode == "verify" else None
        if core_url:
            parsed = connection_identity(core_url)
            if args.target == "local" and (parsed.host != "127.0.0.1"
                    or not re.fullmatch(r"core_608_[0-9a-f]{32}", parsed.database or "")
                    or parsed.username != f"{parsed.database}_reader"):
                raise BackfillError("local Core coverage requires its separate disposable reader database")
            core_url = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
        elif args.mode == "verify":
            raise BackfillError("verify requires the scoped Core reader connection")
        authority = AuthorityConfig.from_json(required_env("ANALYTICS_PRIVACY_AUTHORITY_JSON"))
        if args.target == "release":
            validate_authority_key_separation(settings, authority.public_keys)
        result = asyncio.run(run(args, settings, authority, core_url, witness))
    except (BackfillError, TimeoutError, PrivacyUnavailable, SQLAlchemyError, psycopg.Error,
            OSError, ValidationError, ValueError, TypeError) as error:
        result = dict(version=VERSION, schema=SCHEMA, mode=args.mode, status="STOP",
                      reason=str(error) if isinstance(error, BackfillError) else type(error).__name__)
    if "checkpoint" in result:
        checkpoint = result["checkpoint"]
        cursor = (checkpoint.pop("scan_namespace", None), checkpoint.pop("scan_event", None))
        checkpoint.pop("scan_subject", None)
        checkpoint["scan_cursor_sha256"] = (sha256(str(cursor).encode()).hexdigest() if any(cursor) else None)
    result["tool_sha256"] = sha256(Path(__file__).read_bytes()).hexdigest()
    print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
    return 2 if result["status"] == "STOP" else 0


if __name__ == "__main__":
    raise SystemExit(main())
