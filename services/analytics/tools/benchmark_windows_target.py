"""Read-only target and instrumentation for the CORP-607 paired load harness."""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import psycopg
from psycopg.conninfo import conninfo_to_dict

import benchmark_activity as base
from pymentor_analytics.adapters.privacy_authority import local_server

MIB = 1024 * 1024
ANALYTICS_ROLES = ("collector", "query", "processor", "privacy")
LISTENERS = ("core", *ANALYTICS_ROLES, "authority")
LOOPBACK = "127.0.0.1"
LOCK_PREFIX = "%m [%p] db=%d "
WORKER_COUNTERS = ("keys", "coverage_unknown", "mismatch_before", "mismatch_after", "repairs", "failures")
TABLE = "analytics.student_activity_windows"
CONTROL = "analytics.student_activity_window_control"
CONNECTION_COUNT_SCOPE = "cluster-wide database-connected backends"
LOCK_LINE = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+ UTC) \[(\d+)\] db=(\w+) LOG:\s+"
    r"process \d+ (acquired|still waiting for) .+? after ([\d.]+) ms"
)
DOCKER_METRICS = r"""
awk '$1 == "usage_usec" { usage = $2 }
     END { if (usage !~ /^[0-9]+$/ || usage <= 0) exit 1; print usage }' /sys/fs/cgroup/cpu.stat || exit 20
awk '
BEGIN {
    for (i = 1; i < ARGC; i++) {
        path = ARGV[i]
        rss = ""
        while ((reading = getline < path) > 0) {
            if ($1 == "VmRSS:") {
                if ($2 !~ /^[0-9]+$/ || $3 != "kB") exit 22
                rss = $2
            }
        }
        close(path)
        if (reading < 0) {
            # Only a PID directory that disappeared can excuse a failed read.
            dir = path
            sub(/\/status$/, "", dir)
            if (dir !~ /^[[:alnum:]_./-]+$/ || system("test -d " dir) == 0) exit 21
        } else if (rss != "") {
            total += rss
            samples++
        }
    }
    if (!samples || total <= 0) exit 22
    printf "%.0f\n", total
}' /proc/[0-9]*/status
"""


def private_manifest(path, pair):
    folder = local_server._instance_dir(str(pair))
    pattern = r"windows-benchmark(?:-(?:v2_only|shadow)(?:-[1-9][0-9]{0,3})?)?\.json"
    if path.absolute().parent != folder or not re.fullmatch(pattern, path.name):
        raise ValueError("expected pair owner folder")
    manifest = json.loads(local_server._read(folder, path.name, 131_072), object_pairs_hook=local_server._unique)
    if manifest["owner"] != "corp598-607-benchmark" or UUID(manifest["pair"]) != pair:
        raise ValueError("bad bench owner record")
    if len(manifest["courses"]) != 2 or len({item["course_id"] for item in manifest["courses"]}) != 2:
        raise ValueError("two distinct courses required")
    return manifest


def validate_dsn(dsn, name, owner, port):
    # psycopg accepts libpq service files and multi-host DSNs. Allow only this
    # exact password-authenticated loopback address and owned DB/role.
    fields = conninfo_to_dict(dsn)
    if set(fields) != {"user", "password", "dbname", "host", "port"} or not fields["password"]:
        raise ValueError("direct owner DSN required")
    if (fields["host"], fields["port"], fields["dbname"], fields["user"]) != (LOOPBACK, str(port), name, owner):
        raise ValueError("owner DSN does not match approved loopback target")


def process_info(pid):
    from ctypes import wintypes

    class ProcessMemory(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD),
                    ("peak", ctypes.c_size_t), ("working", ctypes.c_size_t),
                    ("peak_paged", ctypes.c_size_t), ("paged", ctypes.c_size_t),
                    ("peak_nonpaged", ctypes.c_size_t), ("nonpaged", ctypes.c_size_t),
                    ("pagefile", ctypes.c_size_t), ("peak_pagefile", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemory), wintypes.DWORD]
    handle = kernel.OpenProcess(0x0410, False, pid)
    if not handle:
        raise base.StopRun("owned process cannot be sampled")
    try:
        timestamps = [wintypes.FILETIME() for _ in range(4)]
        exit_code = wintypes.DWORD()
        sample = ProcessMemory()
        sample.cb = ctypes.sizeof(sample)
        if (not kernel.GetProcessTimes(handle, *(ctypes.byref(part) for part in timestamps))
                or not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != 259
                or not psapi.GetProcessMemoryInfo(handle, ctypes.byref(sample), sample.cb)):
            raise base.StopRun("owned process cannot be sampled")
        created, _, user, system = [(part.dwHighDateTime << 32) | part.dwLowDateTime for part in timestamps]
        return created, (user + system) / 10_000_000, sample.working
    finally:
        kernel.CloseHandle(handle)


def command(args, limit=2 * MIB, *, encoding="utf-8"):
    # Docker target selection must be local and must not inherit remote host/context overrides.
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in {"DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"}}
    result = subprocess.run(args, capture_output=True, timeout=5, check=True, env=env,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if len(result.stdout) + len(result.stderr) > limit:
        raise base.StopRun("instrumentation output exceeded bound")
    return result.stdout.decode(encoding), result.stderr.decode(encoding)


@dataclass(frozen=True)
class Target:
    shadow: bool
    database: str
    core_database: str
    db: dict
    core_db: dict
    ports: dict
    listener_pids: dict
    processes: dict
    docker: dict
    fingerprint: str
    versions: dict
    issuer: str
    kid: str
    key: Ed25519PrivateKey
    learners: dict
    users: tuple
    t0: datetime
    lock_probe: dict | None
    analytics_pool_by_role: dict

    @classmethod
    def from_record(cls, manifest, mode, plan):
        entry = manifest["targets"][mode]
        instance = str(UUID(entry["instance"]))
        hex_run = UUID(instance).hex
        if UUID(instance).version != 4 or instance == str(plan.run_id):
            raise ValueError("dedicated UUIDv4 target required")
        folder = local_server._instance_dir(instance)
        recorded = json.loads(local_server._read(folder, "corp607-pending.json", 4_096))
        database, core_database = f"analytics_live_{hex_run}", f"core_607_{hex_run}"
        if (recorded["owner"], recorded["instance_id"], recorded["analytics"], recorded["core"]) != (
            "607", instance, database, core_database
        ):
            raise ValueError("target owner record mismatch")
        for kind, name, owner in (("analytics", database, database + "_migrate"),
                                  ("core", core_database, core_database + "_owner")):
            row = entry[kind]
            if recorded[kind + "_owner"] != owner or not re.fullmatch(r"[0-9a-f]{64}", recorded["sentinel"][kind]):
                raise ValueError("target sentinel or owner record mismatch")
            validate_dsn(row["dsn"], name, owner, manifest["postgres"]["port"])
            row.update(name=name, owner=owner, sentinel=recorded["sentinel"][kind])
        ports, listeners, processes = entry["ports"], entry["listener_pids"], entry["processes"]
        if set(ports) != set(LISTENERS) or set(listeners) != set(ports):
            raise ValueError("runtime listener roster mismatch")
        if len(set(ports.values())) != len(ports) or any(type(port) is not int or not 1024 <= port <= 65535
                                                         for port in ports.values()):
            raise ValueError("invalid loopback listener ports")
        if (not set(map(str, listeners.values())) <= set(processes) or
                any(type(start) is not int or start <= 0 or not pid.isdecimal() or int(pid) <= 0
                    for pid, start in processes.items())):
            raise ValueError("process roster missing identities")
        marker = entry["trust_fingerprint"]
        if not re.fullmatch(r"[0-9a-f]{64}", marker):
            raise ValueError("trust manifest fingerprint required")
        key = serialization.load_pem_private_key(local_server._read(folder, "g1.private.pem", 4_096), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("dedicated Ed25519 key required")
        learners = manifest["learners"]
        if set(learners) != {str(user) for user in plan.users} or any(
            set(row) != {"email", "password"} or not row["email"].endswith("@example.test") or not row["password"]
            for row in learners.values()
        ):
            raise ValueError("synthetic learners must exactly equal both profile rosters")
        t0 = datetime.fromisoformat(entry["t0"])
        if t0.utcoffset() != timedelta(0) or not timedelta(0) <= plan.anchor - t0 <= timedelta(hours=1):
            raise ValueError("observation start must cover the modeled schedule")
        versions = manifest["versions"]
        if set(versions) != {"web", "core", "collector", "query", "worker", "privacy", "schema"} or any(
            not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) for value in versions.values()
        ):
            raise ValueError("record accepted builds, not arbitrary text")
        profile = base.profile_load(base.ROOT / "bench/profile-v1.json")
        expected_pool = {"size": profile["pool_per_process"], "max": profile["pool_per_process"] + profile["pool_overflow"],
                         "overflow": profile["pool_overflow"]}
        if entry["analytics_pool_by_role"] != dict.fromkeys(ANALYTICS_ROLES, expected_pool):
            raise ValueError("actual Analytics role pools differ from the frozen profile")
        return cls(mode == "shadow", database, core_database, entry["analytics"], entry["core"], ports,
                   listeners, processes, manifest["postgres"], marker, manifest["versions"],
                   entry["issuer"], "g1", key, learners, plan.users, t0, entry.get("lock_probe"), entry["analytics_pool_by_role"])

    def url(self, role):
        return f"http://{LOOPBACK}:{self.ports[role]}"


def verify_db(connection, info):
    identity = connection.execute("""SELECT current_database(), current_user, pg_get_userbyid(datdba)
        FROM pg_database WHERE datname=current_database()""").fetchone()
    marker = connection.execute("SELECT token FROM public.corp607_sentinel").fetchall()
    if identity != (info["name"], info["owner"], info["owner"]) or marker != [(info["sentinel"],)]:
        raise base.StopRun("target PostgreSQL identity or sentinel changed")


@contextmanager
def guarded_pg(target, *, core=False):
    info = target.core_db if core else target.db
    options = "-c default_transaction_read_only=on -c statement_timeout=3000 -c lock_timeout=1000"
    # These settings override unrelated libpq defaults without accepting runtime DSN target overrides.
    with psycopg.connect(info["dsn"], autocommit=True, connect_timeout=3, sslmode="disable",
                         options=options, application_name="corp598_607_benchmark_observer") as connection:
        verify_db(connection, info)
        yield connection


def table_count(connection, table):
    if table not in {TABLE, CONTROL, "analytics.activity_facts"}:
        raise ValueError("unexpected target table")
    return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def measured_pool(text):
    prefix = "analytics_database_pool_"
    fields = {"size", "max", "overflow"}
    wanted = {prefix + field + suffix for field in fields for suffix in ("", "_known")}
    values = {}
    for line in text.splitlines():
        parts = line.split()
        name = parts[0].split("{", 1)[0] if parts else ""
        if name in wanted:
            if name in values or len(parts) != 2 or not parts[1].isdecimal():
                raise base.StopRun("invalid runtime pool metrics")
            values[name] = int(parts[1])
    if set(values) != wanted or any(values[prefix + field + "_known"] != 1 for field in fields):
        raise base.StopRun("actual runtime pool metrics are unavailable")
    return {field: values[prefix + field] for field in sorted(fields)}


def metrics_overflow(text):
    key = "analytics_database_pool_overflow"
    values = {}
    for line in text.splitlines():
        parts = line.split()
        name = parts[0].split("{", 1)[0] if parts else ""
        if name in {key, key + "_known"}:
            if name in values or len(parts) != 2 or not parts[1].isdecimal():
                return None
            values[name] = int(parts[1])
    return values.get(key) if values.get(key + "_known") == 1 else None


def worker_metrics(text, shadow):
    prefix = "analytics_windows_reconciliation_"
    names = {prefix + "enabled", *(prefix + name + "_total" for name in WORKER_COUNTERS)}
    fields = {}
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] in names:
            if len(parts) != 2 or parts[0] in fields or not parts[1].isdecimal():
                raise base.StopRun("invalid worker metric sample")
            fields[parts[0]] = int(parts[1])
    if fields.get(prefix + "enabled") != int(shadow):
        raise base.StopRun("observed processor worker state differs from variant")
    if not shadow:
        return dict.fromkeys(WORKER_COUNTERS, 0)
    if set(fields) != names:
        raise base.StopRun("shadow worker metric observations unavailable")
    return {name: fields[prefix + name + "_total"] for name in WORKER_COUNTERS}


def bounded_lock_waits(log, *, database, start, end, censor_ms, waiting_at_end):
    """Bound nearest-rank p95 when the right boundary has no unresolved waits."""
    if not 0 < censor_ms <= 100 or end <= start:
        raise ValueError("invalid lock observation window or censoring")
    acquired, still_waiting = [], set()
    for line in log.splitlines():
        match = LOCK_LINE.match(line)
        if not match:
            continue
        when, pid, dbname, event, duration = match.groups()
        stamp = datetime.fromisoformat(when.removesuffix(" UTC")).replace(tzinfo=UTC)
        if dbname != database or not start <= stamp <= end:
            continue
        lock = match.group(0).split(f" {event} ", 1)[1].rsplit(" after ", 1)[0]
        if event == "acquired":
            acquired.append(float(duration))
            still_waiting.discard((pid, lock))
        else:
            still_waiting.add((pid, lock))
    # A newly queued waiter can be absent from the log at the boundary.
    if still_waiting or waiting_at_end != 0:
        return {"lock_p95_upper_ms": None, "lock_waits_reported": len(acquired),
                "lock_waits_unresolved": len(still_waiting), "lock_censor_ms": censor_ms}
    rank = math.ceil(len(acquired) * .95)
    value = sorted(acquired)[rank - 1] if rank else censor_ms
    return {"lock_p95_upper_ms": max(censor_ms, value), "lock_waits_reported": len(acquired),
            "lock_waits_unresolved": 0, "lock_censor_ms": censor_ms}


def index_footprint(first, last):
    business = ("analytics", TABLE.split(".", 1)[1])
    control = ("analytics", CONTROL.split(".", 1)[1])
    if not {business, control} <= first.keys() or first.keys() != last.keys():
        return None
    if any(first[key][0] != last[key][0] for key in first):
        return None
    groups = {
        "user_schemas": first.keys(),
        "analytics": [key for key in first if key[0] == "analytics"],
        business[1]: [business],
        control[1]: [control],
    }
    totals = {}
    for name, keys in groups.items():
        start = sum(first[key][1] for key in keys)
        end = sum(last[key][1] for key in keys)
        totals[name] = {"at_start_bytes": start, "at_end_bytes": end, "delta_bytes": end - start}
    return totals


class Observer:
    def __init__(self, target, connection, monitor):
        self.target, self.connection, self.monitor = target, connection, monitor
        self.samples, self.bounds = [], {}
        self.client = None
        self.docker_context = None

    def docker(self, *args):
        return command(["docker", "--context", self.docker_context, *args])

    def preflight(self):
        self.monitor.check()
        d = self.target.docker
        if d["context"] not in {"desktop-linux", "default"} or not re.fullmatch(r"[0-9a-f]{64}", d["id"]):
            raise base.StopRun("isolated PostgreSQL Docker context or ID expected")
        endpoint, _ = command(["docker", "context", "inspect", d["context"], "--format",
                               "{{.Endpoints.docker.Host}}"])
        if endpoint.strip() not in {"npipe:////./pipe/dockerDesktopLinuxEngine", "npipe:////./pipe/docker_engine"}:
            raise base.StopRun("local Docker Engine endpoint expected")
        self.docker_context = d["context"]
        output, _ = self.docker("inspect", d["id"])
        item, = json.loads(output)
        endpoints = item["NetworkSettings"]["Ports"]["5432/tcp"]
        labels = item["Config"]["Labels"]
        if (item["Id"] != d["id"] or not item["State"]["Running"]
                or labels.get("com.docker.compose.project") != d["project"]
                or labels.get("com.docker.compose.service") != "analytics-postgres"
                or endpoints != [{"HostIp": LOOPBACK, "HostPort": str(d["port"])}]):
            raise base.StopRun("unexpected PostgreSQL container or loopback binding")
        from benchmark_windows_postgres import cluster_connection, verify_relay
        data, record, guarded = cluster_connection(UUID(d["owner"]))
        if (record["project"], record["id"], record["port"]) != (d["project"], d["id"], d["port"]):
            raise base.StopRun("measurement container differs from the dedicated owner record")
        with guarded(data):
            pass
        relay = verify_relay(UUID(d["owner"]))
        if relay["relay_pid"] != d["relay_pid"] or str(d["relay_pid"]) not in self.target.processes:
            raise base.StopRun("IPv6 relay identity is absent from measurement budget")
        self.log_driver = item["HostConfig"]["LogConfig"]["Type"]
        connection = self.connection
        settings = dict(connection.execute("""SELECT name, setting FROM pg_settings WHERE name IN
            ('fsync','synchronous_commit','full_page_writes','max_connections','log_lock_waits','log_line_prefix',
             'log_timezone','log_destination','logging_collector','deadlock_timeout','server_version')""").fetchall())
        if any(settings.get(key) != "on" for key in ("fsync", "synchronous_commit", "full_page_writes")):
            raise base.StopRun("PostgreSQL durability must match the announced budget")
        if settings["max_connections"] != "40":
            raise base.StopRun("PostgreSQL max_connections must match the announced profile")
        revision, = connection.execute("SELECT version_num FROM public.alembic_version").fetchone()
        if not re.fullmatch(r"a0[1-9][5-9]|a0[2-9][0-9]", revision):
            raise base.StopRun("matching expanded PostgreSQL revision required")
        self.settings, self.revision = settings, revision
        if any(table_count(connection, table) for table in [TABLE, CONTROL, "analytics.activity_facts"]):
            raise base.StopRun("fresh owned PostgreSQL required")
        with guarded_pg(self.target, core=True) as core:
            self.core_revision, = core.execute("SELECT version_num FROM public.alembic_version").fetchone()
            refs = {UUID(row[0]) for row in core.execute("SELECT lifecycle_id FROM public.account_lifecycles").fetchall()}
            if refs != set(self.target.users):
                raise base.StopRun("Core lifecycle roster differs from the independent profile")

    def listeners(self):
        executable = Path(os.environ["SystemRoot"]) / "System32/netstat.exe"
        oem_encoding = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        output, _ = command([str(executable), "-ano"], encoding=oem_encoding)
        for role, port in self.target.ports.items():
            owners = []
            for line in output.splitlines():
                fields = line.split()
                if len(fields) == 5 and fields[0] == "TCP" and fields[3] == "LISTENING" and fields[1].endswith(f":{port}"):
                    owners.append((fields[1], int(fields[4])))
            if owners != [(f"{LOOPBACK}:{port}", self.target.listener_pids[role])]:
                raise base.StopRun("owned loopback listener changed")
        alias_owner = [int(fields[4]) for line in output.splitlines() if
                       len(fields := line.split()) == 5 and fields[:2] == ["TCP", "[::1]:57392"]
                       and fields[3] == "LISTENING"]
        if alias_owner != [self.target.docker["relay_pid"]]:
            raise base.StopRun("owned IPv6 PostgreSQL relay listener changed")

    async def check(self, client):
        self.client = client
        self.monitor.check()
        await asyncio.to_thread(self.listeners)
        for pid, expected_start in self.target.processes.items():
            if process_info(int(pid))[0] != expected_start:
                raise base.StopRun("owned runtime PID changed")
        routes = [("core", "/health/ready"), *((role, "/ready") for role in ANALYTICS_ROLES)]
        replies = await asyncio.gather(*(client.get(self.target.url(role) + path) for role, path in routes))
        for (role, _), response in zip(routes, replies, strict=True):
            if response.status_code != 200 or response.json().get("status") != "ready":
                raise base.StopRun("Core, Analytics or Privacy readiness lost")
            if role != "core" and response.headers.get("X-Analytics-Trust-Fingerprint") != self.target.fingerprint:
                raise base.StopRun("Analytics trust identity changed")

    def windows(self, connection):
        rows = connection.execute(
            f"SELECT student_id,activity_date,slot_no,stats,revision FROM {TABLE}"
        ).fetchall()
        return {(subject, day, slot): (sorted((r["tool_id"], r["course_id"], r["lesson_id"], r["active_ms"])
                                               for r in stats), revision)
                for subject, day, slot, stats, revision in rows}

    async def verify_commit(self, key, snapshot, revision):
        with guarded_pg(self.target) as separate:
            row = separate.execute(f"SELECT stats,revision FROM {TABLE} "
                                   "WHERE student_id=%s AND activity_date=%s AND slot_no=%s", key).fetchone()
        if row != ([item.model_dump(mode="json") for item in snapshot.stats], revision):
            raise base.ObservedViolation("separate PostgreSQL read did not observe first committed ACK")

    def v3_queue(self):
        if not self.target.shadow:
            actual = table_count(self.connection, CONTROL)
            if actual:
                raise base.ObservedViolation("baseline unexpectedly enqueued window work")
            return 0
        return self.connection.execute(f"""SELECT count(*) FROM {CONTROL}
            WHERE phase IN ('accumulating','pending_reconciliation') AND student_id=ANY(%s::uuid[])
              AND (activity_date+slot_no*interval '15 minutes') AT TIME ZONE 'Europe/Moscow'
                  > %s
              AND (activity_date+slot_no*interval '15 minutes') AT TIME ZONE 'Europe/Moscow' <= clock_timestamp()""", (list(self.target.users), self.target.t0)).fetchone()[0]

    async def drained(self):
        return self.v3_queue() == 0 and self.connection.execute(
            """SELECT NOT EXISTS(SELECT 1 FROM analytics.activity_projection_buckets
                 WHERE state IN ('dirty','claimed','quarantined'))
               AND NOT EXISTS(SELECT 1 FROM analytics.activity_projection_day_sources
                 WHERE state IN ('dirty','claimed','quarantined'))"""
        ).fetchone()[0]

    def pg_sample(self):
        verify_db(self.connection, self.target.db)
        row = self.connection.execute(f"""SELECT
            clock_timestamp(), pg_database_size(current_database()),
            (pg_current_wal_insert_lsn() - '0/0')::bigint, d.deadlocks, d.stats_reset,
            (SELECT COALESCE(sum(numbackends), 0)::bigint FROM pg_stat_database WHERE datid <> 0),
            (SELECT count(DISTINCT l.pid) FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid
             WHERE NOT l.granted AND a.datid=d.datid),
            (SELECT n_tup_upd FROM pg_stat_user_tables WHERE relid='{TABLE}'::regclass),
            (SELECT count(*) FROM {TABLE})
            FROM pg_stat_database d WHERE datname=current_database()""").fetchone()
        names = ("db_time", "db_bytes", "wal_insert_bytes", "deadlocks", "reset_at", "connections",
                 "waiting_locks", "window_updates", "window_rows")
        return dict(zip(names, row, strict=True))

    def index_sizes(self):
        rows = self.connection.execute("""SELECT n.nspname, c.relname, c.oid, pg_indexes_size(c.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.relkind IN ('r','m') AND n.nspname NOT IN ('pg_catalog','information_schema')
              AND n.nspname !~ '^pg_'""").fetchall()
        return {(schema, name): (oid, size) for schema, name, oid, size in rows}

    def machine(self):
        values = [process_info(int(pid)) for pid in self.target.processes]
        try:
            output, errors = self.docker("exec", self.target.docker["id"], "sh", "-c", DOCKER_METRICS)
            pg_cpu_usec, pg_rss_kib = map(int, output.split())
        except subprocess.CalledProcessError as error:
            detail = {20: "cgroup CPU unavailable", 21: "process status unreadable",
                      22: "no valid RSS sample"}.get(error.returncode, "Docker exec failed")
            raise base.StopRun(f"PostgreSQL CPU/RSS sample failed (exit {error.returncode}: {detail})") from None
        except subprocess.TimeoutExpired:
            raise base.StopRun("PostgreSQL CPU/RSS sample timed out") from None
        except OSError:
            raise base.StopRun("PostgreSQL CPU/RSS Docker exec unavailable") from None
        except ValueError:
            raise base.StopRun("PostgreSQL CPU/RSS sample output invalid") from None
        if errors or pg_cpu_usec <= 0 or pg_rss_kib <= 0:
            raise base.StopRun("PostgreSQL CPU/RSS sample output invalid")
        return dict(cpu_seconds=sum(row[1] for row in values) + pg_cpu_usec / 1_000_000,
                    rss_mib=(sum(row[2] for row in values) + pg_rss_kib * 1024) / MIB)

    async def sample(self, *, include_index_sizes=False):
        observation = self.pg_sample()
        if include_index_sizes:
            observation["index_sizes"] = self.index_sizes()
        observation.update(await asyncio.to_thread(self.machine))
        observation["monotonic"] = time.monotonic()
        observation["v3_worker_backlog"] = self.v3_queue()
        response = await asyncio.gather(*(self.client.get(self.target.url(role) + "/metrics") for role in ANALYTICS_ROLES))
        if any(reply.status_code != 200 for reply in response):
            raise base.StopRun("Analytics pool metrics unavailable")
        pools = {role: measured_pool(reply.text) for role, reply in zip(ANALYTICS_ROLES, response, strict=True)}
        if pools != self.target.analytics_pool_by_role:
            raise base.ObservedViolation("running role pools no longer match the captured profile")
        observation["pool_overflow"] = max(value["overflow"] for value in pools.values())
        processor = response[ANALYTICS_ROLES.index("processor")]
        if processor.status_code != 200:
            raise base.StopRun("processor worker metrics unavailable")
        observation["window_worker"] = worker_metrics(processor.text, self.target.shadow)
        self.samples.append(observation)
        return observation

    async def boundary(self, name):
        self.bounds[name] = await self.sample(include_index_sizes=True)

    def lock_metrics(self, start, end, waiting_at_end):
        settings = self.settings
        if (settings.get("log_lock_waits") != "on" or settings.get("log_line_prefix") != LOCK_PREFIX
                or settings.get("log_timezone") != "UTC" or settings.get("log_destination") != "stderr"
                or settings.get("logging_collector") != "off" or self.log_driver not in {"json-file", "local"}
                or not self.target.lock_probe):
            return {"lock_p95_upper_ms": None, "lock_wait_source": "unavailable"}
        try:
            probe_at = datetime.fromisoformat(self.target.lock_probe["at"])
            probe_pid = str(self.target.lock_probe["pid"])
            if not (start - timedelta(hours=4) <= probe_at < start and probe_pid.isdecimal()):
                raise ValueError("missing correlated lock probe")
            probe_start, probe_end = probe_at - timedelta(seconds=5), probe_at + timedelta(seconds=5)
            probe_stdout, probe_stderr = self.docker("logs", "--since", probe_start.isoformat(),
                                                    "--until", probe_end.isoformat(), self.target.docker["id"])
            lines = (probe_stdout + "\n" + probe_stderr).splitlines()
            if not any((match := LOCK_LINE.match(line)) and match.group(2, 3, 4) ==
                       (probe_pid, self.target.database, "acquired") for line in lines):
                raise ValueError("lock sensor did not observe a known waiting backend")
            stdout, stderr = self.docker("logs", "--since", start.isoformat(), "--until", end.isoformat(),
                                         self.target.docker["id"])
            return {"lock_wait_source": "PostgreSQL lock logs with verified end-to-end probe",
                    **bounded_lock_waits(stdout + "\n" + stderr, database=self.target.database, start=start, end=end,
                                         censor_ms=int(settings["deadlock_timeout"]), waiting_at_end=waiting_at_end)}
        except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError, base.StopRun):
            return {"lock_p95_upper_ms": None, "lock_wait_source": "unavailable"}

    def result(self):
        first, last = (self.bounds[key] for key in ("start", "end"))
        duration = last["monotonic"] - first["monotonic"]
        db_duration = (last["db_time"] - first["db_time"]).total_seconds()
        if min(duration, db_duration) <= 0 or len(self.samples) < 2 or first["reset_at"] != last["reset_at"]:
            raise base.StopRun("measurement observations incomplete or statistics reset")
        def delta(key):
            left, right = first[key], last[key]
            return None if left is None or right is None or right < left else right - left
        wal = delta("wal_insert_bytes")
        db = last["db_bytes"] - first["db_bytes"]
        cpu = delta("cpu_seconds")
        locks = self.lock_metrics(first["db_time"], last["db_time"], last["waiting_locks"])
        worker = {key: last["window_worker"][key] - first["window_worker"][key] for key in WORKER_COUNTERS}
        if any(value < 0 for value in worker.values()):
            raise base.StopRun("window worker counters reset during measurement")
        if worker["failures"]:
            raise base.ObservedViolation("window worker reported failures during measurement")
        phases = self.connection.execute(f"SELECT phase, count(*) FROM {CONTROL} GROUP BY phase").fetchall()
        reasons = self.connection.execute(
            f"SELECT reason,count(*) FROM {CONTROL} CROSS JOIN LATERAL unnest(coverage_reasons) reason GROUP BY reason"
        ).fetchall()
        return dict(**locks, deadlocks=delta("deadlocks"), connections=max(s["connections"] for s in self.samples),
                    connection_count_scope=CONNECTION_COUNT_SCOPE,
                    waiting_locks_max_sampled=max(s["waiting_locks"] for s in self.samples),
                    lock_waiters_at_end=last["waiting_locks"],
                    v3_worker_backlog=max(s["v3_worker_backlog"] for s in self.samples),
                    pool_overflow=None if any(s["pool_overflow"] is None for s in self.samples)
                    else max(s["pool_overflow"] for s in self.samples),
                    cpu_cores=None if cpu is None else cpu / duration, rss_mib=max(s["rss_mib"] for s in self.samples),
                    wal_bytes=wal, wal_mib_per_120s=None if wal is None else wal / db_duration * 120 / MIB,
                    database_measurement_seconds=db_duration,
                    database_bytes_at_start=first["db_bytes"], database_bytes_at_end=last["db_bytes"],
                    database_mib_per_120s=db / db_duration * 120 / MIB,
                    index_footprint=index_footprint(first["index_sizes"], last["index_sizes"]),
                    window_updates=delta("window_updates"),
                    window_rows_added=last["window_rows"] - first["window_rows"], window_rows_total=last["window_rows"],
                    window_phases=dict(phases), coverage_reasons=dict(reasons), worker_counter_deltas=worker,
                    samples=len(self.samples),
                    pg_revision=self.revision, core_revision=self.core_revision, postgres=self.settings["server_version"],
                    versions=self.target.versions, analytics_pool_by_role=self.target.analytics_pool_by_role,
                    pg_target_sha256=hashlib.sha256(self.target.docker["id"].encode("ascii")).hexdigest(),
                    ownership="read-only owner/sentinel and loopback PID verified",
                    resource_floors=self.monitor.result())


@contextmanager
def phase_observer(target):
    monitor = base.ResourceMonitor(3072 * MIB, 8192 * MIB, commit_floor_bytes=4096 * MIB)
    with guarded_pg(target) as connection:
        yield Observer(target, connection, monitor)

