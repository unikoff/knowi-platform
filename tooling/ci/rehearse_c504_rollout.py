#!/usr/bin/env python3
"""Rehearse synthetic Rollouts in a fresh disposable k3s Docker container.

Installs pinned CRDs only inside that cluster and tests the generated maintenance
routes through Traefik and nginx. Core, migration and production TLS are not tested.
No Docker or Kubernetes action occurs without --execute.
"""
import argparse
import http.client
import importlib.util
import json
import pathlib
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import urlparse


PREFIX = "c504-rehearsal-"
OWNER = "pymentor.example/c504-rehearsal"
IMAGE = "hashicorp/http-echo:1.0.0"
NGINX = "nginx:1.30.6-alpine3.24-slim"
TRAEFIK = "traefik:v3.7.15"
ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cutover", ROOT / "infra/gitops/operations/prepare_c504_cutover.py")
cutover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cutover)
PATHS = ("/", "/api/user/profile", "/api/learning/tasks/x/submit", "/api/learning/interpreter/run")


class Rehearsal:
    def __init__(self, kubectl, kubeconfig, context, timeout):
        self.base = [kubectl, "--kubeconfig", kubeconfig, "--context", context]
        self.timeout = timeout
        self.token = uuid.uuid4().hex
        self.namespace = PREFIX + self.token[:12]
        self.uid = None
        self.forwarders = []

    def command(self, *args, obj=None, check=True):
        result = subprocess.run(self.base + list(args), input=json.dumps(obj) if obj else None,
                                text=True, capture_output=True, timeout=max(self.timeout, 150))
        if check and result.returncode:
            raise RuntimeError(f"kubectl {args[0]} failed: {result.stderr.strip()}")
        return result

    def get(self, kind, name=None):
        args = ["-n", self.namespace, "get", kind]
        if name:
            args.append(name)
        return json.loads(self.command(*args, "-o", "json").stdout)

    def create(self, obj):
        self.command("create", "-f", "-", obj=obj)

    def apply(self, obj):
        self.command("-n", self.namespace, "apply", "-f", "-", obj=obj)

    def wait(self, description, predicate):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if predicate():
                print("PASS:", description, flush=True)
                return
            time.sleep(2)
        raise RuntimeError("timeout: " + description)

    def namespace_object(self):
        return {"apiVersion": "v1", "kind": "Namespace", "metadata": {
            "name": self.namespace, "labels": {OWNER: self.token}}}

    def install_traffic(self):
        ns = self.namespace
        def obj(api, kind, name, **fields):
            metadata = {"name": name}
            if kind not in ("ClusterRole", "ClusterRoleBinding"):
                metadata["namespace"] = ns
            return {"apiVersion": api, "kind": kind, "metadata": metadata, **fields}
        self.apply(obj("v1", "ServiceAccount", "traefik"))
        self.apply(obj("rbac.authorization.k8s.io/v1", "ClusterRole", ns, rules=[
            {"apiGroups": [""], "resources": ["services", "endpoints", "secrets", "configmaps"], "verbs": ["get", "list", "watch"]},
            {"apiGroups": ["discovery.k8s.io"], "resources": ["endpointslices"], "verbs": ["get", "list", "watch"]},
            {"apiGroups": ["traefik.io"], "resources": ["*"], "verbs": ["get", "list", "watch"]}]))
        self.apply(obj("rbac.authorization.k8s.io/v1", "ClusterRoleBinding", ns,
            roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": ns},
            subjects=[{"kind": "ServiceAccount", "name": "traefik", "namespace": ns}]))
        self.apply(obj("apps/v1", "Deployment", "traefik", spec={"replicas": 1,
            "selector": {"matchLabels": {"app": "traefik"}}, "template": {"metadata": {"labels": {"app": "traefik"}},
            "spec": {"serviceAccountName": "traefik", "containers": [{"name": "traefik", "image": TRAEFIK,
                "imagePullPolicy": "Never", "args": ["--providers.kubernetescrd=true",
                    "--providers.kubernetescrd.namespaces=" + ns, "--providers.kubernetescrd.disableclusterscoperesources=true",
                    "--entrypoints.web.address=:8000", "--entrypoints.websecure.address=:8443"],
                "ports": [{"containerPort": 8000}, {"containerPort": 8443}]}]}}}))
        self.wait("Traefik available", lambda: self.get("deployment", "traefik").get("status", {}).get("availableReplicas", 0) >= 1)
        self.apply(obj("traefik.io/v1alpha1", "IngressRoute", "normal-http", spec={"entryPoints": ["web"],
            "routes": [{"kind": "Rule", "match": "PathPrefix(`/`)", "priority": 1,
                        "services": [{"name": "core-api-weighted", "kind": "TraefikService"}]}]}))
        self.apply(obj("traefik.io/v1alpha1", "IngressRoute", "normal-https", spec={"entryPoints": ["websecure"],
            "routes": [{"kind": "Rule", "match": "PathPrefix(`/`)", "priority": 1,
                        "services": [{"name": "core-api-weighted", "kind": "TraefikService"}]}], "tls": {}}))
        self.ports = []
        for remote in (8000, 8443):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            process = subprocess.Popen(self.base + ["-n", ns, "port-forward", "--address", "127.0.0.1",
                                       "deployment/traefik", f"{port}:{remote}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.forwarders.append(process)
            self.ports.append(port)

    def http_matches(self, status, body=None):
        try:
            for secure, port in enumerate(self.ports):
                for host in cutover.PUBLIC_HOSTS:
                    for path in PATHS:
                        for method in ("GET", "POST"):
                            cls = http.client.HTTPSConnection if secure else http.client.HTTPConnection
                            kwargs = {"timeout": 3}
                            if secure:
                                kwargs["context"] = ssl._create_unverified_context()
                            conn = cls("127.0.0.1", port, **kwargs)
                            conn.request(method, path, headers={"Host": host})
                            response = conn.getresponse()
                            data = response.read().decode("utf-8")
                            if response.status != status or (body and body not in data):
                                conn.close()
                                return False
                            if status == 503 and "no-store" not in response.getheader("Cache-Control", ""):
                                conn.close()
                                return False
                            conn.close()
            return True
        except (OSError, http.client.HTTPException):
            return False

    def maintenance(self):
        package = cutover.maintenance_artifact("a" * 40, "sha256:" + "1" * 64)
        for item in package["items"]:
            item["metadata"]["namespace"] = self.namespace
            if item["kind"] == "Deployment":
                item["spec"]["template"]["spec"]["containers"][0]["image"] = NGINX
                item["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] = "Never"
            if item["kind"] == "IngressRoute" and "tls" in item["spec"]:
                item["spec"]["tls"] = {}  # Traefik-generated test certificate, no production secret.
        return package["items"]

    def rollout(self, version):
        return {"apiVersion": "argoproj.io/v1alpha1", "kind": "Rollout", "metadata": {
            "name": "core", "namespace": self.namespace}, "spec": {
            "replicas": 1, "selector": {"matchLabels": {"app": "rehearsal-core"}},
            "template": {"metadata": {"labels": {"app": "rehearsal-core", "version": version}},
                         "spec": {"containers": [{"name": "http", "image": IMAGE, "imagePullPolicy": "Never",
                             "args": ["-text=" + version, "-listen=:5678"],
                             "ports": [{"containerPort": 5678}],
                             "readinessProbe": {"httpGet": {"path": "/", "port": 5678},
                                                "periodSeconds": 2}}]}},
            "strategy": {"canary": {"stableService": "core-stable", "canaryService": "core-canary",
                "trafficRouting": {"traefik": {"weightedTraefikServiceName": "core-api-weighted"}},
                "steps": [{"setWeight": 20}, {"pause": {}}, {"analysis": {
                    "templates": [{"templateName": "must-not-run"}]}}]}}}}

    def weights_stable(self):
        services = self.get("traefikservice", "core-api-weighted")["spec"]["weighted"]["services"]
        return len(services) == 2 and {s["name"]: s.get("weight") for s in services} == {
            "core-stable": 100, "core-canary": 0}

    def stable(self):
        status = self.get("rollout", "core").get("status", {})
        return (status.get("stableRS") and status.get("readyReplicas") == 1
                and status.get("availableReplicas") == 1 and not status.get("pauseConditions"))

    def endpoints_are_new(self):
        pods = [p for p in self.get("pods")["items"] if p["metadata"].get("labels", {}).get("app") == "rehearsal-core"]
        if len(pods) != 1 or pods[0]["metadata"]["labels"].get("version") != "c504":
            return False
        pod_uid = pods[0]["metadata"]["uid"]
        for service in ("core-stable", "core-canary"):
            slices = self.get("endpointslices.discovery.k8s.io")["items"]
            endpoints = [e for s in slices if s["metadata"].get("labels", {}).get(
                "kubernetes.io/service-name") == service for e in s.get("endpoints", [])
                if e.get("conditions", {}).get("ready") is True]
            if not endpoints or any(e.get("targetRef", {}).get("uid") != pod_uid for e in endpoints):
                return False
        return True

    def verify_migration_network_policy(self):
        """Exercise the generated peer/port rule against disposable TCP listeners."""
        code = "a" * 40
        labels = {"app.kubernetes.io/component": "migrations", "knowi.io/source-sha": code}
        security = {"runAsNonRoot": True, "runAsUser": 10001,
                    "readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]}}
        self.apply({"apiVersion": "v1", "kind": "Pod", "metadata": {
            "name": "rehearsal-postgres", "namespace": self.namespace,
            "labels": {"app": "rehearsal-postgres"}}, "spec": {
                "automountServiceAccountToken": False, "containers": [{
                    "name": name, "image": IMAGE, "imagePullPolicy": "Never",
                    "args": ["-text=" + name, "-listen=:" + str(port)],
                    "securityContext": security,
                    "readinessProbe": {"tcpSocket": {"port": port}, "periodSeconds": 2}}
                    for name, port in (("synthetic-sql", 57152), ("synthetic-redis", 6379))]}})
        self.apply({"apiVersion": "v1", "kind": "Service", "metadata": {
            "name": "rehearsal-postgres", "namespace": self.namespace}, "spec": {
                "selector": {"app": "rehearsal-postgres"}, "ports": [
                    {"name": "sql", "port": 57152}, {"name": "redis", "port": 6379}]}})
        self.apply({"apiVersion": "v1", "kind": "Pod", "metadata": {
            "name": "migration-probe", "namespace": self.namespace, "labels": labels},
            "spec": {"automountServiceAccountToken": False, "containers": [{
                "name": "probe", "image": NGINX, "imagePullPolicy": "Never",
                "command": ["/bin/sh", "-c", "sleep 600"], "securityContext": security}]}})
        self.wait("network-policy probe pods ready", lambda: all(
            any(c.get("type") == "Ready" and c.get("status") == "True"
                for c in self.get("pod", name).get("status", {}).get("conditions", []))
            for name in ("rehearsal-postgres", "migration-probe")))

        def reachable(port):
            result = self.command("-n", self.namespace, "exec", "migration-probe", "--", "wget",
                                  "-T", "2", "-qO-", "http://rehearsal-postgres:" + str(port), check=False)
            return result.returncode == 0 and "synthetic-" in result.stdout

        self.wait("synthetic SQL and Redis listeners reachable", lambda: reachable(57152) and reachable(6379))
        self.apply({"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {
            "name": "database-deny-all", "namespace": self.namespace}, "spec": {
                "podSelector": {"matchLabels": {"app": "rehearsal-postgres"}},
                "policyTypes": ["Ingress", "Egress"]}})
        self.wait("database deny-all blocks the migration probe", lambda: not reachable(57152))
        policy = cutover.migration_network_policy(code)
        policy["metadata"]["namespace"] = self.namespace
        policy["spec"]["ingress"][0]["from"][0]["namespaceSelector"]["matchLabels"][
            "kubernetes.io/metadata.name"] = self.namespace
        self.apply(policy)
        self.wait("generated migration policy permits SQL", lambda: reachable(57152))
        if reachable(6379):
            raise RuntimeError("migration policy unexpectedly permits Redis")
        self.command("-n", self.namespace, "label", "pod", "migration-probe",
                     "knowi.io/source-sha=" + "b" * 40, "--overwrite")
        self.wait("another release SHA is denied SQL", lambda: not reachable(57152))
        self.command("-n", self.namespace, "label", "pod", "migration-probe",
                     "knowi.io/source-sha=" + code, "--overwrite")
        self.wait("matching release SHA regains SQL only", lambda: reachable(57152) and not reachable(6379))

    def run(self):
        config = json.loads(self.command("config", "view", "--minify", "-o", "json").stdout)
        server = config["clusters"][0]["cluster"]["server"]
        parsed = urlparse(server)
        if parsed.scheme != "https" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError("refusing non-loopback Kubernetes API endpoint")
        self.create(self.namespace_object())
        self.uid = self.get("namespace", self.namespace)["metadata"]["uid"]
        upstream = pathlib.Path(__file__).resolve().parents[2] / "infra/gitops/bootstrap/rollouts/upstream.yaml"
        manifest = upstream.read_text(encoding="utf-8").replace(
            "quay.io/argoproj/argo-rollouts:latest", "quay.io/argoproj/argo-rollouts:v1.10.2").replace(
            "imagePullPolicy: Always", "imagePullPolicy: Never").replace(
            "        - --namespaced\n", "        - --namespaced\n        - --namespace=" + self.namespace + "\n        - --traefik-api-group=traefik.io\n").replace(
            "- kind: ServiceAccount\n  name: argo-rollouts\n", "- kind: ServiceAccount\n  name: argo-rollouts\n  namespace: " + self.namespace + "\n")
        result = subprocess.run(self.base + ["-n", self.namespace, "apply", "-f", "-"], input=manifest,
                                text=True, capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError("controller manifest failed: " + result.stderr)
        self.wait("rehearsal controller available", lambda: self.get("deployment", "argo-rollouts").get("status", {}).get("availableReplicas", 0) >= 1)
        self.verify_migration_network_policy()
        for name in ("core-stable", "core-canary"):
            self.apply({"apiVersion": "v1", "kind": "Service", "metadata": {
                "name": name, "namespace": self.namespace}, "spec": {
                "selector": {"app": "rehearsal-core"}, "ports": [{"port": 80, "targetPort": 5678}]}})
        self.apply({"apiVersion": "traefik.io/v1alpha1", "kind": "TraefikService", "metadata": {
            "name": "core-api-weighted", "namespace": self.namespace}, "spec": {"weighted": {"services": [
                {"name": "core-stable", "port": 80, "weight": 100},
                {"name": "core-canary", "port": 80, "weight": 0}]}}})
        self.apply({"apiVersion": "argoproj.io/v1alpha1", "kind": "AnalysisTemplate", "metadata": {
            "name": "must-not-run", "namespace": self.namespace}, "spec": {"metrics": [{
                "name": "failure", "provider": {"web": {"url": "http://127.0.0.1:1/"}},
                "successCondition": "false"}]}})
        self.apply(self.rollout("c503"))
        self.wait("old synthetic Rollout stable", self.stable)
        self.install_traffic()
        self.wait("normal HTTP and HTTPS route to c503", lambda: self.http_matches(200, "c503"))
        items = self.maintenance()
        for item in items[:3]:
            self.apply(item)
        self.wait("maintenance nginx available", lambda: self.get("deployment", "c504-maintenance").get("status", {}).get("availableReplicas", 0) >= 1)
        for item in items[3:]:
            self.apply(item)
        self.wait("maintenance returns no-store 503 on all hosts, paths and methods", lambda: self.http_matches(503))
        # A paused weighted Rollout can retain a stable replica even at replicas=0.
        # With Argo CD sync stopped, foreground deletion drains its complete owner tree.
        self.command("-n", self.namespace, "delete", "rollout", "core", "--cascade=foreground", "--wait=true", "--timeout=120s")
        self.wait("old writers stopped", lambda: not [p for p in self.get("pods")["items"] if p["metadata"].get("labels", {}).get("app") == "rehearsal-core"])
        self.wait("old ReplicaSets gone", lambda: not [r for r in self.get("replicasets")["items"]
            if r["metadata"].get("labels", {}).get("app") == "rehearsal-core"])
        # Deliberate fail-before-cutover: opening routes is not part of the failure path.
        try:
            raise RuntimeError("injected cutover failure")
        except RuntimeError:
            if not self.http_matches(503):
                raise RuntimeError("maintenance HTTP gate failed after injected failure")
            print("PASS: injected failure leaves HTTP gate closed", flush=True)
        self.apply(self.rollout("c504"))
        self.wait("new initial Rollout stable despite indefinite canary pause", self.stable)
        self.wait("both Services select only new ready endpoints", self.endpoints_are_new)
        self.wait("weighted TraefikService routes 100% to stable", self.weights_stable)
        if self.get("analysisruns.argoproj.io")["items"]:
            raise RuntimeError("unexpected AnalysisRun on initial deployment")
        if not self.http_matches(503):
            raise RuntimeError("maintenance gate changed before checks")
        for item in items[3:]:
            self.command("-n", self.namespace, "delete", "ingressroute", item["metadata"]["name"], "--wait=true")
        self.wait("HTTP and HTTPS routes return c504 after opening", lambda: self.http_matches(200, "c504"))
        print("PASS: no AnalysisRun; gate opened after checks", flush=True)
        print("LIMIT: test nginx image and self-signed TLS substituted; synthetic Core only, no real migration, backup or restore", flush=True)

    def cleanup(self):
        for process in self.forwarders:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if not self.uid:
            return
        result = self.command("get", "namespace", self.namespace, "-o", "json", check=False)
        if result.returncode:
            print("WARNING: unable to verify namespace for cleanup; no deletion attempted", file=sys.stderr)
            return
        metadata = json.loads(result.stdout)["metadata"]
        if metadata.get("uid") != self.uid or metadata.get("labels", {}).get(OWNER) != self.token:
            raise RuntimeError("namespace ownership changed; refusing cleanup")
        self.command("delete", "namespace", self.namespace, "--wait=true", "--timeout=120s")


def run_fresh_cluster(args):
    token = uuid.uuid4().hex
    name = "knowi-c504-" + token[:12]
    label = "pymentor.c504.rehearsal=" + token
    container_id = None

    def docker(*parts, check=True, timeout=300):
        result = subprocess.run([args.docker, *parts], text=True, capture_output=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError("docker " + parts[0] + " failed: " + result.stderr.strip())
        return result

    try:
        for image in ("rancher/k3s:v1.37.1-k3s1", IMAGE, NGINX, TRAEFIK, "quay.io/argoproj/argo-rollouts:v1.10.2"):
            print("Preparing rehearsal image", image, flush=True)
            docker("pull", image, timeout=600)
        result = docker("run", "-d", "--name", name, "--label", label, "--privileged",
                        "--memory", "1536m", "-p", "127.0.0.1::6443", "rancher/k3s:v1.37.1-k3s1",
                        "server", "--disable=traefik,metrics-server,servicelb,local-storage", "--write-kubeconfig-mode=600")
        container_id = result.stdout.strip()
        print("Created isolated test container", name, flush=True)
        with tempfile.TemporaryDirectory(prefix="c504-rehearsal-") as tmp:
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                result = docker("exec", name, "/bin/ctr", "-n", "k8s.io", "images", "ls", check=False, timeout=20)
                if result.returncode == 0:
                    break
                time.sleep(2)
            else:
                raise RuntimeError("timed out waiting for isolated containerd")
            for number, image in enumerate((IMAGE, NGINX, TRAEFIK, "quay.io/argoproj/argo-rollouts:v1.10.2")):
                archive = str(pathlib.Path(tmp) / (str(number) + ".tar"))
                docker("save", "-o", archive, image, timeout=600)
                docker("cp", archive, name + ":/tmp/c504-image.tar", timeout=300)
                docker("exec", name, "/bin/ctr", "-n", "k8s.io", "images", "import", "/tmp/c504-image.tar", timeout=300)
                docker("exec", name, "rm", "/tmp/c504-image.tar")
            port_text = docker("port", name, "6443/tcp").stdout.strip()
            if not port_text.startswith("127.0.0.1:") or "\n" in port_text:
                raise RuntimeError("unexpected API port binding; refusing to continue")
            port = int(port_text.rsplit(":", 1)[1])
            deadline = time.monotonic() + args.timeout
            config = None
            while time.monotonic() < deadline:
                result = docker("exec", name, "cat", "/etc/rancher/k3s/k3s.yaml", check=False)
                if result.returncode == 0 and "https://127.0.0.1:6443" in result.stdout:
                    config = result.stdout.replace("https://127.0.0.1:6443", f"https://127.0.0.1:{port}")
                    break
                time.sleep(2)
            if not config:
                raise RuntimeError("timed out waiting for isolated kubeconfig")
            config_path = pathlib.Path(tmp) / "kubeconfig"
            config_path.write_text(config, encoding="utf-8")
            base = [args.kubectl, "--kubeconfig", str(config_path), "--context", "default"]
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                if subprocess.run(base + ["get", "nodes"], capture_output=True, timeout=20).returncode == 0:
                    break
                time.sleep(2)
            else:
                raise RuntimeError("timed out waiting for isolated API")
            crds = "https://github.com/argoproj/argo-rollouts/manifests/crds?ref=v1.10.2"
            result = subprocess.run(base + ["apply", "--server-side", "-k", crds], text=True, capture_output=True, timeout=300)
            if result.returncode:
                raise RuntimeError("pinned CRD install failed: " + result.stderr)
            traefik_crds = "https://raw.githubusercontent.com/traefik/traefik/v3.7.15/docs/content/reference/dynamic-configuration/kubernetes-crd-definition-v1.yml"
            result = subprocess.run(base + ["apply", "--server-side", "-f", traefik_crds], text=True, capture_output=True, timeout=300)
            if result.returncode:
                raise RuntimeError("pinned Traefik CRD install failed: " + result.stderr)
            rehearsal = Rehearsal(args.kubectl, str(config_path), "default", args.timeout)
            try:
                rehearsal.run()
            except Exception:
                for parts in (("get", "pods", "-o", "wide"), ("get", "events", "--sort-by=.lastTimestamp")):
                    result = rehearsal.command("-n", rehearsal.namespace, *parts, check=False)
                    print(result.stdout, flush=True)
                for deployment in ("argo-rollouts", "traefik", "c504-maintenance"):
                    result = rehearsal.command("-n", rehearsal.namespace, "logs", "deployment/" + deployment,
                                               "--tail=20", check=False)
                    print(deployment + ":\n" + result.stdout + result.stderr, flush=True)
                raise
            finally:
                rehearsal.cleanup()
    finally:
        # Reconcile by unique name as well if docker run timed out after creation.
        candidate = container_id or name
        if candidate:
            result = docker("inspect", "--format", '{{ index .Config.Labels "pymentor.c504.rehearsal" }}', candidate, check=False)
            if result.returncode or result.stdout.strip() != token:
                if container_id:
                    print("WARNING: container ownership could not be verified; cleanup skipped", file=sys.stderr)
            else:
                docker("rm", "-f", "-v", candidate, timeout=120)
                print("Removed owned test container", name, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubectl", default="kubectl")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute or args.timeout < 1:
        parser.error("--execute and a positive --timeout are required")
    run_fresh_cluster(args)


if __name__ == "__main__":
    main()
