"""Create and verify a dedicated disk-backed PostgreSQL resource for 607.G."""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
import os
import re
import secrets
import socket
import time
from uuid import UUID

import psycopg

from benchmark_activity import ResourceMonitor, StopRun
from benchmark_windows_target import LOCK_LINE, LOCK_PREFIX, MIB, command, process_info
from local_personal_live import _write, prepare_folder
from pymentor_analytics.adapters.privacy_authority import local_server

CONTEXT = "desktop-linux"
PORT = 62398
RELAY_PORT = 57392
LABEL = "knowi.corp598.benchmark-owner"
BOOTSTRAP = "analytics_bootstrap"
IMAGE = "sha256:63e83b5a3dc36a917dccb76fe1bd2ac51f1ec9c1deb47ff20d4f04233bf6eae9"
DURABILITY = {"fsync": "on", "synchronous_commit": "on", "full_page_writes": "on", "max_connections": "40"}
LOGGING = {"log_lock_waits": "on", "deadlock_timeout": "10ms", "log_line_prefix": LOCK_PREFIX,
           "log_timezone": "UTC", "log_destination": "stderr", "logging_collector": "off"}
SERVER_ARGS = {**DURABILITY, **LOGGING, "shared_buffers": "48MB", "work_mem": "2MB",
               "temp_buffers": "4MB", "maintenance_work_mem": "32MB", "autovacuum_work_mem": "16MB"}
OWNED_NAME = re.compile(r"(analytics_live_[0-9a-f]{32}|core_607_[0-9a-f]{32})\Z")


def names(cluster):
    if cluster.version != 4:
        raise ValueError("independent UUIDv4 cluster identity required")
    name = f"corp598-607-bench-{cluster.hex}"
    return name, name + "-data"


def docker(*args):
    return command(["docker", "--context", CONTEXT, *args])[0]


def verify_context():
    endpoint, _ = command(["docker", "context", "inspect", CONTEXT, "--format", "{{.Endpoints.docker.Host}}"])
    if endpoint.strip() != "npipe:////./pipe/dockerDesktopLinuxEngine":
        raise ValueError("expected local Docker Desktop Linux Engine")


def read_record(cluster, filename):
    folder = local_server._instance_dir(str(cluster))
    value = json.loads(local_server._read(folder, filename, 8192), object_pairs_hook=local_server._unique)
    project, volume = names(cluster)
    if (value["owner"], value["project"], value["volume"], value["port"], value["image"]) != (
        str(cluster), project, volume, PORT, IMAGE
    ) or not re.fullmatch(r"[0-9a-f]{64}", value["sentinel"]):
        raise ValueError("dedicated PG owner manifest mismatch")
    return folder, value


def verify_container(record, *, running=True):
    verify_context()
    container, = json.loads(docker("inspect", record["id"]))
    volume, = json.loads(docker("volume", "inspect", record["volume"]))
    if (volume["Driver"] != "local" or volume["Scope"] != "local" or volume.get("Options")
            or not volume["Mountpoint"].startswith("/var/lib/docker/volumes/")
            or volume["Labels"].get(LABEL) != record["owner"]):
        raise ValueError("dedicated volume is not owner-bound persisted Docker storage")
    label_map = container["Config"]["Labels"]
    mount, = [row for row in container["Mounts"] if row["Destination"] == "/var/lib/postgresql/data"]
    expected_ports = [{"HostIp": "127.0.0.1", "HostPort": str(PORT)}]
    configured_ports = container["HostConfig"]["PortBindings"].get("5432/tcp")
    published_ports = (container["NetworkSettings"]["Ports"] or {}).get("5432/tcp")
    if (container["Id"] != record["id"] or container["Image"] != IMAGE
            or container["State"]["Running"] is not running or label_map.get(LABEL) != record["owner"]
            or label_map.get("com.docker.compose.project") != record["project"]
            or label_map.get("com.docker.compose.service") != "analytics-postgres"
            or configured_ports != expected_ports or (running and published_ports != expected_ports)
            or mount["Type"] != "volume" or mount["Name"] != record["volume"]
            or container["HostConfig"]["LogConfig"]["Type"] != "local"
            or container["HostConfig"]["Memory"] != 192 * MIB or container["HostConfig"]["NanoCpus"] != 10**9):
        raise ValueError("dedicated socket, mount, image, budget or owner changed")


def pg_connect(password, database=BOOTSTRAP, **opts):
    if database != BOOTSTRAP and not OWNED_NAME.fullmatch(database):
        raise ValueError("database name is outside the disposable benchmark contract")
    return psycopg.connect(host="127.0.0.1", port=PORT, user=BOOTSTRAP, password=password, dbname=database,
                           connect_timeout=3, autocommit=True, sslmode="disable", **opts)


def settings(connection):
    actual = {name: connection.execute("SELECT current_setting(%s)", (name,)).fetchone()[0]
              for name in (DURABILITY | LOGGING)}
    if actual != DURABILITY | LOGGING or not 160000 <= connection.info.server_version < 170000:
        raise ValueError("dedicated PG durability, lock logging or major version mismatch")
    return actual


def cluster_connection(cluster):
    folder, record = read_record(cluster, "pg-ready.json")
    if not re.fullmatch(r"[0-9a-f]{64}", record["id"]):
        raise ValueError("expected the full owned PostgreSQL container ID")
    verify_container(record)
    password = local_server._read(folder, "postgres.password", 256).decode("ascii")
    data = dict(host="127.0.0.1", port=PORT, user=BOOTSTRAP, database=BOOTSTRAP, password=password,
                project=record["project"], synthetic_only=True)

    @contextmanager
    def connect(supplied):
        if supplied != data:
            raise ValueError("bootstrap credentials changed after scope validation")
        verify_container(record)
        with pg_connect(password, options="-c statement_timeout=5000 -c lock_timeout=1000") as conn:
            if conn.execute("SELECT current_database(),current_user,pg_is_in_recovery()").fetchone() != (
                BOOTSTRAP, BOOTSTRAP, False
            ) or conn.execute("SELECT token FROM public.corp598_benchmark_sentinel").fetchall() != [(record["sentinel"],)]:
                raise ValueError("dedicated PG bootstrap identity or sentinel mismatch")
            settings(conn)
            yield conn

    return data, record, connect


def verify_relay(cluster):
    data, record, guarded = cluster_connection(cluster)
    folder, _ = read_record(cluster, "pg-ready.json")
    marker = json.loads(local_server._read(folder, "pg-relay.json", 4096))
    if marker["owner"] != str(cluster) or process_info(marker["pid"])[0] != marker["started"]:
        raise ValueError("dedicated IPv6 relay process identity changed")
    with guarded(data) as direct, psycopg.connect(host="::1", port=RELAY_PORT, user=BOOTSTRAP,
        dbname=BOOTSTRAP, password=data["password"], connect_timeout=3, sslmode="disable", autocommit=True,
        options="-c default_transaction_read_only=on -c statement_timeout=1000") as alias:
        identity_sql = "SELECT system_identifier::text,inet_server_port() FROM pg_control_system()"
        identity = direct.execute(identity_sql).fetchone()
        if (alias.execute(identity_sql).fetchone() != identity or identity[1] != 5432
                or alias.execute("SELECT token FROM public.corp598_benchmark_sentinel").fetchall() != [
                    (record["sentinel"],)
                ] or alias.execute("SELECT current_database(),current_user").fetchone() != (BOOTSTRAP, BOOTSTRAP)):
            raise ValueError("IPv6 and direct routes do not identify the same owned PostgreSQL")
    return dict(system_identifier=identity[0], server_port=identity[1], relay_pid=marker["pid"],
                alias=f"[::1]:{RELAY_PORT}", dedicated=f"127.0.0.1:{PORT}")


async def relay(cluster):
    data, _, guarded = cluster_connection(cluster)
    with guarded(data):
        pass
    folder = local_server._instance_dir(str(cluster))
    old_path = local_server._file(folder, "pg-relay.json", required=False)
    if old_path.exists():
        old = json.loads(local_server._read(folder, old_path.name, 4096))
        if old["owner"] != str(cluster) or type(old["pid"]) is not int or old["pid"] < 1:
            raise ValueError("existing relay owner record did not match")
        try:
            old_started = process_info(old["pid"])[0]
        except StopRun:
            old_started = None
        if old_started == old["started"]:
            raise ValueError("owned relay still active")
        archived = local_server._file(folder, f"pg-relay-stopped-{old['pid']}.json", required=False)
        old_path.rename(archived)
    capacity = asyncio.Semaphore(40)
    active = set()

    async def copy(source, destination):
        while packet := await source.read(65536):
            destination.write(packet)
            await destination.drain()

    async def forward(reader, writer):
        task = asyncio.current_task()
        active.add(task)
        upstream = None
        try:
            if writer.get_extra_info("peername")[0] != "::1" or capacity.locked():
                return
            async with capacity:
                source, upstream = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", PORT), 3)
                directions = [asyncio.create_task(copy(reader, upstream)), asyncio.create_task(copy(source, writer))]
                try:
                    await asyncio.wait(directions, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for direction in directions:
                        direction.cancel()
                    await asyncio.gather(*directions, return_exceptions=True)
        except (ConnectionError, OSError, TimeoutError):
            pass
        finally:
            active.discard(task)
            for stream in (writer, upstream):
                if stream is not None:
                    stream.close()

    marker = dict(owner=str(cluster), pid=os.getpid(), started=process_info(os.getpid())[0])
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as endpoint:
        endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        endpoint.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        endpoint.bind(("::1", RELAY_PORT))
        endpoint.listen(40)
        endpoint.setblocking(False)
        server = await asyncio.start_server(forward, sock=endpoint)
        try:
            _write(local_server, folder, "pg-relay.json", marker)
            print(json.dumps({"status": "LISTENING", "alias": f"[::1]:{RELAY_PORT}",
                              "destination": f"127.0.0.1:{PORT}", "pid": marker["pid"]}), flush=True)
            async with server:
                await server.serve_forever()
        finally:
            server.close()
            await server.wait_closed()
            for task in tuple(active):
                task.cancel()
            await asyncio.gather(*tuple(active), return_exceptions=True)


def prepare(cluster):
    project, volume = names(cluster)
    verify_context()
    image, = json.loads(docker("image", "inspect", IMAGE))
    if image["Id"] != IMAGE:
        raise ValueError("approved immutable PostgreSQL image unavailable")
    local, folder, _ = prepare_folder(str(cluster))
    record = dict(owner=str(cluster), project=project, volume=volume, port=PORT, image=IMAGE,
                  sentinel=secrets.token_hex(32))
    _write(local, folder, "pg-pending.json", record)
    _write(local, folder, "postgres.password", secrets.token_urlsafe(36).encode("ascii"))
    return dict(status="PREPARED", cluster=str(cluster), project=project, port=PORT)


def lock_probe(record, password, database=BOOTSTRAP):
    verify_container(record)
    options = "-c statement_timeout=4000 -c lock_timeout=3000"
    with (pg_connect(password, database, options=options) as holder,
          pg_connect(password, database, options=options) as waiter,
          ThreadPoolExecutor(max_workers=1) as pool):
        started = datetime.now(UTC)
        pid = waiter.info.backend_pid
        key = secrets.randbits(62)
        holder.execute("SELECT pg_advisory_lock(%s)", (key,))
        future = pool.submit(waiter.execute, "SELECT pg_advisory_xact_lock(%s)", (key,))
        try:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                locked = holder.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", (pid,)).fetchone()
                if locked == ("Lock",):
                    time.sleep(.04)
                    break
                time.sleep(.02)
            else:
                raise ValueError("contended synthetic PostgreSQL lock not observed")
        finally:
            holder.execute("SELECT pg_advisory_unlock(%s)", (key,))
        future.result(timeout=4)
        completed = datetime.now(UTC)
    stamp = (started - timedelta(seconds=1)).isoformat()
    for _ in range(10):
        output, errors = command(["docker", "--context", CONTEXT, "logs", "--since", str(stamp), record["id"]])
        for line in (output + "\n" + errors).splitlines():
            match = LOCK_LINE.match(line)
            if match and match.group(2, 3, 4) == (str(pid), database, "acquired"):
                elapsed = float(match.group(5))
                if elapsed >= 10:
                    return dict(pid=pid, at=completed.isoformat(), observed_wait_ms=elapsed)
        time.sleep(.2)
    raise ValueError("PostgreSQL wait duration did not reach the container log")


def wait_on_postgres(password):
    live = ResourceMonitor(3072 * MIB, 8192 * MIB, commit_floor_bytes=4096 * MIB)
    deadline = time.monotonic() + 30
    while True:
        live.check()
        try:
            return pg_connect(password)
        except psycopg.OperationalError:
            if time.monotonic() >= deadline:
                raise ValueError("owned PostgreSQL startup did not become available") from None
            time.sleep(.5)


def resume(cluster):
    ResourceMonitor((3072 + 192) * MIB, 8192 * MIB, commit_floor_bytes=4096 * MIB).check()
    folder, record = read_record(cluster, "pg-ready.json")
    if not re.fullmatch(r"[0-9a-f]{64}", record["id"]):
        raise ValueError("expected full owned PostgreSQL container ID")
    verify_container(record, running=False)
    docker("start", record["id"])
    verify_container(record)
    password = local_server._read(folder, "postgres.password", 256).decode("ascii")
    with wait_on_postgres(password):
        pass
    data, _, guarded = cluster_connection(cluster)
    with guarded(data) as conn:
        observed = settings(conn)
    return dict(status="RESUMED", cluster=str(cluster), container=record["id"], project=record["project"], port=PORT,
                bootstrap_lock_probe=lock_probe(record, password), settings=observed)


def start(cluster):
    # Admission accounts for the whole container limit above 607.A's fixed floor.
    preflight = ResourceMonitor((3072 + 192) * MIB, 8192 * MIB, commit_floor_bytes=4096 * MIB)
    preflight.check()
    verify_context()
    folder, record = read_record(cluster, "pg-pending.json")
    volume, name = record["volume"], record["project"]
    if local_server._file(folder, "pg-ready.json", required=False).exists():
        raise ValueError("dedicated cluster already admitted; inspect its current state")
    if name in docker("ps", "-a", "--format", "{{.Names}}").splitlines() or volume in docker(
        "volume", "ls", "--format", "{{.Name}}"
    ).splitlines():
        raise ValueError("container or volume already exists; reconcile the pending owner record")
    if not local_server._file(folder, "postgres.password").is_file():
        raise ValueError("missing private PostgreSQL bootstrap input")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", PORT))
    docker("volume", "create", "--label", f"{LABEL}={cluster}", volume)
    args = ["run", "-d", "--pull=never", "--name", name, "--label", f"{LABEL}={cluster}",
            "--label", f"com.docker.compose.project={name}", "--label", "com.docker.compose.service=analytics-postgres",
            "--memory", "192m", "--memory-swap", "192m", "--cpus", "1", "--log-driver", "local",
            "-p", f"127.0.0.1:{PORT}:5432", "--mount",
            f"type=volume,src={volume},dst=/var/lib/postgresql/data", "--mount",
            f"type=bind,src={folder / 'postgres.password'},dst=/run/secrets/postgres-bench-password,readonly",
            "-e", f"POSTGRES_USER={BOOTSTRAP}", "-e", f"POSTGRES_DB={BOOTSTRAP}",
            "-e", "POSTGRES_PASSWORD_FILE=/run/secrets/postgres-bench-password", IMAGE,
            *(arg for name, value in SERVER_ARGS.items() for arg in ("-c", f"{name}={value}"))]
    record["id"] = docker(*args).strip()
    verify_container(record)
    password = local_server._read(folder, "postgres.password", 256).decode("ascii")
    with wait_on_postgres(password) as connection:
        observed = settings(connection)
        with connection.transaction():
            connection.execute("CREATE TABLE public.corp598_benchmark_sentinel (token text NOT NULL)")
            connection.execute("INSERT INTO public.corp598_benchmark_sentinel VALUES (%s)", (record["sentinel"],))
            connection.execute("REVOKE ALL ON public.corp598_benchmark_sentinel FROM PUBLIC")
    probe = lock_probe(record, password)
    _write(local_server, folder, "pg-ready.json", record)
    return dict(status="OBSERVED", cluster=str(cluster), container=record["id"], project=name, port=PORT,
                bootstrap_lock_probe=probe, settings=observed)


def phase_probe(cluster, instance):
    ResourceMonitor(3072 * MIB, 8192 * MIB, commit_floor_bytes=4096 * MIB).check()
    data, record, guarded = cluster_connection(cluster)
    folder = local_server._instance_dir(str(instance))
    phase = json.loads(local_server._read(folder, "corp607-pending.json", 4096))
    database = "analytics_live_" + instance.hex
    if (phase["owner"], phase["instance_id"], phase["analytics"]) != ("607", str(instance), database):
        raise ValueError("unowned phase probe target")
    with guarded(data), pg_connect(data["password"], database, options="-c default_transaction_read_only=on") as conn:
        if conn.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone() != (
            database + "_migrate",
        ) or conn.execute("SELECT token FROM public.corp607_sentinel").fetchall() != [(phase["sentinel"]["analytics"],)]:
            raise ValueError("phase probe owner or sentinel mismatch")
        return lock_probe(record, data["password"], database)


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("action", choices=("prepare", "start", "resume", "probe", "relay", "verify-relay"))
    cli.add_argument("--cluster", required=True, type=UUID)
    cli.add_argument("--instance", type=UUID)
    args = cli.parse_args(argv)
    if (args.instance is None) == (args.action == "probe"):
        cli.error("specify --instance only for a phase-specific probe")
    if args.action in {"start", "resume", "probe", "relay"} and os.environ.get("ANALYTICS_BENCHMARK_GO") != "1":
        cli.error("explicit ANALYTICS_BENCHMARK_GO=1 and isolated resource slot required")
    try:
        if args.action == "prepare":
            result = prepare(args.cluster)
        elif args.action == "start":
            result = start(args.cluster)
        elif args.action == "resume":
            result = resume(args.cluster)
        elif args.action == "probe":
            result = phase_probe(args.cluster, args.instance)
        elif args.action == "relay":
            asyncio.run(relay(args.cluster))
            return 0
        else:
            result = verify_relay(args.cluster)
    except Exception as error:
        cli.error("dedicated PG operation stopped, reconcile observed resource before retry: " + type(error).__name__)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

