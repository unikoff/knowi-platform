"""Control the independent knowi maintenance page from an operator workstation."""

import argparse
import http.client
import json
import os
import ssl
import subprocess
import sys
import time
import uuid
from pathlib import Path

from prepare_maintenance import (
    BACKEND_RESOURCES,
    DEFAULT_PAGE,
    NAME,
    NAMESPACE,
    PUBLIC_HOSTS,
    artifacts,
    metadata,
    routes,
)


RELEASE_FILE = Path(__file__).resolve().parents[1] / "preview" / "release.json"
LOCK = "configmap/" + NAME + "-lock"
MARKER = "X-Knowi-Maintenance"
CATALOG = "/api/learning/catalog"
ROUTES = routes()
ROUTE_RESOURCES = tuple("ingressroute.traefik.io/" + item["metadata"]["name"] for item in ROUTES["items"])


def until(check, timeout, description):
    deadline = time.monotonic() + timeout
    while not check():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Timed out waiting for " + description)
        time.sleep(min(2, remaining))


class Kubernetes:
    def __init__(self, context, timeout):
        self.command = ["kubectl", "--context", context, "--namespace", NAMESPACE, "--request-timeout=10s"]
        self.timeout = timeout

    def call(self, *args, document=None):
        result = subprocess.run(
            self.command + list(args),
            input=json.dumps(document) if document is not None else None,
            capture_output=True, encoding="utf-8", errors="replace", timeout=15,
        )
        if result.returncode:
            raise RuntimeError(f"kubectl {args[0]} failed: {result.stderr.strip() or result.stdout.strip()}")
        return result.stdout

    def get(self, resource):
        raw = self.call("get", resource, "--ignore-not-found", "-o", "json")
        return json.loads(raw) if raw.strip() else None

    def write(self, verb, document):
        self.call(verb, "-f", "-", document=document)

    def delete(self, resources):
        # Confirm deletion separately from the HTTP probes that check Traefik propagation.
        self.call("delete", *resources, "--ignore-not-found", "--wait=false")

    def wait_ready(self):
        def ready():
            deployment = self.get(BACKEND_RESOURCES[1]) or {}
            status = deployment.get("status", {})
            return (
                status.get("observedGeneration") == deployment.get("metadata", {}).get("generation")
                and status.get("updatedReplicas") == 1
                and status.get("availableReplicas") == 1
                and status.get("replicas") == 1
            )

        until(ready, self.timeout, "the maintenance Deployment to become ready")


class PublicTraffic:
    def __init__(self, timeout):
        self.timeout = timeout
        self.tls = ssl.create_default_context()
        # The historical raw-IP ingress uses the preview hostname certificate.
        self.ip_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.ip_tls.check_hostname = False
        self.ip_tls.verify_mode = ssl.CERT_NONE

    def get(self, host, scheme, path):
        if scheme == "https":
            tls = self.ip_tls if host == PUBLIC_HOSTS[-1] else self.tls
            connection = http.client.HTTPSConnection(host, timeout=3, context=tls)
        else:
            connection = http.client.HTTPConnection(host, timeout=3)
        try:
            connection.request("GET", f"{path}?_maintenance_probe={uuid.uuid4().hex}",
                               headers={"Cache-Control": "no-cache"})
            response = connection.getresponse()
            matches_app = False
            if host == PUBLIC_HOSTS[0] and scheme == "https" and response.status == 200:
                content_type = response.headers.get_content_type()
                if path == "/" and content_type == "text/html":
                    matches_app = b"<title>knowi</title>" in response.read(32_768)
                elif path == CATALOG and content_type == "application/json":
                    body = json.loads(response.read(2_097_153))
                    matches_app = isinstance(body, dict) and isinstance(body.get("courses"), dict)
            return response.status, response.getheader(MARKER), response.getheader("Cache-Control", ""), matches_app
        finally:
            connection.close()

    def wait(self, token):
        pending = [(host, scheme, path) for host in PUBLIC_HOSTS
                   for scheme in ("http", "https") for path in ("/", CATALOG)]
        deadline = time.monotonic() + self.timeout
        while pending:
            next_pending = []
            for host, scheme, path in pending:
                try:
                    status, marker, cache, matches_app = self.get(host, scheme, path)
                    if token is not None:
                        if status == 503 and marker == token and "no-store" in cache.lower():
                            continue
                    elif marker is None:
                        canonical = host == PUBLIC_HOSTS[0] and scheme == "https"
                        if matches_app or (not canonical and status < 500):
                            continue
                except (OSError, ValueError, http.client.HTTPException):
                    pass
                next_pending.append((host, scheme, path))
            pending = next_pending
            if pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    targets = ", ".join(f"{scheme}://{host}{path}" for host, scheme, path in pending)
                    raise RuntimeError(f"Public maintenance verification timed out: {targets}")
                time.sleep(min(2, remaining))


def owned(kube, resource):
    document = kube.get(resource)
    if document is not None and document["metadata"].get("labels", {}).get("app.kubernetes.io/name") != NAME:
        raise RuntimeError(f"Resource already exists outside maintenance ownership: {resource}")
    return document


def lock_for(kube, recover):
    operation_id = uuid.uuid4().hex
    current = owned(kube, LOCK)
    lock = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata(LOCK.split("/")[1]),
            "data": {"operationId": operation_id}}
    if recover is not None:
        if current is None or current["data"]["operationId"] != recover:
            raise RuntimeError("Recovery ID does not match the current maintenance lock")
        lock["metadata"]["resourceVersion"] = current["metadata"]["resourceVersion"]
        verb = "replace"
    elif current is not None:
        raise RuntimeError(f"Maintenance lock is held by operation {current['data']['operationId']}")
    else:
        verb = "create"
    print(f"Maintenance operation ID: {operation_id}", flush=True)
    kube.write(verb, lock)
    return operation_id


def release_lock(kube, operation_id):
    current = owned(kube, LOCK)
    if current is not None and current["data"]["operationId"] == operation_id:
        kube.delete((LOCK,))


def enable(kube, traffic, release_file):
    state = [owned(kube, resource) for resource in BACKEND_RESOURCES]
    for resource in ROUTE_RESOURCES:
        owned(kube, resource)
    config = state[0]
    existing_token = (config or {}).get("metadata", {}).get("annotations", {}).get("knowi.io/maintenance-token")
    token = existing_token if existing_token and existing_token != "1" else uuid.uuid4().hex
    digest = json.loads(release_file.read_text(encoding="utf-8-sig"))["frontend_digest"]
    package = artifacts(DEFAULT_PAGE.read_text(encoding="utf-8"), digest, token)
    kube.write("apply", package["backend.json"])
    kube.wait_ready()
    kube.write("apply", package["routes.json"])
    traffic.wait(token)
    print("Maintenance mode is active.", flush=True)


def disable(kube, traffic, timeout):
    active = {}
    for resource in ROUTE_RESOURCES:
        if (document := owned(kube, resource)) is not None:
            active[resource] = document
    backend = {}
    for resource in BACKEND_RESOURCES:
        if (document := owned(kube, resource)) is not None:
            backend[resource] = document
    # Keep the backend ready until Traefik has switched back. Restore the routes
    # that were present if normal traffic cannot be verified.
    if active:
        try:
            kube.delete(tuple(active))
            until(lambda: all(kube.get(resource) is None for resource in active), timeout, "route deletion")
            traffic.wait(None)
        except (Exception, KeyboardInterrupt):
            previous = []
            for document in active.values():
                route_metadata = document["metadata"]
                restored = metadata(route_metadata["name"])
                restored["labels"] = route_metadata["labels"]
                restored["annotations"] = {
                    key: value
                    for key, value in route_metadata.get("annotations", {}).items()
                    if key != "kubectl.kubernetes.io/last-applied-configuration"
                }
                previous.append({"apiVersion": document["apiVersion"], "kind": document["kind"],
                                 "metadata": restored, "spec": document["spec"]})
            kube.write("apply", {**ROUTES, "items": previous})
            config = backend.get(BACKEND_RESOURCES[0], {})
            token = config.get("metadata", {}).get("annotations", {}).get("knowi.io/maintenance-token")
            if token and len(active) == len(ROUTE_RESOURCES):
                traffic.wait(token)
                print("Public maintenance responses verified after unsuccessful reopening.", file=sys.stderr)
            raise
    if backend:
        kube.delete(tuple(backend))
    print("Maintenance mode is off.", flush=True)


def load_workflow(path):
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or document.keys() != {"operation", "check"}:
        raise ValueError("Workflow must provide operation and check command arrays")
    for phase, argv in document.items():
        if (not isinstance(argv, list) or not argv
                or not all(isinstance(arg, str) and "\0" not in arg for arg in argv) or not argv[0]):
            raise ValueError(f"Workflow {phase} must be a non-empty array of command arguments")
    return document


def run_step(argv, phase, context):
    env = dict(os.environ, KNOWI_MAINTENANCE_CONTEXT=context, KNOWI_MAINTENANCE_NAMESPACE=NAMESPACE)
    result = subprocess.run(argv, env=env)
    if result.returncode:
        raise RuntimeError(f"Workflow {phase} exited with status {result.returncode}")


def execute(options, kube, traffic):
    workflow = load_workflow(options.workflow) if options.action == "run" else None
    recover = getattr(options, "recover", None)
    operation_id = lock_for(kube, recover)
    retain_lock = recover is not None
    try:
        if options.action == "off":
            disable(kube, traffic, options.timeout)
        elif options.action == "on":
            enable(kube, traffic, options.release_file)
        else:
            if any(owned(kube, resource) is not None for resource in ROUTE_RESOURCES):
                raise RuntimeError("A maintenance window is already active. Resolve it before starting a new one.")
            enable(kube, traffic, options.release_file)
            retain_lock = True
            run_step(workflow["operation"], "operation", options.context)
            run_step(workflow["check"], "check", options.context)
            disable(kube, traffic, options.timeout)
        retain_lock = False
    finally:
        if retain_lock:
            print(f"Maintenance lock retained: {operation_id}. Reconcile the operation before recovery.", file=sys.stderr)
        else:
            release_lock(kube, operation_id)


def parse_options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="explicit kubectl context for the knowi cluster")
    parser.add_argument("--release-file", type=Path, default=RELEASE_FILE, help="release.json with pinned frontend_digest")
    parser.add_argument("--timeout", type=int, default=120, help="convergence timeout in seconds per stage")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("on", help="enable and verify the public maintenance page")
    off = commands.add_parser("off", help="restore normal public traffic")
    off.add_argument("--recover", metavar="ID", help="recover a stopped operation after verifying internal health")
    run = commands.add_parser("run", help="run a disruptive workflow and its health check under maintenance")
    run.add_argument("workflow", type=Path, help="JSON with operation and check argv arrays")
    options = parser.parse_args(argv)
    if options.timeout <= 0:
        parser.error("--timeout must be positive")
    return options


if __name__ == "__main__":
    options = parse_options()
    try:
        execute(options, Kubernetes(options.context, options.timeout), PublicTraffic(options.timeout))
    except (KeyError, OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        print(f"Maintenance command failed: {error}", file=sys.stderr)
        sys.exit(1)
