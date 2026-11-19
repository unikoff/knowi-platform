"""Bounded, read-only resource snapshot of the designated synthetic PostgreSQL.

No Docker metadata, database contents, credentials, or paths are emitted. Docker
commands are fixed and the inspected container ID is pinned for every later read.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
import re
import subprocess
import sys


TARGET = "knowi-analytics-01a1df75-analytics-postgres-1"
PROJECT = "knowi-analytics-01a1df75"
SERVICE = "analytics-postgres"
VOLUME = PROJECT + "_analytics_postgres_data"
DATA = "/var/lib/postgresql/data"
LOCAL_ENDPOINTS = {"npipe:////./pipe/dockerDesktopLinuxEngine", "npipe:////./pipe/docker_engine", "unix:///var/run/docker.sock"}


class Refused(Exception):
    def __init__(self, code):
        self.code = code


def command(args, env):
    try:
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise Refused("command_unavailable") from None
    if result.returncode != 0 or len(result.stdout) > 1048576:
        raise Refused("command_failed")
    return result.stdout.strip()


def parse_json(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        raise Refused("invalid_metadata") from None


def local_context(env):
    # Prevent environment overrides from bypassing the inspected Docker context.
    if env.get("DOCKER_HOST") or env.get("DOCKER_CONTEXT") or env.get("DOCKER_CONFIG"):
        raise Refused("endpoint_unverified")
    context = command(["docker", "context", "show"], env)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", context):
        raise Refused("endpoint_unverified")
    context_info = parse_json(command(["docker", "context", "inspect", context], env))
    try:
        host = context_info[0]["Endpoints"]["docker"]["Host"]
    except (IndexError, KeyError, TypeError):
        raise Refused("endpoint_unverified") from None
    if host not in LOCAL_ENDPOINTS:
        raise Refused("endpoint_unverified")
    return context


def verify_container(info):
    try:
        obj = info[0]
        labels = obj["Config"]["Labels"]
        bindings = obj["HostConfig"]["PortBindings"]
        exposed = obj["NetworkSettings"]["Ports"]
        mounts = [m for m in obj["Mounts"] if m.get("Destination") == DATA]
        limits = obj["HostConfig"]
        identity_ok = (
            obj["Name"] == "/" + TARGET and obj["State"]["Running"] is True
            and labels.get("com.docker.compose.project") == PROJECT
            and labels.get("com.docker.compose.service") == SERVICE
            and labels.get("knowi.analytics.synthetic") == "true"
            and bindings == {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "57392"}]}
            and exposed == {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "57392"}]}
            and len(obj["Mounts"]) == 1 and len(mounts) == 1
            and mounts[0].get("Type") == "volume" and mounts[0].get("Name") == VOLUME
            and re.fullmatch(r"[0-9a-f]{64}", obj["Id"]) is not None
            and isinstance(limits.get("Memory"), int) and limits["Memory"] > 0
            and isinstance(limits.get("NanoCpus"), int) and limits["NanoCpus"] > 0
        )
        if not identity_ok:
            raise Refused("target_identity")
        return obj["Id"], limits["Memory"], limits["NanoCpus"]
    except (IndexError, KeyError, TypeError, AttributeError):
        raise Refused("target_identity") from None


def verify_volume(info):
    try:
        obj = info[0]
        labels = obj["Labels"]
        if (obj["Name"] != VOLUME or obj["Driver"] != "local"
                or labels.get("com.docker.compose.project") != PROJECT
                or labels.get("com.docker.compose.volume") != "analytics_postgres_data"):
            raise Refused("volume_identity")
    except (IndexError, KeyError, TypeError, AttributeError):
        raise Refused("volume_identity") from None


def parse_size(text):
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(B|kB|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\s*", text)
    if not match:
        raise Refused("invalid_measurement")
    factors = {"B": 1, "kB": 1000, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
               "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    try:
        return int(Decimal(match.group(1)) * factors[match.group(2)])
    except (ValueError, InvalidOperation):
        raise Refused("invalid_measurement") from None


def parse_stats(raw):
    try:
        cpu, memory = raw.split("|", 1)
        if not re.fullmatch(r"\d+(?:\.\d+)?%", cpu.strip()):
            raise ValueError
        cpu_value = float(cpu.strip()[:-1])
        if cpu_value > 100000:
            raise ValueError
        used, limit = memory.split("/", 1)
        return cpu_value, parse_size(used), parse_size(limit)
    except (ValueError, TypeError):
        raise Refused("invalid_measurement") from None


def parse_df(raw):
    lines = raw.splitlines()
    if len(lines) != 2:
        raise Refused("invalid_measurement")
    parts = lines[1].split()
    if len(parts) != 6 or not all(re.fullmatch(r"\d+", value) for value in parts[1:4]):
        raise Refused("invalid_measurement")
    total, used, available = (int(value) * 1024 for value in parts[1:4])
    if total <= 0 or used > total or available > total:
        raise Refused("invalid_measurement")
    return total, used, available


def parse_du(raw):
    lines = raw.splitlines()
    if len(lines) != 1:
        raise Refused("invalid_measurement")
    fields = lines[0].split("\t", 1)
    if len(fields) != 2 or not re.fullmatch(r"\d+", fields[0]) or fields[1] != DATA + "/pg_wal":
        raise Refused("invalid_measurement")
    return int(fields[0]) * 1024


def snapshot(env=None):
    env = dict(os.environ if env is None else env)
    context = local_context(env)
    docker = ["docker", "--context", context]
    obj = parse_json(command(docker + ["inspect", "--type", "container", TARGET], env))
    container_id, memory_cap, nano_cpus = verify_container(obj)
    volume = parse_json(command(docker + ["volume", "inspect", VOLUME], env))
    verify_volume(volume)
    cpu, mem_used, stats_limit = parse_stats(command(
        docker + ["stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}", container_id], env))
    total, used, free = parse_df(command(
        docker + ["exec", "--user", "postgres", container_id, "df", "-Pk", DATA], env))
    wal = parse_du(command(
        docker + ["exec", "--user", "postgres", container_id, "du", "-sk", DATA + "/pg_wal"], env))
    return {
        "status": "ok", "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target_identity": "verified", "volume_identity": "verified", "docker_endpoint": "local",
        "cpu_percent": cpu, "cpu_limit_cores": nano_cpus / 1_000_000_000,
        "memory_used_bytes_approx": mem_used, "memory_limit_bytes": memory_cap,
        "stats_memory_limit_bytes_approx": stats_limit,
        "filesystem_total_bytes": total, "filesystem_used_bytes": used,
        "filesystem_available_bytes": free, "wal_disk_usage_bytes": wal,
    }


def main():
    try:
        result = snapshot()
    except Refused as error:
        print(json.dumps({"status": "refused", "reason": error.code}))
        return 1
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
