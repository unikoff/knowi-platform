"""Offline preparation tests: no K3s, database, network, or workload calls."""
import importlib.util
import asyncio
import copy
import http.server
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import socket
import tempfile
import tarfile
import threading
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest


SERVICE = Path(__file__).resolve().parents[2]
ROOT = SERVICE.parents[1]
sys.path.insert(0, str(SERVICE))
sys.path.insert(0, str(SERVICE / "tools"))
import run_k3s
HELM = SERVICE / ".local/bin/helm-v3.21.4.exe"
CHART = ROOT / "infra/helm/analytics"
spec = importlib.util.spec_from_file_location("benchmark_k3s", SERVICE / "tools/benchmark_k3s.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)
RUN = UUID("11111111-1111-4111-8111-111111111111")
IMAGE_REF = "docker.io/library/knowi-analytics@sha256:" + "a" * 64
HOST_IMAGE_ID = "sha256:" + "c" * 64
DIFF_ID = "sha256:" + "d" * 64


def test_plan_is_full_matrix_and_no_io(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline plan attempted I/O")
    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("socket.socket", forbidden)
    assert bench.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "UNRUN" and result["execution"] == "not_started"
    assert len(result["cases"]) == 60 and result["minimum_workload_seconds"] == 72000
    assert {(c["users"], tuple(c["replicas"]), c["repeat"]) for c in result["cases"]} == {
        (s, r, n) for s in bench.STAGES for r in bench.REPLICAS for n in range(1, 4)}
    assert (result["warmup_seconds"], result["measurement_seconds"]) == (300, 900)
    assert result["target_revision"] == "a010" and not result["day_projection_enabled"]
    assert bench.main(["--a011"]) == 0
    day = json.loads(capsys.readouterr().out)
    assert day["target_revision"] == "a011" and day["day_projection_enabled"]
    assert day["cases"] == result["cases"] and day["historical_fixture"] == "UNRUN"


def test_live_is_refused_even_with_environment_go(monkeypatch, capsys):
    monkeypatch.setenv("ANALYTICS_BENCHMARK_GO", "1")
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("no live process allowed"))
    assert bench.main(["--run"]) == 2
    assert "explicit target" in capsys.readouterr().err


def test_target_guard_forwards_explicit_stopped_authority():
    cid = "b" * 64
    seen = []
    args = SimpleNamespace(kubectl="kubectl", kubectl_sha256="hash", k3s_image_id="image",
                           retained_authority_image_id=HOST_IMAGE_ID, retained_authority_stopped_id=cid,
                           web_temporarily_paused=False, server_id="server", cluster_uid="cluster")
    runtime = SimpleNamespace(require_shared_local_budget=lambda *a, **kw: (seen.append(kw), {"ok": True})[1],
                              resource_snapshot=SimpleNamespace(local_context=lambda env: "local"),
                              run=lambda *a: SimpleNamespace(stdout='[{"Id":"server"}]'),
                              validate_server=lambda *a: None, SERVER="server")
    kube = SimpleNamespace(get=lambda *a: {"metadata": {"uid": "cluster"}})
    assert bench._assert_target(args, runtime, kube, {"pods": {}}) == {"ok": True}
    assert seen[0]["expected_stopped_authority_id"] == cid and seen[0]["authority_image_id"] == HOST_IMAGE_ID


@pytest.mark.parametrize("stage,active,overlap,streams", [(10, 6, 2, 8), (15, 9, 2, 11),
                                                            (25, 15, 3, 18), (50, 30, 6, 36)])
def test_recorded_dataset_cardinality_follows_actual_fixed_workload(stage, active, overlap, streams):
    import benchmark_activity as activity
    profile = activity.profile_load(SERVICE / "bench/profile-v1.json")
    planned = activity.stream_plan(profile, stage, bench.SEED, RUN)
    assert (len(planned["subjects"]), planned["active"], planned["overlapping"],
            len(planned["courses"]), len(planned["streams"])) == (stage, active, overlap, 2, streams)
    assert (profile["warmup_seconds"], profile["measurement_seconds"], profile["heartbeat_seconds"]) == (300, 900, 15)


def test_values_are_fresh_scoped_and_never_contain_credentials():
    values = bench.helm_values(RUN, (2, 1, 1), "172.20.0.2", "172.20.0.3", IMAGE_REF)
    assert bench.namespace_for(RUN) == "knowi-analytics-bench-" + RUN.hex
    assert values["activityProjection"]["runId"] == str(RUN)
    assert values["migration"] == {"enabled": False, "revision": "a010"}
    assert values["roles"]["privacy"] == {"enabled": True, "replicas": 1}
    assert values["roles"]["collector"]["replicas"] == 2
    assert values["network"]["databaseCIDR"] == "172.20.0.2/32"
    assert "password" not in json.dumps(values).lower()


def test_authority_config_uses_namespace_local_service_and_separate_keys():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    keys = {name: Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")
        for name in ("grant", "producer", "command", "authority")}
    secret = "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE"
    client, server = bench.authority_configs(RUN, keys, secret)
    assert client["url"] == "http://privacy-authority:57420"
    assert client["authority_id"] == "bench-" + RUN.hex
    assert server["producer_command_public_keys"] == {"bench-producer": keys["command"]}
    keys["authority"] = keys["grant"]
    with pytest.raises(ValueError): bench.authority_configs(RUN, keys, secret)


def test_authority_resources_separate_initialization_from_restart_and_keep_pvc():
    objects = bench.authority_resources(RUN, IMAGE_REF, "local-path")
    pvc, job, deployment = objects["pvc"], objects["init_job"], objects["deployment"]
    assert pvc["spec"]["storageClassName"] == "local-path"
    binder = objects["bind_pod"]["spec"]
    assert binder["containers"][0]["name"] == "volume-probe"
    assert binder["containers"][0]["volumeMounts"][0]["readOnly"] is True
    assert job["spec"]["backoffLimit"] == 0
    job_pod = job["spec"]["template"]["spec"]
    pod = deployment["spec"]["template"]["spec"]
    assert job_pod["restartPolicy"] == "Never" and job_pod["containers"][0]["command"][4] == "init"
    assert pod["containers"][0]["command"][4] == "serve"
    assert binder["containers"][0]["image"] == IMAGE_REF
    assert job_pod["containers"][0]["image"] == IMAGE_REF
    assert pod["containers"][0]["image"] == IMAGE_REF
    assert job_pod["volumes"][0] == pod["volumes"][0]
    assert pod["volumes"][0]["persistentVolumeClaim"]["claimName"] == pvc["metadata"]["name"]
    assert deployment["spec"]["strategy"]["type"] == "Recreate"
    assert objects["network_policy"]["spec"]["egress"] == []
    assert all(obj["metadata"]["namespace"] == bench.namespace_for(RUN) for obj in objects.values())
    with pytest.raises(ValueError): bench.authority_resources(RUN, IMAGE_REF, "foreign-storage")


def test_storage_attestation_checks_binding_and_never_deletes():
    claim = bench.authority_resources(RUN, IMAGE_REF, "local-path")["pvc"]
    class_uid, claim_uid, pv_uid = (str(UUID(int=n)) for n in (1, 2, 3))
    claim["metadata"]["uid"] = claim_uid
    pv_name = "pvc-" + claim_uid
    claim["spec"]["volumeName"] = pv_name
    claim["status"] = {"phase": "Bound"}
    storage = {"metadata": {"name": "local-path", "uid": class_uid}, "provisioner": "rancher.io/local-path",
               "volumeBindingMode": "WaitForFirstConsumer"}
    volume = {"metadata": {"uid": pv_uid, "name": pv_name,
                           "annotations": {"pv.kubernetes.io/provisioned-by": "rancher.io/local-path"}},
              "spec": {"storageClassName": "local-path", "persistentVolumeReclaimPolicy": "Delete",
                       "hostPath": {"path": "/var/lib/rancher/k3s/storage/" + pv_name + "_" + bench.namespace_for(RUN) + "_bench-authority-journal"},
                       "claimRef": {"uid": claim_uid, "namespace": bench.namespace_for(RUN), "name": "bench-authority-journal"}},
              "status": {"phase": "Bound"}}
    result = bench.attest_journal_storage(RUN, storage, claim, volume, class_uid, claim_uid)
    assert result["reclaim_policy"] == "Delete" and result["automatic_deletion"] is False
    changed = copy.deepcopy(volume)
    changed["spec"]["hostPath"]["path"] = "/var/lib/rancher/k3s/storage/retained-volume"
    with pytest.raises(ValueError): bench.attest_journal_storage(RUN, storage, claim, changed, class_uid, claim_uid)
    volume["spec"]["claimRef"]["uid"] = str(UUID(int=4))
    with pytest.raises(ValueError): bench.attest_journal_storage(RUN, storage, claim, volume, class_uid, claim_uid)


def test_local_journal_pv_requires_exact_node_affinity_and_single_source():
    claim = bench.authority_resources(RUN, IMAGE_REF, "local-path")["pvc"]
    class_uid, claim_uid, pv_uid = (str(UUID(int=n)) for n in (1, 2, 3))
    claim["metadata"]["uid"] = claim_uid
    pv_name = "pvc-" + claim_uid
    claim["spec"]["volumeName"] = pv_name
    claim["status"] = {"phase": "Bound"}
    node = run_k3s.SERVER
    path = "/var/lib/rancher/k3s/storage/" + pv_name + "_" + bench.namespace_for(RUN) + "_bench-authority-journal"
    storage = {"metadata": {"name": "local-path", "uid": class_uid}, "provisioner": "rancher.io/local-path",
               "volumeBindingMode": "WaitForFirstConsumer"}
    volume = {"metadata": {"uid": pv_uid, "name": pv_name, "annotations": {
                  "pv.kubernetes.io/provisioned-by": "rancher.io/local-path", "local.path.provisioner/selected-node": node}},
              "spec": {"storageClassName": "local-path", "persistentVolumeReclaimPolicy": "Delete", "local": {"path": path},
                       "nodeAffinity": {"required": {"nodeSelectorTerms": [{"matchExpressions": [
                           {"key": "kubernetes.io/hostname", "operator": "In", "values": [node]}]}]}},
                       "claimRef": {"uid": claim_uid, "namespace": bench.namespace_for(RUN), "name": "bench-authority-journal"}},
              "status": {"phase": "Bound"}}
    def check(value, name=node):
        return bench.attest_journal_storage(RUN, storage, claim, value, class_uid, claim_uid, expected_node_name=name)
    assert check(volume)["pv_uid"] == pv_uid
    for change in ("path", "selected", "affinity_node", "extra_term", "both_sources", "claim", "extra_local"):
        modified = copy.deepcopy(volume)
        if change == "path": modified["spec"]["local"]["path"] = "/var/lib/rancher/k3s/storage/foreign"
        if change == "selected": modified["metadata"]["annotations"]["local.path.provisioner/selected-node"] = "other"
        if change == "affinity_node": modified["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"].append("other")
        if change == "extra_term": modified["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"].append({})
        if change == "both_sources": modified["spec"]["hostPath"] = {"path": path}
        if change == "claim": modified["spec"]["claimRef"]["uid"] = str(UUID(int=4))
        if change == "extra_local": modified["spec"]["local"]["fsType"] = "ext4"
        with pytest.raises(ValueError): check(modified)
    with pytest.raises(ValueError): check(volume, None)
    with pytest.raises(ValueError): check(volume, "different-node")


def test_preexisting_journal_backing_path_stops_before_binding(monkeypatch):
    commands = []
    monkeypatch.setattr(run_k3s.resource_snapshot, "local_context", lambda env: "local")
    def observed(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(run_k3s, "run", observed)
    with pytest.raises(bench.LiveFailure, match="not provably fresh"):
        bench._assert_fresh_journal_path(SimpleNamespace(server_id="a" * 64), bench.namespace_for(RUN), str(UUID(int=2)))
    assert len(commands) == 1 and commands[0][-2] == "-e"


@pytest.mark.parametrize("run,replicas,db,authority,tag", [
    (UUID(int=0), (1, 1, 1), "172.20.0.2", "172.20.0.3", IMAGE_REF),
    (RUN, (3, 1, 1), "172.20.0.2", "172.20.0.3", IMAGE_REF),
    (RUN, (1, 1, 1), "127.0.0.1", "172.20.0.3", IMAGE_REF),
    (RUN, (1, 1, 1), "172.20.0.2", "172.20.0.2", IMAGE_REF),
    (RUN, (1, 1, 1), "172.20.0.2", "172.20.0.3", "bad/tag"),
])
def test_rejects_unsafe_values(run, replicas, db, authority, tag):
    with pytest.raises(ValueError):
        bench.helm_values(run, replicas, db, authority, tag)


@pytest.mark.skipif(not HELM.is_file(), reason="version-pinned Helm binary unavailable")
@pytest.mark.parametrize("namespace,flag,allowed", [
    ("knowi-analytics", None, True),
    ("knowi-analytics", "true", True),
    ("knowi-analytics-bench-" + RUN.hex, None, False),
    ("knowi-analytics-bench-" + RUN.hex, "true", True),
    ("knowi-analytics-bench-" + RUN.hex, "false", False),
    ("knowi-analytics-bench-" + "A" * 32, "true", False),
    ("knowi-analytics-bench-" + RUN.hex[:-1], "true", False),
    ("default", "true", False),
])
def test_chart_namespace_guard_using_installed_helm(namespace, flag, allowed):
    command = [str(HELM), "template", "bench", str(CHART), "--namespace", namespace]
    if flag is not None:
        command.extend(["--set", "syntheticBenchmark.enabled=" + flag])
    if namespace.startswith("knowi-analytics-bench-") and flag == "true":
        command.extend(["--set", "image.digest=sha256:" + "a" * 64])
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert (result.returncode == 0) == allowed, result.stderr


@pytest.mark.skipif(not HELM.is_file(), reason="version-pinned Helm binary unavailable")
def test_values_render_role_counts_and_projection():
    values = bench.helm_values(RUN, (2, 1, 1), "172.20.0.2", "172.20.0.3", IMAGE_REF)
    command = [str(HELM), "template", "bench", str(CHART), "--namespace", bench.namespace_for(RUN), "-f", "-"]
    result = subprocess.run(command, input=json.dumps(values), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    docs = result.stdout.split("\n---\n")
    deployments = [doc for doc in docs if "kind: Deployment\n" in doc]
    assert len(deployments) == 4
    for role, replicas in (("collector", 2), ("processor", 1), ("query", 1), ("privacy", 1)):
        doc, = [doc for doc in deployments if f"name: bench-analytics-{role}\n" in doc]
        assert f"  replicas: {replicas}\n" in doc
    assert result.stdout.count(f'value: "{RUN}"') == 2
    assert "kind: Job\n" not in result.stdout
    assert result.stdout.count('image: "' + IMAGE_REF + '"') == 4
    assert "ANALYTICS_PROCESSOR_ACTIVITY_DAY_WORKER_ENABLED" not in result.stdout
    assert "ANALYTICS_QUERY_ACTIVITY_DAY_QUERY_ENABLED" not in result.stdout


@pytest.mark.skipif(not HELM.is_file(), reason="version-pinned Helm binary unavailable")
def test_day_chart_roles_and_fail_closed_dependencies():
    values = bench.helm_values(RUN, (1, 1, 1), "172.20.0.2", "172.20.0.3", IMAGE_REF, a011=True)
    command = [str(HELM), "template", "bench", str(CHART), "--namespace", bench.namespace_for(RUN), "-f", "-"]
    def render(v):
        return subprocess.run(command, input=json.dumps(v), capture_output=True, text=True, timeout=30)
    result = render(values)
    assert result.returncode == 0, result.stderr
    docs = [d for d in result.stdout.split("\n---\n") if "kind: Deployment\n" in d]
    for role in ("collector", "processor", "query", "privacy"):
        doc, = [d for d in docs if f"name: bench-analytics-{role}\n" in d]
        assert ("ANALYTICS_PROCESSOR_ACTIVITY_DAY_WORKER_ENABLED" in doc) == (role == "processor")
        assert ("ANALYTICS_QUERY_ACTIVITY_DAY_QUERY_ENABLED" in doc) == (role == "query")
        assert (str(RUN) in doc) == (role in ("processor", "query"))
    for section, key, bad in (("migration", "revision", "a010"), ("migration", "revision", "a015"),
                              ("activityProjection", "enabled", False), ("roles", "privacy", {"enabled": False}),
                              ("privacyAuthority", "secretName", ""), ("activityDay", "enabled", "true")):
        changed = copy.deepcopy(values)
        changed[section][key] = bad
        assert render(changed).returncode != 0, (section, key, bad)


@pytest.mark.skipif(not HELM.is_file(), reason="version-pinned Helm binary unavailable")
def test_benchmark_chart_rejects_mutable_tag_and_digest_role_overrides():
    command = [str(HELM), "template", "bench", str(CHART), "--namespace", bench.namespace_for(RUN),
               "--set", "syntheticBenchmark.enabled=true"]
    assert subprocess.run(command, capture_output=True, text=True, timeout=30).returncode != 0
    command += ["--set", "image.digest=sha256:" + "a" * 64, "--set", "roles.query.imageTag=mutable"]
    assert subprocess.run(command, capture_output=True, text=True, timeout=30).returncode != 0


@pytest.mark.skipif(not HELM.is_file(), reason="version-pinned Helm binary unavailable")
def test_chart_default_stays_tagged_and_digest_applies_to_migration():
    base = [str(HELM), "template", "bench", str(CHART), "--namespace", "knowi-analytics"]
    old = subprocess.run(base, capture_output=True, text=True, timeout=30)
    assert old.returncode == 0 and 'image: "knowi-analytics:an1-local"' in old.stdout
    command = [str(HELM), "template", "bench", str(CHART), "--namespace", bench.namespace_for(RUN),
               "--set", "syntheticBenchmark.enabled=true", "--set", "image.repository=" + bench.IMAGE_REPOSITORY,
               "--set", "image.digest=" + bench.manifest_digest(IMAGE_REF), "--set", "migration.enabled=true",
               "--set", "migration.revision=a010"]
    rendered = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert rendered.returncode == 0, rendered.stderr
    job, = [doc for doc in rendered.stdout.split("\n---\n") if "kind: Job\n" in doc]
    assert 'image: "' + IMAGE_REF + '"' in job


def test_packaged_probe_script_compiles_with_existing_history_probe():
    import inspect
    import run_local
    script = (run_local.IMAGE_HISTORY_PROBE + "\n" + inspect.getsource(bench.packaged_source_fingerprint)
              + "\nprint(json.dumps({'packaged_sha256': packaged_source_fingerprint(root, reject_caches=True)}))\n")
    compile(script, "<bounded-source-probe>", "exec")


def test_verified_histories_require_full_head_and_exact_target_prefix():
    import bootstrap_runtime
    full, prefix = bench.verified_histories(bootstrap_runtime, "a010")
    assert full[0] == "a014" and prefix[0] == "a010" and full[1][4:] == prefix[1]
    day_prefix = bench.verified_histories(bootstrap_runtime, "a011")[1]
    assert day_prefix[0] == "a011" and full[1][3:] == day_prefix[1]
    with pytest.raises(bench.LiveFailure): bench.verified_histories(bootstrap_runtime, "a012")
    for bad_full, bad_prefix in ((full[:2] + ("0" * 64,), prefix),
                                 (full, ("a010", tuple(reversed(prefix[1])), prefix[2])),
                                 (full, prefix[:2] + ("0" * 64,))):
        fake = SimpleNamespace(schema_history=lambda revision=None: bad_full if revision is None else bad_prefix)
        with pytest.raises(bench.LiveFailure): bench.verified_histories(fake, "a010")


def test_day_drain_requires_both_workers_and_matching_current_rows():
    calls = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, params):
            calls.append((query, params))
            return SimpleNamespace(fetchone=lambda: (self.ready,))
    connection = Connection()
    fake = SimpleNamespace(projection_drained=lambda target, run: fake.bucket_ready,
                           _guarded_connection=lambda target: connection)
    fake.bucket_ready = False
    assert not bench.projection_drained(fake, {}, RUN, a011=True) and not calls
    fake.bucket_ready = True
    assert bench.projection_drained(fake, {}, RUN, a011=False) and not calls
    connection.ready = False
    assert not bench.projection_drained(fake, {}, RUN, a011=True)
    connection.ready = True
    assert bench.projection_drained(fake, {}, RUN, a011=True)
    query, params = calls[-1]
    assert params == (RUN, RUN) and "phase='complete'" in query
    assert "state<>'ready'" in query and "d.generation IS DISTINCT FROM s.generation" in query
    assert "UPDATE" not in query and "INSERT" not in query


@pytest.mark.parametrize("granted,facts", [(True, 0), (False, 0), (True, 1)])
def test_a011_database_setup_requires_privacy_grants_and_fresh_day_state(monkeypatch, granted, facts):
    events = []
    target = {"name": "synthetic", "owner": "owner"}
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, params=None):
            if "alembic_version" in query:
                return SimpleNamespace(fetchall=lambda: [("a011",)])
            if "count(*) FROM analytics.activity_facts" in query:
                return SimpleNamespace(fetchone=lambda: (facts,))
            if "has_table_privilege" in query:
                return SimpleNamespace(fetchone=lambda: (granted,))
            if "activity_projection_day_backfill" in query:
                return SimpleNamespace(fetchone=lambda: (True,))
            if "activity_projection_backfill" in query:
                return SimpleNamespace(fetchone=lambda: (RUN,))
            raise AssertionError(query)
    owned = SimpleNamespace(create_analytics=lambda *a: (target, {}, {}),
                            install_analytics=lambda urls, **kw: events.append(("install", kw)),
                            verify_analytics_grants=lambda target: events.append("grants"))
    fake = SimpleNamespace(owned=owned, verify_database=lambda *a: None,
                           _guarded_connection=lambda target: Connection(),
                           initialize_projection=lambda *a: events.append("initialize"))
    monkeypatch.setattr(bench, "mark_pending", lambda *a: None)
    monkeypatch.setattr(bench, "mark_confirmed", lambda *a: None)
    monkeypatch.setattr(bench, "_write_exclusive", lambda *a: None)
    if granted and not facts:
        assert bench._database_setup(fake, Path("folder"), "token", RUN, "a011")[0] == target
        assert events[-1] == "initialize"
    else:
        with pytest.raises(bench.LiveFailure, match="privacy grants|preexisting facts"):
            bench._database_setup(fake, Path("folder"), "token", RUN, "a011")
        assert "initialize" not in events
    assert events[0] == ("install", {"full_chain": True, "activity_projection": True, "day_projection": True})


def test_packaged_probe_matches_local_image_tree_and_detects_migration_drift():
    import inspect
    import run_local
    (SERVICE / ".local").mkdir(exist_ok=True)
    base = (SERVICE / ".local").resolve(strict=True)
    folder = base / ("offline-image-source-" + uuid4().hex)
    folder.mkdir()
    try:
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache")
        shutil.copytree(SERVICE / "pymentor_analytics", folder / "pymentor_analytics", ignore=ignore)
        shutil.copytree(SERVICE / "migrations", folder / "migrations", ignore=ignore)
        for name in ("requirements-runtime.txt", "alembic.ini"):
            shutil.copyfile(SERVICE / name, folder / name)
        script = (run_local.IMAGE_HISTORY_PROBE + "\n" + inspect.getsource(bench.packaged_source_fingerprint)
                  + "\nprint(json.dumps({'packaged_sha256': packaged_source_fingerprint(root, reject_caches=True)}))\n")
        script = script.replace('root = Path("/app")', "root = Path(" + repr(str(folder)) + ")")
        result = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        history, packaged = [json.loads(line) for line in result.stdout.splitlines()]
        assert history["runtime_sha256"] == run_local.runtime_files_fingerprint(SERVICE)
        assert packaged["packaged_sha256"] == bench.packaged_source_fingerprint(SERVICE)
        with (folder / "migrations/env.py").open("a", encoding="utf-8") as stream:
            stream.write("\n# offline mismatch fixture\n")
        result = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout.splitlines()[1])["packaged_sha256"] != packaged["packaged_sha256"]
    finally:
        resolved = folder.resolve(strict=True)
        if resolved.parent != base or not resolved.name.startswith("offline-image-source-"):
            raise AssertionError("temporary source cleanup boundary changed")
        shutil.rmtree(resolved)


def test_node_attestation_binds_exact_manifest_to_config_and_platform():
    config = {"os": "linux", "architecture": "amd64", "config": {
        "Entrypoint": ["python", "-m", "pymentor_analytics.app.cli"], "WorkingDir": "/app", "User": "10001"},
        "rootfs": {"type": "layers", "diff_ids": [DIFF_ID]}}
    config_raw = json.dumps(config, separators=(",", ":")).encode()
    config_digest = bench._digest(config_raw)
    manifest = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {"digest": config_digest}, "layers": [{"digest": "sha256:" + "b" * 64, "size": 12}]}
    manifest_raw = json.dumps(manifest, separators=(",", ":")).encode()
    digest = bench._digest(manifest_raw)
    ref = bench.IMAGE_REPOSITORY + "@" + digest
    image_list = ref + " application/vnd.oci.image.manifest.v1+json " + digest + " 1B linux/amd64 -"
    blobs = {digest: manifest_raw, config_digest: config_raw}
    assert bench.verify_node_manifest(ref, config_digest, image_list, blobs.__getitem__)["index"] == "NOT_USED"
    with pytest.raises(bench.LiveFailure): bench.verify_node_manifest(ref, config_digest, "", blobs.__getitem__)
    with pytest.raises(bench.LiveFailure): bench.verify_node_manifest(ref, "sha256:" + "f" * 64, image_list, blobs.__getitem__)
    with pytest.raises(bench.LiveFailure): bench.verify_node_manifest(ref, config_digest, image_list, lambda key: b"tampered")
    with pytest.raises(ValueError): bench.manifest_digest("knowi-analytics:mutable")
    # An index object has a valid digest but is not an executable manifest.
    index_raw = b'{"schemaVersion":2,"manifests":[]}'
    index_digest = bench._digest(index_raw)
    index_ref = bench.IMAGE_REPOSITORY + "@" + index_digest
    index_list = index_ref + " application/vnd.oci.image.index.v1+json " + index_digest + " 1B linux/amd64 -"
    with pytest.raises(bench.LiveFailure): bench.verify_node_manifest(index_ref, config_digest, index_list, lambda key: index_raw)


def test_node_attestation_reads_exact_server_before_any_pod(monkeypatch):
    config = {"os": "linux", "architecture": "amd64", "config": {
        "Entrypoint": ["python", "-m", "pymentor_analytics.app.cli"], "WorkingDir": "/app", "User": "10001"},
        "rootfs": {"type": "layers", "diff_ids": [DIFF_ID]}}
    config_raw = json.dumps(config).encode(); config_digest = bench._digest(config_raw)
    manifest_raw = json.dumps({"schemaVersion": 2, "config": {"digest": config_digest},
                               "layers": [{"digest": "sha256:" + "b" * 64, "size": 12}]}).encode()
    digest = bench._digest(manifest_raw); ref = bench.IMAGE_REPOSITORY + "@" + digest
    image_list = ref + " application/vnd.oci.image.manifest.v1+json " + digest + " 1B linux/amd64 -"
    blobs = {digest: manifest_raw, config_digest: config_raw}
    commands = []
    def command(cmd, **kw):
        commands.append(cmd)
        assert cmd[:7] == ["docker", "--context", "local", "exec", "server-id", "ctr", "--namespace"]
        if cmd[-2:] == ["images", "list"]: return image_list.encode()
        return blobs[cmd[-1]]
    monkeypatch.setattr(bench, "_bounded_command", command)
    runtime = SimpleNamespace(resource_snapshot=SimpleNamespace(local_context=lambda env: "local"))
    args = SimpleNamespace(server_id="server-id", image_ref=ref, image_config_id=config_digest,
                           authority_ref=ref, authority_config_id=config_digest)
    result = bench._image_attestation(args, runtime)
    assert result["application"]["manifest_digest"] == digest
    assert result["authority"]["config_digest"] == config_digest
    assert all("run" not in command for command in commands)


def _fake_docker_archive(*, bad_path=None, symlink=False, duplicate=False, entrypoint=None):
    config = {"os": "linux", "architecture": "amd64", "rootfs": {"type": "layers", "diff_ids": [DIFF_ID]}}
    if entrypoint is not None:
        config["config"] = {"Entrypoint": entrypoint, "WorkingDir": "/app", "User": "10001", "Env": []}
    raw = json.dumps(config, separators=(",", ":")).encode()
    digest = bench._digest(raw)
    config_name = "blobs/sha256/" + digest.removeprefix("sha256:")
    manifest = json.dumps([{"Config": config_name, "RepoTags": None, "Layers": ["layers/one.tar"]}]).encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, data in (("manifest.json", manifest), (config_name, raw), ("layers/one.tar", b"opaque-layer")):
            info = tarfile.TarInfo(name); info.size = len(data); archive.addfile(info, io.BytesIO(data))
        if bad_path:
            info = tarfile.TarInfo(bad_path); info.size = 1; archive.addfile(info, io.BytesIO(b"x"))
        if symlink:
            info = tarfile.TarInfo("linked"); info.type = tarfile.SYMTYPE; info.linkname = "../outside"; archive.addfile(info)
        if duplicate:
            info = tarfile.TarInfo("manifest.json"); info.size = len(manifest); archive.addfile(info, io.BytesIO(manifest))
    return stream.getvalue(), digest


def test_host_archive_checks_config_and_diffids_without_reading_layers(monkeypatch):
    data, digest = _fake_docker_archive()
    reads = []
    original = tarfile.TarFile.extractfile
    def extract(archive, member):
        reads.append(member.name if isinstance(member, tarfile.TarInfo) else member)
        return original(archive, member)
    monkeypatch.setattr(tarfile.TarFile, "extractfile", extract)
    proof = bench.verify_host_archive(io.BytesIO(data), digest, [DIFF_ID], len(data))
    assert proof["config_digest"] == digest and proof["rootfs_diff_ids"] == [DIFF_ID]
    assert proof["layers_extracted"] is False and reads == ["manifest.json", "blobs/sha256/" + digest[7:]]
    with pytest.raises(bench.LiveFailure): bench.verify_host_archive(io.BytesIO(data), digest, ["sha256:" + "f" * 64], len(data))
    with pytest.raises(bench.LiveFailure): bench.verify_host_archive(io.BytesIO(data), "sha256:" + "f" * 64, [DIFF_ID], len(data))
    with pytest.raises(bench.LiveFailure): bench.verify_host_archive(io.BytesIO(data), digest, [DIFF_ID], len(data) - 1)


def test_host_archive_discovers_config_before_node_exists_and_requires_application_entrypoint():
    data, digest = _fake_docker_archive(entrypoint=["python", "-m", "pymentor_analytics.app.cli"])
    proof = bench.verify_host_archive(io.BytesIO(data), None, None, len(data))
    assert proof["config_digest"] == digest and proof["rootfs_diff_ids"] == [DIFF_ID]
    assert proof["layers_extracted"] is False
    invalid, _ = _fake_docker_archive(entrypoint=["sh"])
    with pytest.raises(bench.LiveFailure):
        bench.verify_host_archive(io.BytesIO(invalid), None, None, len(invalid))
    with pytest.raises(bench.LiveFailure):
        bench.verify_host_archive(io.BytesIO(data), None, [DIFF_ID], len(data))


@pytest.mark.parametrize("options", [{"bad_path": "../outside"}, {"bad_path": "/absolute"},
                                    {"bad_path": "C:/outside"}, {"symlink": True}, {"duplicate": True}])
def test_host_archive_rejects_unsafe_paths_links_and_duplicates(options):
    data, digest = _fake_docker_archive(**options)
    with pytest.raises(bench.LiveFailure): bench.verify_host_archive(io.BytesIO(data), digest, [DIFF_ID], len(data))


def test_host_archive_supports_traditional_config_json_name():
    data, digest = _fake_docker_archive()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as source:
        manifest = json.load(source.extractfile("manifest.json"))
        config = source.extractfile(manifest[0]["Config"]).read()
        layer = source.extractfile(manifest[0]["Layers"][0]).read()
    manifest[0]["Config"] = digest[7:] + ".json"
    rebuilt = io.BytesIO()
    with tarfile.open(fileobj=rebuilt, mode="w") as archive:
        for name, raw in (("manifest.json", json.dumps(manifest).encode()), (manifest[0]["Config"], config),
                          ("layers/one.tar", layer)):
            item = tarfile.TarInfo(name); item.size = len(raw); archive.addfile(item, io.BytesIO(raw))
    proof = bench.verify_host_archive(io.BytesIO(rebuilt.getvalue()), digest, [DIFF_ID], len(rebuilt.getvalue()))
    assert proof["config_digest"] == digest


def test_host_archive_rejects_oversized_json_and_tar_metadata():
    config = {"os": "linux", "architecture": "amd64", "padding": "x" * (1024 * 1024),
              "rootfs": {"type": "layers", "diff_ids": [DIFF_ID]}}
    raw = json.dumps(config).encode(); digest = bench._digest(raw)
    config_name = "blobs/sha256/" + digest[7:]
    manifest = json.dumps([{"Config": config_name, "Layers": ["layers/one.tar"]}]).encode()
    archive_data = io.BytesIO()
    with tarfile.open(fileobj=archive_data, mode="w") as archive:
        for name, value in (("manifest.json", manifest), (config_name, raw), ("layers/one.tar", b"layer")):
            member = tarfile.TarInfo(name); member.size = len(value); archive.addfile(member, io.BytesIO(value))
    with pytest.raises(bench.LiveFailure):
        bench.verify_host_archive(io.BytesIO(archive_data.getvalue()), digest, [DIFF_ID], len(archive_data.getvalue()))
    _, good_digest = _fake_docker_archive()
    oversized = io.BytesIO()
    with tarfile.open(fileobj=oversized, mode="w", format=tarfile.PAX_FORMAT) as archive:
        member = tarfile.TarInfo("extra"); member.size = 1; member.pax_headers = {"comment": "x" * (2 * 1024 * 1024 + 1)}
        archive.addfile(member, io.BytesIO(b"x"))
    with pytest.raises(bench.LiveFailure):
        bench.verify_host_archive(io.BytesIO(oversized.getvalue()), good_digest, [DIFF_ID], len(oversized.getvalue()))


@pytest.mark.parametrize("wrong_diff,discover", [(False, False), (True, False), (False, True)])
def test_host_save_pins_docker_id_and_reconciles_on_mismatch(monkeypatch, wrong_diff, discover):
    data, config_digest = _fake_docker_archive(entrypoint=["python", "-m", "pymentor_analytics.app.cli"])
    assert HOST_IMAGE_ID != config_digest
    (SERVICE / ".local").mkdir(exist_ok=True)
    base = (SERVICE / ".local").resolve(strict=True)
    folder = base / ("offline-host-archive-" + uuid4().hex)
    folder.mkdir()
    events, commands = [], []
    runtime = SimpleNamespace(resource_snapshot=SimpleNamespace(local_context=lambda env: "local"))
    owned = SimpleNamespace(record=lambda *a, **kw: events.append("owner"))
    monitor = SimpleNamespace(check=lambda: events.append("floor"))
    monkeypatch.setattr(bench, "_bounded_command", lambda command, **kw: HOST_IMAGE_ID.encode())
    monkeypatch.setattr(bench, "mark_pending", lambda *a: events.append("pending"))
    monkeypatch.setattr(bench, "mark_confirmed", lambda *a: events.append("confirmed"))
    class Process:
        def __init__(self): self.stdout = io.BytesIO(data)
        def poll(self): return 0
        def wait(self, timeout=None): return 0
        def kill(self): events.append("kill")
    def popen(command, **kwargs):
        commands.append(command)
        return Process()
    monkeypatch.setattr(subprocess, "Popen", popen)
    node = {"config_digest": config_digest,
            "rootfs_diff_ids": ["sha256:" + "f" * 64 if wrong_diff else DIFF_ID]}
    if discover: node = None
    try:
        if wrong_diff:
            with pytest.raises(bench.LiveFailure):
                bench._host_image_proof(folder, "token", RUN, "application", HOST_IMAGE_ID, node, runtime, owned, monitor, 128)
            assert "confirmed" not in events and (folder / "host-image-application.tar").exists()
        else:
            proof = bench._host_image_proof(folder, "token", RUN, "application", HOST_IMAGE_ID, node, runtime, owned, monitor, 128)
            assert proof["host_image_id"] == HOST_IMAGE_ID and proof["config_digest"] == config_digest
            assert events.index("pending") < events.index("confirmed")
        assert len(commands) == 1 and commands[0][-4:] == ["save", "--platform", "linux/amd64", HOST_IMAGE_ID]
        assert not any("push" in command or "RepoDigests" in command for command in commands)
    finally:
        resolved = folder.resolve(strict=True)
        if resolved.parent != base or not resolved.name.startswith("offline-host-archive-"):
            raise AssertionError("temporary archive cleanup boundary changed")
        shutil.rmtree(resolved)


def test_host_save_preserves_partial_archive_and_does_not_retry(monkeypatch):
    (SERVICE / ".local").mkdir(exist_ok=True)
    base = (SERVICE / ".local").resolve(strict=True)
    folder = base / ("offline-host-archive-" + uuid4().hex)
    folder.mkdir()
    events = []
    runtime = SimpleNamespace(resource_snapshot=SimpleNamespace(local_context=lambda env: "local"))
    owned = SimpleNamespace(record=lambda *a, **kw: events.append("owner"))
    monkeypatch.setattr(bench, "_bounded_command", lambda command, **kw: HOST_IMAGE_ID.encode())
    monkeypatch.setattr(bench, "mark_pending", lambda *a: events.append("pending"))
    monkeypatch.setattr(bench, "mark_confirmed", lambda *a: events.append("confirmed"))
    class Pipe:
        reads = 0
        def read(self, size):
            self.reads += 1
            if self.reads == 1: return b"partial"
            raise OSError("disconnected")
        def close(self): pass
    class Process:
        def __init__(self): self.stdout = Pipe(); self.stopped = False
        def poll(self): return 0 if self.stopped else None
        def wait(self, timeout=None): return 0
        def kill(self): self.stopped = True; events.append("kill")
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: (events.append("save"), Process())[1])
    node = {"config_digest": "sha256:" + "e" * 64, "rootfs_diff_ids": [DIFF_ID]}
    try:
        with pytest.raises(bench.LiveFailure, match="outcome unknown"):
            bench._host_image_proof(folder, "token", RUN, "application", HOST_IMAGE_ID, node, runtime,
                                    owned, SimpleNamespace(check=lambda: None), 128)
        assert events == ["owner", "pending", "save", "kill"]
        assert (folder / "host-image-application.tar").read_bytes() == b"partial"
    finally:
        resolved = folder.resolve(strict=True)
        if resolved.parent != base or not resolved.name.startswith("offline-host-archive-"):
            raise AssertionError("temporary archive cleanup boundary changed")
        shutil.rmtree(resolved)


def test_source_probe_is_bounded_host_id_pinned_and_preserves_unknown_outcome(monkeypatch):
    events = []
    revisions = tuple(f"a{n:03d}" for n in range(14, 0, -1))
    history = ("a014", revisions, bench.HISTORY_SHA256["a014"])
    prefix = ("a010", revisions[4:], bench.HISTORY_SHA256["a010"])
    source = SimpleNamespace(runtime=SimpleNamespace(schema_history=lambda revision=None: prefix if revision == "a010" else history), ROOT=Path("source"),
                             runtime_files_fingerprint=lambda root: "runtime-hash", IMAGE_HISTORY_PROBE="read-only hash script")
    runtime = SimpleNamespace(resource_snapshot=SimpleNamespace(local_context=lambda env: "local"))
    owned = SimpleNamespace(record=lambda *a, **kw: events.append("owner"))
    monkeypatch.setattr(bench, "mark_pending", lambda *a: events.append("pending"))
    monkeypatch.setattr(bench, "mark_confirmed", lambda *a: events.append("confirmed"))
    monkeypatch.setattr(bench, "packaged_source_fingerprint", lambda root: "packaged-hash")
    monkeypatch.setattr("inspect.getsource", lambda function: "def packaged_source_fingerprint(root, reject_caches=False): return 'packaged-hash'")
    def run(command, **kw):
        if "run" not in command: return b""
        events.append("probe")
        for option in ("--pull=never", "--network=none", "--read-only", "--cap-drop=ALL", "--user=10001", "--no-healthcheck"):
            assert option in command
        assert HOST_IMAGE_ID in command and "--platform=linux/amd64" in command and "--mount" not in command and "--volume" not in command
        first = json.dumps({"history": [history[0], list(history[1]), history[2]], "runtime_sha256": "runtime-hash"})
        return (first + "\n" + json.dumps({"packaged_sha256": "packaged-hash"})).encode()
    monkeypatch.setattr(bench, "_bounded_command", run)
    evidence = bench._source_probe("folder", "token", RUN, "application", HOST_IMAGE_ID, runtime, source, owned)
    assert events == ["owner", "pending", "probe", "confirmed"]
    assert evidence["runtime_sha256"] == "runtime-hash"
    assert evidence["packaged_history_head"] == "a014" and evidence["target_revision"] == "a010"
    events.clear()
    def timeout(command, **kw):
        if "run" in command:
            events.append("probe")
            raise bench.LiveFailure("unknown outcome")
        return b""
    monkeypatch.setattr(bench, "_bounded_command", timeout)
    with pytest.raises(bench.LiveFailure): bench._source_probe("folder", "token", RUN, "application", HOST_IMAGE_ID, runtime, source, owned)
    assert events == ["owner", "pending", "probe"]
    events.clear()
    with pytest.raises(bench.LiveFailure, match="immutable host image ID"):
        bench._source_probe("folder", "token", RUN, "application", "mutable:tag", runtime, source, owned)
    assert not events


def test_pod_inventory_refuses_restart_image_or_replica_drift():
    digest = "sha256:" + "a" * 64
    namespace = bench.namespace_for(RUN)
    items = []
    for index, role in enumerate(("collector", "processor", "query", "privacy"), 1):
        items.append({"metadata": {"namespace": namespace, "name": "bench-" + role,
                                   "uid": str(UUID(int=index)), "labels": {
                                       "app.kubernetes.io/name": "analytics",
                                       "app.kubernetes.io/instance": "bench",
                                       "app.kubernetes.io/component": role}},
                      "spec": {"containers": [{"name": role, "resources": {"limits": {"memory": "256Mi"}}}]},
                      "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                                 "containerStatuses": [{"name": role, "ready": True, "restartCount": 0,
                                                        "imageID": digest}]}})
    snapshot = {"items": items}
    inventory = bench.pod_inventory(snapshot, namespace, (1, 1, 1), digest)
    assert inventory["query"][0]["resources"]["limits"]["memory"] == "256Mi"
    items[0]["status"]["containerStatuses"][0]["restartCount"] = 1
    with pytest.raises(ValueError): bench.pod_inventory(snapshot, namespace, (1, 1, 1), digest)
    items[0]["status"]["containerStatuses"][0]["restartCount"] = 0
    with pytest.raises(ValueError): bench.pod_inventory(snapshot, namespace, (2, 1, 1), digest)
    with pytest.raises(ValueError): bench.pod_inventory(snapshot, namespace, (1, 1, 1), "sha256:" + "b" * 64)


def test_pod_inventory_accepts_pinned_cri_manifest_config_pair_and_rejects_mixed_images():
    digest = "sha256:" + "b" * 64
    namespace = bench.namespace_for(RUN)
    items = []
    for index, role in enumerate(("collector", "processor", "query", "privacy"), 1):
        items.append({"metadata": {"namespace": namespace, "name": "bench-" + role,
                                   "uid": str(UUID(int=index)), "labels": {
                                       "app.kubernetes.io/name": "analytics", "app.kubernetes.io/instance": "bench",
                                       "app.kubernetes.io/component": role}},
                      "spec": {"containers": [{"name": role, "image": IMAGE_REF, "resources": {}}]},
                      "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                                 "containerStatuses": [{"name": role, "ready": True, "restartCount": 0,
                                                        "image": digest, "imageID": IMAGE_REF}]}})
    assert len(bench.pod_inventory({"items": items}, namespace, (1, 1, 1), digest, IMAGE_REF)) == 4
    for change in ("wrong_config", "wrong_manifest", "index_id", "spec_image", "mixed_role"):
        modified = copy.deepcopy(items)
        if change == "wrong_config": modified[0]["status"]["containerStatuses"][0]["image"] = "sha256:" + "c" * 64
        if change in ("wrong_manifest", "index_id"): modified[0]["status"]["containerStatuses"][0]["imageID"] = "docker.io/library/knowi-analytics@sha256:" + "c" * 64
        if change == "spec_image": modified[0]["spec"]["containers"][0]["image"] = "knowi-analytics:mutable"
        if change == "mixed_role": modified[0]["status"]["containerStatuses"][0]["name"] = "query"
        with pytest.raises(ValueError): bench.pod_inventory({"items": modified}, namespace, (1, 1, 1), digest, IMAGE_REF)
    with pytest.raises(ValueError): bench.pod_inventory({"items": items}, namespace, (1, 1, 1), digest)
    legacy = copy.deepcopy(items)
    for item in legacy:
        item["status"]["containerStatuses"][0]["imageID"] = digest
    assert len(bench.pod_inventory({"items": legacy}, namespace, (1, 1, 1), digest, IMAGE_REF)) == 4
    legacy[0]["status"]["containerStatuses"][0]["image"] = "sha256:" + "c" * 64
    with pytest.raises(ValueError): bench.pod_inventory({"items": legacy}, namespace, (1, 1, 1), digest, IMAGE_REF)


def test_http_adapter_requires_guards_and_uses_shared_workload():
    calls = []
    class Benchmark:
        ROOT = Path("unused")
        def profile_load(self, path): return profile
        async def execute_workload(self, *args, **kwargs):
            calls.append((args, kwargs))
            assert await kwargs["metrics_sampler"]() == ["per-pod metrics"]
            return {"oracle": {"status": "UNRUN"}}
        async def sample_metrics(self, client, processes):
            return ["per-pod metrics"]
    profile = {"version": bench.PROFILE, "seed": bench.SEED,
               "warmup_seconds": 300, "measurement_seconds": 900}
    async def drain(): pass
    result = asyncio.run(bench.run_http_workload(Benchmark(), "client", "processes", profile,
                                                10, RUN, "key", "issuer", "fingerprint", "monitor", drain))
    assert result["oracle"]["status"] == "UNRUN"
    assert calls[0][0][2:6] == (profile, 10, bench.SEED, RUN)
    assert calls[0][1]["projection_wait"] is drain
    with pytest.raises(ValueError):
        asyncio.run(bench.run_http_workload(Benchmark(), "client", "processes", profile,
                                           10, RUN, "key", "issuer", "fingerprint", None, drain))


def test_unknown_mutation_is_retained_and_cannot_be_automatically_reissued(monkeypatch):
    identity, observed = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    records = {}
    monkeypatch.setattr(bench, "_operation_path", lambda directory, operation, state: Path(operation + "." + state))
    def write_exclusive(path, value):
        if str(path) in records: raise FileExistsError
        records[str(path)] = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    monkeypatch.setattr(bench, "_write_exclusive", write_exclusive)
    monkeypatch.setattr(Path, "read_bytes", lambda path: records[str(path)])
    monkeypatch.setattr(Path, "is_symlink", lambda path: False)
    monkeypatch.setattr(bench.os.path, "isjunction", lambda path: False, raising=False)
    bench.mark_pending("private", RUN, "create-namespace", identity)
    with pytest.raises(FileExistsError):
        bench.mark_pending("private", RUN, "create-namespace", identity)
    with pytest.raises(ValueError):
        bench.mark_confirmed("private", RUN, "create-namespace", observed, observed)
    assert sorted(records) == ["create-namespace.pending"]
    bench.mark_confirmed("private", RUN, "create-namespace", identity, observed)
    assert sorted(records) == ["create-namespace.confirmed", "create-namespace.pending"]
    with pytest.raises(FileExistsError):
        bench.mark_confirmed("private", RUN, "create-namespace", identity, observed)


def test_port_forward_is_loopback_owned_and_detects_uid_change(monkeypatch):
    namespace = bench.namespace_for(RUN)
    uid = str(UUID(int=1))
    pod = {"name": "bench-query-abc", "uid": uid}
    calls = []
    class Process:
        pid = 123
    class Owned:
        def unused_port(self): return 51234
        def spawn_owned(self, command, **kwargs):
            calls.append(command)
            process = Process()
            kwargs["children"].append(process)
            kwargs["on_spawn"](kwargs["children"])
            return process
        def wait_port(self, process, port): calls.append(("wait", port))
        def record(self, folder, token, **kwargs): calls.append(("record", kwargs["processes"]))
    class Job:
        def assert_listener(self, port): calls.append(("owner", port))
    current = {"metadata": {"uid": uid, "namespace": namespace}}
    def read_pod(name): return current
    result = bench.start_pod_forward(Owned(), "kubectl.exe", "private-config", "k3d-knowi-analytics-01a1df75-v2",
                                    namespace, pod, "folder", "token", Job(), [], read_pod)
    command = calls[0]
    assert command[-4:] == ["--address", "127.0.0.1", "pod/bench-query-abc", "51234:8000"]
    assert result["url"] == "http://127.0.0.1:51234" and ("owner", 51234) in calls
    current["metadata"]["uid"] = str(UUID(int=2))
    with pytest.raises(ValueError):
        bench.start_pod_forward(Owned(), "kubectl.exe", "private-config", "k3d-knowi-analytics-01a1df75-v2",
                                namespace, pod, "folder", "token", Job(), [], read_pod)
    assert len([call for call in calls if isinstance(call, list)]) == 1


def test_budget_fixture_requires_exact_namespace_uid_image_and_resources():
    namespace = bench.namespace_for(RUN)
    uid, image = str(UUID(int=1)), "sha256:" + "a" * 64
    resources = {"requests": {"cpu": "50m", "memory": "64Mi"},
                 "limits": {"cpu": "500m", "memory": "256Mi"}}
    pod = {"metadata": {"name": "bench-collector-abc", "namespace": namespace, "uid": uid,
                        "labels": {"app.kubernetes.io/name": "analytics", "app.kubernetes.io/instance": "bench",
                                   "app.kubernetes.io/component": "collector"}},
           "spec": {"automountServiceAccountToken": False, "securityContext": {"runAsNonRoot": True, "runAsUser": 10001},
                    "containers": [{"name": "collector", "image": "knowi-analytics:frozen", "imagePullPolicy": "Never",
                                    "resources": resources, "securityContext": {"readOnlyRootFilesystem": True,
                                        "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}}]},
           "status": {"containerStatuses": [{"imageID": image, "restartCount": 0}]}}
    fixture = {"namespace": namespace, "pods": {uid: {"name": pod["metadata"]["name"], "role": "collector",
               "image": "knowi-analytics:frozen", "image_id": image, "resources": copy.deepcopy(resources)}}}
    run_k3s.validate_benchmark_fixture(pod, fixture)
    for change in ("namespace", "uid", "image", "resource", "hostpath", "role", "ephemeral"):
        modified = copy.deepcopy(pod)
        if change == "namespace": modified["metadata"]["namespace"] = "other"
        if change == "uid": modified["metadata"]["uid"] = str(UUID(int=2))
        if change == "image": modified["status"]["containerStatuses"][0]["imageID"] = "sha256:" + "b" * 64
        if change == "resource": modified["spec"]["containers"][0]["resources"]["limits"]["memory"] = "1Gi"
        if change == "hostpath": modified["spec"]["volumes"] = [{"name": "host", "hostPath": {"path": "/"}}]
        if change == "role": modified["metadata"]["labels"]["app.kubernetes.io/component"] = "query"
        if change == "ephemeral": modified["spec"]["ephemeralContainers"] = [{"name": "debug"}]
        with pytest.raises(ValueError): run_k3s.validate_benchmark_fixture(modified, fixture)


def test_budget_fixture_accepts_exact_attested_manifest_config_cri_shape():
    namespace = bench.namespace_for(RUN)
    uid, config = str(UUID(int=1)), "sha256:" + "b" * 64
    resources = {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "250m", "memory": "128Mi"}}
    pod = {"metadata": {"name": "bench-journal-binder", "namespace": namespace, "uid": uid,
                        "labels": {"app.kubernetes.io/name": "analytics", "app.kubernetes.io/instance": "bench",
                                   "app.kubernetes.io/component": "volume-probe"}},
           "spec": {"automountServiceAccountToken": False, "securityContext": {"runAsNonRoot": True, "runAsUser": 10001},
                    "containers": [{"name": "volume-probe", "image": IMAGE_REF, "imagePullPolicy": "Never", "resources": resources,
                                    "securityContext": {"readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False,
                                                        "capabilities": {"drop": ["ALL"]}}}]},
           "status": {"containerStatuses": [{"image": config, "imageID": IMAGE_REF, "restartCount": 0}]}}
    fixture = {"namespace": namespace, "pods": {uid: {"name": "bench-journal-binder", "role": "volume-probe",
               "image": IMAGE_REF, "image_id": config, "resources": copy.deepcopy(resources)}}}
    run_k3s.validate_benchmark_fixture(pod, fixture)
    for change in ("manifest", "config", "spec", "role", "index"):
        bad = copy.deepcopy(pod)
        if change in ("manifest", "index"): bad["status"]["containerStatuses"][0]["imageID"] = "docker.io/library/knowi-analytics@sha256:" + "c" * 64
        if change == "config": bad["status"]["containerStatuses"][0]["image"] = "sha256:" + "c" * 64
        if change == "spec": bad["spec"]["containers"][0]["image"] = "knowi-analytics:mutable"
        if change == "role": bad["metadata"]["labels"]["app.kubernetes.io/component"] = "authority"
        with pytest.raises(ValueError): run_k3s.validate_benchmark_fixture(bad, fixture)


def test_budget_guard_still_rejects_unknown_pod_and_invalid_namespace(monkeypatch):
    namespace = bench.namespace_for(RUN)
    fixture = {"namespace": namespace, "pods": {}}
    unknown = {"metadata": {"name": "unrecorded", "namespace": namespace, "uid": str(UUID(int=2)), "labels": {}},
               "status": {"phase": "Running"}}
    def fake_run(command):
        suffix = command[-1]
        if suffix == "/sys/fs/cgroup" and "%T" in command: output = "cgroup2fs"
        elif suffix.endswith("cgroup.controllers"): output = "memory"
        elif suffix.endswith("cgroup.procs") or suffix.endswith("memory.current"): output = "1"
        elif suffix == "json": output = json.dumps({"items": [unknown]})
        else: raise AssertionError(command)
        return SimpleNamespace(stdout=output)
    monkeypatch.setattr(run_k3s, "run", fake_run)
    with pytest.raises(ValueError, match="unexpected Pod scope"):
        run_k3s._verify_cgroup_tree(["docker"], "node", "kubectl", benchmark_fixture=fixture)
    fixture["namespace"] = "knowi-analytics-bench-" + "a" * 32
    with pytest.raises(ValueError, match="invalid benchmark fixture"):
        run_k3s._verify_cgroup_tree(["docker"], "node", "kubectl", benchmark_fixture=fixture)


def test_budget_guard_allows_only_recorded_fixture_in_full_pod_inventory(monkeypatch):
    namespace, uid = bench.namespace_for(RUN), str(UUID(int=1))
    image = "sha256:" + "a" * 64
    resources = {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "500m", "memory": "256Mi"}}
    pod = {"metadata": {"name": "bench-collector-abc", "namespace": namespace, "uid": uid,
                        "labels": {"app.kubernetes.io/name": "analytics", "app.kubernetes.io/instance": "bench",
                                   "app.kubernetes.io/component": "collector"}},
           "spec": {"nodeName": run_k3s.SERVER, "automountServiceAccountToken": False,
                    "securityContext": {"runAsNonRoot": True, "runAsUser": 10001},
                    "containers": [{"name": "collector", "image": "knowi-analytics:frozen", "imagePullPolicy": "Never",
                                    "resources": resources, "securityContext": {"readOnlyRootFilesystem": True,
                                       "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}}]},
           "status": {"phase": "Running", "qosClass": "Burstable",
                      "containerStatuses": [{"imageID": image, "restartCount": 0}]}}
    fixture = {"namespace": namespace, "pods": {uid: {"name": "bench-collector-abc", "role": "collector",
               "image": "knowi-analytics:frozen", "image_id": image, "resources": copy.deepcopy(resources)}}}
    systems = [{"metadata": {"name": name + "-abc", "namespace": "kube-system", "uid": str(UUID(int=i)), "labels": {}},
                "spec": {"nodeName": run_k3s.SERVER}, "status": {"phase": "Running", "qosClass": "Burstable"}}
               for i, name in enumerate(("coredns", "metrics-server", "local-path-provisioner"), 10)]
    pods = systems + [pod]
    def fake_run(command):
        suffix = command[-1]
        if suffix == "/sys/fs/cgroup" and "%T" in command: output = "cgroup2fs"
        elif suffix.endswith("cgroup.controllers"): output = "memory"
        elif suffix.endswith("cgroup.procs") or suffix.endswith("memory.current"): output = "1"
        elif suffix == "json": output = json.dumps({"items": pods})
        else: raise AssertionError(command)
        return SimpleNamespace(stdout=output)
    monkeypatch.setattr(run_k3s, "run", fake_run)
    run_k3s._verify_cgroup_tree(["docker"], "node", "kubectl", benchmark_fixture=fixture)
    pods.pop()
    with pytest.raises(ValueError, match="benchmark fixture missing"):
        run_k3s._verify_cgroup_tree(["docker"], "node", "kubectl", benchmark_fixture=fixture)


def test_live_requires_go_before_any_external_activity(monkeypatch):
    monkeypatch.delenv("ANALYTICS_BENCHMARK_GO", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("unexpected external process"))
    with pytest.raises(bench.LiveFailure, match="GO required"):
        bench.run_case(SimpleNamespace())


def test_source_probe_requires_its_own_go_before_external_activity(monkeypatch):
    monkeypatch.setenv("ANALYTICS_BENCHMARK_GO", "1")
    monkeypatch.delenv("ANALYTICS_BENCHMARK_IMAGE_PROBE_GO", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("unexpected external process"))
    with pytest.raises(bench.LiveFailure, match="image-probe GO"):
        bench.run_case(SimpleNamespace())


def test_uncertain_create_keeps_pending_and_never_confirms(monkeypatch):
    log = []
    monkeypatch.setattr(bench, "mark_pending", lambda *a: log.append("pending"))
    monkeypatch.setattr(bench, "mark_confirmed", lambda *a: log.append("confirmed"))
    class FakeKube:
        namespace = bench.namespace_for(RUN)
        def create(self, obj):
            log.append("create")
            raise bench.LiveFailure("unknown outcome")
    obj = {"kind": "PersistentVolumeClaim", "metadata": {"name": "fresh"}}
    with pytest.raises(bench.LiveFailure):
        bench._create_recorded(FakeKube(), "folder", RUN, "make-pvc", obj)
    assert log == ["pending", "create"]


def test_uncertain_database_creation_keeps_pending_without_retry(monkeypatch):
    log = []
    monkeypatch.setattr(bench, "mark_pending", lambda *a: log.append("pending"))
    monkeypatch.setattr(bench, "mark_confirmed", lambda *a: log.append("confirmed"))
    def create(folder, token):
        log.append("create")
        raise TimeoutError("unknown database outcome")
    fake = SimpleNamespace(owned=SimpleNamespace(create_analytics=create))
    with pytest.raises(TimeoutError): bench._database_setup(fake, "folder", "token", RUN)
    assert log == ["pending", "create"]


def test_private_owner_write_flushes_and_fsyncs_before_return(monkeypatch):
    import benchmark_activity as activity
    events = []
    class Stream:
        def __enter__(self): return self
        def __exit__(self, *args): events.append("close")
        def write(self, value): events.append("write")
        def flush(self): events.append("flush")
        def fileno(self): return 123
    monkeypatch.setattr(activity.owned.os, "open", lambda *a: 123)
    monkeypatch.setattr(activity.owned.os, "fdopen", lambda *a, **kw: Stream())
    monkeypatch.setattr(activity.owned.os, "fsync", lambda fd: events.append("fsync"))
    activity.owned.write_new(Path("owner.json"), {"state": "analytics_create_pending"})
    assert events.index("write") < events.index("flush") < events.index("fsync") < events.index("close")


def test_database_name_and_sentinel_recorded_before_first_create_connection(monkeypatch):
    import benchmark_activity as activity
    from tools import bootstrap_postgres
    events = []
    monkeypatch.setattr(bootstrap_postgres, "config", lambda *a: {})
    monkeypatch.setattr(activity.owned, "record", lambda path, token, **kw: events.append(("record", kw)))
    def connect(*args):
        events.append(("connect", None))
        raise TimeoutError("unknown outcome")
    monkeypatch.setattr(bootstrap_postgres, "connect", connect)
    with pytest.raises(TimeoutError): activity.owned.create_analytics(Path("private"), "token")
    assert [event[0] for event in events] == ["record", "connect"]
    assert events[0][1]["state"] == "analytics_create_pending"
    assert events[0][1]["analytics_name"].startswith("analytics_test_")
    assert len(events[0][1]["analytics_sentinel"]) == 64


def test_real_database_intent_is_fsynced_and_replaced_before_create(monkeypatch):
    import benchmark_activity as activity
    from tools import bootstrap_postgres
    events, parts = [], []
    owner = Path("private") / "owner.json"
    original_read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *a, **kw: '{"token":"token"}' if path == owner else original_read(path, *a, **kw))
    class Stream:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def write(self, value): parts.append(value)
        def flush(self): events.append("flush")
        def fileno(self): return 123
    monkeypatch.setattr(activity.owned.os, "open", lambda *a: 123)
    monkeypatch.setattr(activity.owned.os, "fdopen", lambda *a, **kw: Stream())
    monkeypatch.setattr(activity.owned.os, "fsync", lambda fd: events.append("fsync"))
    monkeypatch.setattr(activity.owned.os, "replace", lambda src, dst: events.append("replace"))
    monkeypatch.setattr(bootstrap_postgres, "config", lambda *a: {})
    def connect(*args):
        events.append("connect")
        raise TimeoutError("unknown outcome")
    monkeypatch.setattr(bootstrap_postgres, "connect", connect)
    with pytest.raises(TimeoutError): activity.owned.create_analytics(Path("private"), "token")
    assert events == ["flush", "fsync", "replace", "connect"]
    record = json.loads("".join(parts))
    assert record["state"] == "analytics_create_pending"
    assert record["analytics_name"].startswith("analytics_test_") and len(record["analytics_sentinel"]) == 64


def test_helm_timeout_never_confirms_or_retries(monkeypatch):
    log = []
    monkeypatch.setattr(bench, "mark_pending", lambda *a: log.append("pending"))
    monkeypatch.setattr(bench, "mark_confirmed", lambda *a: log.append("confirmed"))
    def timeout(*args, **kwargs):
        log.append("invoked")
        raise subprocess.TimeoutExpired("helm", 135)
    monkeypatch.setattr(subprocess, "run", timeout)
    args = SimpleNamespace(helm="helm", kubeconfig="private", context="local")
    kube = SimpleNamespace(namespace=bench.namespace_for(RUN))
    with pytest.raises(bench.LiveFailure, match="outcome unknown"):
        bench._helm_recorded(args, kube, "folder", RUN, "helm-install", "install", {})
    assert log == ["pending", "invoked"]


def test_monitor_stops_on_pod_uid_drift_before_further_activity(monkeypatch):
    host = SimpleNamespace(check=lambda: None)
    fixture = {"namespace": bench.namespace_for(RUN), "pods": {"original": {}}}
    monitor = bench.K3sMonitor(None, None, None, host, fixture,
                              lambda: {"namespace": fixture["namespace"], "pods": {"replacement": {}}}, [], None)
    monkeypatch.setattr(bench, "_assert_target", lambda *a: pytest.fail("target check after Pod drift"))
    with pytest.raises(bench.LiveFailure, match="Pod identity"):
        monitor.check()


def test_resource_samples_fail_closed_when_pod_metrics_are_missing():
    class Kube:
        def call(self, args): return ""
    with pytest.raises(bench.LiveFailure, match="resource metrics unavailable"):
        bench._kube_observations(Kube())


def test_delete_uses_pod_uid_precondition(monkeypatch):
    received = []
    class Response:
        status_code = 200
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request(self, method, url, **kwargs):
            received.append((method, url, kwargs["json"]))
            return Response()
    import httpx
    monkeypatch.setattr(httpx, "Client", Client)
    job = SimpleNamespace(assert_listener=lambda port: None)
    process = SimpleNamespace(poll=lambda: None)
    kube = bench.Kube("kubectl", "private", "context", bench.namespace_for(RUN))
    kube.proxy = {"process": process, "port": 12345, "job": job}
    uid = str(UUID(int=9))
    kube.delete_uid("pods", "bench-journal-binder", uid)
    assert received[0][0] == "DELETE"
    assert received[0][1].endswith("/pods/bench-journal-binder")
    assert received[0][2]["preconditions"] == {"uid": uid}
    assert received[0][2]["propagationPolicy"] == "Foreground"


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl client unavailable")
def test_kubectl_proxy_transmits_delete_uid_to_offline_fake_api():
    import httpx
    class Handler(http.server.BaseHTTPRequestHandler):
        received = None
        def do_GET(self):
            data = b"ok"
            self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_DELETE(self):
            count = int(self.headers.get("Content-Length", "0"))
            Handler.received = (self.path, json.loads(self.rfile.read(count)))
            data = b'{"kind":"Status","status":"Success"}'
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        def log_message(self, *args): pass
    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fd, config = tempfile.mkstemp(prefix="kubectl-offline-", suffix=".yaml", dir=SERVICE / "tests/benchmark")
    process = None
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as stream:
            stream.write("apiVersion: v1\nkind: Config\nclusters:\n- name: fake\n  cluster:\n    server: http://127.0.0.1:"
                         + str(server.server_port) + "\ncontexts:\n- name: fake\n  context:\n    cluster: fake\n"
                         "    user: fake\ncurrent-context: fake\nusers:\n- name: fake\n  user: {}\n")
        with socket.socket() as connection:
            connection.bind(("127.0.0.1", 0)); port = connection.getsockname()[1]
        process = subprocess.Popen(["kubectl", "--kubeconfig", config, "--context", "fake", "proxy",
                                    "--address", "127.0.0.1", "--port", str(port)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ready = False
        for _ in range(100):
            try:
                ready = httpx.get("http://127.0.0.1:" + str(port) + "/readyz", timeout=.2, trust_env=False).status_code == 200
                if ready: break
            except httpx.HTTPError:
                time.sleep(.05)
        assert ready
        kube = bench.Kube("kubectl", config, "fake", bench.namespace_for(RUN))
        kube.proxy = {"process": process, "port": port, "job": SimpleNamespace(assert_listener=lambda value: None)}
        uid = str(UUID(int=9))
        kube.delete_uid("pods", "bench-journal-binder", uid)
        assert Handler.received[0].endswith("/pods/bench-journal-binder")
        assert Handler.received[1]["preconditions"] == {"uid": uid}
    finally:
        if process is not None:
            process.terminate(); process.wait(timeout=5)
        server.shutdown(); server.server_close()
        Path(config).unlink()


@pytest.mark.parametrize("a011,failure", [(False, None), (False, "install"), (False, "hostproof"),
                                           (True, None), (True, "backfill")])
def test_case_orders_fresh_setup_load_and_reconciliation(monkeypatch, a011, failure):
    import benchmark_activity as activity
    import k3s_database
    operations, writes = [], []
    namespace = bench.namespace_for(RUN)
    uid = str(UUID(int=1))
    output = SERVICE / ".local/benchmark-results/mock-case.json"
    lock = SERVICE / ".local/benchmark-k3s.active.json"
    args = SimpleNamespace(run_id=str(RUN), namespace=namespace, stage=10, replicas=(1, 1, 1), repeat=1,
                           context="k3d-knowi-analytics-01a1df75-v2", server_id="a" * 64,
                           cluster_uid=uid, storage_class_uid=uid, resource_window="window-1", kubectl="k",
                           kubectl_sha256="b" * 64, helm="h", helm_sha256="c" * 64, output=str(output),
                           image_ref=IMAGE_REF, image_config_id="sha256:" + "d" * 64, authority_ref=IMAGE_REF,
                           authority_config_id="sha256:" + "e" * 64, k3s_image_id="sha256:" + "f" * 64,
                           host_image_id=HOST_IMAGE_ID, host_authority_image_id=HOST_IMAGE_ID, archive_max_mib=128,
                           retained_authority_image_id=None, web_temporarily_paused=False, a011=a011)
    monkeypatch.setenv("ANALYTICS_BENCHMARK_GO", "1")
    monkeypatch.setenv("ANALYTICS_BENCHMARK_IMAGE_PROBE_GO", "1")
    class WindowsOS:
        name = "nt"
        def __getattr__(self, key): return getattr(os, key)
    monkeypatch.setattr(bench, "os", WindowsOS())
    monkeypatch.setattr(bench, "_binary", lambda path, digest: Path(path))
    monkeypatch.setattr(bench, "_assert_target", lambda *a: {"status": "budget_verified"})
    monkeypatch.setattr(bench, "_image_attestation", lambda *a: {
        "application": {"config_digest": args.image_config_id}, "authority": {"config_digest": args.authority_config_id}})
    def host_proof(*values):
        operations.append("host-proof")
        assert values[4] == HOST_IMAGE_ID
        if failure == "hostproof": raise bench.LiveFailure("archive outcome unknown")
        return {"archive_sha256": "mock"}
    def source_probe(*values):
        operations.append("source-probe")
        assert values[4] == HOST_IMAGE_ID
        return {"scope": "mock"}
    monkeypatch.setattr(bench, "_host_image_proof", host_proof)
    monkeypatch.setattr(bench, "_source_probe", source_probe)
    monkeypatch.setattr(bench, "_source_fingerprint", lambda *a: "sha256:" + "f" * 64)
    monkeypatch.setattr(bench, "verified_histories", lambda runtime, target: (("a011", (), "full"), (target, (), "prefix")))
    monkeypatch.setattr(bench, "_write_exclusive", lambda path, data: writes.append((Path(path).name, copy.deepcopy(data))))
    monkeypatch.setattr(bench, "_save_result", lambda path, run, data: writes.append(("result", copy.deepcopy(data))))
    original_read, original_unlink, original_mkdir = Path.read_text, Path.unlink, Path.mkdir
    monkeypatch.setattr(Path, "read_text", lambda path, *a, **kw: json.dumps({"run_id": str(RUN)}) if path == lock else original_read(path, *a, **kw))
    monkeypatch.setattr(Path, "unlink", lambda path, *a, **kw: operations.append("lock-release") if path == lock else original_unlink(path, *a, **kw))
    monkeypatch.setattr(Path, "mkdir", lambda path, *a, **kw: None if path == output.parent else original_mkdir(path, *a, **kw))
    class FakeKube:
        def __init__(self, binary, kubeconfig, context, ns): self.namespace = ns
        def get(self, kind, name=None, **kwargs):
            if kind == "storageclass": return {"metadata": {"uid": uid}, "provisioner": "rancher.io/local-path", "volumeBindingMode": "WaitForFirstConsumer"}
            if kind == "namespace" and kwargs.get("ignore_missing"): return None
            if kind == "namespace": return {"metadata": {"uid": uid, "labels": {"knowi.analytics.run": RUN.hex}}}
            if kind == "pods": return {"items": []}
            raise AssertionError((kind, name))
    monkeypatch.setattr(bench, "Kube", FakeKube)
    class Host:
        def check(self): pass
        def result(self): return {}
    monkeypatch.setattr(activity, "profile_load", lambda path: {"seed": bench.SEED, "active_ratio": .6, "overlap_ratio": .2})
    monkeypatch.setattr(activity, "resource_floors", lambda env: Host())
    monkeypatch.setattr(activity, "output_path", lambda path: output)
    monkeypatch.setattr(activity, "classify", lambda result, profile: "PASS")
    monkeypatch.setattr(activity.owned, "private_folder", lambda: (SERVICE / ".local/mock-private", "owner"))
    monkeypatch.setattr(activity.owned, "record", lambda *a, **kw: None)
    monkeypatch.setattr(activity.owned, "processes_stop", lambda children: operations.append("stop-processes"))
    monkeypatch.setattr(activity.owned, "OwnedJob", lambda: SimpleNamespace(close=lambda: operations.append("close-job")))
    monkeypatch.setattr(activity.cross_service, "key_material", lambda folder: ({}, "reader"))
    monkeypatch.setattr(k3s_database, "_attested_ip", lambda: "172.20.0.2")
    monkeypatch.setattr(bench, "start_kube_proxy", lambda owned, kube, folder, token, job, children: children.append(SimpleNamespace(pid=99)))
    monkeypatch.setattr(bench, "_create_recorded", lambda *a: (operations.append("namespace"), uid)[1])
    monkeypatch.setattr(bench, "_database_setup", lambda *a: (operations.append("database"), ({"name": "synthetic"}, {}))[1])
    fixture = {"namespace": namespace, "pods": {}}
    owners, auxiliary = {"binder": uid, "init_job": uid, "authority": uid}, {}
    journal = {"pvc_uid": uid, "pv_uid": uid}
    monkeypatch.setattr(bench, "_authority_setup", lambda *a: (operations.append("authority"), ({}, owners.copy(), auxiliary, fixture, "172.20.0.3", journal))[1])
    monkeypatch.setattr(bench, "_configure_workloads", lambda *a: (operations.append("secrets"), ("key", "issuer", "fingerprint"))[1])
    def helm(args, kube, folder, run, operation, action, values=None):
        operations.append("helm-" + action + ("-worker" if operation == "helm-day-worker-upgrade" else ""))
        if operation == "helm-day-worker-upgrade":
            assert values["roles"]["query"]["replicas"] == 0 and values["roles"]["processor"]["replicas"] == 1
            assert values["activityDay"]["enabled"]
        if action == "install" and failure == "install": raise bench.LiveFailure("unknown Helm outcome")
    monkeypatch.setattr(bench, "_helm_recorded", helm)
    monkeypatch.setattr(bench, "_helm_deployment_uids", lambda *a: {role: uid for role in ("collector", "processor", "query", "privacy")})
    monkeypatch.setattr(bench, "_fixture_wait", lambda *a: fixture)
    monkeypatch.setattr(bench, "pod_inventory", lambda *a: {role: [] for role in ("collector", "processor", "query", "privacy")})
    monkeypatch.setattr(bench, "projection_drained", lambda *a, **kw: True)
    async def backfill(condition, monitor, timeout):
        operations.append("backfill")
        assert timeout == 60 and condition()
        if failure == "backfill": raise bench.LiveFailure("backfill timeout")
    monkeypatch.setattr(activity, "wait_condition", backfill)
    async def drive(*a):
        operations.append("load")
        return {"oracle": {"status": "PASS"}}, [{"top": "mock"}]
    monkeypatch.setattr(bench, "_drive_load", drive)
    monkeypatch.setattr(bench, "_finish_resources", lambda *a: operations.append("stop-owned-compute"))
    result = bench.run_case(args)
    if failure == "hostproof":
        assert operations[0] == "host-proof" and "source-probe" not in operations and "namespace" not in operations
        assert result["execution"] == "failed" and result["status"] == "UNRUN" and "lock-release" not in operations
        return
    assert operations[:4] == ["host-proof", "source-probe", "host-proof", "source-probe"]
    assert operations[4:8] == ["namespace", "database", "authority", "secrets"]
    if failure in ("install", "backfill"):
        assert result["execution"] == "failed" and result["status"] == "UNRUN"
        assert "load" not in operations and "lock-release" not in operations
        assert "reconciliation" in result
        if failure == "backfill":
            assert operations.index("helm-upgrade-worker") < operations.index("backfill")
            assert "helm-upgrade" not in operations
    else:
        assert result["execution"] == "finished" and result["status"] == "UNRUN"
        assert operations.index("helm-install") < operations.index("helm-upgrade") < operations.index("load")
        if a011:
            assert operations.index("helm-upgrade-worker") < operations.index("backfill") < operations.index("helm-upgrade")
            assert result["prewarm_backfill"]["status"] == "OBSERVED_COMPLETE"
        assert result["provenance"]["target_revision"] == ("a011" if a011 else "a010")
        assert result["dataset"]["history_query_only_gate"] == "UNRUN"
        dataset = result["dataset"]
        assert (dataset["planned_subjects"], dataset["active_subjects"], dataset["overlapping_subjects"],
                dataset["courses"], dataset["streams"]) == (10, 6, 2, 2, 8)
        assert (dataset["preexisting_facts_observed"], dataset["planned_warmup_segments"],
                dataset["planned_measured_segments"]) == (0, 160, 480)
        assert operations.index("stop-owned-compute") < operations.index("lock-release")
        assert result["retained"]["journal_pvc_uid"] == uid


@pytest.mark.parametrize("fail_init", [False, True])
def test_authority_setup_attests_storage_before_one_shot_init(monkeypatch, fail_init):
    events, uid = [], str(UUID(int=1))
    ns = bench.namespace_for(RUN)
    args = SimpleNamespace(authority_ref=IMAGE_REF, storage_class_uid=uid)
    folder = SERVICE / ".local/mock-private"
    real_read = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *a, **kw: "synthetic-private-key" if path.name == "authority.private.pem" else real_read(path, *a, **kw))
    monkeypatch.setattr(bench, "authority_configs", lambda *a: ({"fixture_mode": True}, {"fixture_mode": True}))
    monkeypatch.setattr(bench, "_write_exclusive", lambda path, data: events.append("storage-recorded"))
    def create(kube, folder, run, operation, obj):
        events.append(operation)
        if fail_init and operation == "journal-initialize": raise bench.LiveFailure("unknown init")
        return uid
    monkeypatch.setattr(bench, "_create_recorded", create)
    monkeypatch.setattr(bench, "_fixture_wait", lambda *a, **kw: {"namespace": ns, "pods": {uid: {}}})
    monkeypatch.setattr(bench, "_assert_fresh_journal_path", lambda *a: events.append("path-verified-empty"))
    monkeypatch.setattr(bench, "attest_journal_storage", lambda *a, **kw: (events.append("storage-attested"), {"pvc_uid": uid, "pv_uid": uid})[1])
    class Kube:
        namespace = ns
        def get(self, kind, name=None, **kwargs):
            if kind == "pvc": return {"status": {"phase": "Bound"}, "spec": {"volumeName": "pv"}}
            if kind in {"pv", "storageclass"}: return {}
            if kind == "pods": return {"items": [{"metadata": {"uid": uid}, "status": {"podIP": "10.42.0.8"}}]}
            raise AssertionError(kind)
    guard = lambda fixture: events.append("guard")
    if fail_init:
        with pytest.raises(bench.LiveFailure): bench._authority_setup(Kube(), folder, RUN, args, {}, "reader", guard, lambda: None)
        assert "authority-deployment" not in events
    else:
        client, owners, aux, fixture, ip, storage = bench._authority_setup(Kube(), folder, RUN, args, {}, "reader", guard, lambda: None)
        assert ip == "10.42.0.8" and owners["authority"] == uid and storage["pvc_uid"] == uid
    assert events.index("path-verified-empty") < events.index("journal-binder") < events.index("storage-attested") < events.index("storage-recorded") < events.index("journal-initialize")


def test_success_cleanup_preserves_database_namespace_pvc_and_release(monkeypatch):
    ns, uid = bench.namespace_for(RUN), str(UUID(int=1))
    roles = ("authority", "collector", "processor", "query", "privacy", "volume-probe", "journal-init")
    fixture = {"namespace": ns, "pods": {str(UUID(int=i+1)): {"role": role} for i, role in enumerate(roles)}}
    owners = {role: uid for role in ("authority", "collector", "processor", "query", "privacy", "binder", "init_job")}
    operations = []
    monkeypatch.setattr(bench, "mark_pending", lambda *a: operations.append(("pending", a[2])))
    monkeypatch.setattr(bench, "mark_confirmed", lambda *a: operations.append(("confirmed", a[2])))
    monkeypatch.setattr(bench, "_delete_recorded", lambda kube, folder, run, op, kind, name, pod_uid: operations.append(("delete", kind, name)))
    class Kube:
        namespace = ns
        def get(self, kind, name=None, **kw):
            if kind == "deployment": return {"metadata": {"uid": uid, "resourceVersion": "123"}}
            if kind == "pods": return {"items": []}
            raise AssertionError(kind)
        def call(self, command): operations.append(("command", command))
    def guard(snapshot): operations.append(("guard", sorted(x["role"] for x in snapshot["pods"].values())))
    bench._finish_resources(SimpleNamespace(replicas=(1, 1, 1)), Kube(), "folder", RUN, owners, fixture, guard)
    deletes = [entry for entry in operations if entry[0] == "delete"]
    assert deletes == [("delete", "pods", "bench-journal-binder"), ("delete", "jobs", "bench-journal-init")]
    commands = [entry[1] for entry in operations if entry[0] == "command"]
    assert len(commands) == 5 and all("--resource-version=123" in command for command in commands)
    assert operations[-1] == ("guard", [])
