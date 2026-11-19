import asyncio
from contextlib import nullcontext
import importlib
import json
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import replay_synthetic as cli
from pymentor_analytics.contracts.replay_v1 import ReplayHandle, ReplayState


SUBJECT = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
REQUEST = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
BASE = ["--consumer", "synthetic.activity", "--subject-ref", str(SUBJECT), "--request-id", str(REQUEST)]
HISTORY = ("r3", ("r3", "r2", "r1"), "a" * 64)


def test_import_does_not_acquire_lock_or_access_database():
    with patch.object(cli.runtime, "operation_lock", side_effect=AssertionError("lock")), \
         patch.object(cli.runtime, "read_record", side_effect=AssertionError("database")), \
         patch.object(asyncio, "run", side_effect=AssertionError("work")):
        importlib.reload(cli)


@pytest.mark.parametrize("extra", [
    ["--generation", "0", "--subject-epoch", "0"],
    ["--generation", "1", "--subject-epoch", "-1"],
    ["--generation", "true", "--subject-epoch", "0"],
    ["--generation", "1", "--subject-epoch", "0", "--limit", "0"],
    ["--generation", "1", "--subject-epoch", "0", "--limit", "101"],
])
def test_rejects_invalid_bounds_before_database(extra, capsys):
    with patch.object(cli.runtime, "operation_lock", side_effect=AssertionError("must not lock")):
        assert cli.main(["step", *BASE, *extra]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and str(SUBJECT) not in captured.err


def test_zero_epoch_and_page_bounds_are_valid():
    args = cli.parse_args(["step", *BASE, "--generation", "1", "--subject-epoch", "0", "--limit", "1"])
    assert args.subject_epoch == 0 and args.generation == 1 and args.limit == 1
    assert cli.parse_args(["step", *BASE, "--generation", "1", "--subject-epoch", "0", "--limit", "100"]).limit == 100
    with pytest.raises(Exception):
        cli.integer_at_least(1)(True)


def test_invalid_identity_and_consumer_are_not_echoed(capsys):
    for args in (["begin", *BASE[:1], "unknown", *BASE[2:]],
                 ["begin", "--consumer", "synthetic.activity", "--subject-ref", "secret-invalid", "--request-id", str(REQUEST)]):
        assert cli.main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "secret-invalid" not in captured.err and str(SUBJECT) not in captured.err


@pytest.mark.parametrize("state,actual", [("migration_pending", [("r3",)]),
                                           ("ready", [("r1",)]), ("ready", [("unknown",)]),
                                           ("ready", [("r3",), ("r2",)])])
def test_unsafe_or_outdated_schema_refuses_operation(state, actual):
    record = {"state": state}
    with patch.object(cli.runtime, "read_record", return_value=record), \
         patch.object(cli.runtime, "validate_record"), patch.object(cli.runtime, "schema_history", return_value=HISTORY), \
         patch.object(cli.bootstrap, "config", return_value={}), patch.object(cli.runtime, "verify_database", return_value=actual):
        with pytest.raises(ValueError):
            cli.verified_record()


def test_invalid_metadata_cannot_reach_database():
    with patch.object(cli.runtime, "read_record", return_value={}), \
         patch.object(cli.runtime, "validate_record", side_effect=ValueError), \
         patch.object(cli.runtime, "verify_database") as verify:
        with pytest.raises(ValueError):
            cli.verified_record()
        verify.assert_not_called()


def test_sentinel_failure_never_invokes_replay(capsys):
    with patch.object(cli.runtime, "operation_lock", return_value=nullcontext()), \
         patch.object(cli.runtime, "read_record", return_value={"state": "ready"}), \
         patch.object(cli.runtime, "validate_record"), patch.object(cli.runtime, "schema_history", return_value=HISTORY), \
         patch.object(cli.bootstrap, "config", return_value={}), \
         patch.object(cli.runtime, "verify_database", side_effect=ValueError("sentinel")), \
         patch.object(cli, "execute", new_callable=AsyncMock) as execute:
        assert cli.main(["begin", *BASE]) == 2
        execute.assert_not_awaited()
    assert capsys.readouterr().out == ""


def test_maintenance_lock_covers_guard_and_operation(capsys):
    active = []
    class Lock:
        def __enter__(self):
            active.append(True)
        def __exit__(self, *unused):
            active.pop()
    def verified():
        assert active
        return {}
    def retired(record):
        assert active
    async def execute(args, record):
        assert active
        return ReplayState(ReplayHandle(args.consumer, 1, args.subject_ref, args.request_id, 1, 0), "cleanup", None, None)
    with patch.object(cli.runtime, "operation_lock", return_value=Lock()), \
         patch.object(cli, "verified_record", side_effect=verified), \
         patch.object(cli.run_local, "verify_replay_retirement", side_effect=retired), \
         patch.object(cli, "execute", side_effect=execute):
        assert cli.main(["begin", *BASE]) == 0
    assert not active
    assert json.loads(capsys.readouterr().out)["subject_epoch"] == 0


def test_result_contains_only_permitted_non_subject_fields(capsys):
    handle = ReplayHandle("synthetic.activity", 1, SUBJECT, REQUEST, 4, 0)
    state = ReplayState(handle, "scan", "private-namespace", UUID(int=5), True)
    with patch.object(cli.runtime, "operation_lock", return_value=nullcontext()), \
         patch.object(cli, "verified_record", return_value={}), patch.object(cli.run_local, "verify_replay_retirement"), \
         patch.object(cli, "execute", new_callable=AsyncMock, return_value=state):
        assert cli.main(["begin", *BASE]) == 0
    output = capsys.readouterr().out
    assert json.loads(output) == {"phase": "scan", "request_id": str(REQUEST), "generation": 4, "subject_epoch": 0, "blocked": True}
    assert str(SUBJECT) not in output and "private-namespace" not in output


def test_result_for_different_generation_fails_closed():
    args = cli.parse_args(["step", *BASE, "--generation", "2", "--subject-epoch", "0"])
    state = ReplayState(ReplayHandle(args.consumer, 1, SUBJECT, REQUEST, 3, 0), "scan", None, None)
    with pytest.raises(ValueError):
        cli.public_result(state, args)


def test_unknown_outcome_does_not_retry_or_leak(capsys):
    with patch.object(cli.runtime, "operation_lock", return_value=nullcontext()), \
         patch.object(cli, "verified_record", return_value={}), \
         patch.object(cli.run_local, "verify_replay_retirement"), \
         patch.object(cli, "execute", new_callable=AsyncMock, side_effect=RuntimeError("secret-url")) as call:
        assert cli.main(["begin", *BASE]) == 2
        assert call.await_count == 1
    result = capsys.readouterr()
    assert result.out == "" and "secret-url" not in result.err and "same request ID" in result.err


def test_begin_refuses_old_running_process_without_touching_replay(capsys):
    with patch.object(cli.runtime, "operation_lock", return_value=nullcontext()), \
         patch.object(cli, "verified_record", return_value={}), \
         patch.object(cli.run_local, "verify_replay_retirement", side_effect=ValueError), \
         patch.object(cli, "execute", new_callable=AsyncMock) as execute:
        assert cli.main(["begin", *BASE]) == 2
        execute.assert_not_awaited()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("action", ["step", "finish"])
def test_step_finish_do_not_require_stopped_containers(action, capsys):
    state = ReplayState(ReplayHandle("synthetic.activity", 1, SUBJECT, REQUEST, 2, 0), "scan", None, None)
    with patch.object(cli.runtime, "operation_lock", return_value=nullcontext()), \
         patch.object(cli, "verified_record", return_value={}), \
         patch.object(cli.run_local, "verify_replay_retirement") as preflight, \
         patch.object(cli, "execute", new_callable=AsyncMock, return_value=state):
        assert cli.main([action, *BASE, "--generation", "2", "--subject-epoch", "0"]) == 0
        preflight.assert_not_called()
    assert json.loads(capsys.readouterr().out)["generation"] == 2


@pytest.mark.parametrize("action", ["begin", "step", "finish"])
def test_exactly_one_adapter_call_and_close(action):
    extra = [] if action == "begin" else ["--generation", "2", "--subject-epoch", "0"]
    if action == "step":
        extra += ["--limit", "7"]
    args = cli.parse_args([action, *BASE, *extra])
    database = MagicMock()
    database.close = AsyncMock()
    store = MagicMock()
    store.begin = AsyncMock(return_value="begun")
    store.step = AsyncMock(return_value="stepped")
    store.finish = AsyncMock(return_value="finished")
    record = {"urls": {"processor": "postgresql://test"}}
    with patch("pymentor_analytics.app.config.Settings"), \
         patch("pymentor_analytics.adapters.postgres.database.Database", return_value=database), \
         patch("pymentor_analytics.adapters.postgres.replay.PostgresReplay", return_value=store):
        asyncio.run(cli.execute(args, record))
    assert sum(getattr(store, name).await_count for name in ("begin", "step", "finish")) == 1
    assert getattr(store, action).await_count == 1
    database.close.assert_awaited_once()
    if action == "step":
        assert store.step.await_args.kwargs == {"limit": 7}


def test_adapter_failure_still_closes_database():
    args = cli.parse_args(["begin", *BASE])
    database, store = MagicMock(), MagicMock()
    database.close = AsyncMock()
    store.begin = AsyncMock(side_effect=RuntimeError("unknown"))
    with patch("pymentor_analytics.app.config.Settings"), \
         patch("pymentor_analytics.adapters.postgres.database.Database", return_value=database), \
         patch("pymentor_analytics.adapters.postgres.replay.PostgresReplay", return_value=store):
        with pytest.raises(RuntimeError):
            asyncio.run(cli.execute(args, {"urls": {"processor": "postgresql://test"}}))
    database.close.assert_awaited_once()
