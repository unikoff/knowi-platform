"""Local admission and failure checks; these tests do not call Docker."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import docker_publisher as d
from . import resources as r


@pytest.fixture
def setup_fixture():
    folder, token = r.private_folder()
    try:
        for filename in d.KEYS.values():
            (folder / filename).write_text("synthetic", encoding="utf-8")
        env = {key: "synthetic" for key in d.ENV_KEYS}
        for key in ("PYMENTOR_ANALYTICS_PUBLISHER_ENABLED", "PYMENTOR_ANALYTICS_DELIVERY_ENABLED",
                    "PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED", "PYMENTOR_ANALYTICS_PUBLISHER_BACKFILL_COMPLETE"):
            env[key] = "1"
        core = {"name": "analytics_delivery_test_" + "a" * 32, "owner": "pymentor_test",
                "dsn": "postgresql://pymentor_test:password@127.0.0.1:57382/analytics_delivery_test_" + "a" * 32}
        fixture = {"collector_url": "http://127.0.0.1:18152"}
        authority = "http://127.0.0.1:18250"
        env.update(DATABASE_URL=core["dsn"].replace("postgresql://", "postgresql+psycopg://"),
                   PYMENTOR_ANALYTICS_TRANSFER_URL=fixture["collector_url"],
                   PYMENTOR_ANALYTICS_AUTHORITY_URL=authority,
                   PYMENTOR_ANALYTICS_PUBLISHER_EXPECTED_DATABASE=core["name"],
                   PYMENTOR_ANALYTICS_PUBLISHER_EXPECTED_USER=core["owner"])
        env.update({key: str(folder / filename) for key, filename in d.KEYS.items()})
        env["UNRELATED_SECRET"] = "must-not-propagate"
        yield folder, token, env, fixture, core, authority
    finally:
        r.cleanup_private(folder, token)


def test_only_owned_endpoints_and_keys_enter_container(setup_fixture):
    folder, _, env, fixture, core, authority = setup_fixture
    result, mounts = d.container_env(env, fixture, core, authority, folder)
    assert "UNRELATED_SECRET" not in result
    assert "@host.docker.internal:57382/" in result["DATABASE_URL"]
    assert result["PYMENTOR_ANALYTICS_TRANSFER_URL"] == "http://127.0.0.1:18152"
    assert len(mounts) == 8 and all("readonly" in mount for mount in mounts[1::2])


def test_docker_missing_network_is_distinct_from_daemon_error(monkeypatch):
    monkeypatch.setattr(d, "docker", lambda *a, **kw: SimpleNamespace(stdout="[]\n", stderr="Error response from daemon: network x not found\n", returncode=1))
    assert d.inspect("network", "x") is None
    monkeypatch.setattr(d, "docker", lambda *a, **kw: SimpleNamespace(stdout="", stderr="daemon unavailable", returncode=1))
    with pytest.raises(RuntimeError, match="outcome unknown"):
        d.inspect("network", "x")


@pytest.mark.parametrize("field", ["DATABASE_URL", "PYMENTOR_ANALYTICS_TRANSFER_URL", "PYMENTOR_ANALYTICS_AUTHORITY_URL"])
def test_unexpected_endpoint_rejected(setup_fixture, field):
    folder, _, env, fixture, core, authority = setup_fixture
    env[field] = "http://example.com:80"
    with pytest.raises(ValueError):
        d.container_env(env, fixture, core, authority, folder)


def test_lost_wait_never_removes_or_force_kills_container(setup_fixture, monkeypatch):
    folder, token, env, fixture, core, authority = setup_fixture
    image = "sha256:" + "f" * 64
    calls = []

    def fake_docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return SimpleNamespace(stdout=image, stderr="", returncode=0)
        if args[0] == "wait":
            raise TimeoutError("unknown wait outcome")
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(d, "docker", fake_docker)
    controller = d.DockerPublisher(image, folder, token, env, fixture, core, authority)
    monkeypatch.setattr(controller, "poll", lambda name: None)
    with pytest.raises(TimeoutError):
        controller.stop("synthetic")
    assert ("kill", "--signal", "SIGTERM", "synthetic") in calls
    assert not any(call[0] in {"rm", "stop"} for call in calls)
    assert json.loads((folder / "owner.json").read_text())["docker_container_state"] == "sigterm_intent"


def test_shutdown_requires_confirmed_exit_and_stopped_log(setup_fixture, monkeypatch):
    folder, token, env, fixture, core, authority = setup_fixture
    image = "sha256:" + "f" * 64
    calls = []
    def fake_docker(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("image", "inspect"):
            return SimpleNamespace(stdout=image, stderr="", returncode=0)
        if args[0] == "wait":
            return SimpleNamespace(stdout="0\n", stderr="", returncode=0)
        if args[0] == "logs":
            return SimpleNamespace(stdout="", stderr="publisher_stopped\n", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=0)
    monkeypatch.setattr(d, "docker", fake_docker)
    controller = d.DockerPublisher(image, folder, token, env, fixture, core, authority)
    monkeypatch.setattr(controller, "poll", lambda name: None)
    states = iter([{"State": {"Running": False, "ExitCode": 0}}, None])
    monkeypatch.setattr(controller, "_container", lambda name: next(states))
    controller.stop("synthetic")
    assert ("kill", "--signal", "SIGTERM", "synthetic") in calls
    assert ("rm", "synthetic") in calls
    assert json.loads((folder / "owner.json").read_text())["docker_container_state"] == "removed"
