"""Synthetic Analytics K3s benchmark: offline plan unless explicitly admitted.

One live invocation creates a fresh synthetic case, retains its DB and journal,
and stops on any unknown outcome. No live action occurs without explicit GO.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import threading
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

STAGES = (10, 15, 25, 50)
REPLICAS = ((1, 1, 1), (2, 1, 1), (1, 2, 1), (1, 1, 2), (2, 2, 2))
PROFILE = "analytics-synthetic-profile-v1"
PROFILE_CANONICAL_SHA256 = "946a55b71ca666b240f67325032d0280e36d72322ebf67dc99493c90e63decbe"
SEED = 11320261116
WARMUP_SECONDS = 300
MEASUREMENT_SECONDS = 900
REPEATS = 3
NAMESPACE_PREFIX = "knowi-analytics-bench-"
IMAGE_REPOSITORY = "docker.io/library/knowi-analytics"
PACKAGED_HISTORY_HEAD = "a014"
HISTORY_SHA256 = {
    "a010": "834da297a72e0c6d7e1738aa76f6b07dcf27868e54fe132c882228f970b6700a",
    "a011": "8a9e80bd33828e53d737fe1f5803af9bb41dd72f95b073fbffb402b90720aa50",
    "a012": "a92fd75f37789bead5c0b6e87b9b1418f5922844bd786249ca3d9f3eb90f21ab",
    "a013": "d77cc10160169d3e3ee656af1c9d4327b30b6e8ddbbc51cbb2a72b2950c797e1",
    "a014": "7fd0bc65c7ce44cd7166f0ddd409c0966e32e604b6791b855ffc0e6de1a3aa04",
}
# Histories are fingerprints of raw migration bytes. These additional exact
# fingerprints correspond to Git's LF checkout; existing local records may
# contain CRLF or mixed line endings and must retain their original identity.
HISTORY_LF_SHA256 = {
    "a011": "5406da21de62ca42291501579da9ed5cf752c6ee2a558d6aa8a70ac8177c5dc4",
    "a014": "8d81c48940e2dfa9b51d1352ac3f47a5aa62f894bc1259d3e3955e421967e896",
}


def manifest_digest(ref):
    match = re.fullmatch(r"docker\.io/library/knowi-analytics@(sha256:[0-9a-f]{64})", ref or "")
    if match is None:
        raise ValueError("an immutable Analytics manifest reference is required")
    return match.group(1)


def plan(*, a011=False):
    """Return the entire fixed matrix without reading a file or the environment."""
    if type(a011) is not bool:
        raise ValueError("a011 mode must be boolean")
    cases = [
        {"users": stage, "replicas": list(replicas), "repeat": repeat}
        for stage in STAGES for replicas in REPLICAS for repeat in range(1, REPEATS + 1)
    ]
    return {"status": "UNRUN", "execution": "not_started", "environment": "local-k3s",
            "profile": PROFILE, "canonical_profile_sha256": PROFILE_CANONICAL_SHA256,
            "seed": SEED, "warmup_seconds": WARMUP_SECONDS,
            "measurement_seconds": MEASUREMENT_SECONDS, "cases": cases,
            "minimum_workload_seconds": len(cases) * (WARMUP_SECONDS + MEASUREMENT_SECONDS),
            "fresh_resources_per_case": True, "target_revision": "a011" if a011 else "a010",
            "packaged_history_head": PACKAGED_HISTORY_HEAD, "day_projection_enabled": a011,
            "historical_fixture": "UNRUN", "dataset": "fresh_without_historical_seed"}


def namespace_for(run_id):
    if not isinstance(run_id, UUID) or run_id.version != 4:
        raise ValueError("a UUIDv4 run identity is required")
    return NAMESPACE_PREFIX + run_id.hex


def _operation_path(directory, operation, state):
    """Keep operation records in a pre-existing, non-link private directory."""
    directory = Path(directory)
    if (not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", operation)
            or state not in {"pending", "confirmed"} or directory.is_symlink()
            or (hasattr(os.path, "isjunction") and os.path.isjunction(directory))
            or not directory.is_dir()):
        raise ValueError("unsafe operation record")
    return directory / (operation + "." + state + ".json")


def _write_exclusive(path, value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def mark_pending(directory, run_id, operation, public_identity):
    """Record one mutation before issuing it; no automatic retry on collision.

    Identity is deliberately restricted to a run UUID, namespace and a digest;
    it must not contain credentials. The caller still owns and attests the
    directory, target and durable external state before any mutation.
    """
    namespace = namespace_for(run_id)
    if (not isinstance(public_identity, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", public_identity)):
        raise ValueError("public identity digest required")
    path = _operation_path(directory, operation, "pending")
    _write_exclusive(path, {"run_id": str(run_id), "namespace": namespace,
                            "operation": operation, "identity": public_identity})


def mark_confirmed(directory, run_id, operation, public_identity, observation):
    """Record a verified observation without erasing the pending evidence."""
    namespace = namespace_for(run_id)
    if (not isinstance(public_identity, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", public_identity)
            or not isinstance(observation, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", observation)):
        raise ValueError("public identity and observation digests required")
    pending = _operation_path(directory, operation, "pending")
    if pending.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(pending)):
        raise ValueError("unsafe operation record")
    raw = pending.read_bytes()
    expected = {"run_id": str(run_id), "namespace": namespace,
                "operation": operation, "identity": public_identity}
    if len(raw) > 4096 or json.loads(raw) != expected:
        raise ValueError("pending operation mismatch; reconcile")
    _write_exclusive(_operation_path(directory, operation, "confirmed"),
                     {**expected, "pending_sha256": hashlib.sha256(raw).hexdigest(), "observation": observation})


def _private_ipv4(value):
    ip = ipaddress.ip_address(value)
    if (ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_unspecified or ip.is_reserved):
        raise ValueError("an exact private fixture IPv4 address is required")
    return str(ip)


def helm_values(run_id, replicas, database_ip, authority_ip, image_ref, *, a011=False):
    """Prepare public values only; IPs and image must be attested before use."""
    namespace_for(run_id)
    if type(a011) is not bool:
        raise ValueError("a011 mode must be boolean")
    if not isinstance(replicas, tuple) or replicas not in REPLICAS:
        raise ValueError("unsupported replica comparison")
    database_ip, authority_ip = _private_ipv4(database_ip), _private_ipv4(authority_ip)
    if database_ip == authority_ip:
        raise ValueError("database and authority must be independent")
    digest = manifest_digest(image_ref)
    return {
        "syntheticBenchmark": {"enabled": True},
        "image": {"repository": IMAGE_REPOSITORY, "digest": digest, "pullPolicy": "Never"},
        "trustManifest": {"configMapName": "analytics-trust-manifest"},
        "fixtureCatalog": {"configMapName": "analytics-fixture-catalog"},
        "privacyAuthority": {"secretName": "analytics-privacy-authority"},
        "activityProjection": {"enabled": True, "runId": str(run_id)},
        "activityDay": {"enabled": a011},
        "roles": {"collector": {"replicas": replicas[0]},
                  "processor": {"replicas": replicas[1]},
                  "query": {"replicas": replicas[2]},
                  "privacy": {"enabled": True, "replicas": 1}},
        "network": {"databaseCIDR": database_ip + "/32", "databasePort": 5432,
                    "authorityCIDR": authority_ip + "/32", "authorityPort": 57420},
        "ingress": {"enabled": False},
        "migration": {"enabled": False, "revision": "a011" if a011 else "a010"},
    }


def authority_configs(run_id, keys, reader_secret):
    """Build fresh synthetic authority configs; returned client data is secret.

    The caller must create these inputs exclusively for this run, keep private
    keys/reader secret private and persist an independently owned journal.
    """
    namespace_for(run_id)
    if not isinstance(keys, dict) or set(keys) != {"grant", "producer", "command", "authority"}:
        raise ValueError("four independent synthetic public keys required")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        public = [serialization.load_pem_public_key(keys[name].encode("ascii"))
                  for name in ("grant", "producer", "command", "authority")]
        if any(not isinstance(key, Ed25519PublicKey) for key in public):
            raise ValueError
        raw = [key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw) for key in public]
        if len(set(raw)) != 4:
            raise ValueError
    except (AttributeError, TypeError, UnicodeError, ValueError):
        raise ValueError("invalid or reused synthetic public keys") from None
    issuer = "bench-" + run_id.hex
    authority_id = issuer
    client = {"url": "http://privacy-authority:57420", "authority_id": authority_id,
              "issuer": issuer + "-authority", "public_keys": {"bench-a": keys["authority"]},
              "reader_secret": reader_secret, "fixture_mode": True}
    service = str(Path(__file__).resolve().parents[1])
    if service not in sys.path:
        sys.path.insert(0, service)
    from pymentor_analytics.adapters.privacy_authority.client import AuthorityConfig
    AuthorityConfig.model_validate(client)
    server = {"fixture_mode": True, "bind_host": "0.0.0.0", "port": 57420,
              "issuer": issuer + "-authority", "kid": "bench-a",
              "signing_key_file": "/app/.local/config/authority.key",
              "producer_issuer": issuer + "-control", "producer_public_keys": {"bench-p1": keys["producer"]},
              "allowed_producers": ["bench-producer"],
              "producer_command_public_keys": {"bench-producer": keys["command"]},
              "reader_secret_file": "/app/.local/config/reader.secret"}
    return client, server


def authority_resources(run_id, image_ref, storage_class):
    """Build public objects for an independently retained per-run journal.

    These are not admission proof: caller must attest the image, StorageClass,
    resulting PVC/PV UIDs, provisioner/reclaim policy, namespace and ownership.
    An init Job is a one-shot operation and must never be blindly retried.
    """
    namespace = namespace_for(run_id)
    if storage_class != "local-path":
        raise ValueError("only the attested local-path class is supported")
    manifest_digest(image_ref)
    authority_id = "bench-" + run_id.hex
    labels = {"app.kubernetes.io/name": "analytics", "app.kubernetes.io/instance": "bench"}
    def metadata(name, role=None):
        return {"name": name, "namespace": namespace,
                "labels": {**labels, **({"app.kubernetes.io/component": role} if role else {})}}
    security = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                "capabilities": {"drop": ["ALL"]}}
    pod_security = {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001,
                    "fsGroup": 10001, "seccompProfile": {"type": "RuntimeDefault"}}
    resources = {"requests": {"cpu": "50m", "memory": "64Mi"},
                 "limits": {"cpu": "250m", "memory": "128Mi"}}
    image = image_ref
    volume = {"name": "journal", "persistentVolumeClaim": {"claimName": "bench-authority-journal"}}
    mount = {"name": "journal", "mountPath": "/app/.local/data"}
    common = {"image": image, "imagePullPolicy": "Never", "resources": resources, "securityContext": security}
    pod_common = {"automountServiceAccountToken": False, "securityContext": pod_security}
    pvc = {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": metadata("bench-authority-journal"),
           "spec": {"accessModes": ["ReadWriteOnce"], "storageClassName": storage_class,
                    "resources": {"requests": {"storage": "128Mi"}}}}
    # WaitForFirstConsumer may defer binding until a Pod is scheduled. This
    # Pod only mounts the claim read-only and does not initialize the journal;
    # the bound PV must be attested before creating the one-shot init Job.
    binder = {"apiVersion": "v1", "kind": "Pod", "metadata": metadata("bench-journal-binder", "volume-probe"),
              "spec": {**pod_common, "restartPolicy": "Never", "volumes": [volume],
                       "containers": [{**common, "name": "volume-probe", "command": ["python", "-B", "-c",
                                           "import time; time.sleep(3600)"],
                                       "volumeMounts": [{**mount, "readOnly": True}]}]}}
    init = {"apiVersion": "batch/v1", "kind": "Job", "metadata": metadata("bench-journal-init", "journal-init"),
            "spec": {"backoffLimit": 0, "template": {"metadata": {"labels": metadata("ignored", "journal-init")["labels"]},
                     "spec": {**pod_common, "restartPolicy": "Never", "volumes": [volume],
                              "containers": [{**common, "name": "journal-init", "command": ["python", "-B", "-m",
                                  "pymentor_analytics.adapters.privacy_authority.server", "init", "--journal",
                                  "/app/.local/data/journal.sqlite", "--authority-id", authority_id],
                                  "volumeMounts": [mount]}]}}}}
    deployment = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": metadata("bench-authority", "authority"),
                  "spec": {"replicas": 1, "strategy": {"type": "Recreate"},
                           "selector": {"matchLabels": metadata("ignored", "authority")["labels"]},
                           "template": {"metadata": {"labels": metadata("ignored", "authority")["labels"]},
                                        "spec": {**pod_common, "volumes": [volume, {"name": "config", "secret": {
                                            "secretName": "bench-authority-config", "defaultMode": 288}}],
                                                 "containers": [{**common, "name": "authority", "command": ["python", "-B", "-m",
                                                     "pymentor_analytics.adapters.privacy_authority.server", "serve",
                                                     "--private-fixture-network", "--journal", "/app/.local/data/journal.sqlite",
                                                     "--authority-id", authority_id, "--config", "/app/.local/config/server.json"],
                                                     "ports": [{"containerPort": 57420, "name": "http"}],
                                                     "volumeMounts": [mount, {"name": "config", "mountPath": "/app/.local/config", "readOnly": True}]}]}}}}
    service = {"apiVersion": "v1", "kind": "Service", "metadata": metadata("privacy-authority"),
               "spec": {"selector": metadata("ignored", "authority")["labels"],
                        "ports": [{"name": "http", "port": 57420, "targetPort": "http"}]}}
    policy = {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": metadata("bench-authority-ingress"),
              "spec": {"podSelector": {"matchLabels": metadata("ignored", "authority")["labels"]}, "policyTypes": ["Ingress", "Egress"],
                       "ingress": [{"from": [{"podSelector": {"matchLabels": labels,
                                   "matchExpressions": [{"key": "app.kubernetes.io/component", "operator": "In",
                                                         "values": ["collector", "processor", "query", "privacy"]}]}}],
                                   "ports": [{"protocol": "TCP", "port": 57420}]}], "egress": []}}
    return {"pvc": pvc, "bind_pod": binder, "init_job": init, "deployment": deployment, "service": service, "network_policy": policy}


def attest_journal_storage(run_id, storage_class, pvc, pv, expected_class_uid, expected_pvc_uid, *, expected_node_name=None):
    """Check observed binding and return public ownership/reclaim information.

    The caller must read these objects from the attested API and save this
    result before using the volume. A Delete reclaim policy is not permission
    to delete the claim; claims and PVs are always retained by this harness.
    """
    namespace = namespace_for(run_id)
    try:
        for value in (expected_class_uid, expected_pvc_uid, pv["metadata"]["uid"]):
            if str(UUID(value)) != value:
                raise ValueError
        sc, claim, volume = storage_class, pvc, pv
        ref = volume["spec"]["claimRef"]
        reclaim = volume["spec"]["persistentVolumeReclaimPolicy"]
        expected_name = "pvc-" + expected_pvc_uid
        expected_path = "/var/lib/rancher/k3s/storage/" + expected_name + "_" + namespace + "_bench-authority-journal"
        host_path = volume["spec"].get("hostPath")
        local_path = volume["spec"].get("local")
        if host_path is not None and local_path is not None:
            raise ValueError
        if local_path is not None:
            expected_affinity = {"required": {"nodeSelectorTerms": [{"matchExpressions": [
                {"key": "kubernetes.io/hostname", "operator": "In", "values": [expected_node_name]}]}]}}
            if (not isinstance(expected_node_name, str)
                    or not re.fullmatch(r"k3d-knowi-analytics-01a1df75-v2-server-0", expected_node_name)
                    or local_path != {"path": expected_path}
                    or volume["spec"].get("nodeAffinity") != expected_affinity
                    or volume["metadata"].get("annotations", {}).get("local.path.provisioner/selected-node") != expected_node_name):
                raise ValueError
        elif not isinstance(host_path, dict) or host_path.get("path") != expected_path:
            raise ValueError
        if (sc["metadata"]["name"] != "local-path" or sc["metadata"]["uid"] != expected_class_uid
                or sc["provisioner"] != "rancher.io/local-path"
                or sc.get("volumeBindingMode") != "WaitForFirstConsumer"
                or claim["metadata"]["name"] != "bench-authority-journal"
                or claim["metadata"]["namespace"] != namespace or claim["metadata"]["uid"] != expected_pvc_uid
                or claim["metadata"].get("labels", {}).get("app.kubernetes.io/instance") != "bench"
                or claim["spec"]["storageClassName"] != "local-path" or claim["status"]["phase"] != "Bound"
                or claim["spec"]["accessModes"] != ["ReadWriteOnce"]
                or claim["spec"]["resources"]["requests"]["storage"] != "128Mi"
                or claim["spec"]["volumeName"] != expected_name or volume["metadata"]["name"] != expected_name
                or volume["metadata"].get("annotations", {}).get("pv.kubernetes.io/provisioned-by") != "rancher.io/local-path"
                or volume["spec"]["storageClassName"] != "local-path"
                or ref["uid"] != expected_pvc_uid or ref["namespace"] != namespace
                or ref["name"] != "bench-authority-journal" or volume["status"]["phase"] != "Bound"
                or reclaim not in {"Delete", "Retain"}):
            raise ValueError
        return {"storage_class_uid": expected_class_uid, "pvc_uid": expected_pvc_uid,
                "pv_uid": volume["metadata"]["uid"], "pv_name": volume["metadata"]["name"],
                "backing_path": expected_path, "reclaim_policy": reclaim, "automatic_deletion": False}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("journal storage identity unavailable; preserve and reconcile") from None


def pod_inventory(document, namespace, replicas, expected_image_id, expected_image_ref=None):
    """Validate a complete per-role pod snapshot and return non-secret metadata.

    A caller must also establish the cluster identity, source-of-truth release,
    and verify each pod UID again before using a port-forward. A snapshot alone
    is not an admission token or proof that a pod will remain ready.
    """
    if not re.fullmatch(r"knowi-analytics-bench-[0-9a-f]{32}", namespace):
        raise ValueError("invalid namespace")
    if not isinstance(replicas, tuple) or replicas not in REPLICAS:
        raise ValueError("invalid replica comparison")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_image_id):
        raise ValueError("image digest required")
    counts = dict(zip(("collector", "processor", "query"), replicas))
    counts["privacy"] = 1
    found = {role: [] for role in counts}
    names, uids = set(), set()
    try:
        items = document["items"]
        if not isinstance(items, list):
            raise ValueError
        for pod in items:
            meta, status, spec = pod["metadata"], pod["status"], pod["spec"]
            labels = meta["labels"]
            role = labels["app.kubernetes.io/component"]
            if (meta["namespace"] != namespace or labels["app.kubernetes.io/name"] != "analytics"
                    or labels["app.kubernetes.io/instance"] != "bench" or role not in found
                    or meta.get("deletionTimestamp") is not None or status["phase"] != "Running"):
                raise ValueError
            name, uid = meta["name"], meta["uid"]
            if (not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", name)
                    or str(UUID(uid)) != uid or name in names or uid in uids):
                raise ValueError
            containers, states = spec["containers"], status["containerStatuses"]
            if len(containers) != 1 or len(states) != 1 or containers[0]["name"] != role:
                raise ValueError
            state = states[0]
            from run_k3s import benchmark_image_identity_matches
            if (state["name"] != role or state["ready"] is not True or state["restartCount"] != 0
                    or (expected_image_ref is not None and containers[0].get("image") != expected_image_ref)
                    or not benchmark_image_identity_matches(state, expected_image_ref, expected_image_id)):
                raise ValueError
            conditions = status["conditions"]
            if sum(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions) != 1:
                raise ValueError
            names.add(name); uids.add(uid)
            found[role].append({"name": name, "uid": uid, "image_id": state["imageID"],
                                "resources": containers[0].get("resources", {}), "restarts": 0})
        if any(len(found[role]) != count for role, count in counts.items()):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("pod identity, readiness, image or replica count mismatch") from None
    return {role: sorted(pods, key=lambda p: p["name"]) for role, pods in found.items()}


def start_pod_forward(owned, kubectl, kubeconfig, context, namespace, pod, folder,
                      token, job, children, read_pod):
    """Start an owned loopback forward to one already attested Pod UID.

    The caller must first verify the executable, exact API/container identity,
    private kubeconfig, namespace ownership and complete pod inventory. This
    helper must only receive a read_pod function tied to that same identity.
    Unknown results are left to the caller's operation ledger and reconciliation.
    """
    if (not re.fullmatch(r"knowi-analytics-bench-[0-9a-f]{32}", namespace)
            or context != "k3d-knowi-analytics-01a1df75-v2"):
        raise ValueError("wrong local K3s target")
    name, uid = pod["name"], pod["uid"]
    if (not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", name)
            or str(UUID(uid)) != uid):
        raise ValueError("invalid pod identity")
    def check_uid():
        current = read_pod(name)
        if (current.get("metadata", {}).get("uid") != uid
                or current.get("metadata", {}).get("namespace") != namespace
                or current.get("metadata", {}).get("deletionTimestamp") is not None):
            raise ValueError("pod identity changed; reconcile")
    check_uid()
    port = owned.unused_port()
    command = [str(kubectl), "--kubeconfig", str(kubeconfig), "--context", context,
               "--namespace", namespace, "port-forward", "--address", "127.0.0.1",
               "pod/" + name, str(port) + ":8000"]
    def record(processes):
        owned.record(folder, token, state="k3s_forwards_starting", processes=[p.pid for p in processes])
    process = owned.spawn_owned(command, cwd=folder, env=owned_forward_env(), folder=folder,
                                label="port-forward-" + uid, children=children, job=job, on_spawn=record)
    owned.wait_port(process, port)
    job.assert_listener(port)
    check_uid()
    return {"url": "http://127.0.0.1:" + str(port), "pod_uid": uid, "process": process}


def owned_forward_env():
    """Keep kubeconfig and Docker environment overrides out of a forward."""
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"}
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


async def run_http_workload(benchmark, client, processes, profile, stage, run_id, key,
                            issuer, fingerprint, monitor, projection_wait, *, stats=None, metrics_sampler=None):
    """Use the shared real HTTP workload/oracle once a future caller admits a fixture.

    This adapter does not provision or certify the fixture and is not exposed
    by the CLI. Its caller owns cluster/DB/journal identity and port-forwarding.
    """
    namespace_for(run_id)
    if stage not in STAGES or profile.get("seed") != SEED:
        raise ValueError("profile or stage mismatch")
    if profile != benchmark.profile_load(benchmark.ROOT / "bench/profile-v1.json"):
        raise ValueError("profile does not match the frozen input")
    if monitor is None or projection_wait is None:
        raise ValueError("monitoring and projection drain are required")
    if metrics_sampler is None:
        metrics_sampler = lambda: benchmark.sample_metrics(client, processes)
    return await benchmark.execute_workload(
        client, processes, profile, stage, SEED, run_id, key, issuer, fingerprint,
        monitor=monitor, metrics_sampler=metrics_sampler, stats=stats, projection_wait=projection_wait)


class LiveFailure(RuntimeError):
    """A stopped attempt; resources remain owned for reconciliation."""


def _digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _binary(path, expected):
    path = Path(path)
    if (not path.is_absolute() or path.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(path))
            or not path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected)
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
        raise LiveFailure("executable identity mismatch")
    return path


class Kube:
    """Explicit private target; no mutation retries or secret-bearing argv."""
    def __init__(self, binary, kubeconfig, context, namespace):
        self.namespace = namespace
        self.base = [str(binary), "--kubeconfig", str(kubeconfig), "--context", context, "--namespace", namespace]
        self.proxy = None

    def call(self, args, *, payload=None, timeout=20):
        try:
            result = subprocess.run(self.base + args, input=payload, text=True, capture_output=True,
                                    timeout=timeout, env=owned_forward_env(), check=False,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            raise LiveFailure("Kubernetes command outcome unknown; reconcile") from None
        if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
            raise LiveFailure("Kubernetes command failed; reconcile if mutation was issued")
        return result.stdout

    def get(self, kind, name=None, *, selector=None, ignore_missing=False):
        args = ["get", kind]
        if name is not None: args.append(name)
        if selector is not None: args += ["-l", selector]
        if ignore_missing: args.append("--ignore-not-found")
        raw = self.call(args + ["-o", "json"])
        return None if not raw.strip() else json.loads(raw)

    def create(self, obj):
        raw = self.call(["create", "-f", "-", "-o", "json"], payload=json.dumps(obj, separators=(",", ":")))
        result = json.loads(raw)
        uid = result["metadata"]["uid"]
        if str(UUID(uid)) != uid:
            raise LiveFailure("created resource identity unavailable")
        return uid

    def delete_uid(self, kind, name, uid):
        if (kind not in {"pods", "jobs"} or not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", name)
                or str(UUID(uid)) != uid):
            raise LiveFailure("invalid owned deletion target")
        if self.proxy is None or self.proxy["process"].poll() is not None:
            raise LiveFailure("owned Kubernetes proxy unavailable")
        self.proxy["job"].assert_listener(self.proxy["port"])
        api = "apis/batch/v1" if kind == "jobs" else "api/v1"
        path = "/" + api + "/namespaces/" + self.namespace + "/" + kind + "/" + name
        options = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": uid},
                   "propagationPolicy": "Foreground"}
        import httpx
        try:
            with httpx.Client(timeout=10, trust_env=False) as client:
                response = client.request("DELETE", "http://127.0.0.1:" + str(self.proxy["port"]) + path, json=options)
        except httpx.HTTPError:
            raise LiveFailure("Kubernetes delete outcome unknown; reconcile") from None
        if response.status_code not in (200, 202):
            raise LiveFailure("owned deletion not confirmed; reconcile")


def start_kube_proxy(owned, kube, folder, token, job, children):
    """Own and verify the local authenticated proxy used for UID-safe deletes."""
    import httpx
    port = owned.unused_port()
    command = kube.base + ["proxy", "--address", "127.0.0.1", "--port", str(port)]
    def record(processes):
        owned.record(folder, token, state="k3s_proxy_starting", processes=[p.pid for p in processes])
    process = owned.spawn_owned(command, cwd=folder, env=owned_forward_env(), folder=folder,
                                label="kubectl-proxy", children=children, job=job, on_spawn=record)
    owned.wait_port(process, port)
    job.assert_listener(port)
    try:
        with httpx.Client(timeout=5, trust_env=False) as client:
            response = client.get("http://127.0.0.1:" + str(port) + "/readyz")
            if response.status_code != 200 or response.text.strip() != "ok":
                raise LiveFailure("Kubernetes proxy identity unavailable")
    except httpx.HTTPError:
        raise LiveFailure("Kubernetes proxy identity unavailable") from None
    kube.proxy = {"process": process, "port": port, "job": job}


def _delete_recorded(kube, folder, run_id, operation, kind, name, uid):
    current = kube.get(kind, name, ignore_missing=True)
    if current is None or current["metadata"]["uid"] != uid:
        raise LiveFailure("owned delete target changed; reconcile")
    identity = _digest({"namespace": kube.namespace, "kind": kind, "name": name, "uid": uid})
    mark_pending(folder, run_id, operation, identity)
    kube.delete_uid(kind, name, uid)
    _wait_read(lambda: kube.get(kind, name, ignore_missing=True) is None, timeout=60)
    mark_confirmed(folder, run_id, operation, identity, _digest({"absent_uid": uid}))


def _wait_read(function, *, timeout, interval=1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = function()
        if value is not None and value is not False:
            return value
        time.sleep(interval)
    raise LiveFailure("readiness deadline expired; reconcile")


def _metadata_object(kind, namespace, name, data):
    return {"apiVersion": "v1", "kind": kind,
            "metadata": {"name": name, "namespace": namespace,
                         "labels": {"app.kubernetes.io/name": "analytics", "app.kubernetes.io/instance": "bench"}},
            ("stringData" if kind == "Secret" else "data"): data}


def _save_result(path, run_id, value):
    current = json.loads(path.read_text(encoding="utf-8"))
    if current.get("run_id") != str(run_id):
        raise LiveFailure("result ownership changed")
    temp = path.with_name(path.name + "." + uuid4().hex + ".pending")
    _write_exclusive(temp, value)
    os.replace(temp, path)


def _create_recorded(kube, folder, run_id, operation, obj):
    """Persist intent before a single create and confirm the observed UID."""
    meta = obj["metadata"]
    kind, name = obj["kind"], meta["name"]
    identity = _digest({"namespace": kube.namespace, "kind": kind, "name": name})
    mark_pending(folder, run_id, operation, identity)
    uid = kube.create(obj)
    observed = kube.get(kind.lower(), name)
    if (observed is None or observed["metadata"]["uid"] != uid
            or (kind != "Namespace" and observed["metadata"]["namespace"] != kube.namespace)):
        raise LiveFailure("created resource not reconciled")
    mark_confirmed(folder, run_id, operation, identity, _digest({"uid": uid}))
    return uid


def _assert_target(args, runtime, kube, fixture):
    """Reattest both cluster identity and the measured shared resource budget."""
    options = {"authority_image_id": args.retained_authority_image_id,
               "benchmark_fixture": fixture, "web_temporarily_paused": args.web_temporarily_paused}
    if getattr(args, "retained_authority_stopped_id", None) is not None:
        options["expected_stopped_authority_id"] = args.retained_authority_stopped_id
    snapshot = runtime.require_shared_local_budget(args.kubectl, args.kubectl_sha256, args.k3s_image_id, **options)
    context = runtime.resource_snapshot.local_context(dict(os.environ))
    data = json.loads(runtime.run(["docker", "--context", context, "inspect", "--type", "container", runtime.SERVER]).stdout)
    runtime.validate_server(data, args.k3s_image_id)
    if len(data) != 1 or data[0].get("Id") != args.server_id:
        raise LiveFailure("K3s server identity changed")
    system = kube.get("namespace", "kube-system")
    if system["metadata"]["uid"] != args.cluster_uid:
        raise LiveFailure("Kubernetes cluster identity changed")
    return snapshot


def _expected_pods(kube, run_id, image_ref, image_config_id, authority_ref, authority_config_id,
                   role_counts, owner_uids, *, auxiliary=None):
    """Build and validate an exact live UID allowlist from this run's owners."""
    document = kube.get("pods", selector="app.kubernetes.io/instance=bench")
    expected_counts = {**role_counts, **(auxiliary or {})}
    counts = {role: 0 for role in expected_counts}
    found = {}
    auth_resources = authority_resources(run_id, authority_ref, "local-path")
    auth_limits = auth_resources["bind_pod"]["spec"]["containers"][0]["resources"]
    chart_resources = {"requests": {"cpu": "50m", "memory": "64Mi"},
                       "limits": {"cpu": "500m", "memory": "256Mi"}}
    privacy_resources = {"requests": {"cpu": "50m", "memory": "64Mi"},
                         "limits": {"cpu": "500m", "memory": "128Mi"}}
    for pod in document["items"]:
        meta, spec, status = pod["metadata"], pod["spec"], pod["status"]
        role = meta.get("labels", {}).get("app.kubernetes.io/component")
        if role not in expected_counts:
            raise LiveFailure("unrecorded benchmark Pod")
        counts[role] += 1
        if counts[role] > expected_counts[role]:
            raise LiveFailure("unexpected replica or stale Pod")
        uid, name = meta["uid"], meta["name"]
        if role == "volume-probe":
            if name != "bench-journal-binder" or uid != owner_uids["binder"] or meta.get("ownerReferences"):
                raise LiveFailure("binder Pod identity mismatch")
        elif role == "journal-init":
            owners = meta.get("ownerReferences", [])
            if (len(owners) != 1 or owners[0].get("kind") != "Job" or owners[0].get("uid") != owner_uids["init_job"]
                    or owners[0].get("name") != "bench-journal-init" or owners[0].get("controller") is not True):
                raise LiveFailure("journal Job Pod identity mismatch")
        else:
            deployment_name = "bench-authority" if role == "authority" else "bench-analytics-" + role
            refs = meta.get("ownerReferences", [])
            if len(refs) != 1 or refs[0].get("kind") != "ReplicaSet" or refs[0].get("controller") is not True:
                raise LiveFailure("Pod controller identity mismatch")
            rs = kube.get("replicaset", refs[0]["name"])
            parents = rs["metadata"].get("ownerReferences", [])
            if (rs["metadata"]["uid"] != refs[0]["uid"] or len(parents) != 1
                    or parents[0].get("kind") != "Deployment" or parents[0].get("name") != deployment_name
                    or parents[0].get("uid") != owner_uids[role] or parents[0].get("controller") is not True):
                raise LiveFailure("Pod Deployment identity mismatch")
        is_authority = role in {"authority", "volume-probe", "journal-init"}
        image = authority_ref if is_authority else image_ref
        digest = authority_config_id if is_authority else image_config_id
        resources = auth_limits if is_authority else privacy_resources if role == "privacy" else chart_resources
        expected = {"name": name, "role": role, "image": image, "image_id": digest, "resources": resources}
        phase = status.get("phase")
        if phase == "Pending" or (role == "journal-init" and phase == "Running"):
            return None
        if phase != ("Succeeded" if role == "journal-init" else "Running"):
            raise LiveFailure("benchmark Pod terminated or failed")
        statuses = status.get("containerStatuses", [])
        if not statuses or not statuses[0].get("imageID"):
            return None
        from run_k3s import validate_benchmark_fixture
        candidate = {"namespace": kube.namespace, "pods": {uid: expected}}
        validate_benchmark_fixture(pod, candidate)
        found[uid] = expected
    if counts != expected_counts:
        return None
    return {"namespace": kube.namespace, "pods": found}


def _helm_recorded(args, kube, folder, run_id, operation, action, values=None):
    if action not in {"install", "upgrade"}:
        raise ValueError("unsupported Helm action")
    identity = _digest({"namespace": kube.namespace, "release": "bench", "action": action,
                        "values": values})
    mark_pending(folder, run_id, operation, identity)
    root = Path(__file__).resolve().parents[3]
    command = [str(args.helm), "--kubeconfig", str(args.kubeconfig), "--kube-context", args.context,
               "--namespace", kube.namespace, action, "bench"]
    command += [str(root / "infra/helm/analytics"), "--values", "-", "--wait", "--timeout", "120s"]
    payload = json.dumps(values, separators=(",", ":"))
    try:
        result = subprocess.run(command, input=payload, text=True, capture_output=True, timeout=135,
                                env=owned_forward_env(), check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        raise LiveFailure("Helm outcome unknown; reconcile") from None
    if result.returncode:
        raise LiveFailure("Helm did not complete; reconcile release before retry")
    # Helm's success is supplemented with the caller's subsequent Pod and
    # Deployment UID/readiness checks. Never include command output in results.
    mark_confirmed(folder, run_id, operation, identity, _digest({"exit": result.returncode}))


class K3sMonitor:
    def __init__(self, args, runtime, kube, profile_monitor, fixture, snapshot_fixture, forwards, job):
        self.args, self.runtime, self.kube = args, runtime, kube
        self.profile_monitor, self.fixture, self.snapshot_fixture = profile_monitor, fixture, snapshot_fixture
        self.forwards, self.job, self.samples = forwards, job, []

    def check(self):
        self.profile_monitor.check()
        current = self.snapshot_fixture()
        if current != self.fixture:
            raise LiveFailure("Pod identity or resources changed; reconcile")
        for forward in self.forwards:
            if forward["process"].poll() is not None:
                raise LiveFailure("owned Pod forward exited")
            self.job.assert_listener(int(forward["url"].rsplit(":", 1)[1]))
        snapshot = _assert_target(self.args, self.runtime, self.kube, self.fixture)
        self.samples.append({"at": datetime.now(timezone.utc).isoformat(), **snapshot})

    def result(self):
        return {"host": self.profile_monitor.result(), "shared_budget_samples": self.samples}


def _kube_observations(kube):
    """Read actual per-Pod usage and a bounded non-secret event summary."""
    top = kube.call(["top", "pods", "--no-headers"])
    rows = []
    for line in top.splitlines():
        fields = line.split()
        if (len(fields) != 3 or not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", fields[0])
                or not re.fullmatch(r"[0-9]+m", fields[1]) or not re.fullmatch(r"[0-9]+Mi", fields[2])):
            raise LiveFailure("Pod resource metrics unavailable")
        rows.append({"pod": fields[0], "cpu": fields[1], "memory": fields[2]})
    if not rows: raise LiveFailure("Pod resource metrics unavailable")
    events = kube.get("events")
    if len(events.get("items", [])) > 1000: raise LiveFailure("event sample exceeds bound")
    reasons = {}
    for event in events.get("items", []):
        reason = event.get("reason", "unknown")
        if not isinstance(reason, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", reason):
            reason = "unknown"
        reasons[reason] = reasons.get(reason, 0) + 1
    return {"at": datetime.now(timezone.utc).isoformat(), "top": rows, "event_reasons": reasons}


def _helm_deployment_uids(kube, roles):
    result = {}
    for role in roles:
        obj = kube.get("deployment", "bench-analytics-" + role)
        uid = obj["metadata"]["uid"]
        if str(UUID(uid)) != uid:
            raise LiveFailure("Helm Deployment UID unavailable")
        result[role] = uid
    return result


def _fixture_wait(kube, run_id, args, counts, owners, auxiliary):
    return _wait_read(lambda: _expected_pods(kube, run_id, args.image_ref, args.image_config_id,
                                            args.authority_ref, args.authority_config_id,
                                            counts, owners, auxiliary=auxiliary), timeout=120)


def _authority_setup(kube, folder, run_id, args, keys, reader, guard, image_guard):
    objects = authority_resources(run_id, args.authority_ref, "local-path")
    client, server = authority_configs(run_id, keys, reader)
    authority_secret = _metadata_object("Secret", kube.namespace, "bench-authority-config", {
        "server.json": json.dumps(server, separators=(",", ":")),
        "authority.key": (folder / "authority.private.pem").read_text(encoding="ascii"),
        "reader.secret": reader})
    _create_recorded(kube, folder, run_id, "authority-config", authority_secret)
    guard({"namespace": kube.namespace, "pods": {}})
    pvc_uid = _create_recorded(kube, folder, run_id, "journal-pvc", objects["pvc"])
    guard({"namespace": kube.namespace, "pods": {}})
    _assert_fresh_journal_path(args, kube.namespace, pvc_uid)
    image_guard()
    binder_uid = _create_recorded(kube, folder, run_id, "journal-binder", objects["bind_pod"])
    owners = {"binder": binder_uid}
    aux = {"volume-probe": 1}
    fixture = _fixture_wait(kube, run_id, args, {}, owners, aux)
    guard(fixture)
    claim = _wait_read(lambda: (p if (p := kube.get("pvc", "bench-authority-journal")).get("status", {}).get("phase") == "Bound" else None), timeout=90)
    pv = kube.get("pv", claim["spec"]["volumeName"])
    storage = kube.get("storageclass", "local-path")
    import run_k3s as runtime
    info = attest_journal_storage(run_id, storage, claim, pv, args.storage_class_uid, pvc_uid,
                                  expected_node_name=runtime.SERVER)
    _write_exclusive(folder / "journal-storage.json", info)
    guard(fixture)
    image_guard()
    owners["init_job"] = _create_recorded(kube, folder, run_id, "journal-initialize", objects["init_job"])
    aux["journal-init"] = 1
    fixture = _fixture_wait(kube, run_id, args, {}, owners, aux)
    guard(fixture)
    for operation, name in (("authority-service", "service"), ("authority-policy", "network_policy")):
        _create_recorded(kube, folder, run_id, operation, objects[name])
        guard(fixture)
    image_guard()
    owners["authority"] = _create_recorded(kube, folder, run_id, "authority-deployment", objects["deployment"])
    aux["authority"] = 1
    fixture = _fixture_wait(kube, run_id, args, {}, owners, aux)
    guard(fixture)
    authority_pods = [p for p in kube.get("pods", selector="app.kubernetes.io/component=authority")["items"]]
    if len(authority_pods) != 1 or authority_pods[0]["metadata"]["uid"] not in fixture["pods"]:
        raise LiveFailure("authority endpoint identity unavailable")
    authority_ip = _private_ipv4(authority_pods[0]["status"]["podIP"])
    return client, owners, aux, fixture, authority_ip, info


def _assert_fresh_journal_path(args, namespace, pvc_uid):
    """Refuse any existing node directory or symlink before provisioning."""
    import run_k3s as runtime
    if str(UUID(pvc_uid)) != pvc_uid:
        raise LiveFailure("PVC identity unavailable")
    expected = "/var/lib/rancher/k3s/storage/pvc-" + pvc_uid + "_" + namespace + "_bench-authority-journal"
    context = runtime.resource_snapshot.local_context(dict(os.environ))
    prefix = ["docker", "--context", context, "exec", args.server_id, "test"]
    for option in ("-e", "-L"):
        result = runtime.run(prefix + [option, expected], allow_missing=True)
        if result.returncode != 1:
            raise LiveFailure("journal backing path is not provably fresh")


def _configure_workloads(kube, folder, run_id, args, b, urls, keys, reader, authority, target, db_ip, guard):
    stream = b.stream_plan(b.profile_load(b.ROOT / "bench/profile-v1.json"), args.stage, SEED, run_id)
    key, issuer, manifest, catalog, fingerprint = b.keys_and_catalog(run_id, stream["courses"], keys, folder)
    b.validate_authority_key_separation(b.Settings(database_url=urls["collector"],
                                        trust_manifest_json=json.dumps(manifest)), authority["public_keys"])
    mapped = __import__("k3s_database")._mapped_urls(urls, db_ip)
    objects = [
        ("trust-manifest", _metadata_object("ConfigMap", kube.namespace, "analytics-trust-manifest",
                            {"TRUST_MANIFEST_JSON": json.dumps(manifest, separators=(",", ":"))})),
        ("fixture-catalog", _metadata_object("ConfigMap", kube.namespace, "analytics-fixture-catalog",
                             {"FIXTURE_CATALOG_JSON": json.dumps(catalog, separators=(",", ":"))})),
        ("authority-client", _metadata_object("Secret", kube.namespace, "analytics-privacy-authority",
                              {"PRIVACY_AUTHORITY_JSON": json.dumps(authority, separators=(",", ":"))})),
    ]
    for role in ("collector", "processor", "query", "privacy"):
        objects.append((role + "-db-secret", _metadata_object("Secret", kube.namespace, "analytics-" + role + "-db",
                                                              {"DATABASE_URL": mapped[role]})))
    for operation, obj in objects:
        guard()
        _create_recorded(kube, folder, run_id, operation, obj)
    return key, issuer, fingerprint


def _source_fingerprint(service):
    """Digest the actual application, migration, chart, harness and oracle inputs."""
    workspace = service.parents[1]
    roots = [service / "pymentor_analytics", service / "migrations", workspace / "infra/helm/analytics"]
    files = [service / "Dockerfile", service / "alembic.ini", service / "requirements-runtime.txt",
             service / "reference/activity_v1.py", service / "tools/benchmark_activity.py",
             service / "tools/run_k3s.py", service / "tools/k3s_database.py",
             service / "tools/k3s_authority.py", service / "tools/resource_snapshot.py",
             service / "tools/bootstrap_postgres.py", service / "tools/bootstrap_runtime.py",
             service / "tools/prepare_k3s_image.py",
             service / "tools/check_k3s.py", service / "tools/run_local.py", service / "tools/test_cross_service.py",
             service / "tests/cross_service/resources.py", service / "tests/cross_service/guards.py",
             Path(__file__).resolve()]
    for root in roots:
        for path in root.rglob("*"):
            if path.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(path)):
                raise LiveFailure("linked source path refused")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
                files.append(path)
    digest = hashlib.sha256()
    for path in sorted(set(files), key=lambda p: p.as_posix()):
        if path.is_symlink() or not path.is_file(): raise LiveFailure("source input unavailable")
        relative = path.relative_to(workspace).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big")); digest.update(relative)
        digest.update(len(data).to_bytes(8, "big")); digest.update(data)
    return "sha256:" + digest.hexdigest()


def _bounded_command(command, *, timeout=20, limit=1024 * 1024):
    """Bound output and deadline; never retry a timed-out command."""
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=owned_forward_env(),
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        raise LiveFailure("bounded command unavailable") from None
    timed_out = threading.Event()
    def stop():
        timed_out.set()
        try: process.kill()
        except OSError: pass
    timer = threading.Timer(timeout, stop)
    timer.daemon = True
    timer.start()
    try:
        data = process.stdout.read(limit + 1)
        if len(data) > limit:
            process.kill()
        status = process.wait(timeout=5)
        if timed_out.is_set() or len(data) > limit or status != 0:
            raise LiveFailure("bounded command failed or outcome unknown; reconcile")
        return data
    except (OSError, subprocess.TimeoutExpired):
        try: process.kill()
        except OSError: pass
        raise LiveFailure("bounded command outcome unknown; reconcile") from None
    finally:
        timer.cancel()
        if process.stdout is not None: process.stdout.close()


def verify_node_manifest(ref, config_digest, image_list, get_blob):
    """Verify manifest-ref -> config using exact node content hashes.

    An OCI index is not a manifest and is rejected: a manifest reference binds
    the amd64 content directly and avoids treating Docker's .Id as config ID.
    """
    digest = manifest_digest(ref)
    if not isinstance(config_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", config_digest):
        raise LiveFailure("image config digest required")
    matches = [line.split() for line in image_list.splitlines() if line.split() and line.split()[0] == ref]
    if len(matches) != 1 or len(matches[0]) < 3 or matches[0][2] != digest:
        raise LiveFailure("exact digest reference is absent or changed on node")
    raw_manifest = get_blob(digest)
    if _digest(raw_manifest) != digest:
        raise LiveFailure("node manifest hash mismatch")
    try:
        manifest = json.loads(raw_manifest)
        media = manifest.get("mediaType")
        if (manifest.get("schemaVersion") != 2 or "manifests" in manifest
                or media not in {None, "application/vnd.oci.image.manifest.v1+json",
                                 "application/vnd.docker.distribution.manifest.v2+json"}
                or manifest["config"]["digest"] != config_digest or not isinstance(manifest["layers"], list)):
            raise ValueError
        raw_config = get_blob(config_digest)
        if _digest(raw_config) != config_digest:
            raise ValueError
        config = json.loads(raw_config)
        command = config["config"]
        rootfs = config["rootfs"]
        diff_ids = rootfs["diff_ids"]
        if (config.get("os") != "linux" or config.get("architecture") != "amd64"
                or command.get("Entrypoint") != ["python", "-m", "pymentor_analytics.app.cli"]
                or command.get("WorkingDir") != "/app" or str(command.get("User")) != "10001"
                or command.get("Volumes") not in (None, {}) or rootfs.get("type") != "layers"
                or not isinstance(diff_ids, list) or not 1 <= len(diff_ids) <= 128
                or len(diff_ids) != len(manifest["layers"])
                or any(not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in diff_ids)
                or any(not isinstance(layer, dict) or not isinstance(layer.get("digest"), str)
                       or not re.fullmatch(r"sha256:[0-9a-f]{64}", layer["digest"])
                       or type(layer.get("size")) is not int or layer["size"] <= 0 for layer in manifest["layers"])):
            raise ValueError
        env = command.get("Env") or []
        if (not isinstance(env, list) or any(not isinstance(value, str) for value in env)
                or any(value.split("=", 1)[0] in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT",
                                                   "PYTHONUSERBASE", "PYTHONBREAKPOINT", "LD_PRELOAD", "LD_LIBRARY_PATH"}
                       for value in env)):
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise LiveFailure("manifest/config mapping or application entrypoint invalid") from None
    return {"reference": ref, "manifest_digest": digest, "config_digest": config_digest, "rootfs_diff_ids": diff_ids,
            "platform": "linux/amd64", "index": "NOT_USED"}


def _image_attestation(args, runtime):
    context = runtime.resource_snapshot.local_context(dict(os.environ))
    prefix = ["docker", "--context", context, "exec", args.server_id, "ctr", "--namespace", "k8s.io"]
    image_list = _bounded_command(prefix + ["images", "list"], limit=2 * 1024 * 1024).decode("utf-8")
    def get_blob(digest):
        return _bounded_command(prefix + ["content", "get", digest], limit=1024 * 1024)
    return {"application": verify_node_manifest(args.image_ref, args.image_config_id, image_list, get_blob),
            "authority": verify_node_manifest(args.authority_ref, args.authority_config_id, image_list, get_blob)}


def _archive_name(name):
    if (not isinstance(name, str) or not name or "\\" in name or "\x00" in name or name.startswith("/")):
        raise ValueError("unsafe archive path")
    parts = PurePosixPath(name).parts
    if not parts or ".." in parts or ":" in parts[0]:
        raise ValueError("unsafe archive path")
    return "/".join(parts)


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class _BoundedTarReader:
    """Bound individual metadata reads while letting tarfile seek over layers."""
    def __init__(self, stream): self.stream = stream
    def read(self, size=-1):
        if size < 0 or size > 2 * 1024 * 1024:
            raise ValueError("oversized tar metadata read")
        return self.stream.read(size)
    def seek(self, *args): return self.stream.seek(*args)
    def tell(self): return self.stream.tell()


def verify_host_archive(stream, expected_config, expected_diff_ids, max_bytes):
    """Inspect only Docker-save manifest and referenced config; never extract layers."""
    discover = expected_config is None and expected_diff_ids is None
    if (type(max_bytes) is not int or max_bytes <= 0 or (not discover and (
            not isinstance(expected_config, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_config)
            or not isinstance(expected_diff_ids, list) or not 1 <= len(expected_diff_ids) <= 128
            or any(not isinstance(item, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", item) for item in expected_diff_ids)))):
        raise LiveFailure("invalid archive verification inputs")
    try:
        stream.seek(0, 2)
        size = stream.tell()
        if not 0 < size <= max_bytes: raise ValueError
        stream.seek(0)
        with tarfile.open(fileobj=_BoundedTarReader(stream), mode="r:") as archive:
            entries = {}
            for index, member in enumerate(archive):
                if index >= 20000: raise ValueError
                name = _archive_name(member.name)
                if name in entries or not (member.isfile() or member.isdir()): raise ValueError
                entries[name] = member
            manifest_entry = entries["manifest.json"]
            if not manifest_entry.isfile() or manifest_entry.size > 1024 * 1024: raise ValueError
            manifest = json.load(archive.extractfile(manifest_entry), object_pairs_hook=_unique_json)
            if not isinstance(manifest, list) or len(manifest) != 1 or not isinstance(manifest[0], dict):
                raise ValueError
            item = manifest[0]
            config_name = _archive_name(item["Config"])
            if discover:
                match = re.fullmatch(r"(?:blobs/sha256/)?([0-9a-f]{64})(?:\.json)?", config_name)
                if match is None: raise ValueError
                expected_config = "sha256:" + match.group(1)
            hexadecimal = expected_config.removeprefix("sha256:")
            if config_name not in {hexadecimal + ".json", "blobs/sha256/" + hexadecimal}:
                raise ValueError
            config_entry = entries[config_name]
            if not config_entry.isfile() or config_entry.size > 1024 * 1024: raise ValueError
            raw = archive.extractfile(config_entry).read(1024 * 1024 + 1)
            if len(raw) != config_entry.size or _digest(raw) != expected_config: raise ValueError
            config = json.loads(raw, object_pairs_hook=_unique_json)
            if discover:
                expected_diff_ids = config["rootfs"]["diff_ids"]
                command = config["config"]
                env = command.get("Env") or []
                if (not isinstance(expected_diff_ids, list) or not 1 <= len(expected_diff_ids) <= 128
                        or any(not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in expected_diff_ids)
                        or command.get("Entrypoint") != ["python", "-m", "pymentor_analytics.app.cli"]
                        or command.get("WorkingDir") != "/app" or str(command.get("User")) != "10001"
                        or command.get("Volumes") not in (None, {}) or not isinstance(env, list)
                        or any(not isinstance(value, str) or value.split("=", 1)[0] in
                               {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT", "PYTHONUSERBASE",
                                "PYTHONBREAKPOINT", "LD_PRELOAD", "LD_LIBRARY_PATH"} for value in env)):
                    raise ValueError
            if (config.get("os") != "linux" or config.get("architecture") != "amd64"
                    or config["rootfs"].get("type") != "layers"
                    or config["rootfs"]["diff_ids"] != expected_diff_ids):
                raise ValueError
            layers = item["Layers"]
            if not isinstance(layers, list) or len(layers) != len(expected_diff_ids): raise ValueError
            for layer in layers:
                entry = entries[_archive_name(layer)]
                if not entry.isfile(): raise ValueError
        return {"config_digest": expected_config, "rootfs_diff_ids": expected_diff_ids,
                "archive_bytes": size, "layers_extracted": False}
    except (ValueError, TypeError, KeyError, AttributeError, OSError, EOFError, tarfile.TarError):
        raise LiveFailure("host archive/config binding could not be verified") from None


def packaged_source_fingerprint(root, reject_caches=False):
    """Hash all packaged app and migration files, requirements and Alembic config."""
    import hashlib
    import os
    allowed = {"pymentor_analytics", "migrations", "requirements-runtime.txt", "alembic.ini"}
    if root.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(root)):
        raise ValueError("linked application root")
    if reject_caches and {path.name for path in root.iterdir()} != allowed:
        raise ValueError("unexpected image application root contents")
    files = [root / "requirements-runtime.txt", root / "alembic.ini"]
    for base in (root / "pymentor_analytics", root / "migrations"):
        if base.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(base)) or not base.is_dir():
            raise ValueError("linked or missing application source root")
        for path in base.rglob("*"):
            if path.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(path)):
                raise ValueError("linked application source path")
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                if reject_caches: raise ValueError("cached image bytecode is not attested")
                continue
            if path.is_file(): files.append(path)
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink() or not path.is_file():
            raise ValueError("linked or missing application source file")
        name = path.relative_to(root).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(name).to_bytes(4, "big")); digest.update(name)
        digest.update(len(data).to_bytes(8, "big")); digest.update(data)
    return digest.hexdigest()


def _host_image_proof(folder, token, run_id, name, host_id, node, runtime, owned, monitor, max_mib):
    """Save one pinned host image; optionally bind its selected config to the node."""
    if (name not in {"application", "authority"} or not re.fullmatch(r"sha256:[0-9a-f]{64}", host_id)
            or type(max_mib) is not int or not 128 <= max_mib <= 8192):
        raise LiveFailure("host image ID or archive limit invalid")
    context = runtime.resource_snapshot.local_context(dict(os.environ))
    docker = ["docker", "--context", context]
    observed = _bounded_command(docker + ["image", "inspect", host_id, "--format", "{{.Id}}"], limit=4096).decode("ascii").strip()
    if observed != host_id:
        raise LiveFailure("host immutable image identity mismatch")
    archive = folder / ("host-image-" + name + ".tar")
    node_config = None if node is None else node["config_digest"]
    owned.record(folder, token, state="k3s_image_archive_pending", archive_name=archive.name,
                 host_image_id=host_id, node_config_digest=node_config)
    intent = _digest({"host_image_id": host_id, "archive": archive.name, "config_digest": node_config})
    mark_pending(folder, run_id, "host-image-save-" + name, intent)
    monitor.check()
    descriptor = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    process = None
    timer = None
    timed_out = threading.Event()
    length, archive_hash = 0, hashlib.sha256()
    try:
        with os.fdopen(descriptor, "wb") as stream:
            try:
                process = subprocess.Popen(docker + ["image", "save", "--platform", "linux/amd64", host_id],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                           env=owned_forward_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError:
                raise LiveFailure("host image save could not start; reconcile") from None
            def stop():
                timed_out.set()
                try: process.kill()
                except OSError: pass
            timer = threading.Timer(180, stop)
            timer.daemon = True
            timer.start()
            next_check = 16 * 1024 * 1024
            while True:
                chunk = process.stdout.read(1024 * 1024)
                if not chunk: break
                if length + len(chunk) > max_mib * 1024 * 1024:
                    raise LiveFailure("host archive size limit reached; reconcile")
                stream.write(chunk); archive_hash.update(chunk); length += len(chunk)
                if length >= next_check:
                    monitor.check(); next_check = length + 16 * 1024 * 1024
            stream.flush(); os.fsync(stream.fileno())
            if process.wait(timeout=5) != 0 or timed_out.is_set():
                raise LiveFailure("host image save outcome unknown; reconcile")
        with archive.open("rb") as stream:
            evidence = verify_host_archive(stream, node_config, None if node is None else node["rootfs_diff_ids"], max_mib * 1024 * 1024)
        evidence.update({"host_image_id": host_id, "archive_name": archive.name,
                         "archive_sha256": "sha256:" + archive_hash.hexdigest()})
        mark_confirmed(folder, run_id, "host-image-save-" + name, intent, _digest(evidence))
        return evidence
    except (OSError, subprocess.TimeoutExpired):
        raise LiveFailure("host image save outcome unknown; reconcile") from None
    finally:
        if timer is not None: timer.cancel()
        if process is not None:
            if process.poll() is None:
                try: process.kill()
                except OSError: pass
                try: process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired): pass
            if process.stdout is not None: process.stdout.close()


def verified_histories(runtime, target_revision):
    """Require the new full image history and the exact requested DB prefix."""
    if target_revision not in ("a010", "a011"):
        raise LiveFailure("unsupported database target")
    full = runtime.schema_history()
    target = runtime.schema_history(target_revision)
    if (full[0] != PACKAGED_HISTORY_HEAD or len(full[1]) != 14
            or full[2] not in {HISTORY_SHA256[PACKAGED_HISTORY_HEAD], HISTORY_LF_SHA256[PACKAGED_HISTORY_HEAD]}
            or target[0] != target_revision
            or target[2] not in {HISTORY_SHA256[target_revision], HISTORY_LF_SHA256.get(target_revision, HISTORY_SHA256[target_revision])}
            or tuple(full[1][-len(target[1]):]) != tuple(target[1])):
        raise LiveFailure("packaged full history or target prefix mismatch")
    return full, target


def _source_probe(folder, token, run_id, name, host_id, runtime, source_tools, owned, target_revision="a010"):
    """Compare the pinned host image's app/migrations with host source."""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", host_id):
        raise LiveFailure("immutable host image ID required")
    if name not in {"application", "authority"}:
        raise LiveFailure("invalid image probe")
    context = runtime.resource_snapshot.local_context(dict(os.environ))
    docker = ["docker", "--context", context]
    container = "knowi-analytics-bench-probe-" + run_id.hex + "-" + name
    listing = _bounded_command(docker + ["ps", "-a", "--filter", "name=^/" + container + "$", "--format", "{{.Names}}"], limit=4096)
    if listing.strip():
        raise LiveFailure("image probe name already exists; reconcile")
    history, target = verified_histories(source_tools.runtime, target_revision)
    expected_runtime = source_tools.runtime_files_fingerprint(source_tools.ROOT)
    expected_packaged = packaged_source_fingerprint(source_tools.ROOT)
    # Persist exact operation/container identity before issuing the probe.
    owned.record(folder, token, state="k3s_image_probe_pending", probe_name=container, host_image_id=host_id)
    intent = _digest({"name": container, "host_image_id": host_id})
    mark_pending(folder, run_id, "source-probe-" + name, intent)
    import inspect
    script = (source_tools.IMAGE_HISTORY_PROBE + "\n" + inspect.getsource(packaged_source_fingerprint)
              + "\nprint(json.dumps({'packaged_sha256': packaged_source_fingerprint(root, reject_caches=True)}))\n")
    command = docker + ["run", "--rm", "--pull=never", "--name", container,
                        "--label", "knowi.analytics.synthetic=true", "--network=none", "--read-only",
                        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=128m", "--cpus=0.25",
                        "--pids-limit=64", "--no-healthcheck", "--platform=linux/amd64", "--user=10001",
                        "--workdir=/app", "--entrypoint=python", host_id, "-B", "-c", script]
    raw = _bounded_command(command, timeout=35, limit=64 * 1024)
    try:
        lines = raw.splitlines()
        if len(lines) != 2: raise ValueError
        observation = json.loads(lines[0])
        packaged = json.loads(lines[1])
        expected_history = [history[0], list(history[1]), history[2]]
        if (not isinstance(observation, dict) or set(observation) != {"history", "runtime_sha256"}
                or observation["history"] != expected_history or observation["runtime_sha256"] != expected_runtime
                or packaged != {"packaged_sha256": expected_packaged}):
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise LiveFailure("packaged source does not match host") from None
    remaining = _bounded_command(docker + ["ps", "-a", "--filter", "name=^/" + container + "$", "--format", "{{.Names}}"], limit=4096)
    if remaining.strip():
        raise LiveFailure("image probe cleanup unresolved")
    mark_confirmed(folder, run_id, "source-probe-" + name, intent, _digest({"runtime": observation, "packaged": packaged}))
    return {"runtime_sha256": expected_runtime, "packaged_history_head": history[0],
            "migration_history_sha256": history[2], "target_revision": target_revision,
            "target_history_sha256": target[2],
            "packaged_sha256": expected_packaged,
            "scope": "all packaged application and migration files, requirements and alembic config"}


def _database_setup(b, folder, token, run_id, target_revision="a010"):
    if target_revision not in HISTORY_SHA256:
        raise LiveFailure("unsupported database target")
    owned = b.owned
    intent = _digest({"operation": "fresh-analytics-database", "run_id": str(run_id)})
    mark_pending(folder, run_id, "database-create", intent)
    target, urls, config = owned.create_analytics(folder, token)
    _write_exclusive(folder / "database-urls.private.json", urls)
    mark_confirmed(folder, run_id, "database-create", intent, _digest({"name": target["name"], "owner": target["owner"]}))
    migration = _digest({"database": target["name"], "revision": target_revision})
    mark_pending(folder, run_id, "database-migrate", migration)
    owned.install_analytics(urls, full_chain=True, activity_projection=True,
                            day_projection=target_revision == "a011")
    b.verify_database(target, config)
    owned.verify_analytics_grants(target)
    with b._guarded_connection(target) as conn:
        if conn.execute("SELECT version_num FROM public.alembic_version").fetchall() != [(target_revision,)]:
            raise LiveFailure("database migration outcome unresolved")
        if conn.execute("SELECT count(*) FROM analytics.activity_facts").fetchone() != (0,):
            raise LiveFailure("fresh database contains preexisting facts")
        if target_revision == "a011":
            privacy = target["name"] + "_privacy"
            allowed = conn.execute("""SELECT
                has_table_privilege(%s,'analytics.activity_projection_day_sources','SELECT') AND
                has_table_privilege(%s,'analytics.activity_projection_day_sources','DELETE') AND
                has_table_privilege(%s,'analytics.activity_projection_days','SELECT') AND
                has_table_privilege(%s,'analytics.activity_projection_days','DELETE')""",
                (privacy, privacy, privacy, privacy)).fetchone()
            fresh = conn.execute("""SELECT NOT EXISTS(SELECT 1 FROM analytics.activity_projection_day_sources)
                AND NOT EXISTS(SELECT 1 FROM analytics.activity_projection_days)
                AND EXISTS(SELECT 1 FROM analytics.activity_projection_day_backfill
                           WHERE singleton=true AND run_id IS NULL AND phase='scanning')""").fetchone()
            if allowed != (True,) or fresh != (True,):
                raise LiveFailure("a011 privacy grants or fresh day state unavailable")
    mark_confirmed(folder, run_id, "database-migrate", migration, _digest({"revision": target_revision}))
    projection = _digest({"database": target["name"], "projection_run_id": str(run_id)})
    mark_pending(folder, run_id, "projection-begin", projection)
    b.initialize_projection(target, run_id)
    with b._guarded_connection(target) as conn:
        row = conn.execute("SELECT run_id FROM analytics.activity_projection_backfill WHERE singleton=true").fetchone()
    if row is None or str(row[0]) != str(run_id):
        raise LiveFailure("projection initialization outcome unresolved")
    mark_confirmed(folder, run_id, "projection-begin", projection, _digest({"run_id": str(run_id)}))
    return target, urls


def projection_drained(b, target, run_id, *, a011):
    """Check real a010 and, when enabled, real a011 worker state; never mark it ready."""
    if not b.projection_drained(target, run_id):
        return False
    if not a011:
        return True
    with b._guarded_connection(target) as conn:
        row = conn.execute("""SELECT
          EXISTS(SELECT 1 FROM analytics.activity_projection_day_backfill
                 WHERE singleton=true AND run_id=%s AND phase='complete')
          AND NOT EXISTS(SELECT 1 FROM analytics.activity_projection_day_sources WHERE state<>'ready')
          AND NOT EXISTS(SELECT 1 FROM analytics.activity_projection_day_sources s
              LEFT JOIN analytics.activity_projection_days d USING(subject_ref,local_day,metric_version)
              WHERE s.state='ready' AND (d.subject_ref IS NULL OR d.run_id IS DISTINCT FROM %s
                    OR d.generation IS DISTINCT FROM s.generation))""", (run_id, run_id)).fetchone()
    return row == (True,)


async def _drive_load(b, args, kube, run_id, profile, key, issuer, fingerprint, target,
                      inventory, monitor, children, stats, observations):
    import httpx
    class Processes:
        pass
    processes = Processes()
    processes.children = children
    processes.endpoints = {role: [] for role in ("collector", "processor", "query", "privacy")}
    for role, pods in inventory.items():
        for pod in pods:
            forward = start_pod_forward(b.owned, args.kubectl, args.kubeconfig, args.context,
                                        kube.namespace, pod, args.folder, args.owner_token, args.job,
                                        children, lambda name: kube.get("pod", name))
            monitor.forwards.append(forward)
            processes.endpoints[role].append(forward["url"])
    async def projection_wait():
        await b.wait_condition(lambda: projection_drained(b, target, run_id, a011=args.a011), monitor, timeout=60)
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=64)
    async with httpx.AsyncClient(timeout=httpx.Timeout(5), limits=limits, trust_env=False) as client:
        await b.await_ready(client, processes, fingerprint)
        monitor.check()
        async def sample():
            observation = _kube_observations(kube)
            actual_names = {row["pod"] for row in observation["top"]}
            required = {p["name"] for pods in inventory.values() for p in pods}
            if not required.issubset(actual_names):
                raise LiveFailure("missing per-Pod resource metrics")
            observations.append(observation)
            return await b.sample_metrics(client, processes)
        result = await run_http_workload(b, client, processes, profile, args.stage, run_id,
                                         key, issuer, fingerprint, monitor, projection_wait,
                                         stats=stats, metrics_sampler=sample)
    return result, observations


def _finish_resources(args, kube, folder, run_id, owners, fixture, guard):
    """Stop only completed run's compute; retain namespace, DB and journal PVC."""
    counts = dict(zip(("collector", "processor", "query"), args.replicas))
    counts.update({"privacy": 1, "authority": 1})
    for role in ("authority", "collector", "processor", "query", "privacy"):
        name = "bench-authority" if role == "authority" else "bench-analytics-" + role
        deployment = kube.get("deployment", name)
        if deployment["metadata"]["uid"] != owners[role]:
            raise LiveFailure("owned Deployment changed before stop")
        version = deployment["metadata"].get("resourceVersion")
        if not isinstance(version, str) or not version.isdigit():
            raise LiveFailure("owned Deployment version unavailable")
        identity = _digest({"namespace": kube.namespace, "deployment_uid": owners[role], "replicas": 0})
        mark_pending(folder, run_id, role + "-stop", identity)
        kube.call(["scale", "deployment/" + name, "--current-replicas=" + str(counts[role]), "--replicas=0",
                   "--resource-version=" + version])
        _wait_read(lambda: not kube.get("pods", selector="app.kubernetes.io/component=" + role)["items"], timeout=90)
        mark_confirmed(folder, run_id, role + "-stop", identity, _digest({"replicas": 0}))
        remaining = {uid: spec for uid, spec in fixture["pods"].items() if spec["role"] != role}
        fixture = {"namespace": kube.namespace, "pods": remaining}
        guard(fixture)
    _delete_recorded(kube, folder, run_id, "binder-stop", "pods", "bench-journal-binder", owners["binder"])
    fixture["pods"] = {uid: spec for uid, spec in fixture["pods"].items() if spec["role"] != "volume-probe"}
    guard(fixture)
    _delete_recorded(kube, folder, run_id, "journal-job-stop", "jobs", "bench-journal-init", owners["init_job"])
    _wait_read(lambda: not kube.get("pods", selector="app.kubernetes.io/component=journal-init")["items"], timeout=90)
    guard({"namespace": kube.namespace, "pods": {}})


def run_case(args):
    """One explicitly admitted case. Incomplete attempts are never retried here."""
    if os.environ.get("ANALYTICS_BENCHMARK_GO") != "1":
        raise LiveFailure("root resource-window GO required")
    if os.environ.get("ANALYTICS_BENCHMARK_IMAGE_PROBE_GO") != "1":
        raise LiveFailure("separate bounded image-probe GO required")
    if os.name != "nt" or sys.version_info[:2] != (3, 12):
        raise LiveFailure("Windows and Analytics Python 3.12 are required")
    run_id = UUID(args.run_id)
    if str(run_id) != args.run_id or namespace_for(run_id) != args.namespace:
        raise LiveFailure("run UUID and explicit namespace mismatch")
    if (args.stage not in STAGES or args.replicas not in REPLICAS or args.repeat not in (1, 2, 3)
            or args.context != "k3d-knowi-analytics-01a1df75-v2"
            or not re.fullmatch(r"[0-9a-f]{64}", args.server_id)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", args.host_image_id)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", args.host_authority_image_id)
            or type(args.archive_max_mib) is not int or not 128 <= args.archive_max_mib <= 8192
            or str(UUID(args.cluster_uid)) != args.cluster_uid
            or str(UUID(args.storage_class_uid)) != args.storage_class_uid
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", args.resource_window)):
        raise LiveFailure("explicit target or case identity invalid")
    stopped_id = getattr(args, "retained_authority_stopped_id", None)
    if (stopped_id is not None and (not re.fullmatch(r"[0-9a-f]{64}", stopped_id)
                                   or not isinstance(args.retained_authority_image_id, str)
                                   or not re.fullmatch(r"sha256:[0-9a-f]{64}", args.retained_authority_image_id))):
        raise LiveFailure("stopped retained authority requires exact container and image identities")
    service = Path(__file__).resolve().parents[1]
    if str(service / "tools") not in sys.path: sys.path.insert(0, str(service / "tools"))
    import benchmark_activity as b
    import bootstrap_runtime as db_history
    import run_k3s as runtime
    import k3s_database
    target_revision = "a011" if args.a011 else "a010"
    full_history, target_history = verified_histories(db_history, target_revision)
    args.kubectl = _binary(args.kubectl, args.kubectl_sha256)
    args.helm = _binary(args.helm, args.helm_sha256)
    args.kubeconfig = runtime.KUBECONFIG
    profile = b.profile_load(service / "bench/profile-v1.json")
    streams = b.stream_plan(profile, args.stage, SEED, run_id)
    stream_count = len(streams["streams"])
    host_monitor = b.resource_floors(os.environ)
    host_monitor.check()
    output = b.output_path(args.output)
    kube = Kube(args.kubectl, args.kubeconfig, args.context, args.namespace)
    baseline = _assert_target(args, runtime, kube, None)
    image_attestation = _image_attestation(args, runtime)
    sc = kube.get("storageclass", "local-path")
    if (sc["metadata"]["uid"] != args.storage_class_uid or sc.get("provisioner") != "rancher.io/local-path"
            or sc.get("volumeBindingMode") != "WaitForFirstConsumer"):
        raise LiveFailure("storage class identity mismatch")
    if kube.get("namespace", args.namespace, ignore_missing=True) is not None:
        raise LiveFailure("benchmark namespace already exists; reconcile")
    source = _source_fingerprint(service)
    local = service / ".local"
    if local.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(local)):
        raise LiveFailure("unsafe local state directory")
    lock = local / "benchmark-k3s.active.json"
    # Exclusive durable marker survives every failed/unknown outcome. Do not
    # start a second attempt until an operator reconciles the original one.
    _write_exclusive(lock, {"run_id": str(run_id), "namespace": args.namespace, "output": str(output)})
    output.parent.mkdir(parents=True, exist_ok=True)
    result = {"status": "UNRUN", "execution": "starting", "run_id": str(run_id),
              "namespace": args.namespace, "stage": args.stage, "repeat": args.repeat,
              "replicas": list(args.replicas), "profile": PROFILE,
              "warmup_seconds": WARMUP_SECONDS, "measurement_seconds": MEASUREMENT_SECONDS,
              "dataset": {"fresh": True, "preexisting_facts_required": 0, "historical_seed_events": 0,
                          "historical_fixture": "UNRUN", "history_query_only_gate": "UNRUN",
                          "planned_subjects": len(streams["subjects"]), "active_subjects": streams["active"],
                          "overlapping_subjects": streams["overlapping"], "courses": len(streams["courses"]),
                          "streams": stream_count, "lessons_per_course": 1, "sections_per_lesson": 2,
                          "planned_opening_events": stream_count, "planned_warmup_segments": stream_count * 20,
                          "planned_measured_segments": stream_count * 60,
                          "planned_live_duration_seconds": 1200, "heartbeat_seconds": 15, "seed": SEED},
              "resource_window": args.resource_window, "baseline": baseline,
              "provenance": {"source_sha256": source,
                             "packaged_history_head": full_history[0], "packaged_history_sha256": full_history[2],
                             "target_revision": target_history[0], "target_history_sha256": target_history[2],
                             "profile_sha256": _digest((service / "bench/profile-v1.json").read_bytes()),
                             "canonical_profile_sha256": PROFILE_CANONICAL_SHA256,
                             "oracle_sha256": _digest((service / "reference/activity_v1.py").read_bytes()),
                             "kubectl_sha256": args.kubectl_sha256, "helm_sha256": args.helm_sha256,
                             "node_images": image_attestation,
                             "k3s_image_id": args.k3s_image_id, "server_id": args.server_id,
                             "cluster_uid": args.cluster_uid, "storage_class_uid": args.storage_class_uid,
                             "image_source_equivalence": "PENDING"},
              "scenarios": {"load": "UNRUN", "oracle": "UNRUN", "privacy_load": "UNRUN",
                            "replay": "UNRUN", "deletion": "UNRUN", "backup_restore": "UNRUN"}}
    _write_exclusive(output, result)
    folder = token = job = None
    children, stats, observations = [], b.new_stats(), []
    monitor = None
    try:
        folder, token = b.owned.private_folder()
        args.folder, args.owner_token = folder, token
        b.owned.record(folder, token, state="k3s_benchmark_starting", run_id=str(run_id), namespace=args.namespace)
        result["owner_record"] = str(folder.relative_to(service) / "owner.json")
        _save_result(output, run_id, result)
        import run_local as source_tools
        source_evidence = {}
        verified_refs = {}
        probes = (("application", args.host_image_id), ("authority", args.host_authority_image_id))
        for role, host_id in probes:
            cache_key = (host_id, image_attestation[role]["config_digest"])
            if cache_key in verified_refs:
                source_evidence[role] = {**verified_refs[cache_key], "reused_same_host_and_config": True}
                continue
            host_monitor.check()
            _assert_target(args, runtime, kube, None)
            host_proof = _host_image_proof(folder, token, run_id, role, host_id, image_attestation[role],
                                           runtime, b.owned, host_monitor, args.archive_max_mib)
            host_monitor.check()
            _assert_target(args, runtime, kube, None)
            source_proof = _source_probe(folder, token, run_id, role, host_id, runtime, source_tools, b.owned, target_revision)
            source_evidence[role] = {"host_archive": host_proof, "packaged_source": source_proof}
            verified_refs[cache_key] = source_evidence[role]
        if _source_fingerprint(service) != source:
            raise LiveFailure("source changed during image probes")
        result["provenance"]["image_source_equivalence"] = source_evidence
        result["provenance"]["base_image_and_installed_dependencies"] = "NOT_VERIFIED"
        _save_result(output, run_id, result)
        job = b.owned.OwnedJob()
        args.job = job
        start_kube_proxy(b.owned, kube, folder, token, job, children)
        namespace_obj = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": args.namespace,
                         "labels": {"knowi.analytics.synthetic": "true", "knowi.analytics.run": run_id.hex}}}
        ns_uid = _create_recorded(kube, folder, run_id, "namespace-create", namespace_obj)
        def guard(fixture):
            ns = kube.get("namespace", args.namespace)
            if (ns["metadata"]["uid"] != ns_uid or ns["metadata"].get("labels", {}).get("knowi.analytics.run") != run_id.hex):
                raise LiveFailure("benchmark namespace identity changed")
            return _assert_target(args, runtime, kube, fixture)
        def image_guard():
            if _image_attestation(args, runtime) != image_attestation:
                raise LiveFailure("node image mapping changed")
        empty_fixture = {"namespace": args.namespace, "pods": {}}
        guard(empty_fixture)
        target, urls = _database_setup(b, folder, token, run_id, target_revision)
        result["dataset"]["preexisting_facts_observed"] = 0
        _save_result(output, run_id, result)
        guard(empty_fixture)
        db_ip = k3s_database._attested_ip()
        keys, reader = b.cross_service.key_material(folder)
        authority, owners, aux, fixture, authority_ip, journal = _authority_setup(
            kube, folder, run_id, args, keys, reader, guard, image_guard)
        result["journal_storage"] = journal
        key, issuer, fingerprint = _configure_workloads(kube, folder, run_id, args, b, urls, keys, reader,
                                                       authority, target, db_ip, lambda: guard(fixture))
        values = helm_values(run_id, args.replicas, db_ip, authority_ip, args.image_ref, a011=args.a011)
        for role in ("collector", "processor", "query"):
            values["roles"][role]["replicas"] = 0
        if _source_fingerprint(service) != source:
            raise LiveFailure("source changed before Helm installation")
        guard(fixture)
        image_guard()
        _helm_recorded(args, kube, folder, run_id, "helm-privacy-install", "install", values)
        owners.update(_helm_deployment_uids(kube, ("collector", "processor", "query", "privacy")))
        initial_counts = {"collector": 0, "processor": 0, "query": 0, "privacy": 1}
        fixture = _fixture_wait(kube, run_id, args, initial_counts, owners, aux)
        guard(fixture)
        values = helm_values(run_id, args.replicas, db_ip, authority_ip, args.image_ref, a011=args.a011)
        if args.a011:
            values["roles"]["collector"]["replicas"] = 0
            values["roles"]["query"]["replicas"] = 0
            if _source_fingerprint(service) != source:
                raise LiveFailure("source changed before day worker upgrade")
            guard(fixture)
            image_guard()
            _helm_recorded(args, kube, folder, run_id, "helm-day-worker-upgrade", "upgrade", values)
            worker_counts = {"collector": 0, "processor": args.replicas[1], "query": 0, "privacy": 1}
            fixture = _fixture_wait(kube, run_id, args, worker_counts, owners, aux)
            guard(fixture)
            def worker_snapshot():
                return _expected_pods(kube, run_id, args.image_ref, args.image_config_id,
                                      args.authority_ref, args.authority_config_id, worker_counts, owners, auxiliary=aux)
            worker_monitor = K3sMonitor(args, runtime, kube, host_monitor, fixture, worker_snapshot, [], job)
            started = time.monotonic()
            asyncio.run(b.wait_condition(lambda: projection_drained(b, target, run_id, a011=True), worker_monitor, timeout=60))
            result["prewarm_backfill"] = {"status": "OBSERVED_COMPLETE", "run_id": str(run_id),
                                          "elapsed_seconds": time.monotonic() - started,
                                          "historical_seed_events": 0}
            _save_result(output, run_id, result)
            values = helm_values(run_id, args.replicas, db_ip, authority_ip, args.image_ref, a011=True)
        if _source_fingerprint(service) != source:
            raise LiveFailure("source changed before Helm upgrade")
        image_guard()
        _helm_recorded(args, kube, folder, run_id, "helm-role-upgrade", "upgrade", values)
        desired = dict(zip(("collector", "processor", "query"), args.replicas))
        desired["privacy"] = 1
        fixture = _fixture_wait(kube, run_id, args, desired, owners, aux)
        guard(fixture)
        app_pods = [p for p in kube.get("pods", selector="app.kubernetes.io/instance=bench")["items"]
                    if p["metadata"].get("labels", {}).get("app.kubernetes.io/component") in desired]
        inventory = pod_inventory({"items": app_pods}, args.namespace, args.replicas, args.image_config_id, args.image_ref)
        if {p["uid"] for pods in inventory.values() for p in pods} != {
                uid for uid, spec in fixture["pods"].items() if spec["role"] in desired}:
            raise LiveFailure("ready Pod inventory differs from guarded set")
        def snapshot_fixture():
            return _expected_pods(kube, run_id, args.image_ref, args.image_config_id,
                                  args.authority_ref, args.authority_config_id, desired, owners, auxiliary=aux)
        monitor = K3sMonitor(args, runtime, kube, host_monitor, fixture, snapshot_fixture, [], job)
        result["execution"] = "running"
        result["pods"] = inventory
        result["fixture_pods"] = [{"uid": uid, **spec} for uid, spec in sorted(fixture["pods"].items())]
        _save_result(output, run_id, result)
        workload, observations = asyncio.run(_drive_load(b, args, kube, run_id, profile, key, issuer,
                                                         fingerprint, target, inventory, monitor,
                                                         children, stats, observations))
        if _source_fingerprint(service) != source:
            raise LiveFailure("source changed during measurement")
        result.update(workload)
        classification = b.classify(workload, profile)
        # The separately required historical/query and recovery gates are not
        # executed by this case. A passing load alone cannot close the profile.
        result["status"] = "FAIL" if classification == "FAIL" else "UNRUN"
        result["scenarios"]["load"] = "OBSERVED"
        result["scenarios"]["oracle"] = workload["oracle"]["status"]
        result["kubernetes_observations"] = observations
        result["resources"] = monitor.result()
        # Keep the verified API proxy alive for UID-preconditioned cleanup.
        b.owned.processes_stop(children[1:])
        _finish_resources(args, kube, folder, run_id, owners, fixture, guard)
        b.owned.processes_stop(children[:1])
        job.close(); job = None
        children.clear()
        result["execution"] = "finished"
        result["retained"] = {"namespace_uid": ns_uid, "database": target["name"],
                              "journal_pvc_uid": journal["pvc_uid"], "journal_pv_uid": journal["pv_uid"],
                              "facts_ttl": False}
        b.owned.record(folder, token, state="k3s_benchmark_finished_resources_retained", processes=[])
        _save_result(output, run_id, result)
        current_lock = json.loads(lock.read_text(encoding="utf-8"))
        if current_lock.get("run_id") != str(run_id): raise LiveFailure("active run ownership changed")
        lock.unlink()
        return result
    except BaseException as exc:
        result["execution"] = "interrupted" if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else "failed"
        result["failure_kind"] = type(exc).__name__
        if isinstance(exc, LiveFailure): result["failure_reason"] = str(exc)
        result["status"] = "FAIL" if isinstance(exc, b.ObservedViolation) or b.observed_violation(stats) else "UNRUN"
        result["reconciliation"] = "required; active marker and all durable resources retained"
        result["collector"] = b.summary(stats["collector_ms"])
        result["query"] = b.summary(stats["query_ms"])
        for name in ("requests", "warmup_requests", "verification_requests", "ack", "opening_ack",
                     "warmup_ack", "query_roles", "retry_after", "timeouts", "metrics"):
            result[name] = stats[name]
        result["max_schedule_lag_ms"] = max(stats["schedule_lag_ms"], default=0)
        result["kubernetes_observations"] = observations
        if monitor is not None: result["resources"] = monitor.result()
        if folder is not None:
            try: b.owned.record(folder, token, state="k3s_benchmark_reconciliation_required", processes=[p.pid for p in children])
            except Exception: pass
        try: _save_result(output, run_id, result)
        except Exception: pass
        if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)): raise
        return result
    finally:
        cleanup_failed = False
        try:
            b.owned.processes_stop(children)
        except Exception:
            cleanup_failed = True
        try:
            if job is not None: job.close()
        except Exception:
            cleanup_failed = True
        if cleanup_failed:
            result["execution"] = "cleanup_failed"
            result["status"] = "UNRUN"
            result["reconciliation"] = "owned process cleanup failed; inspect active marker and retained resources"
            try: _save_result(output, run_id, result)
            except Exception: pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="requires explicit resource-window GO and identities")
    parser.add_argument("--a011", action="store_true", help="opt in to a011 day worker and query")
    parser.add_argument("--run-id")
    parser.add_argument("--namespace")
    parser.add_argument("--context")
    parser.add_argument("--cluster-uid")
    parser.add_argument("--server-id")
    parser.add_argument("--k3s-image-id")
    parser.add_argument("--retained-authority-image-id", help="digest of retained authority, or 'none' when absent")
    parser.add_argument("--retained-authority-stopped-id", help="full container ID of explicitly attested stopped retained authority")
    parser.add_argument("--storage-class-uid")
    parser.add_argument("--kubectl")
    parser.add_argument("--kubectl-sha256")
    parser.add_argument("--helm")
    parser.add_argument("--helm-sha256")
    parser.add_argument("--image-ref", help="docker.io/library/knowi-analytics@sha256:<manifest>")
    parser.add_argument("--image-config-id", help="OCI config digest, distinct from manifest digest")
    parser.add_argument("--authority-ref", help="docker.io/library/knowi-analytics@sha256:<manifest>")
    parser.add_argument("--authority-config-id", help="OCI config digest, distinct from manifest digest")
    parser.add_argument("--host-image-id", help="immutable local Docker image ID; may be an OCI index")
    parser.add_argument("--host-authority-image-id", help="immutable local Docker image ID; may be an OCI index")
    parser.add_argument("--archive-max-mib", type=int, help="per-image archive cap, 128 through 8192 MiB")
    parser.add_argument("--stage", type=int, choices=STAGES)
    parser.add_argument("--replicas", help="collector,processor,query")
    parser.add_argument("--repeat", type=int, choices=(1, 2, 3))
    parser.add_argument("--output")
    parser.add_argument("--resource-window")
    parser.add_argument("--web-temporarily-paused", action="store_true")
    args = parser.parse_args(argv)
    if not args.run:
        print(json.dumps(plan(a011=args.a011), sort_keys=True))
        return 0
    required = ("run_id", "namespace", "context", "cluster_uid", "server_id", "k3s_image_id",
                "retained_authority_image_id", "storage_class_uid", "kubectl", "kubectl_sha256", "helm",
                "helm_sha256", "image_ref", "image_config_id", "authority_ref", "authority_config_id",
                "host_image_id", "host_authority_image_id", "archive_max_mib",
                "stage", "replicas", "repeat", "output", "resource_window")
    if any(getattr(args, name) is None for name in required):
        print("K3s benchmark refused: explicit target, identity and resource-window arguments required.", file=sys.stderr)
        return 2
    try:
        args.replicas = tuple(int(item) for item in args.replicas.split(","))
        if args.retained_authority_image_id == "none":
            args.retained_authority_image_id = None
        elif not re.fullmatch(r"sha256:[0-9a-f]{64}", args.retained_authority_image_id):
            raise ValueError("invalid retained authority image")
        result = run_case(args)
    except (ValueError, OSError, KeyError, TypeError, LiveFailure):
        print("K3s benchmark refused: admission failed or prior state requires reconciliation.", file=sys.stderr)
        return 2
    print(json.dumps({"run_id": result["run_id"], "execution": result["execution"], "status": result["status"]}))
    return 0 if result["execution"] == "finished" and result["status"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
