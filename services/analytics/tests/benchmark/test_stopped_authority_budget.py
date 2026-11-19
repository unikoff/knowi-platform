"""Offline stopped-authority identity and budget boundary checks."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import run_k3s as r

CID, IMAGE = "a" * 64, "sha256:" + "b" * 64


@pytest.fixture
def fixture(monkeypatch):
    prepared = SimpleNamespace(nonce="c" * 32, authority_id="authority-id", volume_name="owned-journal")
    labels = {"knowi.analytics.fixture": "k3s-privacy-authority", "knowi.analytics.nonce": prepared.nonce,
              "knowi.analytics.synthetic": "true", "knowi.analytics.project": "synthetic"}
    authority = SimpleNamespace(PROJECT="synthetic", NETWORK="net", DATA_DIR="/data", CONFIG_DIR="/config", JOURNAL="/data/journal",
        ROOT=Path("source"), _verify_prepared=lambda: prepared, _labels=lambda p: labels,
        _name=lambda p: "authority", _safe=lambda path, **kw: path,
        _bind_source_matches=lambda source, expected, **kw: source == "exact-bind")
    def verify_volume(volume, owner):
        if volume != {"Name": owner.volume_name, "Labels": labels, "Driver": "local"}: raise ValueError
    authority._verify_volume = verify_volume
    cmd = ["-B", "-m", "pymentor_analytics.adapters.privacy_authority.server", "serve", "--private-fixture-network",
           "--journal", authority.JOURNAL, "--authority-id", prepared.authority_id, "--config", "/config/server.json"]
    container = {"Id": CID, "Name": "/authority", "Image": IMAGE,
      "State": {"Running": False, "Status": "exited", "Restarting": False, "ExitCode": 0},
      "Config": {"Labels": {**labels, "knowi.analytics.role": "authority"}, "User": "10001:10001", "Entrypoint": ["python"], "Cmd": cmd},
      "HostConfig": {"Memory": 128*r.MIB, "NanoCpus": 250000000, "CgroupnsMode": "private", "ReadonlyRootfs": True,
                     "Privileged": False, "PidsLimit": 64, "CapDrop": ["ALL"], "CapAdd": None,
                     "SecurityOpt": ["no-new-privileges:true"], "PortBindings": {}, "RestartPolicy": {"Name": "no"}},
      "Mounts": [{"Destination": "/data", "Type": "volume", "Name": "owned-journal", "RW": True},
                 {"Destination": "/config", "Type": "bind", "Source": "exact-bind", "RW": False}],
      "NetworkSettings": {"Networks": {"net": {"NetworkID": "network-id"}}, "Ports": {}}}
    env = {"ids": [CID], "container": container, "volume": {"Name": "owned-journal", "Labels": labels, "Driver": "local"},
           "network": {"Name": "net", "Id": "network-id"}, "name_ids": [CID], "running_image": [], "calls": []}
    def run(args, **kw):
        env["calls"].append(args)
        if "ps" in args:
            if any(item.startswith("ancestor=") for item in args): return SimpleNamespace(stdout="\n".join(env["running_image"]))
            if any(item.startswith("name=") for item in args): return SimpleNamespace(stdout="\n".join(env["name_ids"]))
            return SimpleNamespace(stdout="\n".join(env["ids"]))
        if "container" in args: return SimpleNamespace(stdout=json.dumps([env["container"]]))
        if "volume" in args: return SimpleNamespace(stdout=json.dumps([env["volume"]]))
        if "network" in args: return SimpleNamespace(stdout=json.dumps([env["network"]]))
        raise AssertionError(args)
    monkeypatch.setattr(r, "run", run)
    monkeypatch.setitem(sys.modules, "k3s_authority", authority)
    return env


def test_exact_stopped_owned_authority_and_conservative_reserve(fixture):
    result = r.attest_stopped_authority(["docker"], CID, IMAGE)
    assert result["journal_volume"] == "owned-journal" and result["memory_reserve_bytes"] == 128*r.MIB
    assert all(not {"start", "stop", "rm", "create"}.intersection(call) for call in fixture["calls"])


@pytest.mark.parametrize("change", ["wrong_id", "extra", "unlabelled_extra", "same_image_running", "running", "restarting", "restart_policy", "image", "volume",
                                    "volume_label", "labels", "memory", "network", "config_bind", "invalid_id"])
def test_stopped_authority_fails_closed_on_foreign_or_unsafe_identity(fixture, change):
    e = fixture
    if change == "wrong_id": e["container"]["Id"] = "d" * 64
    if change == "extra": e["ids"].append("d" * 64)
    if change == "unlabelled_extra": e["name_ids"].append("d" * 64)
    if change == "same_image_running": e["running_image"].append("d" * 64)
    if change == "running": e["container"]["State"]["Running"] = True
    if change == "restarting": e["container"]["State"]["Restarting"] = True
    if change == "restart_policy": e["container"]["HostConfig"]["RestartPolicy"]["Name"] = "always"
    if change == "image": e["container"]["Image"] = "sha256:" + "d" * 64
    if change == "volume": e["container"]["Mounts"][0]["Name"] = "foreign"
    if change == "volume_label": e["volume"]["Labels"] = {}
    if change == "labels": e["container"]["Config"]["Labels"]["knowi.analytics.nonce"] = "d" * 32
    if change == "memory": e["container"]["HostConfig"]["Memory"] += 1
    if change == "network": e["container"]["NetworkSettings"]["Networks"]["net"]["NetworkID"] = "other"
    if change == "config_bind": e["container"]["Mounts"][1]["Source"] = "foreign"
    with pytest.raises(ValueError): r.attest_stopped_authority(["docker"], "short" if change == "invalid_id" else CID, IMAGE)


def test_shared_guard_forwards_stopped_authority_only_when_explicit(monkeypatch):
    class Stop(Exception): pass
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs)
        raise Stop
    monkeypatch.setattr(r, "require_post_create_budget", post)
    monkeypatch.setattr(r.check_k3s, "available_memory", lambda: 4*1024**3)
    with pytest.raises(Stop): r.require_shared_local_budget("kubectl", "hash", "image", authority_image_id=IMAGE)
    assert "expected_stopped_authority_id" not in calls[-1]
    with pytest.raises(Stop): r.require_shared_local_budget("kubectl", "hash", "image", authority_image_id=IMAGE, expected_stopped_authority_id=CID)
    assert calls[-1]["expected_stopped_authority_id"] == CID and calls[-1]["authority_image_id"] == IMAGE


def test_post_start_guard_attests_stopped_and_reserves_full_cap(monkeypatch):
    node_id, pg_id = "d" * 64, "e" * 64
    server = {"Id": node_id, "HostConfig": {"CgroupnsMode": "private"},
              "NetworkSettings": {"Networks": {"k3d-" + r.CLUSTER: {}, r.PG_NETWORK: {}}}}
    pg = {"HostConfig": {"CgroupnsMode": "private"}}
    def run(args, **kw):
        if "ps" in args and "label=k3d.cluster=" + r.CLUSTER in args: return SimpleNamespace(stdout=node_id)
        if "ps" in args: return SimpleNamespace(stdout=CID)
        if "network" in args or "volume" in args: return SimpleNamespace(stdout="[]")
        if r.SERVER in args: return SimpleNamespace(stdout=json.dumps([server]))
        if r.resource_snapshot.TARGET in args: return SimpleNamespace(stdout=json.dumps([pg]))
        raise AssertionError(args)
    monkeypatch.setattr(r, "run", run)
    for name in ("verify_api_after_go", "validate_server", "validate_pg_network", "_verify_cgroup_tree"):
        monkeypatch.setattr(r, name, lambda *a, **kw: None)
    monkeypatch.setattr(r.resource_snapshot, "local_context", lambda env: "local")
    monkeypatch.setattr(r.resource_snapshot, "verify_container", lambda pg: (pg_id, 384*r.MIB, 1))
    monkeypatch.setattr(r.resource_snapshot, "verify_volume", lambda volume: None)
    monkeypatch.setattr(r.check_k3s, "available_memory", lambda: 4*1024**3)
    calls = []
    monkeypatch.setattr(r, "attest_stopped_authority", lambda docker, cid, image: calls.append((cid, image)))
    def numbers(docker, cid, *args):
        assert cid in (node_id, pg_id), "stopped authority must not be read as running cgroup"
        return {"cap": 2*1024**3 if cid == node_id else 384*r.MIB, "current": 80*r.MIB,
                "swap": 0, "pressure": False, "oom": False}
    monkeypatch.setattr(r, "_cgroup_numbers", numbers)
    result = r.require_post_create_budget("kubectl", "hash", "image", authority_image_id=IMAGE, expected_stopped_authority_id=CID)
    assert calls == [(CID, IMAGE)] and result["authority_reserve_bytes"] == 128*r.MIB


def test_stopped_restart_guard_keeps_default_rejection_and_explicit_attestation(monkeypatch):
    node_id, pg_id = "d" * 64, "e" * 64
    pg = {"HostConfig": {"CgroupnsMode": "private"}}
    def run(args, **kw):
        if "config" in args: return SimpleNamespace(stdout="{}")
        if "ps" in args and "label=k3d.cluster=" + r.CLUSTER in args: return SimpleNamespace(stdout="")
        if "ps" in args: return SimpleNamespace(stdout=CID)
        if r.SERVER in args: return SimpleNamespace(stdout="[{}]")
        if r.resource_snapshot.TARGET in args: return SimpleNamespace(stdout=json.dumps([pg]))
        if "network" in args or "volume" in args: return SimpleNamespace(stdout="[]")
        raise AssertionError(args)
    monkeypatch.setattr(r, "run", run)
    monkeypatch.setattr(r, "KUBECONFIG", Path(__file__).resolve())
    monkeypatch.setattr(r, "file_sha256", lambda p: "f" * 64)
    monkeypatch.setattr(r, "validate_kubectl_config", lambda *a: None)
    monkeypatch.setattr(r, "validate_stopped_server", lambda *a: None)
    monkeypatch.setattr(r, "validate_pg_network", lambda *a: None)
    monkeypatch.setattr(r.resource_snapshot, "local_context", lambda env: "local")
    monkeypatch.setattr(r.resource_snapshot, "verify_container", lambda pg: (pg_id, 384*r.MIB, 1))
    monkeypatch.setattr(r.resource_snapshot, "verify_volume", lambda volume: None)
    monkeypatch.setattr(r.check_k3s, "available_memory", lambda: 4*1024**3)
    monkeypatch.setattr(r, "_cgroup_numbers", lambda *a: {"cap":384*r.MIB,"current":80*r.MIB,"swap":0,"pressure":False,"oom":False})
    calls = []
    monkeypatch.setattr(r, "attest_stopped_authority", lambda docker, cid, image: calls.append((cid, image)))
    with pytest.raises(ValueError, match="authority scope unknown"):
        r.require_stopped_node_restart_budget(sys.executable, "f"*64, IMAGE, node_id)
    result = r.require_stopped_node_restart_budget(sys.executable, "f"*64, IMAGE, node_id,
                  expected_stopped_authority_id=CID, expected_stopped_authority_image_id=IMAGE)
    assert calls == [(CID, IMAGE)] and result["status"] == "stopped_node_restart_budget_verified"
    assert result["required_bytes"] == 2*1024**3 + (384-80+128+256+512+128)*r.MIB
