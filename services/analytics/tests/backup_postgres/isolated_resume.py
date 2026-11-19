"""Reconcile and restore one attested test node; no retained/source DB connection."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.parse import quote
from uuid import uuid4

from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import synthetic_backup as backup
import run_local


def memory_guard():
    if backup.check_k3s.available_memory() < 1536 * 1024**2:
        raise RuntimeError("insufficient fresh host memory")


def docker(args, *, env=None, input_data=None, input_file=None, timeout=30):
    process = subprocess.run(["docker", *args], env=env, input=input_data, stdin=input_file,
                             capture_output=True, timeout=timeout)
    if process.returncode:
        raise RuntimeError("isolated Docker operation failed")
    return process.stdout.decode("utf-8").strip()


def validate_record(record):
    nonce = record.get("nonce", "")
    if not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ValueError("invalid node nonce")
    name = "knowi-analytics-restore-" + nonce
    if type(record.get("host_reachable", False)) is not bool:
        raise ValueError("invalid fixture network profile")
    if record.get("host_reachable") is True and record.get("host_port") != 57422:
        raise ValueError("invalid host fixture port")
    roles = backup.db.roles_for_revision(record.get("schema_revision", "a006"))
    if (record.get("network") != name or record.get("container") != name
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", record.get("postgres_image", ""))
            or not backup.db.PATTERN.fullmatch(record.get("database", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", record.get("sentinel", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", record.get("password", ""))
            or set(record.get("role_passwords", {})) != set(roles)
            or not all(re.fullmatch(r"[0-9a-f]{64}", x) for x in record["role_passwords"].values())):
        raise ValueError("invalid isolated node metadata")
    addresses = record.get("source_ips")
    if not isinstance(addresses, list) or not addresses or len(addresses) > 16:
        raise ValueError("missing pre-loss source addresses")
    for address in addresses:
        if not isinstance(address, str):
            raise ValueError("invalid source address")
        parsed = ipaddress.ip_address(address)
        if parsed.version != 4 or parsed.is_loopback or parsed.is_unspecified:
            raise ValueError("invalid source address")
    return name


def network_attest(record):
    name = validate_record(record)
    fmt = '{{.Name}}|{{.Internal}}|{{index .Labels "knowi.analytics.synthetic"}}|{{index .Labels "knowi.analytics.fixture"}}'
    internal = "false" if record.get("host_reachable") is True else "true"
    if docker(["network", "inspect", "--format", fmt, name]) != f"{name}|{internal}|true|{record['nonce']}":
        raise ValueError("isolated network identity mismatch")
    if record.get("host_reachable") is True:
        if docker(["network", "inspect", "--format", "{{.Driver}}|{{.Scope}}|{{.Ingress}}", name]) != "bridge|local|false":
            raise ValueError("invalid fixture bridge")


def attest(record):
    network_attest(record)
    name = record["container"]
    fmt = ('{{.Name}}|{{.Image}}|{{index .Config.Labels "knowi.analytics.synthetic"}}|'
           '{{index .Config.Labels "knowi.analytics.fixture"}}|{{.HostConfig.Memory}}|{{.State.Running}}|'
           '{{json .NetworkSettings.Networks}}|{{json .HostConfig.PortBindings}}|{{json .Mounts}}|{{json .HostConfig.Tmpfs}}')
    parts = docker(["inspect", "--format", fmt, name]).split("|", 9)
    expected = [f"/{name}", record["postgres_image"], "true", record["nonce"], "268435456", "true"]
    if len(parts) != 10 or parts[:6] != expected:
        raise ValueError("isolated container identity mismatch")
    networks, bindings, mounts, tmpfs = map(json.loads, parts[6:])
    if (set(networks) != {name} or bindings != {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "57422"}]}
            or any(x.get("Type") != "tmpfs" or x.get("Destination") != "/var/lib/postgresql/data" for x in mounts)
            or not tmpfs or "size=128m" not in tmpfs.get("/var/lib/postgresql/data", "")):
        raise ValueError("isolated node configuration mismatch")
    if record.get("host_reachable") is True:
        attached = json.loads(docker(["network", "inspect", "--format", "{{json .Containers}}", name]))
        if not isinstance(attached, dict) or len(attached) != 1 or next(iter(attached.values())).get("Name") != name:
            raise ValueError("unexpected fixture network attachment")


def env_password(password):
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    env["PGCLIENTENCODING"] = "UTF8"
    return env


def psql_args(record, database):
    if database not in {"analytics_bootstrap", record["database"]}:
        raise ValueError("unexpected destination database")
    return ["exec", "-i", "--env", "PGPASSWORD", "--env", "PGCLIENTENCODING", record["container"],
            "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-A", "-t", "-U", "analytics_bootstrap", "-d", database]


def psql(record, database, query):
    attest(record)
    output = docker(psql_args(record, database), env=env_password(record["password"]), input_data=query.encode("utf-8"))
    if len(output) > 64 * 1024:
        raise ValueError("administrative result too large")
    return output


def table_digests(record, database, names):
    if (not names or len(names) > 128
            or not all(re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name) for name in names)):
        raise ValueError("invalid table names")
    attest(record)
    pieces = ["BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;", "SET LOCAL statement_timeout='20s';"]
    for name in names:
        pieces.append("\\echo __TABLE__:" + name)
        pieces.append(sql.SQL("SELECT to_jsonb(t)::text FROM analytics.{} t ORDER BY 1;").format(sql.Identifier(name)).as_string())
    pieces.append("COMMIT;")
    query = "\n".join(pieces) + "\n"
    process = subprocess.Popen(["docker", *psql_args(record, database)], env=env_password(record["password"]),
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    process.stdin.write(query.encode("utf-8"))
    process.stdin.close()
    result, errors = {}, []
    def stream():
        try:
            active = None
            digest, count, size = None, 0, 0
            while line := process.stdout.readline(backup.crypto.CHUNK + 1):
                if len(line) > backup.crypto.CHUNK or not line.endswith(b"\n"):
                    raise ValueError("row exceeds limit")
                value = line[:-1]
                if value.startswith(b"__TABLE__:"):
                    if active is not None:
                        result[active] = (count, digest.hexdigest())
                    active = value[len(b"__TABLE__:"):].decode("ascii")
                    if active not in names or active in result:
                        raise ValueError("unexpected table marker")
                    digest, count, size = hashlib.sha256(), 0, 0
                    continue
                if active is None or not value.startswith(b"{") or not value.endswith(b"}"):
                    raise ValueError("unexpected row")
                count += 1
                size += len(value)
                if count > 1_000_000 or size > backup.crypto.MAX_BYTES:
                    raise ValueError("parity exceeds limit")
                digest.update(len(value).to_bytes(8, "big"))
                digest.update(value)
            if active is not None:
                result[active] = (count, digest.hexdigest())
        except Exception as exc:
            errors.append(exc)
            process.kill()
    reader = threading.Thread(target=stream, daemon=True)
    reader.start()
    try:
        process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise RuntimeError("parity outcome unknown") from None
    finally:
        reader.join(timeout=10)
    if reader.is_alive() or errors or process.returncode or set(result) != set(names):
        raise RuntimeError("parity query failed")
    return result


def tables(record):
    result = psql(record, record["database"], "SELECT tablename FROM pg_tables WHERE schemaname='analytics' ORDER BY tablename;")
    names = result.splitlines()
    if not names or len(names) > 128 or not all(re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name) for name in names):
        raise ValueError("invalid analytics tables")
    return names


def create_node(record):
    backup.check_k3s.local_docker_env()
    name = validate_record(record)
    memory_guard()
    profile = [] if record.get("host_reachable") is True else ["--internal"]
    docker(["network", "create", *profile, "--label", "knowi.analytics.synthetic=true",
            "--label", f"knowi.analytics.fixture={record['nonce']}", name])
    network_attest(record)
    env = os.environ.copy()
    env.update(POSTGRES_USER="analytics_bootstrap", POSTGRES_PASSWORD=record["password"], POSTGRES_DB="analytics_bootstrap")
    memory_guard()
    docker(["run", "-d", "--pull=never", "--name", name, "--label", "knowi.analytics.synthetic=true",
            "--label", f"knowi.analytics.fixture={record['nonce']}", "--network", name, "--network-alias", "restore-postgres",
            "--memory=256m", "--cpus=0.5", "--tmpfs", "/var/lib/postgresql/data:rw,size=128m",
            "-p", "127.0.0.1:57422:5432", "-e", "POSTGRES_USER", "-e", "POSTGRES_PASSWORD", "-e", "POSTGRES_DB",
            record["postgres_image"], "-c", "shared_buffers=16MB", "-c", "max_connections=20"], env=env)


def resume(operation_id, image_id, *, expected_fact_count=1, after_parity=None):
    backup.tree()
    record = backup.read_record(operation_id, "isolated_node", "provisioning")
    name = validate_record(record)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("invalid migrator image")
    backup.check_k3s.local_docker_env()
    # Verify encrypted artifact again before even read-only target SQL.
    header, stage = backup.crypto.decrypt_verified(backup.path_for("artifacts", record["artifact_id"], ".enc"),
                                                   backup.BASE / "staging", backup.key_for(record["key_id"]))
    expected = backup.history_for_revision("a006")
    metadata = header["metadata"]
    if (metadata.get("history") != expected or metadata.get("synthetic_only") is not True
            or not re.fullmatch(r"[0-9a-f]{64}", metadata.get("fixture_state_sha256", ""))):
        stage.unlink(missing_ok=True)
        raise ValueError("authenticated source history differs")
    previous_stage = record.get("stage", "")
    if not re.fullmatch(r"\.verified-dump-[0-9a-f]{32}\.tmp", previous_stage):
        raise ValueError("invalid saved stage")
    record.update(stage=stage.name, previous_stage=previous_stage, crypto_verified=True)
    backup.write_record(operation_id, record)
    attest(record)
    deadline = time.monotonic() + 45
    while True:
        response = subprocess.run(["docker", "exec", name, "pg_isready", "-U", "analytics_bootstrap", "-d", "analytics_bootstrap"],
                                  capture_output=True, timeout=5)
        if response.returncode == 0:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("isolated PostgreSQL not ready")
        time.sleep(0.25)
    for address in record["source_ips"]:
        attest(record)
        response = subprocess.run(["docker", "exec", name, "pg_isready", "-h", address, "-p", "5432", "-t", "2"],
                                  capture_output=True, timeout=5)
        if response.returncode != 2:
            raise ValueError("source node isolation not established")
    memory_guard()
    if docker(["image", "inspect", "--format", "{{.Id}}", image_id]) != image_id:
        raise ValueError("migrator image identity mismatch")
    probe = docker(["run", "--rm", "--pull=never", "--name", name + "-probe",
                    "--label", "knowi.analytics.synthetic=true", "--label", f"knowi.analytics.fixture={record['nonce']}",
                    "--network=none", "--read-only", "--memory=128m", "--cpus=0.25",
                    "--pids-limit=64", "--user=10001", "--workdir=/app", "--entrypoint=python", image_id,
                    "-c", run_local.IMAGE_HISTORY_PROBE])
    if json.loads(probe)["history"] != [expected["head"], expected["chain"], expected["sha256"]]:
        raise ValueError("migrator image history mismatch")

    target = record["database"]
    role_names = [f"{target}_{role}" for role in backup.db.ROLES]
    quoted = sql.SQL(",").join(map(sql.Literal, role_names)).as_string()
    check = psql(record, "analytics_bootstrap", f"SELECT count(*) FROM pg_roles WHERE rolname IN ({quoted});")
    exists = psql(record, "analytics_bootstrap", sql.SQL("SELECT count(*) FROM pg_database WHERE datname={};").format(sql.Literal(target)).as_string())
    if check != "0" or exists != "0":
        raise ValueError("prior target creation outcome is unresolved")
    started = time.perf_counter()
    record["state"] = "creating_roles"
    backup.write_record(operation_id, record)
    role_commands = []
    for role in backup.db.ROLES:
        command = sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;").format(
            sql.Identifier(f"{target}_{role}"), sql.Literal(record["role_passwords"][role])).as_string()
        role_commands.append(command)
    psql(record, "analytics_bootstrap", "\n".join(role_commands))
    psql(record, "analytics_bootstrap", sql.SQL("CREATE DATABASE {} OWNER {};").format(
        sql.Identifier(target), sql.Identifier(f"{target}_migrate")).as_string())
    grant_commands = [sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC;").format(sql.Identifier(target)).as_string()]
    for role in backup.db.ROLES:
        grant_commands.append(sql.SQL("GRANT CONNECT ON DATABASE {} TO {};").format(
            sql.Identifier(target), sql.Identifier(f"{target}_{role}")).as_string())
    psql(record, "analytics_bootstrap", "\n".join(grant_commands))
    sentinel_sql = sql.SQL("CREATE TABLE public.analytics_test_sentinel (token text NOT NULL); INSERT INTO public.analytics_test_sentinel VALUES ({});").format(sql.Literal(record["sentinel"])).as_string()
    psql(record, target, sentinel_sql)
    record["state"] = "migration_pending"
    backup.write_record(operation_id, record)
    url = f"postgresql://{target}_migrate:{quote(record['role_passwords']['migrate'], safe='')}@restore-postgres:5432/{target}"
    env = os.environ.copy()
    env["ANALYTICS_MIGRATE_DATABASE_URL"] = url
    attest(record)
    memory_guard()
    docker(["run", "--rm", "--pull=never", "--name", name + "-migrate", "--label", "knowi.analytics.synthetic=true", "--label", f"knowi.analytics.fixture={record['nonce']}",
            "--network", name, "--memory=128m", "--cpus=0.25", "--pids-limit=64", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "-e", "ANALYTICS_MIGRATE_DATABASE_URL", image_id, "migrate", "--revision", expected["head"]],
           env=env, timeout=60)
    if psql(record, target, "SELECT version_num FROM public.alembic_version;") != expected["head"]:
        raise ValueError("migration head mismatch")
    if psql(record, target, "SELECT token FROM public.analytics_test_sentinel;") != record["sentinel"]:
        raise ValueError("sentinel mismatch")
    owner_sql = sql.SQL("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname={};").format(sql.Literal(target)).as_string()
    if psql(record, "analytics_bootstrap", owner_sql) != f"{target}_migrate":
        raise ValueError("destination owner mismatch")
    names = tables(record)
    checks = ["BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;", "SET LOCAL statement_timeout='20s';"]
    checks.extend(sql.SQL("SELECT count(*) FROM analytics.{};").format(sql.Identifier(table)).as_string() for table in names)
    checks.append("SELECT json_build_array(singleton,subject_per_minute,global_per_minute,max_outstanding_jobs,max_database_bytes,warning_percent,critical_percent,window_epoch,accepted_count,lock_nonce)::text FROM analytics.admission_budget;")
    checks.append("COMMIT;")
    outputs = psql(record, target, "\n".join(checks)).splitlines()
    if len(outputs) != len(names) + 1:
        raise ValueError("unexpected fresh schema output")
    for table, count in zip(names, outputs[:-1], strict=True):
        if count != ("1" if table == "admission_budget" else "0"):
            raise ValueError("target is not fresh")
    if json.loads(outputs[-1]) != [True, 60, 600, 10000, 536870912, 80, 90, 0, 0, False]:
        raise ValueError("unexpected budget seed")
    record["state"] = "restore_pending"
    backup.write_record(operation_id, record)
    psql(record, target, "DELETE FROM analytics.admission_budget WHERE singleton=true;")
    attest(record)
    restore_env = env_password(record["role_passwords"]["migrate"])
    with stage.open("rb") as source:
        docker(["exec", "-i", "--env", "PGPASSWORD", name, "pg_restore", "--host", "127.0.0.1", "--username",
                f"{target}_migrate", "--dbname", target, "--data-only", "--single-transaction", "--exit-on-error",
                "--no-owner", "--no-acl"], env=restore_env, input_file=source, timeout=120)
    result = table_digests(record, target, names)
    if backup.state_digest(result) != metadata.get("fixture_state_sha256") or result.get("activity_facts", (0,))[0] != expected_fact_count:
        raise ValueError("isolated fixture parity mismatch")
    elapsed = time.perf_counter() - started
    record.update(state="parity_verified", data_restored=True, parity_verified=True, privacy_opened=False,
                  post_node_ready_restore_seconds=elapsed)
    backup.write_record(operation_id, record)
    print(f"isolated_post_node_ready_restore_seconds={elapsed:.3f} snapshot_fact_parity=true privacy_opened=false")
    # An optional proof must finish before deleting this exact owned node. On
    # failure its metadata and data remain available for reconciliation.
    if after_parity is not None:
        evidence = after_parity(record)
        if not isinstance(evidence, dict):
            raise ValueError("privacy proof did not return evidence")
        record.update(evidence)
        backup.write_record(operation_id, record)
    attest(record)
    docker(["rm", "--force", name])
    network_attest(record)
    docker(["network", "rm", name])
    for filename in (previous_stage, stage.name):
        path = backup.BASE / "staging" / filename
        if path.is_symlink() or path.is_junction():
            raise ValueError("unsafe staging file")
        path.unlink()
    record["isolated_node_deleted_after_proof"] = True
    backup.write_record(operation_id, record)


if __name__ == "__main__":
    try:
        resume(os.environ["ANALYTICS_ISOLATED_RESUME_ID"], os.environ["ANALYTICS_MIGRATOR_IMAGE_ID"])
    except Exception:
        print("Isolated restore stopped; preserve operation metadata and reconcile before retry.", file=sys.stderr)
        raise SystemExit(2) from None
