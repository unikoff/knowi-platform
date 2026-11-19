"""Offline tests for the host-only preparation boundary; no Docker or node."""
import json
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest
import benchmark_activity as activity
import prepare_k3s_image as prepare

HOST_ID = "sha256:" + "c" * 64


@pytest.fixture
def windows_boundary(monkeypatch):
    class WindowsOS:
        name = "nt"
        def __getattr__(self, key): return getattr(os, key)
    monkeypatch.setattr(prepare, "os", WindowsOS())


def test_default_plan_and_incomplete_cli_never_open_external_resources(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("unexpected process"))
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("unexpected socket"))
    assert prepare.main([]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "UNRUN"
    assert prepare.main(["--prepare-image"]) == 2


def test_preparation_requires_go_and_rejects_invalid_cap_before_external_work(monkeypatch, windows_boundary):
    monkeypatch.delenv("ANALYTICS_BENCHMARK_IMAGE_PREPARE_GO", raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("unexpected process"))
    args = SimpleNamespace(operation_id=str(uuid4()), host_image_id=HOST_ID, archive_max_mib=1025, target_revision="a011")
    with pytest.raises(prepare.bench.LiveFailure, match="GO"): prepare.prepare(args)
    monkeypatch.setenv("ANALYTICS_BENCHMARK_IMAGE_PREPARE_GO", "1")
    monkeypatch.setenv("ANALYTICS_BENCHMARK_IMAGE_PROBE_GO", "1")
    with pytest.raises(prepare.bench.LiveFailure, match="archive cap"): prepare.prepare(args)
    args.archive_max_mib = 1024
    args.host_image_id = "latest"
    with pytest.raises(prepare.bench.LiveFailure, match="identity"): prepare.prepare(args)


@pytest.mark.parametrize("failed", [False, True])
def test_one_shot_preparation_records_intent_before_save_and_retains_unknown(monkeypatch, tmp_path, failed, windows_boundary):
    service = tmp_path / "analytics"
    local = service / ".local"
    (service / "tools").mkdir(parents=True)
    local.mkdir()
    monkeypatch.setattr(prepare, "__file__", str(service / "tools/prepare_k3s_image.py"))
    monkeypatch.setenv("ANALYTICS_BENCHMARK_IMAGE_PREPARE_GO", "1")
    monkeypatch.setenv("ANALYTICS_BENCHMARK_IMAGE_PROBE_GO", "1")
    args = SimpleNamespace(operation_id=str(uuid4()), host_image_id=HOST_ID, archive_max_mib=128, target_revision="a011")
    events = []
    class Monitor:
        disk_floor_bytes = 1
        samples = [{"free_disk_bytes": 1024 * 1024 * 1024}]
        def check(self): events.append("floor")
    monkeypatch.setattr(activity, "resource_floors", lambda env: Monitor())
    def private_folder():
        folder = local / "cross-service" / uuid4().hex
        folder.mkdir(parents=True)
        return folder, "token"
    monkeypatch.setattr(activity.owned, "private_folder", private_folder)
    monkeypatch.setattr(activity.owned, "record", lambda folder, token, **kw: events.append(kw["state"]))
    monkeypatch.setattr(prepare.bench, "verified_histories", lambda *a: None)
    monkeypatch.setattr(prepare.bench, "_source_fingerprint", lambda *a: "sha256:" + "f" * 64)
    original_fsync = os.fsync
    def fsync(fd):
        events.append("fsync")
        original_fsync(fd)
    monkeypatch.setattr(os, "fsync", fsync)
    def export(folder, token, run, role, host, node, runtime, owned, monitor, cap):
        events.append("save")
        assert node is None and host == HOST_ID and cap == 128
        assert (local / "benchmark-image-prepare.active.json").is_file()
        assert (local / ("benchmark-image-prepare-" + run.hex + ".intent.json")).is_file()
        assert events.count("fsync") >= 2
        if failed: raise prepare.bench.LiveFailure("save outcome unknown")
        return {"archive_name": "host-image-application.tar", "config_digest": "sha256:" + "a" * 64}
    def probe(*values):
        events.append("probe")
        return {"packaged_history_head": "a011"}
    monkeypatch.setattr(prepare.bench, "_host_image_proof", export)
    monkeypatch.setattr(prepare.bench, "_source_probe", probe)
    lock = local / "benchmark-image-prepare.active.json"
    if failed:
        with pytest.raises(prepare.bench.LiveFailure): prepare.prepare(args)
        assert lock.is_file() and "probe" not in events
        with pytest.raises(FileExistsError): prepare.prepare(args)
        assert events.count("save") == 1
    else:
        result = prepare.prepare(args)
        assert not lock.exists() and result["node_import"] == "UNRUN"
        assert events.index("save") < events.index("probe")
        assert (service / result["owner_record"]).parent.joinpath("image-preparation-result.json").is_file()
