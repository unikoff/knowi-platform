"""Prepare guarded c504 cutover artifacts; never connect to or modify a cluster."""
import argparse
import json
from pathlib import Path
import re

APP_UID = "69a2f746-52ef-4965-a581-4b9d3f6dba1e"
OLD_PATH = "deploy/gitops/preview"
NEW_PATH = "infra/gitops/preview"
HOST = "migrated-data.pymentor-rehearsal-data-20261109.svc.cluster.local"


def migration_user(code_revision):
    if not re.fullmatch(r"[0-9a-f]{40}", code_revision):
        raise ValueError("Migration login requires an immutable code revision")
    return "knowi_c504_" + code_revision[:12]
PUBLIC_HOSTS = ("knowi.ru", "www.knowi.ru", "13-140-29-11.sslip.io", "13.140.29.11")


def migration_network_policy(code_sha):
    if not re.fullmatch(r"[0-9a-f]{40}", code_sha):
        raise ValueError("Migration NetworkPolicy requires an immutable source SHA")
    return {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {
        "name": "c504-migrator-" + code_sha, "namespace": "pymentor-rehearsal-data-20261109",
        "labels": {"knowi.io/source-sha": code_sha}}, "spec": {
            "podSelector": {"matchLabels": {"app": "rehearsal-postgres"}}, "policyTypes": ["Ingress"],
            "ingress": [{"from": [{
                "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "pymentor"}},
                "podSelector": {"matchLabels": {"app.kubernetes.io/component": "migrations",
                                                   "knowi.io/source-sha": code_sha}}}],
                "ports": [{"protocol": "TCP", "port": 57152}]}]}}


def maintenance_artifact(code_revision, frontend_digest):
    if not re.fullmatch(r"[0-9a-f]{40}", code_revision) or not re.fullmatch(r"sha256:[0-9a-f]{64}", frontend_digest):
        raise ValueError("Maintenance requires immutable source SHA and frontend digest")
    labels = {"app.kubernetes.io/name": "c504-maintenance", "knowi.io/source-sha": code_revision}
    def meta(name):
        return {"name": name, "namespace": "pymentor", "labels": labels.copy()}
    nginx = """pid /tmp/nginx.pid;
events { worker_connections 256; }
http {
  client_body_temp_path /tmp/client_body;
  proxy_temp_path /tmp/proxy;
  fastcgi_temp_path /tmp/fastcgi;
  uwsgi_temp_path /tmp/uwsgi;
  scgi_temp_path /tmp/scgi;
  server { listen 8080; add_header Cache-Control 'no-store' always;
    return 503 'Service temporarily unavailable'; }
}
"""
    config = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": meta("c504-maintenance"), "data": {"nginx.conf": nginx}}
    deployment = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": meta("c504-maintenance"), "spec": {
        "replicas": 1, "selector": {"matchLabels": {"app": "c504-maintenance"}}, "template": {
            "metadata": {"labels": {"app": "c504-maintenance"}}, "spec": {
                "automountServiceAccountToken": False,
                "securityContext": {"runAsNonRoot": True, "runAsUser": 101, "runAsGroup": 101, "fsGroup": 101},
                "containers": [{"name": "nginx", "image": "ghcr.io/unikoff/knowi-platform-frontend@" + frontend_digest,
                    "command": ["nginx", "-c", "/etc/c504/nginx.conf", "-g", "daemon off;"],
                    "ports": [{"containerPort": 8080}],
                    "readinessProbe": {"tcpSocket": {"port": 8080}, "periodSeconds": 2},
                    "securityContext": {"readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                    "volumeMounts": [{"name": "config", "mountPath": "/etc/c504", "readOnly": True}, {"name": "tmp", "mountPath": "/tmp"}]}],
                "volumes": [{"name": "config", "configMap": {"name": "c504-maintenance"}}, {"name": "tmp", "emptyDir": {}}]}}}}
    service = {"apiVersion": "v1", "kind": "Service", "metadata": meta("c504-maintenance"), "spec": {
        "selector": {"app": "c504-maintenance"}, "ports": [{"port": 8080, "targetPort": 8080}]}}
    def route(name, hosts, entrypoint, secret=None):
        match = "(" + " || ".join("Host(`" + host + "`)" for host in hosts) + ") && PathPrefix(`/`)"
        if entrypoint == "web":
            match += " && !PathPrefix(`/.well-known/acme-challenge/`)"
        spec = {"entryPoints": [entrypoint], "routes": [{"kind": "Rule", "match": match,
            "priority": 2000000000, "services": [{"name": "c504-maintenance", "port": 8080}]}]}
        if secret:
            spec["tls"] = {"secretName": secret}
        return {"apiVersion": "traefik.io/v1alpha1", "kind": "IngressRoute", "metadata": meta(name), "spec": spec}
    return {"apiVersion": "v1", "kind": "List", "items": [config, deployment, service,
        route("c504-maintenance-http", PUBLIC_HOSTS, "web"),
        route("c504-maintenance-domain-https", PUBLIC_HOSTS[:2], "websecure", "knowi-tls"),
        route("c504-maintenance-preview-https", PUBLIC_HOSTS[2:], "websecure", "preview-tls")]}


def prepare(release_file, code_revision, gitops_revision, output):
    if any(not re.fullmatch(r"[0-9a-f]{40}", value) for value in (code_revision, gitops_revision)):
        raise ValueError("Both code and GitOps revisions must be immutable commit SHAs")
    release = json.loads(Path(release_file).read_text(encoding="utf-8-sig"))
    digest = release.get("migration_digest", "")
    if release.get("source_sha") != code_revision or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Published release must match the code revision and contain a migration digest")
    maintenance = maintenance_artifact(code_revision, release.get("frontend_digest", ""))
    output = Path(output)
    if output.exists():
        raise ValueError("Choose a new output directory; existing artifacts are never overwritten")
    identity = {"op": "test", "path": "/metadata/uid", "value": APP_UID}
    pause = [identity, {"op": "test", "path": "/spec/source/path", "value": OLD_PATH},
             {"op": "test", "path": "/spec/source/targetRevision", "value": "Release"},
             {"op": "replace", "path": "/spec/syncPolicy/automated/enabled", "value": False}]
    activate = [identity, {"op": "test", "path": "/spec/syncPolicy/automated/enabled", "value": False},
                {"op": "test", "path": "/spec/source/path", "value": OLD_PATH},
                {"op": "replace", "path": "/spec/source/targetRevision", "value": gitops_revision},
                {"op": "replace", "path": "/spec/source/path", "value": NEW_PATH}]
    job = {"apiVersion": "batch/v1", "kind": "Job", "metadata": {
        "name": "knowi-c504-" + code_revision[:12], "namespace": "pymentor",
        "annotations": {"knowi.io/code-revision": code_revision}}, "spec": {
        "backoffLimit": 0, "activeDeadlineSeconds": 300, "template": {
            "metadata": {"labels": {"app.kubernetes.io/component": "migrations", "knowi.io/source-sha": code_revision}}, "spec": {
                "restartPolicy": "Never", "automountServiceAccountToken": False,
                "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001},
                "containers": [{"name": "migrate",
                    "image": "ghcr.io/unikoff/knowi-platform-core-migrations@" + digest,
                    "command": ["python", "-m", "alembic", "-c", "/app/services/core-api/alembic-runtime.ini", "upgrade", "c504_course_progress"],
                    "env": [
                        {"name": "KNOWI_MIGRATION_URL", "valueFrom": {"secretKeyRef": {"name": "knowi-core-migrations", "key": "KNOWI_MIGRATION_URL"}}},
                        {"name": "KNOWI_MIGRATION_HOST", "value": HOST},
                        {"name": "KNOWI_MIGRATION_DATABASE", "value": "knowi_core"},
                        {"name": "KNOWI_MIGRATION_USER", "value": migration_user(code_revision)}],
                    "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "1", "memory": "256Mi"}},
                    "securityContext": {"readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}}]}}}}
    artifacts = {"01-pause-application.patch.json": pause,
                 "01-migrator-networkpolicy.json": migration_network_policy(code_revision),
                 "02-migration-job.json": job, "03-switch-pinned-path.patch.json": activate,
                 "00-maintenance.json": maintenance}
    output.mkdir(parents=True)
    for name, document in artifacts.items():
        with (output / name).open("x", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2)
            stream.write("\n")
    return artifacts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("release-file", "code-revision", "gitops-revision", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        prepare(args.release_file, args.code_revision, args.gitops_revision, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
    print("Prepared only: no cluster changes. Follow the maintenance cutover runbook.")
