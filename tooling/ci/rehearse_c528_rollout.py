#!/usr/bin/env python3
"""Rehearse c504 to c528 traffic cutover in a disposable k3s cluster.

Synthetic workloads only. PostgreSQL recovery is a separate rehearsal. The shared
harness makes a nonce-owned Docker cluster and never uses a live kubeconfig.
"""
import argparse
import http.client
import importlib.util
import json
import pathlib
from pathlib import Path
import ssl
import subprocess
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("c504_harness", HERE / "rehearse_c504_rollout.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)
cutover = harness.cutover
PATHS = harness.PATHS + ("/api/learning/catalog",)
spec = importlib.util.spec_from_file_location("current_maintenance", harness.ROOT / "infra/gitops/operations/prepare_maintenance.py")
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)


class Rehearsal(harness.Rehearsal):
    def __init__(self, *args):
        super().__init__(*args)
        self.expected_version = "c528"

    def maintenance(self):
        package = maintenance.artifacts(maintenance.DEFAULT_PAGE.read_text(encoding="utf-8"),
                                        "sha256:" + "1" * 64, self.token)
        items = package["backend.json"]["items"] + package["routes.json"]["items"]
        for item in items:
            item["metadata"]["namespace"] = self.namespace
            if item["kind"] == "Deployment":
                container = item["spec"]["template"]["spec"]["containers"][0]
                container["image"] = harness.NGINX
                container["imagePullPolicy"] = "Never"
            if item["kind"] == "IngressRoute" and "tls" in item["spec"]:
                item["spec"]["tls"] = {}
        return items

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
                            if status == 503 and ("no-store" not in response.getheader("Cache-Control", "") or response.getheader("X-Knowi-Maintenance") != self.token):
                                conn.close()
                                return False
                            conn.close()
            return True
        except (OSError, http.client.HTTPException):
            return False

    def endpoints_are_new(self):
        pods = [p for p in self.get("pods")["items"] if p["metadata"].get("labels", {}).get("app") == "rehearsal-core"]
        if len(pods) != 1 or pods[0]["metadata"]["labels"].get("version") != self.expected_version:
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

    def opening_guard(self, inject_failure=False):
        if inject_failure:
            raise RuntimeError("injected readiness failure")
        if not self.stable() or not self.endpoints_are_new() or not self.weights_stable():
            raise RuntimeError("readiness, endpoints or weights invalid")
        if self.get("analysisruns.argoproj.io")["items"] or not self.http_matches(503):
            raise RuntimeError("analysis ran or maintenance gate changed before reopening")

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
        self.apply(self.rollout("c504"))
        self.wait("old synthetic Rollout stable", self.stable)
        self.install_traffic()
        self.wait("normal HTTP and HTTPS route to c504", lambda: self.http_matches(200, "c504"))
        # This independent process represents a background writer, not real Core code.
        self.apply({"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {
            "name": "synthetic-writer", "namespace": self.namespace}, "spec": {
                "replicas": 1, "selector": {"matchLabels": {"app": "synthetic-writer"}},
                "template": {"metadata": {"labels": {"app": "synthetic-writer"}},
                    "spec": {"containers": [{"name": "writer", "image": harness.IMAGE,
                        "imagePullPolicy": "Never", "args": ["-text=writer", "-listen=:5678"]}]}}}})
        self.wait("synthetic independent writer available", lambda: self.get("deployment", "synthetic-writer").get("status", {}).get("availableReplicas", 0) == 1)
        items = self.maintenance()
        for item in items[:3]:
            self.apply(item)
        self.wait("maintenance nginx available", lambda: self.get("deployment", "knowi-maintenance").get("status", {}).get("availableReplicas", 0) >= 1)
        for item in items[3:]:
            self.apply(item)
        self.wait("maintenance returns no-store 503 on all hosts, paths and methods", lambda: self.http_matches(503))
        self.command("-n", self.namespace, "delete", "deployment", "synthetic-writer", "--cascade=foreground", "--wait=true", "--timeout=120s")
        self.wait("independent writer stopped", lambda: not [p for p in self.get("pods")["items"] if p["metadata"].get("labels", {}).get("app") == "synthetic-writer"])
        # A paused weighted Rollout can retain a stable replica even at replicas=0.
        # With Argo CD sync stopped, foreground deletion drains its complete owner tree.
        self.command("-n", self.namespace, "delete", "rollout", "core", "--cascade=foreground", "--wait=true", "--timeout=120s")
        self.wait("old writers stopped", lambda: not [p for p in self.get("pods")["items"] if p["metadata"].get("labels", {}).get("app") == "rehearsal-core"])
        self.wait("old ReplicaSets gone", lambda: not [r for r in self.get("replicasets")["items"]
            if r["metadata"].get("labels", {}).get("app") == "rehearsal-core"])
        # Exercise failure hold and a traffic-only rollback before the success path.
        # Database restore/compatibility must be established by the separate PG rehearsal.
        self.apply(self.rollout("c528"))
        self.wait("new initial Rollout stable despite indefinite canary pause", self.stable)
        self.wait("both Services select only c528 ready endpoints", self.endpoints_are_new)
        self.wait("weighted TraefikService routes 100% to stable", self.weights_stable)
        if self.get("analysisruns.argoproj.io")["items"]:
            raise RuntimeError("unexpected AnalysisRun on initial deployment")
        try:
            self.opening_guard(inject_failure=True)
        except RuntimeError as exc:
            if str(exc) != "injected readiness failure":
                raise
        else:
            raise RuntimeError("injected failure did not stop opening")
        if not self.http_matches(503):
            raise RuntimeError("maintenance gate changed after injected failure")
        print("PASS: injected post-bootstrap failure holds public maintenance", flush=True)
        self.command("-n", self.namespace, "delete", "rollout", "core", "--cascade=foreground", "--wait=true", "--timeout=120s")
        self.wait("failed c528 pods stopped", lambda: not [p for p in self.get("pods")["items"] if p["metadata"].get("labels", {}).get("app") == "rehearsal-core"])
        self.expected_version = "c504"
        self.apply(self.rollout("c504"))
        self.wait("rollback c504 Rollout stable", self.stable)
        self.wait("rollback Services select only c504 endpoints", self.endpoints_are_new)
        self.wait("rollback weights stable", self.weights_stable)
        if not self.http_matches(503):
            raise RuntimeError("maintenance gate changed during rollback")
        print("PASS: synthetic traffic rollback ready behind maintenance; database restore is separate", flush=True)
        self.command("-n", self.namespace, "delete", "rollout", "core", "--cascade=foreground", "--wait=true", "--timeout=120s")
        self.wait("rollback c504 pods stopped", lambda: not [p for p in self.get("pods")["items"] if p["metadata"].get("labels", {}).get("app") == "rehearsal-core"])
        self.expected_version = "c528"
        self.apply(self.rollout("c528"))
        self.wait("final c528 Rollout stable", self.stable)
        self.wait("final Services select only c528 ready endpoints", self.endpoints_are_new)
        self.wait("final weighted TraefikService routes 100% to stable", self.weights_stable)
        self.opening_guard()
        for item in items[3:]:
            self.command("-n", self.namespace, "delete", "ingressroute", item["metadata"]["name"], "--wait=true")
        self.wait("HTTP and HTTPS routes return c528 after opening", lambda: self.http_matches(200, "c528"))
        print("PASS: no AnalysisRun; gate opened after checks", flush=True)
        print("LIMIT: test nginx image and self-signed TLS substituted; synthetic Core and writer only, no real migration, backup or restore", flush=True)



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubectl", default="kubectl")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute or args.timeout < 1:
        parser.error("--execute and a positive --timeout are required")
    harness.PREFIX = "c528-rehearsal-"
    harness.OWNER = "pymentor.example/c528-rehearsal"
    harness.run_fresh_cluster(args, rehearsal_class=Rehearsal, run_id="c528")


if __name__ == "__main__":
    main()
