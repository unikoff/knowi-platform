"""Offline tests for one explicitly reconciled protective stop; no Docker."""
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import run_k3s as r


@pytest.fixture
def evidence(monkeypatch, tmp_path):
    local = tmp_path / ".local"
    local.mkdir()
    intent_path = local / ("node-protective-stop-" + r.PROTECTIVE_STOP_OPERATION + ".json")
    confirmed_path = local / ("node-protective-stop-" + r.PROTECTIVE_STOP_OPERATION + ".confirmed.json")
    monkeypatch.setattr(r, "ROOT", tmp_path)
    monkeypatch.setattr(r, "PROTECTIVE_STOP_INTENT", intent_path)
    monkeypatch.setattr(r, "PROTECTIVE_STOP_CONFIRMED", confirmed_path)
    server_id = r.PROTECTIVE_STOP_SERVER
    image = r.PROTECTIVE_STOP_IMAGE
    finished = r.PROTECTIVE_STOP_FINISHED_AT
    intent = {"operation": r.PROTECTIVE_STOP_OPERATION, "server_id": server_id,
              "cluster_uid": "75bf9b44-eb92-4f93-ac83-e82528d9acb1", "status": "pending",
              "reason": "low_host_memory_no_measurement"}
    confirmed = {"operation": r.PROTECTIVE_STOP_OPERATION, "status": "reconciled_stopped",
                 "data_retained": True, "graceful_shutdown": False,
                 "result": server_id + "|false|137|false|" + finished}
    intent_path.write_text(json.dumps(intent), encoding="utf-8")
    confirmed_path.write_text(json.dumps(confirmed), encoding="utf-8")
    mounts = [{"Destination": dest, "Type": "volume", "Name": name, "RW": True}
              for dest, name in r.PROTECTIVE_STOP_VOLUMES.items()]
    mounts.append({"Destination": "/proc/meminfo", "Type": "bind", "RW": False})
    server = [{"Id": server_id, "Name": "/" + r.SERVER, "Image": image,
               "State": {"Running": False, "Status": "exited", "ExitCode": 137, "OOMKilled": False, "FinishedAt": finished},
               "Config": {"Labels": {"k3d.cluster": r.CLUSTER, "k3d.role": "server", "knowi.analytics.synthetic": "true"}},
               "HostConfig": {"Memory": 2 * 1024**3, "CgroupnsMode": "private",
                              "PortBindings": {"6443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "57443"}]}},
               "NetworkSettings": {"Ports": {}, "Networks": {"k3d-" + r.CLUSTER: {}, r.PG_NETWORK: {}}}, "Mounts": mounts}]
    return SimpleNamespace(server=server, server_id=server_id, image=image, intent=intent, confirmed=confirmed,
                           intent_path=intent_path, confirmed_path=confirmed_path)


def test_receipt_opt_in_and_default_143_contract(evidence):
    x = evidence
    with pytest.raises(ValueError): r.validate_stopped_server(x.server, x.image, x.server_id)
    r.validate_stopped_server(x.server, x.image, x.server_id, protective_stop_receipt=x.confirmed_path)
    old = copy.deepcopy(x.server)
    old[0]["State"] = {"Running": False, "Status": "exited", "ExitCode": 143}
    del old[0]["Mounts"]
    r.validate_stopped_server(old, x.image, x.server_id)
    with pytest.raises(ValueError): r.validate_stopped_server(old, x.image, x.server_id, protective_stop_receipt=x.confirmed_path)


@pytest.mark.parametrize("change", ["exit", "oom", "running", "finished_at", "server_id", "image", "network",
                                    "cap", "volume_name", "extra_mount", "writable_bind", "wrong_receipt"])
def test_receipt_refuses_wrong_state_identity_or_storage(evidence, change):
    x = evidence
    changed = copy.deepcopy(x.server)
    obj = changed[0]
    receipt = x.confirmed_path
    if change == "exit": obj["State"]["ExitCode"] = 1
    if change == "oom": obj["State"]["OOMKilled"] = True
    if change == "running": obj["State"]["Running"] = True
    if change == "finished_at": obj["State"]["FinishedAt"] = "2026-11-17T14:07:00Z"
    if change == "server_id": obj["Id"] = "c" * 64
    if change == "image": obj["Image"] = "sha256:" + "c" * 64
    if change == "network": obj["NetworkSettings"]["Networks"]["foreign"] = {}
    if change == "cap": obj["HostConfig"]["Memory"] -= 1
    if change == "volume_name": obj["Mounts"][0]["Name"] = "foreign"
    if change == "extra_mount": obj["Mounts"].append({"Destination": "/foreign"})
    if change == "writable_bind": obj["Mounts"][-1]["RW"] = True
    if change == "wrong_receipt": receipt = x.intent_path
    with pytest.raises(ValueError): r.validate_stopped_server(changed, x.image, x.server_id, protective_stop_receipt=receipt)


@pytest.mark.parametrize("field", ["missing_intent", "wrong_intent", "wrong_confirmed", "missing_confirmed", "unretained", "wrong_exit_record"])
def test_receipt_requires_both_unmodified_consistent_evidence(evidence, field):
    x = evidence
    if field == "missing_intent": x.intent_path.unlink()
    if field == "missing_confirmed": x.confirmed_path.unlink()
    if field == "wrong_intent":
        x.intent["server_id"] = "c" * 64
        x.intent_path.write_text(json.dumps(x.intent), encoding="utf-8")
    if field == "wrong_confirmed":
        x.confirmed["status"] = "unknown"
        x.confirmed_path.write_text(json.dumps(x.confirmed), encoding="utf-8")
    if field == "unretained":
        x.confirmed["data_retained"] = False
        x.confirmed_path.write_text(json.dumps(x.confirmed), encoding="utf-8")
    if field == "wrong_exit_record":
        x.confirmed["result"] = x.confirmed["result"].replace("|137|", "|143|")
        x.confirmed_path.write_text(json.dumps(x.confirmed), encoding="utf-8")
    with pytest.raises(ValueError): r.validate_stopped_server(x.server, x.image, x.server_id, protective_stop_receipt=x.confirmed_path)


def test_restart_guard_forwards_explicit_receipt_without_relaxing_other_inputs(monkeypatch, evidence):
    x = evidence
    calls = []
    class Stop(Exception): pass
    def validate(server, image, server_id, **kw):
        calls.append((server, image, server_id, kw))
        raise Stop
    def run(args, **kwargs):
        if "config" in args: return SimpleNamespace(stdout="{}")
        return SimpleNamespace(stdout=json.dumps(x.server))
    monkeypatch.setattr(r, "KUBECONFIG", Path(__file__).resolve())
    monkeypatch.setattr(r, "file_sha256", lambda path: "d" * 64)
    monkeypatch.setattr(r, "validate_kubectl_config", lambda value: None)
    monkeypatch.setattr(r.resource_snapshot, "local_context", lambda env: "local")
    monkeypatch.setattr(r.check_k3s, "available_memory", lambda: 4 * 1024**3)
    monkeypatch.setattr(r, "run", run)
    monkeypatch.setattr(r, "validate_stopped_server", validate)
    with pytest.raises(Stop):
        r.require_stopped_node_restart_budget(sys.executable, "d" * 64, x.image, x.server_id,
                                              protective_stop_receipt=x.confirmed_path)
    assert calls[0][1:] == (x.image, x.server_id, {"protective_stop_receipt": x.confirmed_path})
