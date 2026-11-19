"""Guarded synthetic operator reconciliation; check by default, one repair step only."""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from uuid import UUID

import bootstrap_runtime as runtime
import replay_synthetic
import run_local

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.quality import PostgresReconciler
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.quality_v1 import QualityReport


class SafeParser(argparse.ArgumentParser):
    def error(self, message): raise ValueError("invalid command arguments")


def number(value, minimum, maximum):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        raise ValueError("invalid numeric bound")
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise ValueError("numeric bound out of range")
    return parsed


def parse_args(argv=None):
    parser = SafeParser(description="Check one synthetic projection or run one explicit replay operation.")
    parser.add_argument("--consumer", choices=("synthetic.activity", "synthetic.independent"), required=True)
    parser.add_argument("--subject-ref", type=UUID, required=True)
    parser.add_argument("--course-id")
    parser.add_argument("--repair", choices=("begin", "step", "finish"))
    parser.add_argument("--request-id", type=UUID)
    parser.add_argument("--generation", type=lambda v: number(v, 1, 2**63 - 1))
    parser.add_argument("--subject-epoch", type=lambda v: number(v, 0, 2**63 - 1))
    parser.add_argument("--limit", type=lambda v: number(v, 1, 100), default=100)
    args = parser.parse_args(argv)
    if args.repair:
        if args.request_id is None or args.course_id is not None:
            raise ValueError("repair requires request identity and whole subject")
        if args.repair == "begin" and (args.generation is not None or args.subject_epoch is not None):
            raise ValueError("begin has no prior generation")
        if args.repair != "begin" and (args.generation is None or args.subject_epoch is None):
            raise ValueError("repair continuation requires prior generation and epoch")
    elif args.request_id is not None or args.generation is not None or args.subject_epoch is not None:
        raise ValueError("repair arguments require explicit repair action")
    return args


async def check(args, record):
    database = Database(Settings(database_url=record["urls"]["processor"], pool_size=1, pool_overflow=0, concurrency=1))
    try:
        return await PostgresReconciler(database).check(args.consumer, 1, args.subject_ref, course_id=args.course_id)
    finally:
        await database.close()


def public_check(report):
    if not isinstance(report, QualityReport): raise ValueError("invalid reconciliation result")
    value = asdict(report)
    if isinstance(value["data_through"], datetime): value["data_through"] = value["data_through"].isoformat()
    return value


def main(argv=None):
    try:
        args = parse_args(argv)
        with runtime.operation_lock():
            record = replay_synthetic.verified_record()
            if args.repair:
                if args.repair == "begin": run_local.verify_replay_retirement(record)
                args.action = args.repair
                state = asyncio.run(replay_synthetic.execute(args, record))
                validated = replay_synthetic.public_result(state, args)
                output = {key: validated[key] for key in ("phase", "generation", "subject_epoch", "blocked")}
            else:
                output = public_check(asyncio.run(check(args, record)))
        print(json.dumps(output, separators=(",", ":")))
        return 0
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):
        print("Operation failed or outcome is unknown; reconcile the same request ID before retry.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
