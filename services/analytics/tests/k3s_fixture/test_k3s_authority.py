import importlib.util
import json
import os
from pathlib import Path
import subprocess
import threading

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SERVICE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("k3s_authority", SERVICE / "tools/k3s_authority.py")
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)

IMAGE = "sha256:" + "a" * 64
CONTAINER = "b" * 64
NETWORK_ID = "c" * 64
PG_ID = "d" * 64


def public(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


@pytest.fixture
def local(tmp_path, monkeypatch):
    service = tmp_path / "analytics"
    local_dir = service / ".local"
    local_dir.mkdir(parents=True)
    monkeypatch.setattr(module, "SERVICE", service)
    monkeypatch.setattr(module, "LOCAL", local_dir)
    monkeypatch.setattr(module, "ROOT", local_dir / "k3s-authority")
    monkeypatch.setattr(module, "TRUST", local_dir / "trust-manifest.json")
    grant, producer = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    trust = {"grant": {"issuer": "synthetic-grants", "keys": {"g1": public(grant)}},
             "producer": {"issuer": "synthetic-control", "keys": {"p1": public(producer)}, "allowlist": ["synthetic-producer"]}}
    module.TRUST.write_text(json.dumps(trust), encoding="utf-8")
    return service, trust


def test_prepare_only_public_manifest_and_owned_new_files(local, monkeypatch, capsys):
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: pytest.fail("prepare must not call Docker"))
    original = module.TRUST.read_bytes()
    prepared = module.prepare()
    assert prepared.trust_manifest_path == module.TRUST and module.TRUST.read_bytes() == original
    assert prepared.client_config_path.is_file() and prepared.command_private_path.is_file() and prepared.command_public_path.is_file()
    assert len(prepared.nonce) == 32 and prepared.nonce in prepared.volume_name and prepared.authority_id.endswith(prepared.nonce)
    client = json.loads(prepared.client_config_path.read_text(encoding="utf-8"))
    server = json.loads((module.ROOT / "config/server.json").read_text(encoding="utf-8"))
    assert client["url"] == "http://privacy-authority.knowi-analytics.svc:57420"
    assert server["producer_public_keys"] == local[1]["producer"]["keys"]
    assert server["producer_command_public_keys"] == {"synthetic-producer": prepared.command_public_path.read_text()}
    assert "PRIVATE KEY" not in repr(prepared) and client["reader_secret"] not in repr(prepared)
    before = {str(path.relative_to(module.ROOT)): path.read_bytes() for path in module.ROOT.rglob("*") if path.is_file()}
    assert module.prepare() == prepared
    assert before == {str(path.relative_to(module.ROOT)): path.read_bytes() for path in module.ROOT.rglob("*") if path.is_file()}
    assert not capsys.readouterr().out


def test_unknown_partial_or_changed_state_is_preserved(local):
    module.ROOT.mkdir()
    marker = module.ROOT / "foreign.txt"; marker.write_text("keep")
    with pytest.raises(module.Refused): module.prepare()
    assert marker.read_text() == "keep"


def test_existing_tampered_file_and_changed_trust_are_never_replaced(local):
    prepared = module.prepare()
    key_before = prepared.command_private_path.read_bytes()
    prepared.client_config_path.write_text("foreign-secret")
    with pytest.raises(module.Refused): module.prepare()
    assert prepared.client_config_path.read_text() == "foreign-secret"
    assert prepared.command_private_path.read_bytes() == key_before


def test_trust_drift_rejected_without_reading_private_trust_keys(local):
    prepared = module.prepare()
    changed = dict(local[1]); changed["grant"] = {"issuer": "other", "keys": local[1]["grant"]["keys"]}
    module.TRUST.write_text(json.dumps(changed))
    with pytest.raises(module.Refused): module.prepare()
    assert prepared.command_private_path.exists()


def test_rejects_hardlinked_or_outside_path(local, tmp_path):
    target = module.LOCAL / "shared.txt"; target.write_text("keep")
    link = module.LOCAL / "linked.txt"
    try: os.link(target, link)
    except OSError: pytest.skip("hardlinks unavailable")
    with pytest.raises(module.Refused): module._read(link)
    with pytest.raises(module.Refused): module._safe(tmp_path / "outside", must_exist=False)
    assert target.read_text() == "keep"


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_rejects_linked_local_ancestor_before_preparing(local, monkeypatch, kind):
    method = "is_symlink" if kind == "symlink" else "is_junction"
    original = getattr(Path, method)
    monkeypatch.setattr(Path, method, lambda path: path == module.LOCAL or original(path))
    with pytest.raises(module.Refused, match="unsafe_path"):
        module.prepare()
    assert not module.ROOT.exists()


def test_no_start_without_explicit_go_or_if_capacity_unavailable(local, monkeypatch):
    module.prepare()
    calls = []
    monkeypatch.setattr(module, "_run", lambda *a, **kw: calls.append(a))
    with pytest.raises(module.Refused, match="explicit_go_required"): module.start(IMAGE)
    monkeypatch.setattr(module, "_capacity", lambda: (_ for _ in ()).throw(module.Refused("insufficient_host_memory")))
    with pytest.raises(module.Refused, match="insufficient_host_memory"): module.start(IMAGE, authorized=True)
    assert not calls


def test_phase_one_cli_cannot_start_or_inspect_containers(local, monkeypatch, capsys):
    monkeypatch.setattr(module.sys, "argv", ["k3s_authority.py", "start"])
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: pytest.fail("CLI must not invoke Docker"))
    assert module.main() == 2
    output = capsys.readouterr()
    assert "requires root GO" in output.err and not output.out


def volume(prepared, *, nonce=None):
    labels = dict(module._labels(prepared))
    if nonce is not None: labels["knowi.analytics.nonce"] = nonce
    return {"Name": prepared.volume_name, "Driver": "local", "Labels": labels}


class DockerFixture:
    def __init__(self, prepared, *, existing=False, labels_ok=True, names=()):
        self.prepared = prepared; self.existing = existing; self.labels_ok = labels_ok
        self.names = list(names); self.calls = []
    def run(self, args, *, timeout=20):
        self.calls.append(args)
        cmd = args[3:]
        if cmd[:3] == ["ps", "-a", "--format"]: return "\n".join(self.names)
        if cmd[:3] == ["volume", "ls", "--format"]: return self.prepared.volume_name if self.existing else ""
        if cmd[:2] == ["volume", "inspect"]:
            return json.dumps([volume(self.prepared, nonce=None if self.labels_ok else "foreign")])
        if cmd[:2] == ["volume", "create"]:
            self.existing = True; return self.prepared.volume_name
        if cmd[:2] == ["run", "-d"]: return CONTAINER
        if cmd[:2] == ["run", "--rm"]: return ""
        raise AssertionError("unexpected mocked Docker command")


def test_start_new_volume_only_after_guard_and_bounded_commands(local, monkeypatch):
    prepared = module.prepare(); docker = DockerFixture(prepared)
    monkeypatch.setattr(module, "_environment", lambda image: (["docker", "--context", "fixture"], {}))
    monkeypatch.setattr(module, "_capacity", lambda: None)
    monkeypatch.setattr(module, "_run", docker.run)
    monkeypatch.setattr(module, "status", lambda image: module.Status("172.20.0.3", CONTAINER, prepared.authority_id))
    result = module.start(IMAGE, authorized=True)
    assert result.ip == "172.20.0.3"
    assert not (module.LOCAL / ("k3s-authority-start-" + prepared.nonce + ".pending")).exists()
    text = [" ".join(args) for args in docker.calls]
    assert next(i for i, line in enumerate(text) if "config-probe" in line) < next(i for i, line in enumerate(text) if "volume create" in line)
    assert sum("volume create" in line for line in text) == 1
    assert sum("volume-owner" in line for line in text) == 1 and sum("journal-init" in line for line in text) == 1
    assert sum("journal-verify" in line for line in text) == 1
    runtime = next(args for args in docker.calls if args[3:5] == ["run", "-d"])
    assert "--publish" not in runtime and "-p" not in runtime and "--read-only" in runtime
    assert runtime[runtime.index("--network") + 1] == module.NETWORK
    assert runtime[runtime.index("--user") + 1] == "10001:10001"
    assert runtime[runtime.index("--memory") + 1] == "128m" and runtime[runtime.index("--cpus") + 1] == "0.25"
    assert any("volume-nocopy" in x and module.DATA_DIR in x for x in runtime)
    assert any("readonly" in x and module.CONFIG_DIR in x for x in runtime)
    assert not any("command-private.pem" in x for x in runtime)


def test_existing_volume_validates_without_initialization(local, monkeypatch):
    prepared = module.prepare(); docker = DockerFixture(prepared, existing=True)
    monkeypatch.setattr(module, "_environment", lambda image: (["docker", "--context", "fixture"], {}))
    monkeypatch.setattr(module, "_capacity", lambda: None)
    monkeypatch.setattr(module, "_run", docker.run)
    monkeypatch.setattr(module, "status", lambda image: module.Status("172.20.0.3", CONTAINER, prepared.authority_id))
    module.start(IMAGE, authorized=True)
    calls = [" ".join(args) for args in docker.calls]
    assert not any("volume create" in line or "volume-owner" in line or "journal-init" in line for line in calls)
    assert any("journal-verify" in line for line in calls)


@pytest.mark.parametrize("scenario", ["unowned-volume", "partial-helper"])
def test_unowned_volume_and_partial_helper_refused_before_mutation(local, monkeypatch, scenario):
    prepared = module.prepare()
    monkeypatch.setattr(module, "_environment", lambda image: (["docker", "--context", "fixture"], {}))
    monkeypatch.setattr(module, "_capacity", lambda: None)
    docker = (DockerFixture(prepared, existing=True, labels_ok=False) if scenario == "unowned-volume"
              else DockerFixture(prepared, names=[module._name(prepared) + "-journal-init"]))
    monkeypatch.setattr(module, "_run", docker.run)
    with pytest.raises(module.Refused): module.start(IMAGE, authorized=True)
    assert not any(args[3] == "run" or args[3:5] == ["volume", "create"] for args in docker.calls)


def test_failed_config_probe_cannot_create_volume(local, monkeypatch):
    prepared = module.prepare(); docker = DockerFixture(prepared)
    monkeypatch.setattr(module, "_environment", lambda image: (["docker", "--context", "fixture"], {}))
    monkeypatch.setattr(module, "_capacity", lambda: None)
    def run(args, *, timeout=20):
        if "config-probe" in " ".join(args):
            docker.calls.append(args)
            raise module.Refused("docker_command_failed")
        return docker.run(args, timeout=timeout)
    monkeypatch.setattr(module, "_run", run)
    with pytest.raises(module.Refused): module.start(IMAGE, authorized=True)
    assert not any(args[3:5] == ["volume", "create"] for args in docker.calls)


def test_unknown_volume_create_outcome_never_retries_or_cleans_up(local, monkeypatch):
    prepared = module.prepare(); docker = DockerFixture(prepared)
    monkeypatch.setattr(module, "_environment", lambda image: (["docker", "--context", "fixture"], {}))
    monkeypatch.setattr(module, "_capacity", lambda: None)
    def run(args, *, timeout=20):
        if args[3:5] == ["volume", "create"]:
            docker.calls.append(args)
            raise module.Refused("docker_outcome_unknown")
        return docker.run(args, timeout=timeout)
    monkeypatch.setattr(module, "_run", run)
    with pytest.raises(module.Refused, match="docker_outcome_unknown"): module.start(IMAGE, authorized=True)
    assert sum(args[3:5] == ["volume", "create"] for args in docker.calls) == 1
    assert not any("journal-init" in " ".join(args) or "volume-owner" in " ".join(args) or " rm " in " ".join(args) for args in docker.calls)
    assert (module.LOCAL / ("k3s-authority-start-" + prepared.nonce + ".pending")).is_file()
    with pytest.raises(module.Refused, match="exclusive_write_failed"):
        module.start(IMAGE, authorized=True)
    assert sum(args[3:5] == ["volume", "create"] for args in docker.calls) == 1


def test_parallel_starts_cannot_both_create_owned_volume(local, monkeypatch):
    prepared = module.prepare(); docker = DockerFixture(prepared)
    monkeypatch.setattr(module, "_environment", lambda image: (["docker", "--context", "fixture"], {}))
    monkeypatch.setattr(module, "_capacity", lambda: None)
    monkeypatch.setattr(module, "status", lambda image: module.Status("172.20.0.3", CONTAINER, prepared.authority_id))
    entered, release = threading.Event(), threading.Event()
    def run(args, *, timeout=20):
        if "config-probe" in " ".join(args):
            entered.set()
            if not release.wait(3): raise AssertionError("test timed out")
        return docker.run(args, timeout=timeout)
    monkeypatch.setattr(module, "_run", run)
    results = []
    def first():
        try: results.append(module.start(IMAGE, authorized=True))
        except Exception as exc: results.append(exc)
    worker = threading.Thread(target=first)
    worker.start()
    try:
        assert entered.wait(3)
        with pytest.raises(module.Refused, match="exclusive_write_failed"): module.start(IMAGE, authorized=True)
    finally:
        release.set(); worker.join(timeout=5)
    assert not worker.is_alive() and results == [module.Status("172.20.0.3", CONTAINER, prepared.authority_id)]
    assert sum(args[3:5] == ["volume", "create"] for args in docker.calls) == 1


def test_existing_uninitialized_journal_never_reinitialized(local, monkeypatch):
    prepared = module.prepare(); docker = DockerFixture(prepared, existing=True)
    monkeypatch.setattr(module, "_environment", lambda image: (["docker", "--context", "fixture"], {}))
    monkeypatch.setattr(module, "_capacity", lambda: None)
    def run(args, *, timeout=20):
        if "journal-verify" in " ".join(args):
            docker.calls.append(args)
            raise module.Refused("docker_command_failed")
        return docker.run(args, timeout=timeout)
    monkeypatch.setattr(module, "_run", run)
    with pytest.raises(module.Refused): module.start(IMAGE, authorized=True)
    assert not any("journal-init" in " ".join(args) or "volume-owner" in " ".join(args) or args[3:5] == ["run", "-d"] for args in docker.calls)


def test_unknown_docker_outcome_never_echoes_secrets(monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("PRIVATE-SECRET", 1, output="PRIVATE-SECRET")
    monkeypatch.setattr(module.subprocess, "run", fail)
    with pytest.raises(module.Refused) as exc: module._run(["docker", "run"])
    assert str(exc.value) == "docker_outcome_unknown" and "PRIVATE-SECRET" not in str(exc.value)


def container_metadata(prepared):
    name = module._name(prepared)
    network = {"Id": NETWORK_ID, "IPAM": {"Config": [{"Subnet": "172.20.0.0/16"}]},
               "Containers": {CONTAINER: {"Name": name, "IPv4Address": "172.20.0.3/16"}}}
    obj = {"Name": "/" + name, "Id": CONTAINER, "Image": IMAGE, "State": {"Running": True},
           "Config": {"Labels": {**module._labels(prepared), "knowi.analytics.role": "authority"}, "User": "10001:10001", "Entrypoint": ["python"],
                      "Cmd": ["-B", "-m", "pymentor_analytics.adapters.privacy_authority.server", "serve",
                              "--private-fixture-network", "--journal", module.JOURNAL, "--authority-id", prepared.authority_id,
                              "--config", module.CONFIG_DIR + "/server.json"]},
           "HostConfig": {"ReadonlyRootfs": True, "Memory": 128 * 1024 * 1024, "NanoCpus": 250_000_000,
                          "Privileged": False, "PidsLimit": 64, "CapDrop": ["ALL"], "CapAdd": None,
                          "SecurityOpt": ["no-new-privileges:true"], "PortBindings": {}},
           "Mounts": [{"Destination": module.DATA_DIR, "Type": "volume", "Name": prepared.volume_name, "RW": True},
                      {"Destination": module.CONFIG_DIR, "Type": "bind", "Source": str(module.ROOT / "config"), "RW": False}],
           "NetworkSettings": {"Ports": {}, "Networks": {module.NETWORK: {"NetworkID": NETWORK_ID, "IPAddress": "172.20.0.3"}}}}
    return obj, network


def test_reconcile_started_clears_only_matching_marker_after_attestation(local, monkeypatch):
    prepared = module.prepare()
    marker = module.LOCAL / ("k3s-authority-start-" + prepared.nonce + ".pending")
    marker.write_bytes((prepared.authority_id + "\n").encode("ascii"))
    with pytest.raises(module.Refused, match="explicit_go_required"):
        module.reconcile_started(IMAGE)
    monkeypatch.setattr(module, "status", lambda image: (_ for _ in ()).throw(module.Refused("unverified")))
    with pytest.raises(module.Refused, match="unverified"):
        module.reconcile_started(IMAGE, authorized=True)
    assert marker.exists()
    monkeypatch.setattr(module, "status", lambda image: module.Status("172.20.0.3", CONTAINER, prepared.authority_id))
    result = module.reconcile_started(IMAGE, authorized=True)
    assert result.container_id == CONTAINER and not marker.exists()


def test_windows_desktop_bind_requires_exact_mapped_path():
    expected = r"C:\Project Python\Pymentor_platform\services\analytics\.local\k3s-authority\config"
    mapped = "/run/desktop/mnt/host/c/Project Python/Pymentor_platform/services/analytics/.local/k3s-authority/config"
    assert module._bind_source_matches(mapped, expected, windows=True)
    assert module._bind_source_matches(expected, expected, windows=True)
    assert not module._bind_source_matches(mapped + "-other", expected, windows=True)
    assert not module._bind_source_matches(mapped.replace("/host/c/", "/host/d/"), expected, windows=True)
    assert not module._bind_source_matches(mapped.replace("/config", "/../config"), expected, windows=True)
    assert not module._bind_source_matches(mapped, r"\\server\share\config", windows=True)


def test_status_attests_exact_container_network_and_volume(local, monkeypatch):
    prepared = module.prepare()
    obj, network = container_metadata(prepared)
    monkeypatch.setattr(module, "_environment", lambda image: (["docker", "--context", "fixture"], network))
    def run(args, *, timeout=20):
        if args[3:5] == ["volume", "inspect"]: return json.dumps([volume(prepared)])
        if args[3:6] == ["inspect", "--type", "container"]: return json.dumps([obj])
        raise AssertionError("status may inspect only")
    monkeypatch.setattr(module, "_run", run)
    result = module.status(IMAGE)
    assert result == module.Status("172.20.0.3", CONTAINER, prepared.authority_id)


@pytest.mark.parametrize("change", ["published", "memory", "cpu", "privileged", "writable", "capadd",
                                    "wrong_user", "wrong_command", "wrong_label", "foreign_volume",
                                    "bind_writable", "other_network", "public_ip", "pids"])
def test_container_attestation_rejects_each_unsafe_drift(local, change):
    prepared = module.prepare()
    obj, network = container_metadata(prepared)
    if change == "published": obj["HostConfig"]["PortBindings"] = {"57420/tcp": [{"HostIp": "0.0.0.0", "HostPort": "57420"}]}
    elif change == "memory": obj["HostConfig"]["Memory"] *= 2
    elif change == "cpu": obj["HostConfig"]["NanoCpus"] = 500_000_000
    elif change == "privileged": obj["HostConfig"]["Privileged"] = True
    elif change == "writable": obj["HostConfig"]["ReadonlyRootfs"] = False
    elif change == "capadd": obj["HostConfig"]["CapAdd"] = ["SYS_ADMIN"]
    elif change == "wrong_user": obj["Config"]["User"] = "0:0"
    elif change == "wrong_command": obj["Config"]["Cmd"] = ["malicious"]
    elif change == "wrong_label": obj["Config"]["Labels"]["knowi.analytics.nonce"] = "foreign"
    elif change == "foreign_volume": obj["Mounts"][0]["Name"] = "foreign"
    elif change == "bind_writable": obj["Mounts"][1]["RW"] = True
    elif change == "other_network": obj["NetworkSettings"]["Networks"]["foreign"] = {}
    elif change == "public_ip":
        obj["NetworkSettings"]["Networks"][module.NETWORK]["IPAddress"] = "8.8.8.8"
        network["Containers"][CONTAINER]["IPv4Address"] = "8.8.8.8/24"
    elif change == "pids": obj["HostConfig"]["PidsLimit"] = 0
    with pytest.raises(module.Refused): module._verify_container(obj, prepared, IMAGE, network)


def test_environment_validates_exact_local_image_and_existing_pg_network(monkeypatch):
    monkeypatch.setattr(module.resource_snapshot, "local_context", lambda env: "fixture")
    monkeypatch.setattr(module.resource_snapshot, "verify_container", lambda info: (PG_ID, 1, 1))
    monkeypatch.setattr(module.resource_snapshot, "verify_volume", lambda info: None)
    image = {"Id": IMAGE, "Os": "linux", "Architecture": "amd64", "RepoTags": ["knowi-analytics:frozen"],
             "Config": {"User": "10001", "WorkingDir": "/app"}}
    pg = [{"NetworkSettings": {"Networks": {module.NETWORK: {"NetworkID": NETWORK_ID, "IPAddress": "172.20.0.2"}}}}]
    network = {"Id": NETWORK_ID, "Name": module.NETWORK, "Driver": "bridge", "IPAM": {"Config": [{"Subnet": "172.20.0.0/16"}]},
               "Labels": {"com.docker.compose.project": module.PROJECT, "com.docker.compose.network": "analytics"},
               "Containers": {PG_ID: {"Name": module.resource_snapshot.TARGET, "IPv4Address": "172.20.0.2/16"}}}
    def run(args, *, timeout=20):
        if args[3:5] == ["image", "inspect"]: return json.dumps([image])
        if args[3:6] == ["inspect", "--type", "container"]: return json.dumps(pg)
        if args[3:5] == ["volume", "inspect"]: return json.dumps([{}])
        if args[3:5] == ["network", "inspect"]: return json.dumps([network])
        raise AssertionError("unexpected command")
    monkeypatch.setattr(module, "_run", run)
    assert module._environment(IMAGE)[1] == network
    image["RepoTags"] = ["foreign:latest"]
    with pytest.raises(module.Refused, match="image_unverified"): module._environment(IMAGE)
    image["RepoTags"] = ["knowi-analytics:frozen"]
    network["Labels"]["com.docker.compose.project"] = "foreign"
    with pytest.raises(module.Refused, match="network_identity_unverified"): module._environment(IMAGE)
    with pytest.raises(module.Refused, match="image_id_invalid"): module._environment("knowi-analytics:latest")
