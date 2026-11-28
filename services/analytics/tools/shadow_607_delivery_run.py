"""Start and stop one delivery rehearsal in its assigned local port slot."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import UUID

import httpx
from shadow_607_delivery_target import ARMED, COMMITTED, DISCONNECTED, WORKTREE, checked_plan, core_recipe, target, wire

PORTS = tuple(range(18041, 18047))


def free_ports():
    for port in PORTS:
        with socket.socket() as sock:
            sock.settimeout(.25)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"assigned port {port} is still occupied")


def wait_ready(child, url, seconds=20, status=200):
    deadline = time.monotonic() + seconds
    with httpx.Client(timeout=1.5, trust_env=False) as probe:
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError("delivery helper exited before its listener became ready")
            try:
                if probe.get(url).status_code == status:
                    return
            except httpx.TransportError:
                pass
            time.sleep(.15)
    raise RuntimeError(f"readiness deadline at {url}")


def checked_output(path):
    directory = (WORKTREE / "docs/analytics/evidence-598/607/delivery").resolve()
    result = path.resolve()
    if not result.is_relative_to(directory) or result.exists():
        raise ValueError("use a new delivery evidence filename")
    return result


def runner(instance, result):
    _, local, folder, _, _, _, plan = checked_plan(instance)
    if any(local._file(folder, name, required=False).exists() for name in (ARMED, COMMITTED, DISCONNECTED, "runtime.pid.json")):
        raise ValueError("delivery runtime already started; reconcile before starting a new run")
    free_ports()
    create_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = {**target.base_env(), "PYTHONPATH": str(target.ANALYTICS), "PYTHONIOENCODING": "utf-8"}
    processes = {}
    roles = "authority", "core", "analytics"
    with ExitStack() as logs:
        def launch(role, command, directory, environment):
            log = logs.enter_context((folder / f"delivery-run-{role}.log").open("xb"))
            process = subprocess.Popen(command, cwd=directory, env={**environment, "PYTHONIOENCODING": "utf-8"},
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=create_flags)
            processes[role] = process
            print(wire({"component": role, "pid": process.pid}).decode("ascii"), flush=True)
            return process

        try:
            authority = launch("authority", [sys.executable, "-B", "-m",
                "pymentor_analytics.adapters.privacy_authority.local_server", "serve", "--instance-id", str(instance)],
                target.ANALYTICS, env)
            core_command, core_settings = core_recipe(instance, local, folder, plan)
            core = launch("core", core_command, target.CORE, core_settings)
            analytics = launch("analytics", [sys.executable, "-B", "tools/shadow_607_delivery_target.py",
                "serve-analytics", "--instance-id", str(instance)], target.ANALYTICS, env)
            wait_ready(authority, "http://127.0.0.1:18046/", status=404)
            wait_ready(core, "http://127.0.0.1:18041/health/ready")
            wait_ready(analytics, "http://127.0.0.1:18042/ready")
            wait_ready(analytics, "http://127.0.0.1:18043/ready")
            with (folder / "delivery-run-catalog.log").open("xb") as log:
                subprocess.run([str(target.CORE_PYTHON), "-B", "-m", "pymentor_core.app.analytics_catalog", "--course-id", "backend"],
                               cwd=target.CORE, env=core_settings, stdin=subprocess.DEVNULL, stdout=log,
                               stderr=subprocess.STDOUT, timeout=30, check=True, creationflags=create_flags)
            subprocess.run([sys.executable, "-B", "tools/shadow_607_delivery.py", "--instance-id", str(instance),
                            "--output", str(result)], cwd=target.ANALYTICS, env=env, check=True, timeout=100,
                           creationflags=create_flags)
        finally:
            analytics = processes.get("analytics")
            if analytics is not None and analytics.poll() is None:
                try:
                    target.runtime.request_stop(str(instance))
                    analytics.wait(timeout=22)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
            for role in reversed(roles):
                process = processes.get(role)
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            free_ports()
            print("607 delivery owned listener slot released", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", required=True, type=UUID)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    runner(args.instance_id, checked_output(args.output))


if __name__ == "__main__":
    main()
