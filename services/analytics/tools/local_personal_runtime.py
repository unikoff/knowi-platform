"""Foreground, four-role loopback runtime for one protected local-live instance.

Default is an inert plan. It never creates a DB, journal, keys or Core process.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
from contextlib import nullcontext
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

SERVICE = Path(__file__).resolve().parents[1]
if str(SERVICE) not in sys.path:
    sys.path.insert(0, str(SERVICE))
ROLES = {"collector": 18042, "query": 18043, "processor": 18044, "privacy": 18045}
PROJECTION_REVISIONS = ("a011", "a012", "a013", "a014")


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value or UUID(value).int == 0:
        raise ValueError("invalid local instance")
    return value


def _identity(instance_id):
    from pymentor_analytics.adapters.privacy_authority import local_server
    instance_id = _uuid(instance_id)
    folder = local_server._instance_dir(instance_id)
    owner = local_server._owner(folder, instance_id)
    return local_server, folder, owner


def _write_new(local_server, folder, name, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    local_server._write_new(local_server._file(folder, name, required=False), raw)


def _process_start_identity(pid):
    if os.name != "nt" or type(pid) is not int or pid < 1:
        raise ValueError("runtime process identity unavailable")
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle: raise ValueError("runtime process identity unavailable")
    try:
        created, exited, user, system = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(user), ctypes.byref(system)):
            raise ValueError("runtime process identity unavailable")
        exit_code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != 259:
            raise ValueError("runtime process has exited")
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def _marker(local_server, folder, instance_id):
    value = json.loads(local_server._read(folder, "runtime.pid.json", 4096), object_pairs_hook=local_server._unique)
    if (not isinstance(value, dict) or set(value) != {"version", "instance_id", "pid", "started", "nonce"}
            or value["version"] != 1 or value["instance_id"] != instance_id or type(value["pid"]) is not int
            or type(value["started"]) is not int or value["pid"] < 1 or value["started"] < 1
            or not isinstance(value["nonce"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["nonce"])
            or _process_start_identity(value["pid"]) != value["started"]):
        raise ValueError("runtime identity unavailable")
    return value


def _verify_listeners(pid):
    executable = Path(os.environ["SystemRoot"]) / "System32/netstat.exe"
    result = subprocess.run([str(executable), "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=5, check=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if len(result.stdout) > 2 * 1024 * 1024: raise ValueError("listener inventory unavailable")
    for port in ROLES.values():
        matches = []
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 5 and fields[0] == "TCP" and fields[1] == f"127.0.0.1:{port}" and fields[-2] == "LISTENING":
                matches.append(int(fields[-1]))
        if matches != [pid]: raise ValueError("runtime listener identity unavailable")


def check_ready(instance_id):
    import httpx
    from pymentor_analytics.adapters.auth.config import load_manifest
    local, folder, _, configs = read_settings(instance_id)
    marker = _marker(local, folder, instance_id)
    _verify_listeners(marker["pid"])
    fingerprint = load_manifest(configs["collector"].trust_manifest_json)[2]
    with httpx.Client(timeout=2, trust_env=False) as client:
        for port in ROLES.values():
            response = client.get(f"http://127.0.0.1:{port}/ready")
            if (response.status_code != 200 or response.json() != {"status": "ready"}
                    or response.headers.get("X-Analytics-Trust-Fingerprint") != fingerprint):
                raise ValueError("runtime readiness unavailable")
    # Prevent a process switch while the independent readiness calls execute.
    if _marker(local, folder, instance_id) != marker: raise ValueError("runtime identity changed")
    _verify_listeners(marker["pid"])
    return {"status": "ready", "instance_id": instance_id, "roles": list(ROLES),
            "source_and_backfill": "verified_by_collector_and_query_readiness"}


def read_settings(instance_id, *, require_prepared=True, projection_revision="a011"):
    from pymentor_analytics.app.config import Settings, local_deletion_config, activity_projection_config, catalog_config, core_transfer_config
    from pymentor_analytics.adapters.privacy_authority.client import AuthorityConfig
    from pymentor_analytics.adapters.auth.config import load_manifest, validate_authority_key_separation
    from pymentor_analytics.adapters.core_privacy.relay import RelayConfig
    from cryptography.hazmat.primitives import serialization
    local_server, folder, owner = _identity(instance_id)
    if require_prepared:
        import bootstrap_runtime
        prepared = json.loads(local_server._read(folder, "prepared.json", 4096), object_pairs_hook=local_server._unique)
        if (not isinstance(prepared, dict) or set(prepared) != {"version", "instance_id", "database", "schema_revision", "history_sha256", "backfill", "do_not_drop"}
                or prepared["version"] != 1 or prepared["instance_id"] != instance_id
                or prepared["database"] != "analytics_live_" + UUID(instance_id).hex
                or prepared["schema_revision"] not in PROJECTION_REVISIONS
                or prepared["do_not_drop"] is not True):
            raise ValueError("persistent preparation is incomplete")
        projection_revision = prepared["schema_revision"]
        history = bootstrap_runtime.schema_history(projection_revision)
        if history[0] != projection_revision or prepared["history_sha256"] != history[2]:
            raise ValueError("persistent schema history differs from preparation")
    if projection_revision not in PROJECTION_REVISIONS:
        raise ValueError("invalid local projection revision")
    course_projection = projection_revision != "a011"
    boundary_credit = projection_revision in {"a013", "a014"}
    raw = json.loads(local_server._read(folder, "role-settings.json", 128 * 1024), object_pairs_hook=local_server._unique)
    if (not isinstance(raw, dict) or set(raw) != {"version", "instance_id", "roles"} or raw["version"] != 1
            or raw["instance_id"] != instance_id or not isinstance(raw["roles"], dict) or set(raw["roles"]) != set(ROLES)):
        raise ValueError("invalid role configuration")
    dbname = "analytics_live_" + UUID(instance_id).hex
    configs = {}
    for role in ROLES:
        cfg = Settings.model_validate(raw["roles"][role])
        parsed = urlsplit(cfg.database_url.get_secret_value())
        if (cfg.real_collection is not True or cfg.issuer_mode != "local" or parsed.hostname != "127.0.0.1"
                or parsed.port != 57392 or unquote(parsed.path.lstrip("/")) != dbname
                or unquote(parsed.username or "") != dbname + "_" + role
                or cfg.pool_size != 1 or cfg.pool_overflow != 0):
            raise ValueError("invalid role isolation")
        source = local_deletion_config(cfg, role)
        if source.instance_id != UUID(owner["core_instance_id"]) or cfg.core_transfer_enabled or cfg.core_transfer_proof_enabled:
            raise ValueError("invalid local source identity")
        activity_projection_config(cfg, role)
        catalog_config(cfg, role)
        core_transfer_config(cfg, role)
        configs[role] = cfg
    if (not configs["collector"].catalog_enabled or not configs["processor"].activity_day_worker_enabled
            or not configs["processor"].processor_bucket_worker_enabled or configs["processor"].processor_generic_worker_enabled
            or not configs["query"].activity_day_query_enabled
            or configs["processor"].activity_course_worker_enabled != course_projection
            or configs["query"].activity_course_query_enabled != course_projection
            or configs["processor"].activity_boundary_credit_enabled != boundary_credit
            or configs["processor"].activity_projection_run_id != UUID(owner["dataset_run_id"])
            or configs["query"].activity_projection_run_id != UUID(owner["dataset_run_id"])):
        raise ValueError("invalid local projection configuration")
    for key in ("trust_manifest_json", "privacy_authority_json", "core_deletion_source_json"):
        values = [getattr(value, key).get_secret_value() for value in configs.values()]
        if len(set(values)) != 1:
            raise ValueError("inconsistent shared local trust")
    sample = configs["collector"]
    authority = AuthorityConfig.from_json(sample.privacy_authority_json.get_secret_value())
    manifest = json.loads(sample.trust_manifest_json.get_secret_value(), object_pairs_hook=local_server._unique)
    load_manifest(sample.trust_manifest_json)
    validate_authority_key_separation(sample, authority.public_keys)
    relay = RelayConfig.from_json(configs["privacy"].core_deletion_relay_json.get_secret_value())
    def public(pem):
        return serialization.load_pem_public_key(pem.encode("ascii")).public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    grant_key = public(manifest["grant"]["keys"]["g1"])
    producer_key = public(manifest["producer"]["keys"]["p1"])
    authority_key = public(authority.public_keys["a1"])
    relay_signer = relay.signing_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    command_key = relay.command_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    reader_secret = base64.urlsafe_b64decode(authority.reader_secret.get_secret_value() + "=")
    if (authority.url != "http://127.0.0.1:18046" or authority.authority_id != owner["authority_id"]
            or authority.issuer != owner["authority_id"] or set(authority.public_keys) != {"a1"}
            or manifest["grant"]["issuer"] != "local-grants:" + instance_id
            or set(manifest["grant"]["keys"]) != {"g1"}
            or manifest["producer"]["issuer"] != "local-producer:" + instance_id
            or set(manifest["producer"]["keys"]) != {"p1"}
            or manifest["producer"]["allowlist"] != ["core-local"]
            or relay.producer != "core-local" or relay.issuer != "local-producer:" + instance_id or relay.kid != "p1"
            or len({grant_key, producer_key, authority_key, command_key}) != 4 or relay_signer != producer_key
            or source.secret == reader_secret
            or configs["collector"].catalog_namespace != "core-local:" + owner["core_instance_id"]):
        raise ValueError("invalid local trust binding")
    return local_server, folder, owner, configs


def enable_core_transfer(owner, configs):
    """Build an opt-in overlay after the persisted local settings passed validation."""
    from pymentor_analytics.app.config import core_transfer_config, local_deletion_config

    namespace = "core-local:" + _uuid(owner["core_instance_id"])
    collector = configs["collector"]
    if core_transfer_config(collector, "collector") is not None:
        raise ValueError("unexpected persisted transfer configuration")
    mapping = json.loads(collector.core_transfer_namespaces_json.get_secret_value())
    if mapping != {"core-local": namespace} or collector.catalog_namespace != namespace:
        raise ValueError("invalid local transfer namespace")
    before = {role: local_deletion_config(cfg, role) for role, cfg in configs.items()}
    result = dict(configs)
    for role in ("collector", "processor", "query"):
        updates = {"core_transfer_enabled": True, "core_transfer_proof_enabled": True}
        if role == "collector":
            updates["core_transfer_status_database_url"] = collector.core_deletion_status_database_url
        elif role == "query":
            updates["core_transfer_namespace"] = namespace
        result[role] = configs[role].model_copy(update=updates)
        core_transfer_config(result[role], role)
        if local_deletion_config(result[role], role) != before[role]:
            raise ValueError("local deletion source changed")
    if result["privacy"] is not configs["privacy"]:
        raise ValueError("local privacy configuration changed")
    return result


async def _serve_all(instance_id, local_server, folder, configs):
    import uvicorn
    from pymentor_analytics.app.factory import create_app
    # Uvicorn tracebacks can contain driver URLs or request details; expose
    # health through the role endpoints and keep process output secret-free.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).disabled = True
    marker = {"version": 1, "instance_id": instance_id, "pid": os.getpid(),
              "started": _process_start_identity(os.getpid()), "nonce": uuid4().hex}
    _write_new(local_server, folder, "runtime.pid.json", marker)
    servers = []
    tasks = []
    graceful = False
    try:
        for role, port in ROLES.items():
            server = uvicorn.Server(uvicorn.Config(create_app(role, configs[role]), host="127.0.0.1", port=port,
                workers=1, access_log=False, log_config=None, timeout_graceful_shutdown=10))
            server.capture_signals = lambda: nullcontext()
            servers.append(server)
            tasks.append(asyncio.create_task(server.serve(), name="local-" + role))
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for item in (signal.SIGINT, signal.SIGTERM):
            try: signal.signal(item, lambda *_: loop.call_soon_threadsafe(stop_event.set))
            except (OSError, ValueError): pass
        async def watch_stop():
            while not stop_event.is_set():
                path = local_server._file(folder, "runtime.stop.json", required=False)
                if path.exists():
                    request = json.loads(local_server._read(folder, "runtime.stop.json", 4096), object_pairs_hook=local_server._unique)
                    if request != {"version": 1, "instance_id": instance_id, "nonce": marker["nonce"]}:
                        raise ValueError("stop request identity mismatch")
                    stop_event.set()
                    return
                await asyncio.sleep(1)
        watch = asyncio.create_task(watch_stop(), name="local-stop-watch")
        done, _ = await asyncio.wait([*tasks, watch], return_when=asyncio.FIRST_COMPLETED)
        graceful = watch in done and stop_event.is_set() and watch.exception() is None
        for server in servers: server.should_exit = True
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=20)
        if not watch.done():
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
        if not graceful:
            raise RuntimeError("local runtime stopped unexpectedly")
    finally:
        for server in servers: server.should_exit = True
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if graceful:
            path = local_server._file(folder, "runtime.pid.json")
            if json.loads(local_server._read(folder, "runtime.pid.json", 4096), object_pairs_hook=local_server._unique) != marker:
                raise ValueError("runtime marker changed")
            request_path = local_server._file(folder, "runtime.stop.json", required=False)
            if request_path.exists(): request_path.unlink()
            path.unlink()


def request_stop(instance_id):
    local_server, folder, _ = _identity(instance_id)
    marker = _marker(local_server, folder, instance_id)
    _write_new(local_server, folder, "runtime.stop.json", {"version": 1, "instance_id": instance_id, "nonce": marker["nonce"]})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id")
    parser.add_argument("--core-transfer", action="store_true", help="enable signed Core transfer for this serve invocation")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--serve", action="store_true")
    action.add_argument("--status", action="store_true")
    action.add_argument("--stop", action="store_true")
    args = parser.parse_args(argv)
    if args.core_transfer and not args.serve:
        parser.error("--core-transfer requires --serve")
    if not (args.serve or args.status or args.stop):
        print(json.dumps({"status": "UNRUN", "action": "plan", "ports": ROLES}))
        return 0
    try:
        instance = _uuid(args.instance_id)
        if args.stop:
            request_stop(instance)
            print(json.dumps({"status": "stop_requested", "instance_id": instance}))
        elif args.status:
            local_server, folder, _ = _identity(instance)
            marker = local_server._file(folder, "runtime.pid.json", required=False)
            if marker.exists(): print(json.dumps(check_ready(instance)))
            else: print(json.dumps({"status": "not_running_or_unverified", "instance_id": instance}))
        else:
            local_server, folder, owner, configs = read_settings(instance)
            if args.core_transfer:
                configs = enable_core_transfer(owner, configs)
            asyncio.run(_serve_all(instance, local_server, folder, configs))
        return 0
    except Exception:
        print("Local Analytics runtime unavailable; inspect owned private state.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
