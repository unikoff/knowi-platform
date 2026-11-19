"""Bounded synthetic k3d inventory; guarded create is reserved for root GO."""
import hashlib
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import UUID

import check_k3s
import resource_snapshot

ROOT = Path(__file__).resolve().parents[1]
K3D = ROOT / ".local/bin/k3d.exe"
K3D_SHA256 = "49d0b9c796f5b8ba6f58a1dd5469e8b7b34bf9b6ce9078633eedb2789680a034"
CLUSTER = "knowi-analytics-01a1df75-v2"
K3S_IMAGE = "rancher/k3s:v1.35.5-k3s1"
CONFIG = ROOT.parents[1] / "infra/analytics-local/k3d-direct-v2.yaml"
CONFIG_SHA256 = "7f42250167a341a473e998e24dc1cccd5291a4f82b84982a6f8a724ea17cd8c9"
KUBECONFIG = ROOT / ".local/k3s-kubeconfig-v2.yaml"
SERVER = "k3d-" + CLUSTER + "-server-0"
CONTEXT = "k3d-" + CLUSTER
PG_NETWORK = "knowi-analytics-01a1df75-net"
MIB = 1024 * 1024
POST_AUTHORITY = 128 * MIB
POST_TRANSIENT = 256 * MIB
POST_RESERVE = 512 * MIB
PROTECTIVE_STOP_OPERATION = "d11af186-fed2-4947-b71d-6fc7d8d9a703"
PROTECTIVE_STOP_SERVER = "96cc40b0343d9387bed6f1721895095b211eaf897276b5c62e247c728fadeb4f"
PROTECTIVE_STOP_IMAGE = "sha256:2074403abe1bded11ef3dde09d457e13be8e0b64c218b1c4f8269b4565cfbc65"
PROTECTIVE_STOP_FINISHED_AT = "2026-11-17T14:06:51.140275426Z"
PROTECTIVE_STOP_INTENT = ROOT / (".local/node-protective-stop-" + PROTECTIVE_STOP_OPERATION + ".json")
PROTECTIVE_STOP_CONFIRMED = ROOT / (".local/node-protective-stop-" + PROTECTIVE_STOP_OPERATION + ".confirmed.json")
PROTECTIVE_STOP_VOLUMES = {
    "/k3d/images": "k3d-knowi-analytics-01a1df75-v2-images",
    "/var/log": "8376c2415fc2938e7cd3b6c30ccda2a440b08e405ffd69b75b8f1c2f09cb340d",
    "/var/lib/cni": "d4a5ba56062144ea876e071aa346d7705613c466e87bcda25310bc9a88f53936",
    "/var/lib/kubelet": "fd80ade5bd72b53cbb8360b62dfb9d4f91cc5e28c8b4c0a3a00a84178eb3e4d9",
    "/var/lib/rancher/k3s": "04f8b87b501158073cf4141ef9657e37073d7403d88f12edb8c1491b8cb1212d",
}


class InsufficientPostCreateBudget(ValueError):
    def __init__(self, available_bytes, required_bytes):
        super().__init__("insufficient post-create budget")
        self.available_bytes = available_bytes
        self.required_bytes = required_bytes


def run(args, *, allow_missing=False, env=None, timeout=15):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("read-only command unavailable") from None
    if result.returncode and not allow_missing:
        raise ValueError("read-only command failed")
    return result


def file_sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory():
    if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT") or os.environ.get("DOCKER_CONFIG"):
        raise ValueError("Docker endpoint override refused")
    if K3D.is_symlink() or K3D.is_junction() or not K3D.is_file():
        raise ValueError("k3d binary unavailable")
    if file_sha256(K3D) != K3D_SHA256:
        raise ValueError("k3d binary digest differs from verified release")
    docker_context = resource_snapshot.local_context(dict(os.environ))
    docker = ["docker", "--context", docker_context]
    free = check_k3s.available_memory()
    clusters = json.loads(run([str(K3D), "cluster", "list", "-o", "json"]).stdout)
    if not isinstance(clusters, list):
        raise ValueError("invalid cluster inventory")
    named = [item for item in clusters if isinstance(item, dict) and item.get("name") == CLUSTER]
    image = run(docker + ["image", "inspect", K3S_IMAGE, "--format", "{{.Id}}"], allow_missing=True)
    pg = json.loads(run(docker + ["inspect", "--type", "container", resource_snapshot.TARGET]).stdout)
    resource_snapshot.verify_container(pg)
    volume = json.loads(run(docker + ["volume", "inspect", resource_snapshot.VOLUME]).stdout)
    resource_snapshot.verify_volume(volume)
    return {"status": "phase1_inventory_only", "go_authorized": False,
            "k3d_version": "5.9.0", "k3d_sha256_verified": True,
            "memory_available_mib": free // (1024 * 1024),
            "memory_threshold_mib": check_k3s.MIN_FREE // (1024 * 1024),
            "memory_threshold_met": check_k3s.admission(free),
            "target_cluster_present": bool(named), "k3s_image_present": image.returncode == 0,
            "synthetic_postgres_identity": "verified"}


def validate_server(info, image_id):
    try:
        obj = info[0]
        labels = obj["Config"]["Labels"]
        binding = [{"HostIp": "127.0.0.1", "HostPort": "57443"}]
        if (obj["Name"] != "/" + SERVER or not obj["State"]["Running"]
                or labels.get("k3d.cluster") != CLUSTER
                or labels.get("k3d.role") != "server"
                or labels.get("knowi.analytics.synthetic") != "true"
                or obj["Image"] != image_id
                or obj["HostConfig"]["Memory"] != 2 * 1024**3
                or obj["HostConfig"]["PortBindings"] != {"6443/tcp": binding}
                or obj["NetworkSettings"]["Ports"] != {"6443/tcp": binding}):
            raise ValueError
    except (IndexError, KeyError, TypeError, AttributeError, ValueError):
        raise ValueError("server identity did not match; preserve and reconcile") from None


def _protective_stop_receipt(receipt, obj, server_id):
    """Accept only the recorded, non-OOM protective stop of this exact node."""
    try:
        requested = Path(receipt)
        if (requested != PROTECTIVE_STOP_CONFIRMED or server_id != PROTECTIVE_STOP_SERVER
                or obj.get("Image") != PROTECTIVE_STOP_IMAGE):
            raise ValueError
        for path in (ROOT / ".local", PROTECTIVE_STOP_INTENT, PROTECTIVE_STOP_CONFIRMED):
            if path.is_symlink() or path.is_junction() or not path.exists():
                raise ValueError
        if any(not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > 4096
               for path in (PROTECTIVE_STOP_INTENT, PROTECTIVE_STOP_CONFIRMED)):
            raise ValueError
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result: raise ValueError
                result[key] = value
            return result
        intent = json.loads(PROTECTIVE_STOP_INTENT.read_text(encoding="utf-8"), object_pairs_hook=unique)
        confirmed = json.loads(PROTECTIVE_STOP_CONFIRMED.read_text(encoding="utf-8"), object_pairs_hook=unique)
        state = obj["State"]
        expected_result = server_id + "|false|137|false|" + state["FinishedAt"]
        if (intent.get("operation") != PROTECTIVE_STOP_OPERATION or intent.get("server_id") != server_id
                or intent.get("cluster_uid") != "75bf9b44-eb92-4f93-ac83-e82528d9acb1"
                or intent.get("status") != "pending" or intent.get("reason") != "low_host_memory_no_measurement"
                or confirmed.get("operation") != PROTECTIVE_STOP_OPERATION or confirmed.get("status") != "reconciled_stopped"
                or confirmed.get("data_retained") is not True or confirmed.get("graceful_shutdown") is not False
                or confirmed.get("result") != expected_result or state.get("FinishedAt") != PROTECTIVE_STOP_FINISHED_AT
                or state.get("OOMKilled") is not False
                or state.get("ExitCode") != 137):
            raise ValueError
        mounts = obj["Mounts"]
        if not isinstance(mounts, list) or len(mounts) != len(PROTECTIVE_STOP_VOLUMES) + 1:
            raise ValueError
        destinations = {m["Destination"]: m for m in mounts}
        if len(destinations) != len(mounts) or set(destinations) != set(PROTECTIVE_STOP_VOLUMES) | {"/proc/meminfo"}:
            raise ValueError
        for destination, name in PROTECTIVE_STOP_VOLUMES.items():
            mount = destinations[destination]
            if mount.get("Type") != "volume" or mount.get("Name") != name or mount.get("RW") is not True:
                raise ValueError
        if destinations["/proc/meminfo"].get("Type") != "bind" or destinations["/proc/meminfo"].get("RW") is not False:
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("protective stop receipt or retained volume identity mismatch") from None


def validate_stopped_server(info, image_id, server_id, *, protective_stop_receipt=None):
    """Attest the stopped fixture before estimating its full restart capacity."""
    try:
        obj = info[0]
        labels = obj["Config"]["Labels"]
        binding = [{"HostIp": "127.0.0.1", "HostPort": "57443"}]
        if (len(info) != 1 or not re.fullmatch(r"[0-9a-f]{64}", server_id)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
                or obj["Id"] != server_id or obj["Name"] != "/" + SERVER
                or obj["State"]["Running"] is not False or obj["State"]["Status"] != "exited"
                or (protective_stop_receipt is None and obj["State"]["ExitCode"] != 143) or obj["Image"] != image_id
                or labels.get("k3d.cluster") != CLUSTER or labels.get("k3d.role") != "server"
                or labels.get("knowi.analytics.synthetic") != "true"
                or obj["HostConfig"]["Memory"] != 2 * 1024**3
                or obj["HostConfig"].get("CgroupnsMode") != "private"
                or obj["HostConfig"]["PortBindings"] != {"6443/tcp": binding}
                or obj["NetworkSettings"].get("Ports") not in ({}, None)
                or set(obj["NetworkSettings"]["Networks"]) != {"k3d-" + CLUSTER, PG_NETWORK}):
            raise ValueError
        if protective_stop_receipt is not None:
            _protective_stop_receipt(protective_stop_receipt, obj, server_id)
    except (IndexError, KeyError, TypeError, AttributeError, ValueError):
        raise ValueError("stopped server identity did not match; preserve and reconcile") from None


def validate_kubeconfig(config):
    if len(config) > 256 * 1024:
        raise ValueError("private kubeconfig too large")
    endpoints = re.findall(r"(?m)^\s*server:\s*(\S+)\s*$", config)
    contexts = re.findall(r"(?m)^\s*name:\s*(\S+)\s*$", config)
    if endpoints != ["https://127.0.0.1:57443"] or CONTEXT not in contexts:
        raise ValueError("private kubeconfig target mismatch; preserve and reconcile")


def create_after_go(expected_k3s_image_id):
    """Not called in phase one. Preserve all partial state on any error."""
    state = inventory()
    if state["target_cluster_present"] or not state["memory_threshold_met"] or not state["k3s_image_present"]:
        raise ValueError("preconditions not satisfied")
    if CONFIG.is_symlink() or CONFIG.is_junction() or hashlib.sha256(CONFIG.read_bytes()).hexdigest() != CONFIG_SHA256:
        raise ValueError("fixture configuration differs; review required")
    if ROOT.joinpath(".local").is_symlink() or ROOT.joinpath(".local").is_junction() or KUBECONFIG.exists() or KUBECONFIG.is_symlink() or KUBECONFIG.is_junction():
        raise ValueError("private kubeconfig path is not new")
    context = resource_snapshot.local_context(dict(os.environ))
    docker = ["docker", "--context", context]
    for kind, name in (("network", "k3d-" + CLUSTER), ("volume", "k3d-" + CLUSTER + "-images")):
        existing = run(docker + [kind, "ls", "--format", "{{.Name}}", "--filter", "name=^" + name + "$"]).stdout.strip()
        if existing:
            raise ValueError("an owned-name resource already exists; reconcile")
    for search in ("name=^/" + SERVER + "$", "label=k3d.cluster=" + CLUSTER):
        if run(docker + ["ps", "-aq", "--filter", search]).stdout.strip():
            raise ValueError("target server already exists; reconcile")
    image_id = run(docker + ["image", "inspect", K3S_IMAGE, "--format", "{{.Id}}"]).stdout.strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_k3s_image_id) or image_id != expected_k3s_image_id:
        raise ValueError("invalid local image identity")
    pre_create_budget(check_k3s.available_memory())
    # This exclusive placeholder is deliberately retained if creation has an
    # unknown outcome. No automatic retry or deletion is safe in that case.
    fd = os.open(KUBECONFIG, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        env = {**os.environ, "DOCKER_CONTEXT": context, "KUBECONFIG": str(KUBECONFIG)}
        run([str(K3D), "cluster", "create", "--config", str(CONFIG), "--servers-memory", "2g",
             "--kubeconfig-update-default=false", "--kubeconfig-switch-context=false"], env=env, timeout=210)
        info = json.loads(run(docker + ["inspect", "--type", "container", SERVER]).stdout)
        validate_server(info, image_id)
        config = run([str(K3D), "kubeconfig", "get", CLUSTER], env=env, timeout=15).stdout
        validate_kubeconfig(config)
        out.write(config)
        out.flush()
        os.fsync(out.fileno())
    return {"status": "created_api_unverified", "kubeconfig": "private"}


def validate_kubectl_config(config):
    try:
        contexts = [entry for entry in config["contexts"] if entry["name"] == CONTEXT]
        if len(contexts) != 1:
            raise ValueError
        cluster_name = contexts[0]["context"]["cluster"]
        clusters = [entry for entry in config["clusters"] if entry["name"] == cluster_name]
        if len(clusters) != 1 or clusters[0]["cluster"]["server"] != "https://127.0.0.1:57443":
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError("private kubeconfig context mismatch") from None


def verify_api_after_go(kubectl, expected_kubectl_sha256, expected_k3s_image_id):
    """Authenticate the exact local API; required before any Kubernetes mutation."""
    executable = Path(kubectl)
    if (not executable.is_absolute() or executable.is_symlink() or executable.is_junction()
            or not executable.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected_kubectl_sha256)
            or file_sha256(executable) != expected_kubectl_sha256):
        raise ValueError("kubectl binary identity mismatch")
    if (KUBECONFIG.is_symlink() or KUBECONFIG.is_junction() or not KUBECONFIG.is_file()
            or KUBECONFIG.stat().st_size > 256 * 1024):
        raise ValueError("unsafe private kubeconfig")
    context = resource_snapshot.local_context(dict(os.environ))
    docker = ["docker", "--context", context]
    info = json.loads(run(docker + ["inspect", "--type", "container", SERVER]).stdout)
    validate_server(info, expected_k3s_image_id)
    cmd = [str(executable), "--kubeconfig", str(KUBECONFIG), "--context", CONTEXT,
           "--namespace", "knowi-analytics"]
    config = json.loads(run(cmd + ["config", "view", "-o", "json"], timeout=15).stdout)
    validate_kubectl_config(config)
    if run(cmd + ["get", "--raw=/readyz"], timeout=15).stdout.strip() != "ok":
        raise ValueError("target API is not ready")
    return {"status": "api_verified"}


def validate_pg_network(network, pg):
    try:
        info = network[0]
        postgres = pg[0]
        resource_snapshot.verify_container(pg)
        labels = info["Labels"]
        endpoint = postgres["NetworkSettings"]["Networks"][PG_NETWORK]
        if (info["Name"] != PG_NETWORK or labels.get("com.docker.compose.project") != resource_snapshot.PROJECT
                or labels.get("com.docker.compose.network") != "analytics"
                or endpoint["NetworkID"] != info["Id"]
                or postgres["Id"] not in info["Containers"]
                or not endpoint["IPAddress"]):
            raise ValueError
        from setup_k3s_fixture import private_ipv4
        return private_ipv4(endpoint["IPAddress"])
    except (IndexError, KeyError, TypeError, AttributeError, ValueError):
        raise ValueError("synthetic PostgreSQL network identity mismatch") from None


def post_create_budget(node, postgres, host_available, authority=None):
    """Fail closed; raw charged bytes and exact caps, never double-count Pods."""
    if type(host_available) is not int or host_available < POST_RESERVE:
        raise ValueError("host memory measurement or reserve unavailable")
    states = [(node, 2 * 1024**3), (postgres, 384 * MIB)]
    if authority is not None:
        states.append((authority, POST_AUTHORITY))
    for state, cap in states:
        if (not isinstance(state, dict) or state.get("cap") != cap
                or type(state.get("current")) is not int or not 0 <= state["current"] <= cap
                or state.get("swap") != 0 or state.get("pressure") is not False
                or state.get("oom") is not False or state["current"] * 100 >= cap * 85):
            raise ValueError("cgroup limit, swap, pressure, OOM, or usage guard failed")
    authority_remaining = POST_AUTHORITY if authority is None else authority["cap"] - authority["current"]
    required = (node["cap"] - node["current"] + postgres["cap"] - postgres["current"]
                + authority_remaining + POST_TRANSIENT + POST_RESERVE)
    if host_available < required:
        raise InsufficientPostCreateBudget(host_available, required)
    return {"status": "post_create_budget_verified", "available_bytes": host_available,
            "required_bytes": required, "node_current_bytes": node["current"],
            "postgres_current_bytes": postgres["current"], "authority_reserve_bytes": authority_remaining,
            "transient_reserve_bytes": POST_TRANSIENT, "host_reserve_bytes": POST_RESERVE}


def require_post_create_margin(sample):
    """Keep the independently approved 128 MiB beyond the calculated reserve."""
    required = sample["required_bytes"] + 128 * MIB
    if sample["available_bytes"] < required:
        raise InsufficientPostCreateBudget(sample["available_bytes"], required)
    return {**sample, "margin_bytes": 128 * MIB}


def pre_create_budget(host_available):
    """Reserve full node and PG caps plus authority, transient, host and margin."""
    if type(host_available) is not int or host_available < 0:
        raise ValueError("host memory measurement unavailable")
    required = max(check_k3s.MIN_FREE, 2 * 1024**3 + 384 * MIB + POST_AUTHORITY
                   + POST_TRANSIENT + POST_RESERVE + 128 * MIB)
    if host_available < required:
        raise InsufficientPostCreateBudget(host_available, required)
    return {"status": "pre_create_budget_verified", "available_bytes": host_available,
            "required_bytes": required, "node_reserve_bytes": 2 * 1024**3,
            "postgres_reserve_bytes": 384 * MIB, "authority_reserve_bytes": POST_AUTHORITY,
            "transient_reserve_bytes": POST_TRANSIENT, "host_reserve_bytes": POST_RESERVE,
            "margin_bytes": 128 * MIB}


def stopped_node_restart_budget(postgres, host_available):
    """Conservatively reserve the entire stopped node cap plus the 128 MiB margin."""
    if type(host_available) is not int or host_available < 0:
        raise ValueError("host memory measurement unavailable")
    node = {"cap": 2 * 1024**3, "current": 0, "swap": 0, "pressure": False, "oom": False}
    sample = post_create_budget(node, postgres, 8 * 1024**3)
    required = max(check_k3s.MIN_FREE, sample["required_bytes"] + 128 * MIB)
    if host_available < required:
        raise InsufficientPostCreateBudget(host_available, required)
    return {"status": "stopped_node_restart_budget_verified", "available_bytes": host_available,
            "required_bytes": required, "node_reserve_bytes": node["cap"],
            "postgres_current_bytes": postgres["current"], "margin_bytes": 128 * MIB}


def attest_stopped_authority(docker, expected_container_id, expected_image_id):
    """Read-only proof of the one retained stopped authority and its owned journal."""
    if (not isinstance(expected_container_id, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_container_id)
            or not isinstance(expected_image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_image_id)):
        raise ValueError("stopped authority requires exact container and image IDs")
    matches = run(docker + ["ps", "-aq", "--no-trunc", "--filter",
                            "label=knowi.analytics.fixture=k3s-privacy-authority"]).stdout.splitlines()
    if matches != [expected_container_id]:
        raise ValueError("stopped authority scope mismatch")
    import k3s_authority as authority
    names = run(docker + ["ps", "-aq", "--no-trunc", "--filter",
                          "name=^/" + authority.PROJECT + "-privacy-authority-"]).stdout.splitlines()
    running_image = run(docker + ["ps", "-q", "--no-trunc", "--filter",
                                  "ancestor=" + expected_image_id]).stdout.splitlines()
    if names != [expected_container_id] or running_image:
        raise ValueError("additional authority or same-image container present")
    try:
        prepared = authority._verify_prepared()
        objects = json.loads(run(docker + ["inspect", "--type", "container", expected_container_id]).stdout)
        volumes = json.loads(run(docker + ["volume", "inspect", prepared.volume_name]).stdout)
        networks = json.loads(run(docker + ["network", "inspect", authority.NETWORK]).stdout)
        if len(objects) != 1 or len(volumes) != 1 or len(networks) != 1:
            raise ValueError
        obj, network = objects[0], networks[0]
        authority._verify_volume(volumes[0], prepared)
        host, state, config = obj["HostConfig"], obj["State"], obj["Config"]
        labels, mounts, net = config["Labels"], obj["Mounts"], obj["NetworkSettings"]["Networks"]
        data = [m for m in mounts if m.get("Destination") == authority.DATA_DIR]
        private = [m for m in mounts if m.get("Destination") == authority.CONFIG_DIR]
        expected_config = os.path.normcase(str(authority._safe(authority.ROOT / "config", directory=True)))
        expected_cmd = ["-B", "-m", "pymentor_analytics.adapters.privacy_authority.server", "serve", "--private-fixture-network",
                        "--journal", authority.JOURNAL, "--authority-id", prepared.authority_id,
                        "--config", authority.CONFIG_DIR + "/server.json"]
        if (obj["Id"] != expected_container_id or obj["Name"] != "/" + authority._name(prepared)
                or obj["Image"] != expected_image_id or state["Running"] is not False
                or state["Status"] != "exited" or state.get("Restarting") is not False
                or state.get("ExitCode") != 0 or host.get("RestartPolicy", {}).get("Name") != "no"
                or any(labels.get(k) != v for k, v in authority._labels(prepared).items())
                or labels.get("knowi.analytics.role") != "authority"
                or config["User"] != "10001:10001" or config["Entrypoint"] != ["python"] or config["Cmd"] != expected_cmd
                or host["Memory"] != POST_AUTHORITY or host["NanoCpus"] != 250_000_000
                or host.get("CgroupnsMode") != "private" or host.get("ReadonlyRootfs") is not True
                or host.get("Privileged") is not False or host.get("PidsLimit") != 64
                or "ALL" not in (host.get("CapDrop") or []) or host.get("CapAdd")
                or not any(x in ("no-new-privileges", "no-new-privileges:true") for x in (host.get("SecurityOpt") or []))
                or host.get("PortBindings") or any(value for value in (obj["NetworkSettings"].get("Ports") or {}).values())
                or len(mounts) != 2 or len(data) != 1 or data[0].get("Type") != "volume"
                or data[0].get("Name") != prepared.volume_name or data[0].get("RW") is not True
                or len(private) != 1 or private[0].get("Type") != "bind" or private[0].get("RW") is not False
                or not authority._bind_source_matches(private[0]["Source"], expected_config, windows=os.name == "nt")
                or set(net) != {authority.NETWORK} or network["Name"] != authority.NETWORK
                or net[authority.NETWORK]["NetworkID"] != network["Id"]):
            raise ValueError
    except Exception:
        raise ValueError("stopped authority identity or owned journal mismatch") from None
    return {"container_id": expected_container_id, "image_id": expected_image_id,
            "journal_volume": prepared.volume_name, "memory_reserve_bytes": POST_AUTHORITY}


def require_stopped_node_restart_budget(kubectl, expected_kubectl_sha256, expected_k3s_image_id, expected_server_id,
                                        *, expected_stopped_authority_id=None, expected_stopped_authority_image_id=None,
                                        protective_stop_receipt=None):
    """Read-only admission for the exact stopped node; never starts a container."""
    before = check_k3s.available_memory()
    executable = Path(kubectl)
    if (not executable.is_absolute() or executable.is_symlink() or executable.is_junction()
            or not executable.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected_kubectl_sha256)
            or file_sha256(executable) != expected_kubectl_sha256):
        raise ValueError("kubectl binary identity mismatch")
    if (KUBECONFIG.is_symlink() or KUBECONFIG.is_junction() or not KUBECONFIG.is_file()
            or KUBECONFIG.stat().st_size > 256 * 1024):
        raise ValueError("unsafe private kubeconfig")
    command = [str(executable), "--kubeconfig", str(KUBECONFIG), "--context", CONTEXT,
               "--namespace", "knowi-analytics", "config", "view", "-o", "json"]
    validate_kubectl_config(json.loads(run(command).stdout))
    context = resource_snapshot.local_context(dict(os.environ))
    docker = ["docker", "--context", context]
    server = json.loads(run(docker + ["inspect", "--type", "container", SERVER]).stdout)
    if protective_stop_receipt is None:
        validate_stopped_server(server, expected_k3s_image_id, expected_server_id)
    else:
        validate_stopped_server(server, expected_k3s_image_id, expected_server_id, protective_stop_receipt=protective_stop_receipt)
    if run(docker + ["ps", "-q", "--no-trunc", "--filter", "label=k3d.cluster=" + CLUSTER]).stdout.strip():
        raise ValueError("unexpected running k3d cluster container")
    if expected_stopped_authority_id is None and expected_stopped_authority_image_id is None:
        if run(docker + ["ps", "-aq", "--no-trunc", "--filter",
                         "label=knowi.analytics.fixture=k3s-privacy-authority"]).stdout.strip():
            raise ValueError("authority scope unknown; reconcile before restart")
    else:
        attest_stopped_authority(docker, expected_stopped_authority_id, expected_stopped_authority_image_id)
    pg = json.loads(run(docker + ["inspect", "--type", "container", resource_snapshot.TARGET]).stdout)
    pg_id, pg_cap, _ = resource_snapshot.verify_container(pg)
    resource_snapshot.verify_volume(json.loads(run(docker + ["volume", "inspect", resource_snapshot.VOLUME]).stdout))
    if pg_cap != 384 * MIB or pg[0]["HostConfig"].get("CgroupnsMode") != "private":
        raise ValueError("PostgreSQL cgroup identity mismatch")
    validate_pg_network(json.loads(run(docker + ["network", "inspect", PG_NETWORK]).stdout), pg)
    postgres = _cgroup_numbers(docker, pg_id, "postgres")
    after = check_k3s.available_memory()
    return stopped_node_restart_budget(postgres, min(before, after))


def _cgroup_numbers(docker, container, user=None):
    prefix = docker + ["exec"] + (["--user", user] if user else []) + [container]
    if run(prefix + ["stat", "-f", "-c", "%T", "/sys/fs/cgroup"]).stdout.strip() != "cgroup2fs":
        raise ValueError("cgroup v2 filesystem unavailable")
    files = ["/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.swap.current"]
    values = run(prefix + ["cat", *files]).stdout.splitlines()
    if len(values) != 3 or any(not re.fullmatch(r"[0-9]+", item) for item in values):
        raise ValueError("cgroup memory measurement unavailable")
    events_lines = run(prefix + ["cat", "/sys/fs/cgroup/memory.events"]).stdout.splitlines()
    events = {}
    for line in events_lines:
        match = re.fullmatch(r"([a-z_]+) ([0-9]+)", line)
        if not match or match.group(1) in events:
            raise ValueError("cgroup events measurement invalid")
        events[match.group(1)] = int(match.group(2))
    if not {"max", "oom", "oom_kill"} <= set(events):
        raise ValueError("cgroup events measurement incomplete")
    pressure_lines = run(prefix + ["cat", "/sys/fs/cgroup/memory.pressure"]).stdout.splitlines()
    parsed = {}
    for line in pressure_lines:
        match = re.fullmatch(r"(some|full) avg10=([0-9]+(?:\.[0-9]+)?) avg60=[0-9.]+ avg300=[0-9.]+ total=[0-9]+", line)
        if not match or match.group(1) in parsed:
            raise ValueError("cgroup pressure measurement invalid")
        try:
            parsed[match.group(1)] = Decimal(match.group(2))
        except InvalidOperation:
            raise ValueError("cgroup pressure measurement invalid") from None
    if set(parsed) != {"some", "full"}:
        raise ValueError("cgroup pressure measurement incomplete")
    return {"current": int(values[0]), "cap": int(values[1]), "swap": int(values[2]),
            "pressure": any(value > 0 for value in parsed.values()),
            "oom": any(events[key] > 0 for key in ("max", "oom", "oom_kill"))}


def validate_ingress_fixture(pod, uid):
    """Permit exactly the explicitly identified local test proxy, never a generic Pod."""
    try:
        meta, spec, status = pod["metadata"], pod["spec"], pod["status"]
        labels, containers = meta["labels"], spec["containers"]
        container, statuses = containers[0], status["containerStatuses"]
        security = container["securityContext"]
        if (not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", uid)
                or meta["uid"] != uid or meta["name"] != "analytics-traefik-fixture" or meta["namespace"] != "kube-system"
                or labels.get("app.kubernetes.io/name") != "traefik" or labels.get("knowi.analytics.fixture") != "traefik"
                or spec.get("automountServiceAccountToken") is not False or spec.get("hostNetwork", False)
                or spec.get("restartPolicy") != "Never" or len(containers) != 1 or container.get("name") != "traefik"
                or container.get("image") != "traefik:v3.7.15" or container.get("imagePullPolicy") != "Never"
                or container.get("resources", {}).get("limits", {}).get("memory") != "128Mi"
                or security.get("readOnlyRootFilesystem") is not True or security.get("allowPrivilegeEscalation") is not False
                or "ALL" not in security.get("capabilities", {}).get("drop", [])
                or len(statuses) != 1 or statuses[0].get("imageID") != "sha256:6cfd9663a5d3591fde0170042b72dd00614a6b99208cb377a46ec23a91560b65"
                or statuses[0].get("ready") is not True):
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise ValueError("ingress fixture identity unavailable") from None


def validate_lease_fixture(pod, uid, job_uid):
    """Permit only the exact operator-created processor claim Job Pod."""
    try:
        meta, spec, status = pod["metadata"], pod["spec"], pod["status"]
        labels, containers, owners = meta["labels"], spec["containers"], meta["ownerReferences"]
        c = containers[0]; security = c["securityContext"]
        env = {entry["name"]: entry for entry in c["env"]}
        source = (ROOT.parents[1] / "infra/analytics-local/lease-claim.py").read_text(encoding="utf-8")
        if (len(c["env"]) != 4 or len(env) != 4 or set(env) != {"ANALYTICS_PROCESSOR_DATABASE_URL", "ANALYTICS_PRIVACY_AUTHORITY_JSON", "FIXTURE_SUBJECT", "FIXTURE_EVENT"}
                or env["ANALYTICS_PROCESSOR_DATABASE_URL"].get("valueFrom", {}).get("secretKeyRef") != {"name": "analytics-processor-db", "key": "DATABASE_URL"}
                or env["ANALYTICS_PRIVACY_AUTHORITY_JSON"].get("valueFrom", {}).get("secretKeyRef") != {"name": "analytics-privacy-authority", "key": "PRIVACY_AUTHORITY_JSON"}
                or any(not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", env[key].get("value", "")) for key in ("FIXTURE_SUBJECT", "FIXTURE_EVENT"))
                or c.get("command") != ["python", "-u", "-B", "-c", source]):
            raise ValueError
        if (not all(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", x) for x in (uid, job_uid))
                or meta["uid"] != uid or meta["namespace"] != "knowi-analytics"
                or not meta["name"].startswith("analytics-lease-holder-")
                or labels.get("knowi.analytics.fixture") != "lease-holder"
                or labels.get("app.kubernetes.io/name") != "analytics" or labels.get("app.kubernetes.io/instance") != "analytics"
                or labels.get("app.kubernetes.io/component") != "processor"
                or len(owners) != 1 or owners[0].get("kind") != "Job" or owners[0].get("name") != "analytics-lease-holder"
                or owners[0].get("uid") != job_uid or owners[0].get("controller") is not True
                or spec.get("automountServiceAccountToken") is not False or spec.get("hostNetwork", False)
                or spec.get("restartPolicy") != "Never" or spec.get("serviceAccountName") != "analytics-analytics-processor"
                or spec.get("securityContext", {}).get("runAsNonRoot") is not True
                or spec.get("securityContext", {}).get("runAsUser") != 10001
                or len(containers) != 1 or c.get("name") != "claim"
                or c.get("image") != "knowi-analytics:an1-20261117-report" or c.get("imagePullPolicy") != "Never"
                or c.get("resources", {}).get("limits", {}).get("memory") != "128Mi"
                or security.get("readOnlyRootFilesystem") is not True or security.get("allowPrivilegeEscalation") is not False
                or "ALL" not in security.get("capabilities", {}).get("drop", [])
                or len(status.get("containerStatuses", [])) != 1
                or status["containerStatuses"][0].get("imageID") != "sha256:f51528ba1b3e7182ef4f771964f432473ff142693f1928b837e7d1dab267f90a"):
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, OSError):
        raise ValueError("lease fixture identity unavailable") from None


def benchmark_image_identity_matches(state, expected_ref, expected_config):
    """Compare a CRI identity after the caller attests the pinned manifest/config.

    Kubernetes permits runtime-resolved fields. Accept the prior config-ID
    form, or the observed canonical manifest-ID form only when the separately
    observed image field equals the attested config digest as well.
    """
    if not isinstance(expected_config, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_config):
        return False
    if state.get("imageID") == expected_config:
        observed_image = state.get("image")
        return not (isinstance(observed_image, str) and observed_image.startswith("sha256:")
                    and observed_image != expected_config)
    return (isinstance(expected_ref, str)
            and re.fullmatch(r"docker\.io/library/knowi-analytics@sha256:[0-9a-f]{64}", expected_ref) is not None
            and state.get("imageID") == expected_ref and state.get("image") == expected_config)


def validate_benchmark_fixture(pod, fixture):
    """Accept only a previously recorded benchmark Pod and bounded role."""
    try:
        namespace = fixture["namespace"]
        expected = fixture["pods"][pod["metadata"]["uid"]]
        meta, spec, status = pod["metadata"], pod["spec"], pod["status"]
        uid, role = meta["uid"], expected["role"]
        if (set(fixture) != {"namespace", "pods"}
                or not re.fullmatch(r"knowi-analytics-bench-[0-9a-f]{32}", namespace)
                or UUID(namespace.removeprefix("knowi-analytics-bench-")).version != 4
                or not isinstance(fixture["pods"], dict)
                or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", uid)
                or set(expected) != {"name", "role", "image", "image_id", "resources"}
                or role not in {"collector", "processor", "query", "privacy", "authority", "volume-probe", "journal-init", "migrate"}
                or meta["namespace"] != namespace or meta["name"] != expected["name"]
                or meta.get("deletionTimestamp") is not None
                or meta["labels"].get("app.kubernetes.io/name") != "analytics"
                or meta["labels"].get("app.kubernetes.io/instance") != "bench"
                or meta["labels"].get("app.kubernetes.io/component") != role
                or spec.get("automountServiceAccountToken") is not False
                or spec.get("hostNetwork", False) or spec.get("hostPID", False) or spec.get("hostIPC", False)
                or spec.get("securityContext", {}).get("runAsNonRoot") is not True
                or spec.get("securityContext", {}).get("runAsUser") != 10001
                or any("hostPath" in volume for volume in spec.get("volumes", []))
                or len(spec["containers"]) != 1 or spec.get("initContainers") or spec.get("ephemeralContainers")
                or len(status["containerStatuses"]) != 1):
            raise ValueError
        container, state = spec["containers"][0], status["containerStatuses"][0]
        security = container["securityContext"]
        maximum_memory = "128Mi" if role in {"authority", "volume-probe", "journal-init", "privacy"} else "256Mi"
        if (container["name"] != role or container["image"] != expected["image"]
                or container["imagePullPolicy"] != "Never"
                or not benchmark_image_identity_matches(state, expected["image"], expected["image_id"])
                or state["restartCount"] != 0
                or container["resources"] != expected["resources"]
                or container["resources"]["limits"]["memory"] != maximum_memory
                or security.get("readOnlyRootFilesystem") is not True
                or security.get("allowPrivilegeEscalation") is not False
                or security.get("privileged", False) is not False
                or "ALL" not in security.get("capabilities", {}).get("drop", [])):
            raise ValueError
    except (KeyError, TypeError, IndexError, ValueError, AttributeError):
        raise ValueError("benchmark fixture identity unavailable") from None


def _verify_cgroup_tree(docker, node_id, kubectl, ingress_fixture_uid=None, lease_fixture=None, benchmark_fixture=None):
    if (benchmark_fixture is not None and
            (not isinstance(benchmark_fixture, dict) or set(benchmark_fixture) != {"namespace", "pods"}
             or not isinstance(benchmark_fixture["namespace"], str)
             or not re.fullmatch(r"knowi-analytics-bench-[0-9a-f]{32}", benchmark_fixture["namespace"])
             or UUID(benchmark_fixture["namespace"].removeprefix("knowi-analytics-bench-")).version != 4
             or not isinstance(benchmark_fixture["pods"], dict))):
        raise ValueError("invalid benchmark fixture")
    prefix = docker + ["exec", node_id]
    if run(prefix + ["stat", "-f", "-c", "%T", "/sys/fs/cgroup"]).stdout.strip() != "cgroup2fs":
        raise ValueError("node is not cgroup v2")
    controllers = run(prefix + ["cat", "/sys/fs/cgroup/cgroup.controllers"]).stdout.split()
    if "memory" not in controllers or not run(prefix + ["cat", "/sys/fs/cgroup/k3s/cgroup.procs"]).stdout.strip():
        raise ValueError("node memory/k3s hierarchy unavailable")
    kubepods = run(prefix + ["cat", "/sys/fs/cgroup/kubepods/memory.current"]).stdout.strip()
    if not re.fullmatch(r"[0-9]+", kubepods) or int(kubepods) <= 0:
        raise ValueError("pod cgroup hierarchy unavailable")
    command = [str(kubectl), "--kubeconfig", str(KUBECONFIG), "--context", CONTEXT,
               "--namespace", "kube-system", "get", "pods", "--all-namespaces", "-o", "json"]
    objects = json.loads(run(command).stdout)["items"]
    found = set()
    seen_ingress = False
    seen_lease = False
    seen_benchmark = set()
    for pod in objects:
        name = pod["metadata"]["name"]
        namespace, phase = pod["metadata"]["namespace"], pod["status"].get("phase")
        component = next((kind for kind in ("coredns", "metrics-server", "local-path-provisioner")
                          if namespace == "kube-system" and name.startswith(kind + "-")), None)
        labels = pod["metadata"].get("labels", {})
        known_app = (namespace == "knowi-analytics" and labels.get("app.kubernetes.io/name") == "analytics"
                     and labels.get("app.kubernetes.io/component") in {"collector", "processor", "query", "privacy", "migrate"}
                     and labels.get("knowi.analytics.fixture") != "lease-holder")
        known_ingress = (ingress_fixture_uid is not None and namespace == "kube-system"
                         and name == "analytics-traefik-fixture" and pod["metadata"].get("uid") == ingress_fixture_uid)
        if known_ingress:
            validate_ingress_fixture(pod, ingress_fixture_uid)
            seen_ingress = True
        known_lease = (lease_fixture is not None and labels.get("knowi.analytics.fixture") == "lease-holder"
                       and pod["metadata"].get("uid") == lease_fixture[0])
        if known_lease:
            validate_lease_fixture(pod, *lease_fixture)
            seen_lease = True
        known_benchmark = (benchmark_fixture is not None and namespace == benchmark_fixture.get("namespace")
                           and pod["metadata"].get("uid") in benchmark_fixture.get("pods", {}))
        if known_benchmark:
            validate_benchmark_fixture(pod, benchmark_fixture)
            seen_benchmark.add(pod["metadata"]["uid"])
        if not component and not known_app and not known_ingress and not known_lease and not known_benchmark:
            raise ValueError("unexpected Pod scope")
        if known_lease and phase == "Succeeded":
            continue
        if phase == "Succeeded" and known_app and labels.get("app.kubernetes.io/component") == "migrate":
            continue
        if phase == "Succeeded" and known_benchmark and labels.get("app.kubernetes.io/component") in {"migrate", "journal-init"}:
            continue
        uid, qos = pod["metadata"]["uid"], pod["status"].get("qosClass")
        if (not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", uid)
                or qos not in {"Guaranteed", "Burstable", "BestEffort"}
                or phase != "Running" or pod["spec"].get("nodeName") != SERVER):
            raise ValueError("system Pod cgroup identity unavailable")
        suffix = {"Guaranteed": "", "Burstable": "burstable/", "BestEffort": "besteffort/"}[qos]
        path = "/sys/fs/cgroup/kubepods/" + suffix + "pod" + uid + "/memory.current"
        raw = run(prefix + ["cat", path]).stdout.strip()
        if not re.fullmatch(r"[0-9]+", raw) or int(raw) <= 0:
            raise ValueError("system Pod is outside expected cgroup hierarchy")
        if component:
            found.add(component)
    if lease_fixture is not None and not seen_lease:
        raise ValueError("lease fixture missing")
    if ingress_fixture_uid is not None and not seen_ingress:
        raise ValueError("ingress fixture missing")
    if benchmark_fixture is not None and seen_benchmark != set(benchmark_fixture.get("pods", {})):
        raise ValueError("benchmark fixture missing")
    if found != {"coredns", "metrics-server", "local-path-provisioner"}:
        raise ValueError("system Pod hierarchy incomplete")


def validate_running_cluster_containers(ids, server_id):
    if ids != [server_id]:
        raise ValueError("unexpected running k3d auxiliary container; reconcile before allocation")


def require_post_create_budget(kubectl, expected_kubectl_sha256, expected_k3s_image_id, *, authority_image_id=None, ingress_fixture_uid=None, lease_fixture=None, benchmark_fixture=None, expected_stopped_authority_id=None):
    """Reattest live target and memory for one future bounded allocation stage."""
    before = check_k3s.available_memory()
    verify_api_after_go(kubectl, expected_kubectl_sha256, expected_k3s_image_id)
    context = resource_snapshot.local_context(dict(os.environ))
    docker = ["docker", "--context", context]
    server = json.loads(run(docker + ["inspect", "--type", "container", SERVER]).stdout)
    validate_server(server, expected_k3s_image_id)
    if server[0]["HostConfig"].get("CgroupnsMode") != "private":
        raise ValueError("node cgroup namespace mismatch")
    server_id = server[0]["Id"]
    if not re.fullmatch(r"[0-9a-f]{64}", server_id):
        raise ValueError("node identity unavailable")
    cluster_ids = [line for line in run(docker + ["ps", "-q", "--no-trunc", "--filter", "label=k3d.cluster=" + CLUSTER]).stdout.splitlines() if line]
    validate_running_cluster_containers(cluster_ids, server_id)
    pg = json.loads(run(docker + ["inspect", "--type", "container", resource_snapshot.TARGET]).stdout)
    pg_id, pg_cap, _ = resource_snapshot.verify_container(pg)
    resource_snapshot.verify_volume(json.loads(run(docker + ["volume", "inspect", resource_snapshot.VOLUME]).stdout))
    if pg_cap != 384 * MIB or pg[0]["HostConfig"].get("CgroupnsMode") != "private":
        raise ValueError("PostgreSQL cgroup identity mismatch")
    network = json.loads(run(docker + ["network", "inspect", PG_NETWORK]).stdout)
    validate_pg_network(network, pg)
    if set(server[0]["NetworkSettings"]["Networks"]) != {"k3d-" + CLUSTER, PG_NETWORK}:
        raise ValueError("node network scope mismatch")
    if ingress_fixture_uid is None and lease_fixture is None and benchmark_fixture is None:
        _verify_cgroup_tree(docker, server_id, kubectl)
    else:
        tree_options = {"ingress_fixture_uid": ingress_fixture_uid, "lease_fixture": lease_fixture}
        if benchmark_fixture is not None:
            tree_options["benchmark_fixture"] = benchmark_fixture
        _verify_cgroup_tree(docker, server_id, kubectl, **tree_options)
    node = _cgroup_numbers(docker, server_id)
    postgres = _cgroup_numbers(docker, pg_id, "postgres")
    matches = [line for line in run(docker + ["ps", "-aq", "--no-trunc", "--filter", "label=knowi.analytics.fixture=k3s-privacy-authority"]).stdout.splitlines() if line]
    authority = None
    if expected_stopped_authority_id is not None:
        attest_stopped_authority(docker, expected_stopped_authority_id, authority_image_id)
    elif authority_image_id is None:
        if matches:
            raise ValueError("authority scope unknown; explicit image identity required")
    else:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import k3s_authority
        status = k3s_authority.status(authority_image_id)
        if matches != [status.container_id]:
            raise ValueError("authority container scope mismatch")
        info = json.loads(run(docker + ["inspect", "--type", "container", status.container_id]).stdout)
        if info[0]["HostConfig"].get("CgroupnsMode") != "private":
            raise ValueError("authority cgroup namespace mismatch")
        authority = _cgroup_numbers(docker, status.container_id, "10001:10001")
    after = check_k3s.available_memory()
    return require_post_create_margin(post_create_budget(node, postgres, min(before, after), authority))


def shared_local_budget(sample, host_before, host_after, core_remaining):
    """Combine already charged fixture memory with bounded additional reserves."""
    values = (host_before, host_after, core_remaining, sample.get("available_bytes"), sample.get("required_bytes"))
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("shared local budget measurement unavailable")
    if core_remaining > (512 + 64) * MIB:
        raise ValueError("Core reserve exceeds verified caps")
    available = min(host_before, sample["available_bytes"], host_after)
    required = sample["required_bytes"] + 128 * MIB + core_remaining + 512 * MIB
    if available < required:
        raise InsufficientPostCreateBudget(available, required)
    return {"status": "shared_local_budget_verified", "available_bytes": available,
            "required_bytes": required, "core_remaining_bytes": core_remaining,
            "fixture_inside_node_cap": True, "core_web_growth_reserve_bytes": 512 * MIB,
            "node_current_bytes": sample["node_current_bytes"]}


def verify_local_services(web_temporarily_paused=False):
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:18041/ready", timeout=4) as response:
            if response.status != 200:
                raise ValueError
        if web_temporarily_paused is True:
            import socket
            with socket.socket() as connection:
                connection.settimeout(2)
                if connection.connect_ex(("127.0.0.1", 5173)) == 0:
                    raise ValueError
        elif web_temporarily_paused is False:
            with urllib.request.urlopen("http://127.0.0.1:5173/backend/", timeout=4) as response:
                if response.status != 200:
                    raise ValueError
        else:
            raise ValueError
    except Exception:
        raise ValueError("Core or Web health unavailable") from None

def require_shared_local_budget(kubectl, expected_kubectl_sha256, expected_k3s_image_id, *,
                                authority_image_id, ingress_fixture_uid=None, lease_fixture=None, web_temporarily_paused=False,
                                benchmark_fixture=None, expected_stopped_authority_id=None):
    """Guard this specific local runtime including running Core caps and operator reserves."""
    before = check_k3s.available_memory()
    options = {"authority_image_id": authority_image_id, "ingress_fixture_uid": ingress_fixture_uid, "lease_fixture": lease_fixture}
    if benchmark_fixture is not None:
        options["benchmark_fixture"] = benchmark_fixture
    if expected_stopped_authority_id is not None:
        options["expected_stopped_authority_id"] = expected_stopped_authority_id
    sample = require_post_create_budget(kubectl, expected_kubectl_sha256, expected_k3s_image_id, **options)
    context = resource_snapshot.local_context(dict(os.environ))
    docker = ["docker", "--context", context]
    remaining = 0
    for identity, cap in (("cb0ddbbd527440655f472e6ca9a8a7ee19438b2f30ca8e75ffd98d04d84e8b87", 512 * MIB),
                          ("7cb055c7d46760e229ca2ec6c7c21164de04daf817afe04b7b3c18d92797195a", 64 * MIB)):
        obj = json.loads(run(docker + ["inspect", "--type", "container", identity]).stdout)[0]
        if (obj.get("Id") != identity or obj.get("State", {}).get("Running") is not True
                or obj.get("HostConfig", {}).get("Memory") != cap
                or obj["HostConfig"].get("CgroupnsMode") != "private"
                or run(docker + ["exec", identity, "test", "-f", "/sys/fs/cgroup/cgroup.controllers"], allow_missing=True).returncode):
            raise ValueError("Core container or cgroup identity unavailable")
        numbers = run(docker + ["exec", identity, "cat", "/sys/fs/cgroup/memory.current",
                                "/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.swap.current"]).stdout.splitlines()
        if (len(numbers) != 3 or any(not re.fullmatch(r"[0-9]+", number) for number in numbers)
                or int(numbers[1]) != cap or int(numbers[2]) != 0 or int(numbers[0]) > cap):
            raise ValueError("Core memory measurement unavailable")
        remaining += cap - int(numbers[0])
    verify_local_services(web_temporarily_paused)
    # The bounded ingress Pod is inside the already reserved node cgroup; do not
    # reserve its limit twice. Keep the normal margin and Core/Web allowance.
    return shared_local_budget(sample, before, check_k3s.available_memory(), remaining)


def initialize_database_after_go(kubectl, expected_kubectl_sha256, expected_k3s_image_id):
    """One bounded DB provisioning attempt; unknown outcomes require reconciliation."""
    require_post_create_budget(kubectl, expected_kubectl_sha256, expected_k3s_image_id)
    program = ("import sys; sys.path.insert(0,'tools'); import k3s_database as db; "
               "db._attested_ip(); record=db.initialize(); print(record['state'])")
    try:
        result = subprocess.run([sys.executable, "-B", "-c", program], cwd=ROOT,
                                capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("database outcome unknown; preserve record and reconcile") from None
    if result.returncode or result.stdout.strip() not in {"created", "ready"}:
        raise ValueError("database outcome unknown; preserve record and reconcile")
    return {"status": "verified", "state": result.stdout.strip()}


def prepare_authority_after_go(kubectl, expected_kubectl_sha256, expected_k3s_image_id):
    """Prepare private synthetic files only; no authority container is started."""
    require_post_create_budget(kubectl, expected_kubectl_sha256, expected_k3s_image_id)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import k3s_authority
    k3s_authority.prepare()
    return {"status": "prepared"}


def attach_pg_network_after_go(kubectl, expected_kubectl_sha256, expected_k3s_image_id):
    """Attach the one verified node to the one verified synthetic PG network."""
    verify_api_after_go(kubectl, expected_kubectl_sha256, expected_k3s_image_id)
    context = resource_snapshot.local_context(dict(os.environ))
    docker = ["docker", "--context", context]
    pg = json.loads(run(docker + ["inspect", "--type", "container", resource_snapshot.TARGET]).stdout)
    volume = json.loads(run(docker + ["volume", "inspect", resource_snapshot.VOLUME]).stdout)
    resource_snapshot.verify_volume(volume)
    network = json.loads(run(docker + ["network", "inspect", PG_NETWORK]).stdout)
    validate_pg_network(network, pg)
    info = json.loads(run(docker + ["inspect", "--type", "container", SERVER]).stdout)
    validate_server(info, expected_k3s_image_id)
    expected = {"k3d-" + CLUSTER}
    attached = set(info[0]["NetworkSettings"]["Networks"])
    if attached != expected:
        raise ValueError("unexpected node networks; reconcile without connecting")
    # A timeout has unknown outcome. Never repeat this call automatically.
    run(docker + ["network", "connect", PG_NETWORK, SERVER], timeout=15)
    after = json.loads(run(docker + ["inspect", "--type", "container", SERVER]).stdout)
    validate_server(after, expected_k3s_image_id)
    if set(after[0]["NetworkSettings"]["Networks"]) != expected | {PG_NETWORK}:
        raise ValueError("node network outcome requires reconciliation")
    return {"status": "attached"}


def _kubectl_create(kubectl, obj):
    payload = json.dumps(obj)
    command = [str(kubectl), "--kubeconfig", str(KUBECONFIG), "--context", CONTEXT,
               "--namespace", "knowi-analytics", "create", "--filename", "-"]
    try:
        result = subprocess.run(command, input=payload, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("creation outcome unknown; reconcile before any retry") from None
    if result.returncode:
        raise ValueError("creation failed or outcome unknown; reconcile before retry")


def create_public_fixture_after_go(kubectl, expected_kubectl_sha256, expected_k3s_image_id, kind, authority_image_id=None):
    """Create only one fixed public fixture object; unknown outcomes are not retried."""
    if kind not in {"namespace", "trust", "authority-service", "authority-endpoints"}:
        raise ValueError("unknown fixture object")
    require_post_create_budget(kubectl, expected_kubectl_sha256, expected_k3s_image_id, authority_image_id=authority_image_id)
    if kind == "namespace":
        obj = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "knowi-analytics",
               "labels": {"knowi.analytics.synthetic": "true"}}}
    elif kind == "trust":
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import k3s_authority
        from pydantic import SecretStr
        from pymentor_analytics.adapters.auth.config import load_manifest
        prepared = k3s_authority.prepare()
        content = prepared.trust_manifest_path.read_text(encoding="utf-8")
        load_manifest(SecretStr(content))
        obj = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "analytics-trust-manifest",
               "namespace": "knowi-analytics"}, "data": {"TRUST_MANIFEST_JSON": content}}
    elif kind == "authority-service":
        obj = {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "privacy-authority", "namespace": "knowi-analytics"},
               "spec": {"ports": [{"name": "http", "port": 57420, "protocol": "TCP", "targetPort": 57420}]}}
    else:
        import k3s_authority
        from setup_k3s_fixture import private_ipv4
        address = private_ipv4(k3s_authority.status(authority_image_id).ip)
        obj = {"apiVersion": "discovery.k8s.io/v1", "kind": "EndpointSlice", "metadata": {
            "name": "privacy-authority-fixture", "namespace": "knowi-analytics",
            "labels": {"kubernetes.io/service-name": "privacy-authority",
                       "endpointslice.kubernetes.io/managed-by": "knowi-analytics-fixture"}},
            "addressType": "IPv4", "ports": [{"name": "http", "protocol": "TCP", "port": 57420}],
            "endpoints": [{"addresses": [address]}]}
    _kubectl_create(kubectl, obj)
    return {"status": "created"}


def create_secret_after_go(kubectl, expected_kubectl_sha256, expected_k3s_image_id, role, record=None, *, authority_image_id=None):
    """Create one fixed fixture Secret via stdin; never retry an unknown outcome.

    DB URLs come only from the independently attested K3s DB helper. The
    authority reader config is validated locally and is never printed.
    """
    if role not in {"migrate", "collector", "processor", "query", "privacy", "authority"}:
        raise ValueError("unknown fixture role")
    require_post_create_budget(kubectl, expected_kubectl_sha256, expected_k3s_image_id, authority_image_id=authority_image_id)
    if role == "authority":
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import k3s_authority
        from pydantic import SecretStr
        from pymentor_analytics.adapters.privacy_authority.client import AuthorityConfig
        from pymentor_analytics.adapters.auth.config import validate_authority_key_separation
        from pymentor_analytics.app.config import Settings
        prepared = k3s_authority.prepare()
        content = prepared.client_config_path.read_text(encoding="utf-8")
        manifest = prepared.trust_manifest_path.read_text(encoding="utf-8")
        cfg = AuthorityConfig.from_json(content)
        if cfg.url != "http://privacy-authority.knowi-analytics.svc:57420" or cfg.authority_id != prepared.authority_id:
            raise ValueError("authority configuration identity mismatch")
        settings = Settings(database_url=SecretStr("postgresql://fixture@127.0.0.1:57392/analytics_fixture"),
                            trust_manifest_json=SecretStr(manifest))
        validate_authority_key_separation(settings, cfg.public_keys)
        name, key, value = "analytics-privacy-authority", "PRIVACY_AUTHORITY_JSON", content
    else:
        import k3s_database
        value = (k3s_database.migration_role_url(record) if role == "migrate"
                 else k3s_database.container_role_urls(record)[role])
        name, key = "analytics-" + role + "-db", "DATABASE_URL"
    _kubectl_create(kubectl, {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": "knowi-analytics"},
                              "type": "Opaque", "stringData": {key: value}})
    return {"status": "created"}


def main():
    # Phase-one CLI cannot invoke create_after_go. Root GO is a separate gate.
    if sys.argv[1:] not in ([], ["inventory"]):
        print("Only read-only inventory is available; wait for root GO.", file=sys.stderr)
        return 2
    try:
        print(json.dumps(inventory(), sort_keys=True))
    except Exception:
        print(json.dumps({"status": "inventory_refused", "go_authorized": False}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
