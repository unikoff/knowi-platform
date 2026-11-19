import importlib.util
import copy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
TOOLS = ROOT / "services/analytics/tools"
spec = importlib.util.spec_from_file_location("setup_k3s_fixture", TOOLS / "setup_k3s_fixture.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
sys.path.insert(0, str(TOOLS))
import run_k3s


class FixturePreparationTests(unittest.TestCase):
    def test_only_exact_private_distinct_destinations(self):
        result = fixture.manifests("172.20.0.2", "172.20.0.3", "frozen-image")
        network = result["helmValues"]["network"]
        self.assertEqual(network, {"databaseCIDR": "172.20.0.2/32", "databasePort": 5432,
                                   "authorityCIDR": "172.20.0.3/32", "authorityPort": 57420})
        endpoint = result["endpointSlice"]
        self.assertEqual(endpoint["metadata"]["labels"]["kubernetes.io/service-name"], "privacy-authority")
        self.assertEqual(endpoint["endpoints"][0]["addresses"], ["172.20.0.3"])
        self.assertNotIn("selector", result["service"]["spec"])
        self.assertFalse(result["helmValues"]["migration"]["enabled"])
        self.assertEqual(result["helmValues"]["migration"]["revision"], "a006")
        self.assertFalse(result["helmValues"]["ingress"]["enabled"])
        self.assertTrue(result["helmValues"]["roles"]["privacy"]["enabled"])
        self.assertEqual(result["helmValues"]["fixtureCatalog"]["configMapName"], "analytics-fixture-catalog")
        self.assertEqual(result["helmValues"]["image"]["pullPolicy"], "Never")

    def test_refuses_wildcards_loopback_and_unverified_image(self):
        for db, auth, image in (("0.0.0.0", "172.20.0.3", "frozen"),
                                ("8.8.8.8", "172.20.0.3", "frozen"),
                                ("172.20.0.2", "127.0.0.1", "frozen"),
                                ("172.20.0.2", "172.20.0.2", "frozen"),
                                ("172.20.0.0/16", "172.20.0.3", "frozen"),
                                ("172.20.0.2", "172.20.0.3", "")):
            with self.subTest(db=db, auth=auth, image=image), self.assertRaises(ValueError):
                fixture.manifests(db, auth, image)

    def test_chart_privacy_is_disabled_by_default_and_secret_is_not_configmap(self):
        root = ROOT / "infra/helm/analytics"
        values = (root / "values.yaml").read_text()
        workloads = (root / "templates/workloads.yaml").read_text()
        config = (root / "templates/configmap.yaml").read_text()
        policy = (root / "templates/networkpolicy.yaml").read_text()
        self.assertIn("privacy:\n    enabled: false", values)
        self.assertIn("ANALYTICS_PRIVACY_AUTHORITY_JSON", workloads)
        self.assertIn("secretKeyRef", workloads)
        self.assertNotIn("ANALYTICS_PRIVACY_AUTHORITY_JSON", config)
        self.assertIn('if and (eq $role "collector") $.Values.fixtureCatalog.configMapName', workloads)
        self.assertIn("ANALYTICS_FIXTURE_CATALOG_JSON", workloads)
        self.assertNotIn("ANALYTICS_FIXTURE_CATALOG_JSON", config)
        self.assertIn('(eq $role "collector") (eq $role "query")', workloads)
        self.assertIn("authorityCIDR", policy)
        self.assertIn("$exactIPv4", policy)
        self.assertIn("$exactIPv6", policy)
        self.assertIn('"0.0.0.0/0"', policy)

    def test_phase_one_runner_cannot_start_cluster(self):
        result = subprocess.run([sys.executable, "-B", str(TOOLS / "run_k3s.py"), "create"],
                                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 2)
        self.assertIn("read-only inventory", result.stderr)

    def test_direct_config_has_bounded_isolated_target(self):
        source = (ROOT / "infra/analytics-local/k3d-direct-v2.yaml").read_text(encoding="utf-8")
        for value in ("name: knowi-analytics-01a1df75-v2", "servers: 1", "agents: 0",
                      "image: rancher/k3s:v1.35.5-k3s1", 'hostPort: "57443"',
                      "hostIP: 127.0.0.1", "disableLoadbalancer: true", "serversMemory: 2g",
                      "--disable=traefik,servicelb", "updateDefaultKubeconfig: false",
                      "switchCurrentContext: false", "timeout: 180s", "disableRollback: true"):
            self.assertIn(value, source)

    def test_v2_profile_is_isolated_and_reserves_full_caps(self):
        self.assertEqual(run_k3s.CLUSTER, "knowi-analytics-01a1df75-v2")
        self.assertEqual(run_k3s.KUBECONFIG.name, "k3s-kubeconfig-v2.yaml")
        self.assertEqual(run_k3s.file_sha256(run_k3s.CONFIG), run_k3s.CONFIG_SHA256)
        required = 3456 * run_k3s.MIB
        with self.assertRaises(run_k3s.InsufficientPostCreateBudget):
            run_k3s.pre_create_budget(required - 1)
        self.assertEqual(run_k3s.pre_create_budget(required)["required_bytes"], required)
        with self.assertRaises(ValueError):
            run_k3s.validate_kubeconfig("server: https://127.0.0.1:57443\nname: k3d-knowi-analytics-01a1df75\n")

    def test_post_create_margin_is_required_for_each_stage(self):
        base = {"available_bytes": 1127 * run_k3s.MIB, "required_bytes": 1000 * run_k3s.MIB}
        with self.assertRaises(run_k3s.InsufficientPostCreateBudget):
            run_k3s.require_post_create_margin(base)
        base["available_bytes"] += run_k3s.MIB
        self.assertEqual(run_k3s.require_post_create_margin(base)["margin_bytes"], 128 * run_k3s.MIB)

    def test_web_pause_is_explicit_and_keeps_core_health_check(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
        urls = []
        def open_url(url, **kwargs):
            urls.append(url)
            return Response()
        class Socket:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def settimeout(self, value): pass
            def connect_ex(self, address): return 1
        with patch("urllib.request.urlopen", side_effect=open_url), patch("socket.socket", return_value=Socket()):
            run_k3s.verify_local_services()
            self.assertEqual(urls, ["http://127.0.0.1:18041/ready", "http://127.0.0.1:5173/backend/"])
            urls.clear()
            run_k3s.verify_local_services(True)
            self.assertEqual(urls, ["http://127.0.0.1:18041/ready"])
            with self.assertRaises(ValueError):
                run_k3s.verify_local_services("true")

    def test_shared_budget_keeps_all_reserves_and_lowest_host_sample(self):
        unit = run_k3s.MIB
        sample = {"available_bytes": 5000 * unit, "required_bytes": 2000 * unit, "node_current_bytes": 800 * unit}
        required = (2000 + 128 + 500 + 512) * unit
        good = run_k3s.shared_local_budget(sample, 4800 * unit, required, 500 * unit)
        self.assertEqual(good["required_bytes"], required)
        self.assertEqual(good["available_bytes"], required)
        with self.assertRaises(run_k3s.InsufficientPostCreateBudget):
            run_k3s.shared_local_budget(sample, 4800 * unit, required - 1, 500 * unit)
        with self.assertRaises(ValueError):
            run_k3s.shared_local_budget(sample, 4800 * unit, required, 577 * unit)

    def test_traefik_fixture_routes_only_chart_paths(self):
        source = (ROOT / "infra/analytics-local/traefik-fixture.yaml").read_text(encoding="utf-8")
        chart = (ROOT / "infra/helm/analytics/templates/ingressroute.yaml").read_text(encoding="utf-8")
        for rule in ("Path(`/api/analytics/events/batch`)", "Path(`/api/analytics/reports`)",
                     "PathPrefix(`/api/analytics/reports/`)"):
            self.assertIn(rule, source)
            self.assertIn(rule, chart)
        self.assertNotIn("PathPrefix(`/api/analytics/reports`)", source)
        self.assertNotIn("/metrics", source)
        self.assertIn("image: traefik:v3.7.15", source)
        self.assertIn("limits: {memory: 128Mi}", source)
        self.assertIn("automountServiceAccountToken: false", source)

    def test_ingress_fixture_requires_exact_uid_image_and_security(self):
        uid = "11111111-1111-4111-8111-111111111111"
        pod = {"metadata": {"name": "analytics-traefik-fixture", "namespace": "kube-system", "uid": uid,
                            "labels": {"app.kubernetes.io/name": "traefik", "knowi.analytics.fixture": "traefik"}},
               "spec": {"automountServiceAccountToken": False, "restartPolicy": "Never", "containers": [
                   {"name": "traefik", "image": "traefik:v3.7.15", "imagePullPolicy": "Never",
                    "resources": {"limits": {"memory": "128Mi"}},
                    "securityContext": {"readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False,
                                        "capabilities": {"drop": ["ALL"]}}}]},
               "status": {"containerStatuses": [{"imageID": "sha256:6cfd9663a5d3591fde0170042b72dd00614a6b99208cb377a46ec23a91560b65", "ready": True}]}}
        run_k3s.validate_ingress_fixture(pod, uid)
        for field in ("uid", "image", "memory", "security", "host_network", "ready"):
            value = copy.deepcopy(pod)
            if field == "uid": value["metadata"]["uid"] = "22222222-2222-4222-8222-222222222222"
            elif field == "image": value["status"]["containerStatuses"][0]["imageID"] = "sha256:" + "a" * 64
            elif field == "memory": value["spec"]["containers"][0]["resources"]["limits"]["memory"] = "129Mi"
            elif field == "security": value["spec"]["containers"][0]["securityContext"]["readOnlyRootFilesystem"] = False
            elif field == "host_network": value["spec"]["hostNetwork"] = True
            elif field == "ready": value["status"]["containerStatuses"][0]["ready"] = False
            with self.subTest(field=field), self.assertRaises(ValueError):
                run_k3s.validate_ingress_fixture(value, uid)

    def test_lease_holder_is_exactly_bound_to_job_uid_secrets_and_source(self):
        uid = "11111111-1111-4111-8111-111111111111"
        job = "22222222-2222-4222-8222-222222222222"
        source = (ROOT / "infra/analytics-local/lease-claim.py").read_text(encoding="utf-8")
        pod = {"metadata": {"name": "analytics-lease-holder-abcde", "namespace": "knowi-analytics", "uid": uid,
                            "labels": {"knowi.analytics.fixture": "lease-holder", "app.kubernetes.io/name": "analytics",
                                       "app.kubernetes.io/instance": "analytics", "app.kubernetes.io/component": "processor"},
                            "ownerReferences": [{"kind": "Job", "name": "analytics-lease-holder", "uid": job, "controller": True}]},
               "spec": {"serviceAccountName": "analytics-analytics-processor", "automountServiceAccountToken": False,
                        "securityContext": {"runAsNonRoot": True, "runAsUser": 10001},
                        "restartPolicy": "Never", "containers": [{"name": "claim", "image": "knowi-analytics:an1-20261117-report",
                        "imagePullPolicy": "Never", "command": ["python", "-u", "-B", "-c", source],
                        "env": [{"name": "ANALYTICS_PROCESSOR_DATABASE_URL", "valueFrom": {"secretKeyRef": {"name": "analytics-processor-db", "key": "DATABASE_URL"}}},
                                {"name": "ANALYTICS_PRIVACY_AUTHORITY_JSON", "valueFrom": {"secretKeyRef": {"name": "analytics-privacy-authority", "key": "PRIVACY_AUTHORITY_JSON"}}},
                                {"name": "FIXTURE_SUBJECT", "value": uid}, {"name": "FIXTURE_EVENT", "value": job}],
                        "resources": {"limits": {"memory": "128Mi"}}, "securityContext": {"readOnlyRootFilesystem": True,
                        "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}}]},
               "status": {"containerStatuses": [{"imageID": "sha256:f51528ba1b3e7182ef4f771964f432473ff142693f1928b837e7d1dab267f90a"}]}}
        run_k3s.validate_lease_fixture(pod, uid, job)
        for field in ("job", "source", "secret", "image", "memory", "namespace"):
            value = copy.deepcopy(pod)
            if field == "job": value["metadata"]["ownerReferences"][0]["uid"] = uid
            elif field == "source": value["spec"]["containers"][0]["command"][-1] = "other"
            elif field == "secret": value["spec"]["containers"][0]["env"][0]["valueFrom"]["secretKeyRef"]["name"] = "other"
            elif field == "image": value["status"]["containerStatuses"][0]["imageID"] = "sha256:" + "a" * 64
            elif field == "memory": value["spec"]["containers"][0]["resources"]["limits"]["memory"] = "129Mi"
            elif field == "namespace": value["metadata"]["namespace"] = "default"
            with self.subTest(field=field), self.assertRaises(ValueError):
                run_k3s.validate_lease_fixture(value, uid, job)

    def test_create_server_attestation_refuses_wrong_target_or_limit(self):
        image_id = "sha256:" + "a" * 64
        binding = [{"HostIp": "127.0.0.1", "HostPort": "57443"}]
        server = [{"Name": "/" + run_k3s.SERVER, "State": {"Running": True}, "Image": image_id,
                   "Config": {"Labels": {"k3d.cluster": run_k3s.CLUSTER, "k3d.role": "server",
                                         "knowi.analytics.synthetic": "true"}},
                   "HostConfig": {"Memory": 2 * 1024**3, "PortBindings": {"6443/tcp": binding}},
                   "NetworkSettings": {"Ports": {"6443/tcp": binding}}}]
        run_k3s.validate_server(server, image_id)
        for field in ("project", "role", "memory", "port", "image"):
            value = copy.deepcopy(server)
            obj = value[0]
            if field == "project": obj["Config"]["Labels"]["k3d.cluster"] = "other"
            if field == "role": obj["Config"]["Labels"]["k3d.role"] = "agent"
            if field == "memory": obj["HostConfig"]["Memory"] = 0
            if field == "port": obj["NetworkSettings"]["Ports"]["6443/tcp"][0]["HostIp"] = "0.0.0.0"
            if field == "image": obj["Image"] = "sha256:" + "b" * 64
            with self.subTest(field=field), self.assertRaises(ValueError):
                run_k3s.validate_server(value, image_id)

    def test_private_kubeconfig_target_validation(self):
        run_k3s.validate_kubeconfig("clusters:\n- cluster:\n    server: https://127.0.0.1:57443\n  name: " + run_k3s.CONTEXT + "\n")
        for endpoint in ("https://127.0.0.1:6443", "https://example.com:57443"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                run_k3s.validate_kubeconfig("server: " + endpoint + "\nname: " + run_k3s.CONTEXT + "\n")

    def test_kubectl_config_binds_exact_context_to_local_api(self):
        value = {"contexts": [{"name": run_k3s.CONTEXT, "context": {"cluster": "local-cluster"}}],
                 "clusters": [{"name": "local-cluster", "cluster": {"server": "https://127.0.0.1:57443"}}]}
        run_k3s.validate_kubectl_config(value)
        for change in ("wrong-context", "wrong-cluster", "wrong-server", "duplicate-context"):
            data = copy.deepcopy(value)
            if change == "wrong-context": data["contexts"][0]["name"] = "unrelated"
            if change == "wrong-cluster": data["contexts"][0]["context"]["cluster"] = "unrelated"
            if change == "wrong-server": data["clusters"][0]["cluster"]["server"] = "https://127.0.0.1:6443"
            if change == "duplicate-context": data["contexts"].append(copy.deepcopy(data["contexts"][0]))
            with self.subTest(change=change), self.assertRaises(ValueError):
                run_k3s.validate_kubectl_config(data)

    def test_secret_input_is_stdin_only_and_issuance_is_after_attestation(self):
        events = []
        def verify(*args, **kwargs):
            events.append("verify")
        def issue(record):
            events.append("issue")
            return "postgresql://synthetic:private@172.20.0.2:5432/analytics_fixture"
        def create(command, **kwargs):
            events.append("create")
            self.assertNotIn("private", " ".join(command))
            obj = json.loads(kwargs["input"])
            self.assertEqual(obj["metadata"]["name"], "analytics-migrate-db")
            self.assertEqual(obj["metadata"]["namespace"], "knowi-analytics")
            self.assertIn("private", obj["stringData"]["DATABASE_URL"])
            self.assertEqual(command[-3:], ["create", "--filename", "-"])
            return SimpleNamespace(returncode=0)
        fake = SimpleNamespace(migration_role_url=issue)
        with patch.object(run_k3s, "require_post_create_budget", side_effect=verify), \
             patch.dict(sys.modules, {"k3s_database": fake}), \
             patch.object(run_k3s.subprocess, "run", side_effect=create):
            result = run_k3s.create_secret_after_go("kubectl", "hash", "image", "migrate", {})
        self.assertEqual(events, ["verify", "issue", "create"])
        self.assertEqual(result, {"status": "created"})

    def test_endpoint_uses_only_attested_authority_ip(self):
        events = []
        def verify(*args, **kwargs):
            events.append("verify-api")
        def status(image_id):
            events.append("verify-authority")
            self.assertEqual(image_id, "sha256:" + "a" * 64)
            return SimpleNamespace(ip="172.20.0.3")
        def create(command, **kwargs):
            events.append("create")
            obj = json.loads(kwargs["input"])
            self.assertEqual(obj["kind"], "EndpointSlice")
            self.assertEqual(obj["endpoints"][0]["addresses"], ["172.20.0.3"])
            self.assertEqual(obj["metadata"]["namespace"], "knowi-analytics")
            return SimpleNamespace(returncode=0)
        with patch.object(run_k3s, "require_post_create_budget", side_effect=verify), \
             patch.dict(sys.modules, {"k3s_authority": SimpleNamespace(status=status)}), \
             patch.object(run_k3s.subprocess, "run", side_effect=create):
            run_k3s.create_public_fixture_after_go("kubectl", "hash", "image", "authority-endpoints", "sha256:" + "a" * 64)
        self.assertEqual(events, ["verify-api", "verify-authority", "create"])

    def test_network_attestation_rejects_other_network_and_ip(self):
        pg_id, network_id = "a" * 64, "b" * 64
        pg = [{"Id": pg_id, "NetworkSettings": {"Networks": {run_k3s.PG_NETWORK:
             {"NetworkID": network_id, "IPAddress": "172.20.0.2"}}}}]
        net = [{"Name": run_k3s.PG_NETWORK, "Id": network_id, "Labels": {
            "com.docker.compose.project": run_k3s.resource_snapshot.PROJECT, "com.docker.compose.network": "analytics"},
                "Containers": {pg_id: {}}}]
        with patch.object(run_k3s.resource_snapshot, "verify_container"):
            self.assertEqual(run_k3s.validate_pg_network(net, pg), "172.20.0.2")
            for change in ("project", "network-id", "attachment", "loopback"):
                n, p = copy.deepcopy(net), copy.deepcopy(pg)
                if change == "project": n[0]["Labels"]["com.docker.compose.project"] = "core"
                if change == "network-id": p[0]["NetworkSettings"]["Networks"][run_k3s.PG_NETWORK]["NetworkID"] = "other"
                if change == "attachment": n[0]["Containers"] = {}
                if change == "loopback": p[0]["NetworkSettings"]["Networks"][run_k3s.PG_NETWORK]["IPAddress"] = "127.0.0.1"
                with self.subTest(change=change), self.assertRaises(ValueError):
                    run_k3s.validate_pg_network(n, p)

    def test_phase_aware_budget_uses_raw_charged_and_keeps_precreate_guard(self):
        node = {"cap": 2 * 1024**3, "current": 1107841024, "swap": 0, "pressure": False, "oom": False}
        pg = {"cap": 384 * run_k3s.MIB, "current": 148525056, "swap": 0, "pressure": False, "oom": False}
        result = run_k3s.post_create_budget(node, pg, 2256 * run_k3s.MIB)
        self.assertEqual(result["required_bytes"], (2 * 1024**3 - 1107841024)
                         + (384 * run_k3s.MIB - 148525056) + (128 + 256 + 512) * run_k3s.MIB)
        self.assertFalse(run_k3s.check_k3s.admission(2256 * run_k3s.MIB))
        self.assertTrue(run_k3s.check_k3s.admission(2560 * run_k3s.MIB))
        with self.assertRaises(ValueError):
            run_k3s.post_create_budget(node, pg, result["required_bytes"] - 1)
        authority = {"cap": 128 * run_k3s.MIB, "current": 32 * run_k3s.MIB,
                     "swap": 0, "pressure": False, "oom": False}
        charged = run_k3s.post_create_budget(node, pg, 2256 * run_k3s.MIB, authority)
        self.assertEqual(charged["authority_reserve_bytes"], 96 * run_k3s.MIB)
        self.assertEqual(charged["required_bytes"], result["required_bytes"] - 32 * run_k3s.MIB)
        authority["swap"] = 1
        with self.assertRaises(ValueError):
            run_k3s.post_create_budget(node, pg, 3000 * run_k3s.MIB, authority)

    def test_post_create_budget_refuses_unknown_or_unsafe_samples(self):
        node = {"cap": 2 * 1024**3, "current": 1107841024, "swap": 0, "pressure": False, "oom": False}
        pg = {"cap": 384 * run_k3s.MIB, "current": 148525056, "swap": 0, "pressure": False, "oom": False}
        for change in ("missing-current", "missing-oom", "bad-cap", "swap", "pressure", "oom", "85-percent", "host"):
            n, p, host = copy.deepcopy(node), copy.deepcopy(pg), 3000 * run_k3s.MIB
            if change == "missing-current": del n["current"]
            if change == "missing-oom": del p["oom"]
            if change == "bad-cap": n["cap"] -= 1
            if change == "swap": p["swap"] = 1
            if change == "pressure": n["pressure"] = True
            if change == "oom": p["oom"] = True
            if change == "85-percent": n["current"] = n["cap"] * 85 // 100 + 1
            if change == "host": host = 511 * run_k3s.MIB
            with self.subTest(change=change), self.assertRaises(ValueError):
                run_k3s.post_create_budget(n, p, host)

    def test_running_k3d_helper_is_never_omitted_from_budget(self):
        server_id, tools_id = "a" * 64, "b" * 64
        run_k3s.validate_running_cluster_containers([server_id], server_id)
        for ids in ([], [server_id, tools_id], [tools_id], [server_id, server_id]):
            with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, "auxiliary"):
                run_k3s.validate_running_cluster_containers(ids, server_id)

    def test_stopped_node_restart_identity_is_exact(self):
        server_id, image_id = "a" * 64, "sha256:" + "b" * 64
        binding = [{"HostIp": "127.0.0.1", "HostPort": "57443"}]
        server = [{"Id": server_id, "Name": "/" + run_k3s.SERVER, "Image": image_id,
                   "State": {"Running": False, "Status": "exited", "ExitCode": 143},
                   "Config": {"Labels": {"k3d.cluster": run_k3s.CLUSTER, "k3d.role": "server",
                                         "knowi.analytics.synthetic": "true"}},
                   "HostConfig": {"Memory": 2 * 1024**3, "CgroupnsMode": "private",
                                  "PortBindings": {"6443/tcp": binding}},
                   "NetworkSettings": {"Ports": {}, "Networks": {"k3d-" + run_k3s.CLUSTER: {},
                                                                   run_k3s.PG_NETWORK: {}}}}]
        run_k3s.validate_stopped_server(server, image_id, server_id)
        for change in ("id", "image", "exit", "running", "memory", "network", "port", "label"):
            value = copy.deepcopy(server)
            obj = value[0]
            if change == "id": obj["Id"] = "c" * 64
            if change == "image": obj["Image"] = "sha256:" + "c" * 64
            if change == "exit": obj["State"]["ExitCode"] = 1
            if change == "running": obj["State"]["Running"] = True
            if change == "memory": obj["HostConfig"]["Memory"] -= 1
            if change == "network": obj["NetworkSettings"]["Networks"]["other"] = {}
            if change == "port": obj["HostConfig"]["PortBindings"] = {}
            if change == "label": obj["Config"]["Labels"]["k3d.cluster"] = "other"
            with self.subTest(change=change), self.assertRaises(ValueError):
                run_k3s.validate_stopped_server(value, image_id, server_id)

    def test_stopped_node_budget_reserves_full_cap_and_margin(self):
        pg = {"cap": 384 * run_k3s.MIB, "current": 79880192,
              "swap": 0, "pressure": False, "oom": False}
        required = 2 * 1024**3 + (pg["cap"] - pg["current"]) + (128 + 256 + 512 + 128) * run_k3s.MIB
        self.assertEqual(run_k3s.stopped_node_restart_budget(pg, required)["required_bytes"], required)
        with self.assertRaises(run_k3s.InsufficientPostCreateBudget):
            run_k3s.stopped_node_restart_budget(pg, required - 1)
        pg["pressure"] = True
        with self.assertRaises(ValueError):
            run_k3s.stopped_node_restart_budget(pg, required)

    def test_stopped_node_admission_is_read_only_and_uses_lower_host_sample(self):
        node_id, image_id, pg_id = "a" * 64, "sha256:" + "b" * 64, "c" * 64
        pg = {"cap": 384 * run_k3s.MIB, "current": 80 * run_k3s.MIB,
              "swap": 0, "pressure": False, "oom": False}
        required = 2 * 1024**3 + (384 - 80 + 128 + 256 + 512 + 128) * run_k3s.MIB
        calls = []
        def fake_run(args, **kwargs):
            calls.append(args)
            if "config" in args: return SimpleNamespace(stdout="{}")
            if "ps" in args: return SimpleNamespace(stdout="")
            if run_k3s.SERVER in args: return SimpleNamespace(stdout="[{}]")
            if run_k3s.resource_snapshot.TARGET in args:
                return SimpleNamespace(stdout=json.dumps([{"HostConfig": {"CgroupnsMode": "private"}}]))
            if "volume" in args or "network" in args: return SimpleNamespace(stdout="[]")
            raise AssertionError("unexpected command")
        with patch.object(run_k3s, "KUBECONFIG", Path(__file__).resolve()), \
             patch.object(run_k3s, "file_sha256", return_value="d" * 64), \
             patch.object(run_k3s, "validate_kubectl_config"), \
             patch.object(run_k3s, "validate_stopped_server"), \
             patch.object(run_k3s, "validate_pg_network"), \
             patch.object(run_k3s.resource_snapshot, "local_context", return_value="local"), \
             patch.object(run_k3s.resource_snapshot, "verify_container", return_value=(pg_id, 384 * run_k3s.MIB, 1)), \
             patch.object(run_k3s.resource_snapshot, "verify_volume"), \
             patch.object(run_k3s.check_k3s, "available_memory", side_effect=[required + 1, required - 1]), \
             patch.object(run_k3s, "run", side_effect=fake_run), \
             patch.object(run_k3s, "_cgroup_numbers", return_value=pg):
            with self.assertRaises(run_k3s.InsufficientPostCreateBudget) as error:
                run_k3s.require_stopped_node_restart_budget(str(Path(__file__).resolve()), "d" * 64, image_id, node_id)
        self.assertEqual(error.exception.available_bytes, required - 1)
        self.assertEqual(error.exception.required_bytes, required)
        self.assertTrue(all(not {"start", "create", "rm", "stop"}.intersection(args) for args in calls))

    def test_authority_scope_must_be_exact_before_subtracting_current(self):
        node_id, pg_id, authority_id = "a" * 64, "b" * 64, "c" * 64
        server = [{"Id": node_id, "HostConfig": {"CgroupnsMode": "private"},
                   "NetworkSettings": {"Networks": {"k3d-" + run_k3s.CLUSTER: {}, run_k3s.PG_NETWORK: {}}}}]
        pg = [{"HostConfig": {"CgroupnsMode": "private"}}]
        authority = [{"HostConfig": {"CgroupnsMode": "private"}}]
        state = lambda cap, current: {"cap": cap, "current": current, "swap": 0, "pressure": False, "oom": False}
        node_state = state(2 * 1024**3, 1107841024)
        pg_state = state(384 * run_k3s.MIB, 148525056)
        authority_state = state(128 * run_k3s.MIB, 32 * run_k3s.MIB)
        def fake_run(args, **kwargs):
            if "ps" in args and "label=k3d.cluster=" + run_k3s.CLUSTER in args:
                return SimpleNamespace(stdout=node_id + "\n")
            if "ps" in args: return SimpleNamespace(stdout=authority_id + "\n")
            if "network" in args: return SimpleNamespace(stdout="[]")
            if "volume" in args: return SimpleNamespace(stdout="[]")
            if run_k3s.SERVER in args: return SimpleNamespace(stdout=json.dumps(server))
            if run_k3s.resource_snapshot.TARGET in args: return SimpleNamespace(stdout=json.dumps(pg))
            if authority_id in args: return SimpleNamespace(stdout=json.dumps(authority))
            raise AssertionError("unexpected command")
        with patch.object(run_k3s, "verify_api_after_go"), patch.object(run_k3s, "validate_server"), \
             patch.object(run_k3s, "validate_pg_network"), patch.object(run_k3s, "_verify_cgroup_tree"), \
             patch.object(run_k3s.resource_snapshot, "local_context", return_value="local"), \
             patch.object(run_k3s.resource_snapshot, "verify_container", return_value=(pg_id, 384 * run_k3s.MIB, 1)), \
             patch.object(run_k3s.resource_snapshot, "verify_volume"), \
             patch.object(run_k3s.check_k3s, "available_memory", return_value=3000 * run_k3s.MIB), \
             patch.object(run_k3s, "run", side_effect=fake_run), \
             patch.object(run_k3s, "_cgroup_numbers", side_effect=[node_state, pg_state, authority_state]), \
             patch.dict(sys.modules, {"k3s_authority": SimpleNamespace(status=lambda image: SimpleNamespace(container_id=authority_id))}):
            result = run_k3s.require_post_create_budget("kubectl", "hash", "image", authority_image_id="fixture-image")
        self.assertEqual(result["authority_reserve_bytes"], 96 * run_k3s.MIB)
        with patch.object(run_k3s, "verify_api_after_go"), patch.object(run_k3s, "validate_server"), \
             patch.object(run_k3s, "validate_pg_network"), patch.object(run_k3s, "_verify_cgroup_tree"), \
             patch.object(run_k3s.resource_snapshot, "local_context", return_value="local"), \
             patch.object(run_k3s.resource_snapshot, "verify_container", return_value=(pg_id, 384 * run_k3s.MIB, 1)), \
             patch.object(run_k3s.resource_snapshot, "verify_volume"), \
             patch.object(run_k3s.check_k3s, "available_memory", return_value=3000 * run_k3s.MIB), \
             patch.object(run_k3s, "run", side_effect=fake_run), \
             patch.object(run_k3s, "_cgroup_numbers", side_effect=[node_state, pg_state]):
            with self.assertRaisesRegex(ValueError, "authority scope unknown"):
                run_k3s.require_post_create_budget("kubectl", "hash", "image")

    def test_cgroup_parser_refuses_pressure_and_missing_events(self):
        outputs = ["cgroup2fs\n", "100\n200\n0\n", "low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n",
                   "some avg10=0.01 avg60=0.00 avg300=0.00 total=3\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"]
        with patch.object(run_k3s, "run", side_effect=[SimpleNamespace(stdout=s) for s in outputs]):
            parsed = run_k3s._cgroup_numbers(["docker"], "synthetic")
        self.assertTrue(parsed["pressure"])
        outputs[2] = "max 0\noom 0\n"
        with patch.object(run_k3s, "run", side_effect=[SimpleNamespace(stdout=s) for s in outputs]):
            with self.assertRaises(ValueError):
                run_k3s._cgroup_numbers(["docker"], "synthetic")

    def test_database_stage_is_guarded_bounded_and_never_auto_retries(self):
        events = []
        def guard(*args): events.append("guard")
        def execute(command, **kwargs):
            events.append("attempt")
            self.assertEqual(kwargs["timeout"], 60)
            raise subprocess.TimeoutExpired(command, 60)
        with patch.object(run_k3s, "require_post_create_budget", side_effect=guard), \
             patch.object(run_k3s.subprocess, "run", side_effect=execute):
            with self.assertRaisesRegex(ValueError, "outcome unknown"):
                run_k3s.initialize_database_after_go("kubectl", "hash", "image")
        self.assertEqual(events, ["guard", "attempt"])


if __name__ == "__main__":
    unittest.main()
