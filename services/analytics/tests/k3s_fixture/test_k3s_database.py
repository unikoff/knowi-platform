"""Offline lifecycle tests: never contact a database or Docker daemon."""
import importlib
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
k3s = importlib.import_module("k3s_database")


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(k3s, "LOCAL", tmp_path)
    monkeypatch.setattr(k3s, "RECORD", tmp_path / "k3s-runtime.json")
    monkeypatch.setattr(k3s, "LOCK", tmp_path / "k3s-database.operation.lock")
    monkeypatch.setattr(k3s, "CONNECTION", tmp_path / "postgres-connection.json")
    history = ("a006", ("a006", "a005", "a001"), "f" * 64)
    monkeypatch.setattr(k3s.runtime, "schema_history", lambda revision=None: history)
    monkeypatch.setattr(k3s.db, "config", lambda path: {"test": True})
    monkeypatch.setattr(k3s.db, "connect", lambda config: nullcontext())
    monkeypatch.setattr(k3s, "_main_database", lambda: None)
    name = "analytics_test_" + uuid4().hex
    urls = {role: f"postgresql://{name}_{role}:private%40token@127.0.0.1:57392/{name}" for role in k3s.db.ROLES}
    state = {"revision": None, "creates": 0}
    def create(config):
        state["creates"] += 1
        return name, "a" * 64, urls
    monkeypatch.setattr(k3s.db, "create", create)
    monkeypatch.setattr(k3s.runtime, "verify_database", lambda config, record: [] if state["revision"] is None else [(state["revision"],)])
    monkeypatch.setattr(k3s, "_attested_ip", lambda: "172.22.0.2")
    return state


def test_create_once_and_pending_migration_unknown_outcome(fixture):
    state = fixture
    created = k3s.initialize()
    assert created["state"] == "created" and created["revision"] is None
    assert state["creates"] == 1 and k3s.initialize() == created
    assert k3s.get_record() == created and state["creates"] == 1
    with pytest.raises(ValueError): k3s.container_role_urls(created)
    pending = k3s.prepare_migration()
    assert pending["target_revision"] == "a006" and pending["history_sha256"] == "f" * 64
    url = k3s.migration_role_url(pending)
    assert urlsplit(url).hostname == "172.22.0.2" and urlsplit(url).port == 5432
    assert urlsplit(url).username.endswith("_migrate")
    assert json.loads(k3s.RECORD.read_text())["state"] == "migration_issued"
    with pytest.raises(ValueError): k3s.migration_role_url(pending)
    with pytest.raises(ValueError): k3s.initialize()
    with pytest.raises(ValueError): k3s.reconcile_migration()
    assert state["creates"] == 1
    state["revision"] = "a006"
    ready = k3s.reconcile_migration()
    assert ready["state"] == "ready" and ready["revision"] == "a006"
    assert k3s.get_record() == ready
    mapped = k3s.container_role_urls(ready)
    assert set(mapped) == set(k3s.db.ROLES)
    assert all(urlsplit(value).hostname == "172.22.0.2" and urlsplit(value).port == 5432 for value in mapped.values())
    assert all(urlsplit(value).hostname == "127.0.0.1" for value in ready["urls"].values())


def test_interrupted_provision_is_not_automatically_retried(fixture, monkeypatch):
    calls = []
    def interrupted(config):
        calls.append(1)
        raise TimeoutError("unknown outcome")
    monkeypatch.setattr(k3s.db, "create", interrupted)
    with pytest.raises(TimeoutError): k3s.initialize()
    saved = json.loads(k3s.RECORD.read_text())
    assert saved["state"] == "provisioning" and saved["do_not_drop"]
    with pytest.raises(ValueError): k3s.initialize()
    assert calls == [1] and json.loads(k3s.RECORD.read_text()) == saved


def test_untrusted_metadata_and_changed_revisions_fail_closed(fixture, monkeypatch):
    state = fixture
    record = k3s.initialize()
    for change in ({"project": "knowi-analytics-01a1df75"}, {"other": True},
                   {"history_sha256": "e" * 64}, {"nonce": "wrong"}):
        k3s.RECORD.write_text(json.dumps({**record, **change}))
        with pytest.raises(ValueError): k3s.get_record()
    k3s.RECORD.write_text(json.dumps(record))
    state["revision"] = "a005"
    with pytest.raises(ValueError): k3s.get_record()
    state["revision"] = "another-head"
    with pytest.raises(ValueError): k3s.get_record()
    monkeypatch.setattr(k3s.runtime, "schema_history", lambda revision=None: ("a007", ("a007", "a006"), "e" * 64))
    with pytest.raises(ValueError): k3s.initialize()
    assert fixture["creates"] == 1


def test_symlink_or_junction_metadata_refused_before_side_effect(fixture, monkeypatch):
    original = k3s._unsafe
    monkeypatch.setattr(k3s, "_unsafe", lambda path: path == k3s.RECORD or original(path))
    with pytest.raises(ValueError): k3s.initialize()
    assert fixture["creates"] == 0 and not k3s.RECORD.exists()


def test_retained_runtime_alias_is_never_accepted(fixture, monkeypatch):
    created = k3s.initialize()
    monkeypatch.setattr(k3s, "_main_database", lambda: created["database"])
    with pytest.raises(ValueError): k3s.get_record()
    with pytest.raises(ValueError): k3s.prepare_migration()
    assert fixture["creates"] == 1


def test_failed_owner_sentinel_check_and_busy_lock_preserve_record(fixture, monkeypatch):
    created = k3s.initialize()
    saved = k3s.RECORD.read_bytes()
    def refused(config, record):
        raise ValueError("owner or sentinel mismatch")
    monkeypatch.setattr(k3s.runtime, "verify_database", refused)
    with pytest.raises(ValueError): k3s.prepare_migration()
    assert k3s.RECORD.read_bytes() == saved
    k3s.LOCK.write_text("busy")
    with pytest.raises(FileExistsError): k3s.initialize()
    assert k3s.RECORD.read_bytes() == saved and fixture["creates"] == 1


def test_attested_ip_requires_exact_container_volume_and_network(monkeypatch):
    ident, network_id = "a" * 64, "b" * 64
    target, volume = k3s.docker.TARGET, k3s.docker.VOLUME
    bind = {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "57392"}]}
    container = [{"Id": ident, "Name": "/" + target, "State": {"Running": True},
                  "Config": {"Labels": {"com.docker.compose.project": k3s.docker.PROJECT,
                                        "com.docker.compose.service": k3s.docker.SERVICE,
                                        "knowi.analytics.synthetic": "true"}},
                  "HostConfig": {"PortBindings": bind, "Memory": 1, "NanoCpus": 1},
                  "NetworkSettings": {"Ports": bind, "Networks": {k3s.NETWORK: {"NetworkID": network_id, "IPAddress": "172.22.0.2"}}},
                  "Mounts": [{"Type": "volume", "Name": volume, "Destination": k3s.docker.DATA}]}]
    volumes = [{"Name": volume, "Driver": "local", "Labels": {"com.docker.compose.project": k3s.docker.PROJECT,
                "com.docker.compose.volume": "analytics_postgres_data"}}]
    network = [{"Name": k3s.NETWORK, "Id": network_id, "Driver": "bridge", "Scope": "local",
                "Labels": {"com.docker.compose.project": k3s.docker.PROJECT},
                "Containers": {ident: {"Name": target, "IPv4Address": "172.22.0.2/24"}}}]
    monkeypatch.setattr(k3s.docker, "local_context", lambda env: "local")
    def command(args, env):
        return json.dumps(network if "network" in args else volumes if "volume" in args else container)
    monkeypatch.setattr(k3s.docker, "command", command)
    assert k3s._attested_ip() == "172.22.0.2"
    network[0]["Containers"][ident]["IPv4Address"] = "172.22.0.3/24"
    with pytest.raises(ValueError): k3s._attested_ip()
    network[0]["Containers"][ident]["IPv4Address"] = "172.22.0.2/24"
    container[0]["Config"]["Labels"]["com.docker.compose.project"] = "core"
    with pytest.raises(k3s.docker.Refused): k3s._attested_ip()
