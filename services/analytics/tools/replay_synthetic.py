"""One explicitly requested synthetic replay operation; never auto-retry."""
import argparse
import asyncio
import json
from pathlib import Path
import re
import sys
from uuid import UUID

import bootstrap_postgres as bootstrap
import bootstrap_runtime as runtime
import run_local

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))

from pymentor_analytics.contracts.replay_v1 import MAX_REPLAY_PAGE, ReplayHandle, ReplayState
CONSUMERS = ("synthetic.activity", "synthetic.independent")
PAGE_LIMIT = min(100, MAX_REPLAY_PAGE)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid command arguments")


def integer_at_least(minimum, maximum=None):
    def parse(value):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
            raise argparse.ArgumentTypeError("invalid number")
        number = int(value)
        if number < minimum or (maximum is not None and number > maximum):
            raise argparse.ArgumentTypeError("number outside allowed range")
        return number
    return parse


def parse_args(argv=None):
    parser = SafeParser(description="Run one guarded synthetic replay operation.")
    subparsers = parser.add_subparsers(dest="action", required=True, parser_class=SafeParser)
    for action in ("begin", "step", "finish"):
        command = subparsers.add_parser(action)
        command.add_argument("--consumer", choices=CONSUMERS, required=True)
        command.add_argument("--subject-ref", type=UUID, required=True)
        command.add_argument("--request-id", type=UUID, required=True)
        if action != "begin":
            command.add_argument("--generation", type=integer_at_least(1), required=True)
            command.add_argument("--subject-epoch", type=integer_at_least(0), required=True)
        if action == "step":
            command.add_argument("--limit", type=integer_at_least(1, PAGE_LIMIT), default=PAGE_LIMIT)
    return parser.parse_args(argv)


def verified_record():
    record = runtime.read_record()
    runtime.validate_record(record)
    if record["state"] != "ready":
        raise ValueError("runtime is not initialized")
    head, revisions, _ = runtime.schema_history()
    actual = runtime.current_revision(runtime.verify_database(bootstrap.config(runtime.CONNECTION), record), revisions)
    if actual != head:
        raise ValueError("schema is not at current head")
    return record


async def execute(args, record):
    # All connections and transactions belong to this command and its adapter.
    from pymentor_analytics.adapters.postgres.database import Database
    from pymentor_analytics.adapters.postgres.replay import PostgresReplay
    from pymentor_analytics.app.config import Settings

    database = Database(Settings(database_url=record["urls"]["processor"], pool_size=1,
                                 pool_overflow=0, concurrency=1))
    try:
        replay = PostgresReplay(database)
        if args.action == "begin":
            return await replay.begin(args.consumer, 1, args.subject_ref, args.request_id)
        handle = ReplayHandle(args.consumer, 1, args.subject_ref, args.request_id,
                              args.generation, args.subject_epoch)
        if args.action == "step":
            return await replay.step(handle, limit=args.limit)
        return await replay.finish(handle)
    finally:
        await database.close()


def public_result(state, args):
    if (not isinstance(state, ReplayState) or not isinstance(state.handle, ReplayHandle)
            or state.handle.request_id != args.request_id or state.handle.subject_ref != args.subject_ref
            or state.handle.consumer != args.consumer or state.handle.version != 1
            or (args.action != "begin" and (state.handle.generation, state.handle.subject_epoch)
                != (args.generation, args.subject_epoch))
            or state.phase not in {"cleanup", "scan", "drain", "complete"}
            or type(state.handle.generation) is not int or state.handle.generation < 1
            or type(state.handle.subject_epoch) is not int or state.handle.subject_epoch < 0
            or type(state.blocked) is not bool):
        raise ValueError("invalid replay response")
    return {"phase": state.phase, "request_id": str(args.request_id), "generation": state.handle.generation,
            "subject_epoch": state.handle.subject_epoch, "blocked": state.blocked}


def main(argv=None):
    try:
        args = parse_args(argv)
        with runtime.operation_lock():
            record = verified_record()
            if args.action == "begin":
                run_local.verify_replay_retirement(record)
            state = asyncio.run(execute(args, record))
        print(json.dumps(public_result(state, args), separators=(",", ":")))
        return 0
    except (Exception, KeyboardInterrupt, asyncio.CancelledError):
        print("Operation failed or outcome is unknown. Reconcile using the same request ID before any retry.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
