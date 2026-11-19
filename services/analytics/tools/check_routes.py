"""Disposable local synthetic route smoke. Requires Docker and cached images.

Creates only a uniquely named Compose project and an internal network, then
removes that project without deleting volumes. Does not connect to any DB.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "infra/analytics-local/compose.yaml"
ROUTES = ROOT / "infra/analytics-local/routes.yaml"
APP_IMAGE = "knowi-analytics:corp63-synthetic"
PROXY_IMAGE = "traefik:v3.7.15"
PROJECT = "knowi-analytics-c64-routes-" + uuid.uuid4().hex[:12]
NETWORK = PROJECT + "-net"
EDGE_NETWORK = PROJECT + "-edge"
PORT = 57400
TMP_PARENT = ROOT / "services/analytics/.local/route-smoke"


def run(args, env=None, timeout=40):
    result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Command failed: {args[0]} (exit {result.returncode}); output withheld")
    return result.stdout.strip()


def request(path, headers=None, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}" + path, headers=headers or {}, method=method)
    try:
        response = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=3)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read().decode(), dict(response.headers)


def expect(condition, detail):
    if not condition:
        raise AssertionError(detail)
    print("PASS", detail, flush=True)


def main():
    env = os.environ.copy()
    for role in ("COLLECTOR", "PROCESSOR", "QUERY", "MIGRATE"):
        env[f"ANALYTICS_{role}_DATABASE_URL"] = (
            "postgresql://smoke:synthetic@192.0.2.1:5432/analytics_route_smoke"
        )
        env[f"ANALYTICS_{role}_DB_TIMEOUT_SECONDS"] = "0.2"

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", PORT))

    existing = run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}"])
    expect(not existing, "unique project has no existing containers")
    print("Project", PROJECT, flush=True)
    print("Docker server", run(["docker", "version", "--format", "{{.Server.Version}}"]), flush=True)
    for image in (APP_IMAGE, PROXY_IMAGE):
        print("Image", image, run(["docker", "image", "inspect", image, "--format", "{{.Id}}"]), flush=True)

    TMP_PARENT.mkdir(parents=True, exist_ok=True)
    parent = TMP_PARENT.resolve(strict=True)
    if not parent.is_relative_to(ROOT.resolve() / "services" / "analytics" / ".local"):
        raise RuntimeError("route-smoke temporary parent is outside the expected workspace")
    with tempfile.TemporaryDirectory(prefix="run-", dir=parent) as tmp:
        tmp_path = Path(tmp)
        if not tmp_path.resolve(strict=True).is_relative_to(parent):
            raise RuntimeError("temporary directory is outside the route-smoke parent")
        fallback = tmp_path / "fallback.yaml"
        fallback.write_text('''http:
  routers:
    core-fallback:
      rule: "Host(`route-check.local`) && (Path(`/api`) || PathPrefix(`/api/`))"
      priority: 20
      service: core-fallback
    mentor-fallback:
      rule: "Host(`route-check.local`) && PathPrefix(`/api/mentor/`)"
      priority: 90
      service: mentor-fallback
  services:
    core-fallback:
      loadBalancer:
        servers: [{url: "http://core-fallback:8000"}]
    mentor-fallback:
      loadBalancer:
        servers: [{url: "http://mentor-fallback:8000"}]
''', encoding="utf-8")
        echo = ("from http.server import BaseHTTPRequestHandler,HTTPServer; import os; "
                "H=type('H',(BaseHTTPRequestHandler,),{'do_GET':lambda s:(s.send_response(200),"
                "s.end_headers(),s.wfile.write(os.environ['SMOKE_LABEL'].encode())),"
                "'log_message':lambda *a:None}); HTTPServer(('0.0.0.0',8000),H).serve_forever()")
        app = {"image": APP_IMAGE, "mem_limit": "128m", "cpus": 0.25}
        fake = lambda label: {**app, "entrypoint": ["python", "-u", "-c", echo], "command": [],
                              "environment": {"SMOKE_LABEL": label}, "read_only": True,
                              "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
                              "networks": ["analytics-data"]}
        override = {
            "services": {
                "collector": app, "processor": app, "query": app,
                "core-fallback": fake("synthetic-core"), "mentor-fallback": fake("synthetic-mentor"),
                "ingress": {"image": PROXY_IMAGE, "mem_limit": "128m", "cpus": 0.25,
                            "networks": ["analytics-data", "edge"],
                            "command": ["--providers.file.directory=/etc/traefik/dynamic",
                                        "--entrypoints.web.address=:8000", "--api.dashboard=false", "--accesslog=false"],
                            "volumes": [f"{ROUTES.as_posix()}:/etc/traefik/dynamic/routes.yaml:ro",
                                        f"{fallback.as_posix()}:/etc/traefik/dynamic/fallback.yaml:ro"]},
            },
            "networks": {"analytics-data": {"external": False, "internal": True, "name": NETWORK},
                         "edge": {"name": EDGE_NETWORK}},
        }
        override_path = tmp_path / "override.json"
        override_path.write_text(json.dumps(override), encoding="utf-8")
        compose = ["docker", "compose", "-p", PROJECT, "-f", str(BASE), "-f", str(override_path)]
        attempted = False
        cleanup_error = None
        try:
            config = json.loads(run(compose + ["config", "--format", "json"], env))
            expect(config["networks"]["analytics-data"]["internal"] is True and
                   config["networks"]["analytics-data"]["name"] == NETWORK,
                   "project uses its own internal network")
            for role in ("collector", "processor", "query", "core-fallback", "mentor-fallback"):
                expect(not config["services"][role].get("ports"), f"{role} has no published port")
                expect(set(config["services"][role]["networks"]) == {"analytics-data"}, f"{role} uses only internal network")
                expect(int(config["services"][role]["mem_limit"]) <= 128 * 1024 * 1024, f"{role} memory capped at 128 MiB")
            expect(int(config["services"]["ingress"]["mem_limit"]) <= 128 * 1024 * 1024, "ingress memory capped at 128 MiB")
            attempted = True
            run(compose + ["up", "-d", "--no-build", "--pull", "never", "collector", "processor", "query", "core-fallback", "mentor-fallback", "ingress"], env, timeout=60)
            for role in ("collector", "processor", "query", "core-fallback", "mentor-fallback", "ingress"):
                container_id = run(compose + ["ps", "-q", role], env)
                details = run(["docker", "inspect", container_id, "--format", "{{.HostConfig.Memory}} {{json .HostConfig.PortBindings}}"])
                memory, bindings = details.split(" ", 1)
                expect(0 < int(memory) <= 128 * 1024 * 1024, f"{role} running memory limit is at most 128 MiB")
                if role != "ingress":
                    expect(not json.loads(bindings), f"{role} running container has no host port bindings")
                else:
                    actual = json.loads(run(["docker", "inspect", container_id, "--format", "{{json .NetworkSettings.Ports}}"]))
                    expect(actual.get("8000/tcp") == [{"HostIp": "127.0.0.1", "HostPort": str(PORT)}], "ingress is published only to loopback")
            for path in ("/api/analytics/events/batch", "/api/analytics/reports", "/api/analytics/reports/activity"):
                deadline = time.monotonic() + 20
                last = "no response"
                while time.monotonic() < deadline:
                    try:
                        status, body, _ = request(path)
                        last = f"status={status}, body={body[:80]!r}"
                        if status == 404 and json.loads(body) == {"detail": "Not Found"}:
                            break
                    except (OSError, ValueError, RuntimeError):
                        pass
                    time.sleep(0.25)
                else:
                    raise AssertionError(f"{path} did not reach FastAPI before deadline: {last}")
                expect(True, f"{path} forwarded to FastAPI (404; handler deferred)")
            collector_id = run(compose + ["ps", "-q", "collector"], env)
            query_id = run(compose + ["ps", "-q", "query"], env)
            collector_logs = run(["docker", "logs", "--tail", "30", collector_id])
            query_logs = run(["docker", "logs", "--tail", "30", query_id])
            expect("GET /api/analytics/events/batch " in collector_logs, "collector received event request")
            expect("GET /api/analytics/reports/activity " in query_logs, "query received report request")
            for path in ("/api/analytics/events/batch-extra", "/api/analytics/reports-other", "/live", "/api/analytics/control", "/processor", "/db"):
                status, body, _ = request(path)
                expect(status == 404 and body.strip() == "404 page not found", f"{path} rejected by Traefik")
            for path, expected in (("/api/user/login", "synthetic-core"), ("/api/mentor/chat", "synthetic-mentor")):
                status, body, _ = request(path, {"Host": "route-check.local"})
                expect(status == 200 and body == expected, f"{path} reaches {expected} fallback")
            status, body, _ = request("/api/analytics/events/batch", {"Host": "route-check.local"})
            expect(status == 404 and json.loads(body) == {"detail": "Not Found"}, "Analytics priority beats synthetic Core fallback")
            status, body, _ = request("/api/analytics/reports/activity", {"Host": "route-check.local"})
            expect(status == 404 and json.loads(body) == {"detail": "Not Found"}, "Query priority beats synthetic Core fallback")
            status, _, headers = request("/api/analytics/events/batch", {"Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,x-forged"}, "OPTIONS")
            expect(status == 404 and not any(k.lower() == "access-control-allow-origin" for k in headers), "preflight has no wildcard CORS")
            expect(not any(k.lower() == "access-control-allow-headers" and v.strip() == "*" for k, v in headers.items()), "preflight has no wildcard allowed headers")

            check = ("import urllib.request,urllib.error; "
                     "exec(\"try:\\n r=urllib.request.urlopen('http://127.0.0.1:8000/ready',timeout=4); s=r.status\\n"
                     "except urllib.error.HTTPError as e:\\n s=e.code\"); "
                     "assert s==503; print('503')")
            for role in ("collector", "processor", "query"):
                result = run(compose + ["exec", "-T", role, "python", "-c", check], env)
                expect(result == "503", f"{role} readiness remains 503 without DB/privacy")

            forwarded = '''import asyncio, os
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
assert os.environ.get("FORWARDED_ALLOW_IPS") == ""
seen = {}
async def app(scope, receive, send): seen.update(scope)
async def go():
    scope = {"type":"http", "client":("172.20.0.10",1234), "scheme":"http", "headers":[(b"x-forwarded-for",b"203.0.113.42"),(b"x-forwarded-proto",b"https")]}
    await ProxyHeadersMiddleware(app, trusted_hosts=os.environ["FORWARDED_ALLOW_IPS"])(scope, None, None)
    assert seen["client"][0] == "172.20.0.10" and seen["scheme"] == "http"
asyncio.run(go())
print("not-trusted")
'''
            result = run(compose + ["exec", "-T", "collector", "python", "-c", forwarded], env)
            expect(result == "not-trusted", "running image middleware ignores spoofed forwarded headers")
        finally:
            if attempted:
                try:
                    run(compose + ["down", "--remove-orphans"], env, timeout=60)
                    remaining = run(["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}"])
                    if remaining:
                        raise RuntimeError("project containers remain")
                    for network in (NETWORK, EDGE_NETWORK):
                        networks = run(["docker", "network", "ls", "--format", "{{.Name}}", "--filter", f"name=^{network}$"])
                        if networks:
                            raise RuntimeError("project network remains")
                    print("PASS disposable project containers removed", flush=True)
                    print("PASS disposable project network removed", flush=True)
                except Exception as exc:
                    cleanup_error = exc
                    print(f"CLEANUP FAILED for {PROJECT}; inspect only this project", flush=True)
            if cleanup_error:
                raise cleanup_error


if __name__ == "__main__":
    main()
