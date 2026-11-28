"""Read-only unless requested, on a verified disposable Analytics target."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from uuid import UUID

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))

from psycopg import Error as PostgresError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.student_windows_reconcile import PostgresWindowDiagnosticStore, diagnostic_document
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.windows_v1 import window_bounds
from pymentor_analytics.modules.analytics.activity.student_windows_evidence import MAX_MANIFEST_KEYS, load_evidence
from pymentor_analytics.modules.analytics.activity.student_windows_reconcile import CoverageEvidence
from pymentor_analytics.modules.analytics.activity.student_windows_worker import WindowReconcileWorker
from pymentor_analytics.modules.collector.ports import IntakeForbidden
from tools.bootstrap_postgres import PATTERN, config, connect

MAX_KEYS = MAX_MANIFEST_KEYS
MISMATCH = {"missing_v3", "extra_v3", "different", "both_lost"}


class TargetError(ValueError):
    """Execution target did not meet the synthetic fixture boundary."""


def _target(args, processor_url: str) -> tuple[str, int]:
    if not PATTERN.fullmatch(args.target) or not re.fullmatch(r"[0-9a-f]{64}", args.sentinel_sha256):
        raise TargetError("a disposable database name and sentinel SHA-256 are required")
    parsed = make_url(processor_url)
    if (
        parsed.drivername != "postgresql" or parsed.host != "127.0.0.1" or parsed.port != 57392
        or parsed.database != args.target or parsed.username != f"{args.target}_processor"
        or not parsed.password or parsed.query
    ):
        raise TargetError("processor URL must use the named disposable target on loopback")
    data = config(args.pg_config)
    with connect(data, args.target) as connection:
        owner = connection.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()"
        ).fetchone()
        sentinels = connection.execute("SELECT token FROM public.analytics_test_sentinel").fetchall()
        revisions = connection.execute("SELECT version_num FROM public.alembic_version").fetchall()
        if owner != (args.target + "_migrate",) or len(sentinels) != 1 or revisions not in ([("a015",)], [("a016",)]):
            raise TargetError("target owner, sentinel or migration revision mismatch")
        if sha256(sentinels[0][0].encode()).hexdigest() != args.sentinel_sha256:
            raise TargetError("target sentinel mismatch")
        prefix = connection.execute(
            "SELECT authority_id, applied_through FROM analytics.privacy_state WHERE singleton"
        ).fetchone()
        pending = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM analytics.privacy_receipts WHERE phase <> 'complete')"
        ).fetchone()[0]
    if prefix is None or pending:
        raise PrivacyUnavailable()
    return prefix


def _clock(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected ISO 8601 time with UTC offset") from None
    if result.utcoffset() is None:
        raise argparse.ArgumentTypeError("expected ISO 8601 time with UTC offset")
    return result


def parser() -> argparse.ArgumentParser:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--pg-config", type=Path, default=SERVICE / ".local/postgres-connection.json")
    args.add_argument("--target", required=True, help="name of the synthetic disposable a015 or a016 database")
    args.add_argument("--sentinel-sha256", required=True, help="SHA-256 printed when the target was provisioned")
    audience = args.add_mutually_exclusive_group(required=True)
    audience.add_argument("--student-id", type=UUID, action="append", help="synthetic lifecycle; repeat for a cohort")
    audience.add_argument("--all-eligible", action="store_true", help="inspect all subjects on the verified synthetic target")
    args.add_argument("--cohort-t0", type=_clock, required=True)
    args.add_argument("--through", type=_clock, required=True, help="diagnostic cutoff and trusted observation clock")
    args.add_argument("--manifest", type=Path, help="independent expected windows and coverage evidence")
    args.add_argument("--limit", type=int, default=100, help="maximum candidate keys to return, up to 1000")
    args.add_argument("--repair", action="store_true", help="explicitly allow reconciliation on the verified target")
    return args


async def inspect_windows(
    args, processor_url: str, *, privacy_prefix: tuple[str, int], readonly_settings: Settings | None = None,
) -> dict[str, object]:
    subjects = set(args.student_id or ())
    all_eligible = getattr(args, "all_eligible", False)
    if (all_eligible and subjects) or (not all_eligible and not 1 <= len(subjects) <= MAX_KEYS):
        raise TargetError("invalid diagnostic audience")
    if not 1 <= args.limit <= MAX_KEYS:
        raise TargetError("invalid report limit")
    if not timedelta(0) < args.through - args.cohort_t0 <= timedelta(days=90):
        raise TargetError("the diagnostic interval must be positive and at most 90 days")
    evidence = load_evidence(args.manifest)
    for key in evidence:
        if ((not all_eligible and key.student_id not in subjects)
                or not args.cohort_t0 < window_bounds(key)[1] <= args.through):
            raise TargetError("manifest key is outside the selected cohort or interval")

    if readonly_settings is not None:
        url = make_url(processor_url)
        if (args.repair or readonly_settings.database_url.get_secret_value() != processor_url
                or url.host != "127.0.0.1" or url.port != 57392
                or url.database != args.target or url.username != f"{args.target}_processor"):
            raise TargetError("validated local target settings may only be used for matching read-only diagnostics")
    settings = readonly_settings or Settings(database_url=processor_url, db_timeout_seconds=5)
    database = Database(settings)

    async def guard(session):
        if not args.repair and not session.info.get("diagnostic_read_only"):
            await session.execute(text("SET TRANSACTION READ ONLY"))
            session.info["diagnostic_read_only"] = True
        cursor = (await session.execute(text("""
            SELECT authority_id, applied_through FROM analytics.privacy_state WHERE singleton
        """))).one_or_none()
        if cursor != privacy_prefix:
            raise PrivacyUnavailable()

    database.transaction_guard = guard
    store = PostgresWindowDiagnosticStore(
        database, cohort=subjects, observation_start=args.cohort_t0, all_eligible=all_eligible,
    )
    worker = WindowReconcileWorker(store, cohort_t0=args.cohort_t0)
    try:
        candidates = await store.diagnostic_keys(through=args.through, limit=args.limit + 1)
        keys = sorted(set(candidates) | evidence.keys(), key=lambda key: (key.activity_date, key.slot_no, key.student_id))
        truncated = len(keys) > args.limit
        rows = []
        for key in keys[:args.limit]:
            item = await worker.run_key(key, now=args.through, repair=args.repair,
                                        evidence=evidence.get(key, CoverageEvidence()))
            persisted = None
            if item.status != "fenced":
                try:
                    persisted = await store.repair_evidence(key)
                except IntakeForbidden:
                    pass
            rows.append(diagnostic_document(item, persisted))
    finally:
        await database.close()

    compared = [row for row in rows if "outcome" in row]
    mismatch = [row for row in compared if row["outcome"] in MISMATCH]
    still_different = [row for row in mismatch if row["after"] is None or row["after"]["context_diff"]]
    unknown = sum(row["outcome"] in {"unknown", "old_only"} for row in compared) + (len(rows) - len(compared))
    outcome = "unknown"
    if mismatch:
        outcome = "mismatch"
    elif not truncated and not unknown and any(row.get("expected_stats") for row in compared):
        outcome = "ready"
    return {
        "report_version": 1, "mode": "repair" if args.repair else "read_only", "status": outcome,
        "target": args.target, "truncated": truncated, "denominator": len(rows), "unknown": unknown,
        "old_only": sum(row["outcome"] == "old_only" for row in compared),
        "v3_only": sum("v3_only" in row["diagnostics"]["coverage_reasons"] for row in compared),
        "writer_conflict": sum("writer_conflict" in row["diagnostics"]["coverage_reasons"] for row in compared),
        "mismatch_before": len(mismatch), "mismatch_after": len(still_different) if args.repair else None,
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    role_url = os.environ.get("ANALYTICS_STUDENT_WINDOWS_PROCESSOR_URL", "")
    try:
        prefix = _target(args, role_url)
        result = asyncio.run(inspect_windows(args, role_url, privacy_prefix=prefix))
    except (TargetError, OSError, SQLAlchemyError, PostgresError, PrivacyUnavailable, ValueError):
        print(json.dumps({"status": "unavailable", "error": "invalid or inaccessible synthetic target or manifest"}))
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
