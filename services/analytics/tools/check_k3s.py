"""Read-only local capacity admission; never creates, selects or deletes a cluster."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CLUSTER = "knowi-analytics-01a1df75"
MIN_FREE = 5 * 1024**3 // 2


def available_memory():
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
                        ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                        ("avail_extended", ctypes.c_ulonglong)]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("cannot measure host memory")
        return status.avail_phys
    values = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line)
    return int(values["MemAvailable"].split()[0]) * 1024


def admission(free):
    return free >= MIN_FREE


def local_docker_env():
    if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT"):
        raise ValueError("explicit Docker host/context is not permitted")
    result = subprocess.run(["docker", "context", "inspect"], capture_output=True, text=True, timeout=10, check=True)
    endpoint = json.loads(result.stdout)[0]["Endpoints"]["docker"]["Host"].lower()
    allowed = {"npipe:////./pipe/docker_engine", "npipe:////./pipe/dockerdesktoplinuxengine", "unix:///var/run/docker.sock"}
    if endpoint not in allowed:
        raise ValueError("Docker endpoint is not an allowed local socket")


def main():
    free = available_memory()
    print(f"Available host RAM: {free // (1024 * 1024)} MiB; minimum: {MIN_FREE // (1024 * 1024)} MiB")
    if not admission(free):
        print("BLOCKED: insufficient host memory; no cluster operation attempted.")
        return 2
    try:
        local_docker_env()
    except Exception:
        print("BLOCKED: local Docker context cannot be verified.")
        return 2
    print("Capacity threshold passed; this does not create or verify a cluster.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
