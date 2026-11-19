"""Prepare standalone maintenance manifests locally; never access a cluster."""

import argparse
import json
import re
from pathlib import Path


PUBLIC_HOSTS = ("knowi.ru", "www.knowi.ru", "13-140-29-11.sslip.io", "13.140.29.11")
DEFAULT_PAGE = Path(__file__).resolve().parents[3] / "apps/web/public/maintenance.html"
NAME = "knowi-maintenance"
NAMESPACE = "pymentor"

NGINX_CONFIG = """pid /tmp/nginx.pid;
events { worker_connections 256; }
http {
  client_body_temp_path /tmp/client_body;
  proxy_temp_path /tmp/proxy;
  fastcgi_temp_path /tmp/fastcgi;
  uwsgi_temp_path /tmp/uwsgi;
  scgi_temp_path /tmp/scgi;
  server {
    listen 8080;
    add_header Cache-Control 'no-store' always;
    error_page 404 =503 /maintenance-content.html;
    location ~ ^/api(?:/|$) {
      default_type application/json;
      return 503 '{"detail":"Service temporarily unavailable"}';
    }
    location = /maintenance-content.html {
      internal;
      default_type text/html;
      charset utf-8;
      alias /etc/maintenance/maintenance.html;
    }
    location / {
      error_page 503 /maintenance-content.html;
      return 503;
    }
  }
}
"""


def metadata(name):
    return {"name": name, "namespace": NAMESPACE,
            "labels": {"app.kubernetes.io/name": NAME}}


def route(name, hosts, entrypoint, secret=None):
    match = "(" + " || ".join("Host(`" + host + "`)" for host in hosts) + ") && PathPrefix(`/`)"
    if entrypoint == "web":
        match += " && !PathPrefix(`/.well-known/acme-challenge/`)"
    spec = {"entryPoints": [entrypoint], "routes": [{"kind": "Rule", "match": match,
            "priority": 2000000000, "services": [{"name": NAME, "port": 8080}]}]}
    if secret:
        spec["tls"] = {"secretName": secret}
    return {"apiVersion": "traefik.io/v1alpha1", "kind": "IngressRoute",
            "metadata": metadata(name), "spec": spec}


def artifacts(page, digest):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Frontend digest must be sha256: followed by 64 lowercase hex characters")
    if not page.strip():
        raise ValueError("Maintenance page must not be empty")
    config = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata(NAME),
              "data": {"nginx.conf": NGINX_CONFIG, "maintenance.html": page}}
    deployment = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": metadata(NAME), "spec": {
        "replicas": 1, "selector": {"matchLabels": {"app": NAME}}, "template": {
            "metadata": {"labels": {"app": NAME}}, "spec": {
                "automountServiceAccountToken": False,
                "securityContext": {"runAsNonRoot": True, "runAsUser": 101, "runAsGroup": 101, "fsGroup": 101},
                "containers": [{"name": "nginx", "image": "ghcr.io/unikoff/knowi-platform-frontend@" + digest,
                    "command": ["nginx", "-c", "/etc/maintenance/nginx.conf", "-g", "daemon off;"],
                    "ports": [{"containerPort": 8080}],
                    "readinessProbe": {"tcpSocket": {"port": 8080}, "periodSeconds": 2},
                    "securityContext": {"readOnlyRootFilesystem": True, "allowPrivilegeEscalation": False,
                                        "capabilities": {"drop": ["ALL"]}},
                    "volumeMounts": [{"name": "config", "mountPath": "/etc/maintenance", "readOnly": True},
                                     {"name": "tmp", "mountPath": "/tmp"}]}],
                "volumes": [{"name": "config", "configMap": {"name": NAME}},
                            {"name": "tmp", "emptyDir": {}}]}}}}
    service = {"apiVersion": "v1", "kind": "Service", "metadata": metadata(NAME), "spec": {
        "selector": {"app": NAME}, "ports": [{"port": 8080, "targetPort": 8080}]}}
    routes = [route(NAME + "-http", PUBLIC_HOSTS, "web"),
              route(NAME + "-domain-https", PUBLIC_HOSTS[:2], "websecure", "knowi-tls"),
              route(NAME + "-preview-https", PUBLIC_HOSTS[2:], "websecure", "preview-tls")]
    return {"backend.json": {"apiVersion": "v1", "kind": "List", "items": [config, deployment, service]},
            "routes.json": {"apiVersion": "v1", "kind": "List", "items": routes}}


def prepare(page_path, digest, output):
    page = Path(page_path).read_text(encoding="utf-8")
    documents = artifacts(page, digest)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for name, document in documents.items():
        with (output / name).open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, indent=2, ensure_ascii=False)
            stream.write("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-digest", required=True)
    parser.add_argument("--page", type=Path, default=DEFAULT_PAGE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.page, args.frontend_digest, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
    print("Prepared locally; no cluster changes. See infra/gitops/operations/maintenance.md.")
