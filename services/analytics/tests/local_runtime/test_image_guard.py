from contextlib import nullcontext
import json
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import bootstrap_runtime as runtime
import run_local as local


IMAGE_ID = "sha256:" + "1" * 64
HISTORY = ("v3", ("v3", "v2", "v1"), "a" * 64)
FINGERPRINT = "b" * 64
RESULT = json.dumps({"history": [HISTORY[0], list(HISTORY[1]), HISTORY[2]], "runtime_sha256": FINGERPRINT})
NAME = "analytics_test_" + "a" * 32
RECORD = {"synthetic_only": True, "do_not_drop": True, "project": runtime.PROJECT,
          "state": "ready", "database": NAME, "nonce": "b" * 64,
          "urls": {role: f"postgresql://{NAME}_{role}:synthetic@127.0.0.1:57392/{NAME}"
                   for role in ("migrate", "collector", "processor", "query", "privacy")}}


@pytest.fixture(autouse=True)
def no_docker_context_access(monkeypatch):
    monkeypatch.setattr(local.check_k3s, "local_docker_env", lambda: None)


def test_probe_matches_local_history_and_runtime_fingerprint(capsys):
    # Execute probe code locally with the same source tree; no Docker or DB access.
    code = local.IMAGE_HISTORY_PROBE.replace('Path("/app")', f"Path({str(local.ROOT)!r})")
    try:
        exec(code, {"__name__": "__image_probe_test__"})
    finally:
        if hasattr(signal, "alarm"):
            signal.alarm(0)
    result = json.loads(capsys.readouterr().out)
    history = runtime.schema_history()
    assert result["history"] == [history[0], list(history[1]), history[2]]
    assert result["runtime_sha256"] == local.runtime_files_fingerprint(local.ROOT)


def test_resolves_immutable_id_and_runs_isolated_probe():
    with patch.object(local, "docker", side_effect=[IMAGE_ID, RESULT]) as docker, \
         patch.object(local.check_k3s, "available_memory", return_value=1024**3):
        assert local.resolve_verified_image("knowi-analytics:local", HISTORY, FINGERPRINT) == IMAGE_ID
    inspect, run = [call.args[0] for call in docker.call_args_list]
    assert inspect[:4] == ["image", "inspect", "--format", "{{.Id}}"]
    assert run[0] == "run" and "--network=none" in run and "--read-only" in run
    assert "--cap-drop=ALL" in run and "--pull=never" in run and IMAGE_ID in run
    assert "--memory=128m" in run and "--pids-limit=64" in run and "--user=10001" in run
    assert "knowi-analytics:local" not in run


@pytest.mark.parametrize("image_output", ["", "sha256:short", "repo:tag", "sha256:" + "A" * 64])
def test_invalid_image_id_is_refused(image_output):
    with patch.object(local, "docker", return_value=image_output) as docker:
        with pytest.raises(ValueError):
            local.resolve_verified_image("tag", HISTORY, FINGERPRINT)
        assert docker.call_count == 1


@pytest.mark.parametrize("mutate", [
    lambda result: result.update(history=["old", [], "x"]),
    lambda result: result.update(runtime_sha256="c" * 64),
    lambda result: result.update(extra="unexpected"),
])
def test_stale_or_invalid_image_is_refused(mutate):
    result = json.loads(RESULT)
    mutate(result)
    with patch.object(local, "docker", side_effect=[IMAGE_ID, json.dumps(result)]), \
         patch.object(local.check_k3s, "available_memory", return_value=1024**3):
        with pytest.raises(ValueError):
            local.resolve_verified_image("tag", HISTORY, FINGERPRINT)


def test_image_probe_timeout_cleans_only_verified_container():
    token = "c" * 32
    timeout = subprocess.TimeoutExpired("docker", 25)
    with patch.object(local, "uuid4", return_value=SimpleNamespace(hex=token)), \
         patch.object(local, "docker", side_effect=[IMAGE_ID, timeout, f"{IMAGE_ID} {token}", ""]) as docker, \
         patch.object(local.check_k3s, "available_memory", return_value=1024**3):
        with pytest.raises(RuntimeError, match="timed out"):
            local.resolve_verified_image("tag", HISTORY, FINGERPRINT)
    assert docker.call_args_list[-1].args[0] == ["rm", "--force", "knowi-analytics-imagecheck-" + token]


def test_image_probe_timeout_refuses_to_remove_unverified_container():
    token = "c" * 32
    timeout = subprocess.TimeoutExpired("docker", 25)
    with patch.object(local, "uuid4", return_value=SimpleNamespace(hex=token)), \
         patch.object(local, "docker", side_effect=[IMAGE_ID, timeout, "different identity"]) as docker, \
         patch.object(local.check_k3s, "available_memory", return_value=1024**3):
        with pytest.raises(RuntimeError, match="cleanup outcome unknown"):
            local.resolve_verified_image("tag", HISTORY, FINGERPRINT)
    assert docker.call_count == 3


def container(role, status="exited", running=False, *, project=local.PROJECT):
    return {"Config": {"Labels": {"com.docker.compose.project": project,
                                   "com.docker.compose.service": role,
                                   "com.docker.compose.project.working_dir": str(local.COMPOSE_DIR)}},
            "State": {"Status": status, "Running": running}}


def test_running_old_processor_refuses_replay_without_database_access():
    objects = [container("processor", "running", True)]
    with patch.object(local.check_k3s, "local_docker_env"), \
         patch.object(local, "docker", side_effect=["abc", json.dumps(objects)]), \
         patch.object(local.db, "connect") as connect:
        with pytest.raises(ValueError):
            local.verify_replay_retirement(RECORD)
        connect.assert_not_called()


def test_wrong_container_identity_refuses_replay():
    objects = [container("query", project="other")]
    with patch.object(local.check_k3s, "local_docker_env"), \
         patch.object(local, "docker", side_effect=["abc", json.dumps(objects)]), \
         patch.object(local.db, "connect") as connect:
        with pytest.raises(ValueError):
            local.verify_replay_retirement(RECORD)
        connect.assert_not_called()


@pytest.mark.parametrize("sessions,accepted", [(0, True), (1, False)])
def test_replay_retirement_checks_exact_database_roles(sessions, accepted):
    connection = MagicMock()
    connection.__enter__.return_value.execute.return_value.fetchone.side_effect = [(True,), (sessions,)]
    objects = [container("processor"), container("query")]
    with patch.object(local.check_k3s, "local_docker_env"), \
         patch.object(local, "docker", side_effect=["first\nsecond", json.dumps(objects)]), \
         patch.object(local.db, "config", return_value={}), patch.object(local.db, "connect", return_value=connection):
        if accepted:
            local.verify_replay_retirement(RECORD)
        else:
            with pytest.raises(ValueError):
                local.verify_replay_retirement(RECORD)
    execute = connection.__enter__.return_value.execute
    assert execute.call_args.args[1] == (NAME + "_processor", NAME + "_query")


def test_replay_retirement_refuses_insufficient_session_visibility():
    connection = MagicMock()
    connection.__enter__.return_value.execute.return_value.fetchone.return_value = (False,)
    with patch.object(local.check_k3s, "local_docker_env"), patch.object(local, "docker", return_value=""), \
         patch.object(local.db, "config", return_value={}), patch.object(local.db, "connect", return_value=connection):
        with pytest.raises(ValueError):
            local.verify_replay_retirement(RECORD)
    assert connection.__enter__.return_value.execute.call_count == 1


def test_unsafe_retirement_metadata_does_not_inspect_docker():
    with patch.object(local, "docker") as docker:
        with pytest.raises(ValueError):
            local.verify_replay_retirement({"state": "ready"})
        docker.assert_not_called()


@pytest.mark.parametrize("fingerprints,should_start", [
    ([FINGERPRINT, "c" * 64], False),
    ([FINGERPRINT, FINGERPRINT, "c" * 64], True),
    ([FINGERPRINT, FINGERPRINT, FINGERPRINT], True),
])
def test_source_change_guard_and_compose_uses_verified_id(fingerprints, should_start):
    services = {role: {"mem_limit": str(128 * 1024**2)} for role in ("collector", "processor", "query", "ingress")}
    commands = []
    def compose(args, env, **kwargs):
        commands.append((args, dict(env)))
        return json.dumps({"services": services}) if args[0] == "config" else ""
    with patch.object(runtime, "operation_lock", return_value=nullcontext()), \
         patch.object(runtime, "read_record", return_value=RECORD), \
         patch.object(runtime, "schema_history", return_value=HISTORY), \
         patch.object(runtime.db, "config", return_value={}), \
         patch.object(runtime, "verify_database", return_value=[("v3",)]), \
         patch.object(local, "trust_manifest", return_value=None), \
         patch.object(local.check_k3s, "local_docker_env"), patch.object(local, "verify_project"), \
         patch.object(local, "runtime_files_fingerprint", side_effect=fingerprints), \
         patch.object(local, "resolve_verified_image", return_value=IMAGE_ID), \
         patch.object(local.check_k3s, "available_memory", return_value=4 * 1024**3), \
         patch.object(local, "compose", side_effect=compose):
        if len(fingerprints) == 3 and fingerprints[-1] == FINGERPRINT:
            local.start()
        else:
            with pytest.raises(ValueError):
                local.start()
    operations = [args[0] for args, env in commands]
    assert ("up" in operations) is should_start
    assert all(env["ANALYTICS_IMAGE"] == IMAGE_ID for args, env in commands)
    if should_start and fingerprints[-1] != FINGERPRINT:
        assert operations[-1] == "stop"
