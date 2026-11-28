"""Prepare private synthetic credentials and Core scope inputs for a benchmark pair."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import UUID
from urllib.parse import urlsplit, urlunsplit

import benchmark_activity as base
from benchmark_activity_windows import Course, Plan
from benchmark_windows_target import ANALYTICS_ROLES, LISTENERS, measured_pool, process_info
from pymentor_analytics.adapters.auth.config import load_manifest
from pymentor_analytics.adapters.privacy_authority import local_server
from pydantic import SecretStr

import benchmark_windows_postgres as bench_pg
import local_personal_live as local_live
from local_personal_live import prepare_folder, _write


def fixture_inputs(plan, make_password):
    learners, core_seed, scope = {}, {}, {}
    for index, subject in enumerate(plan.users):
        name = f"corp607-bench-{index:02}"
        learner = {"email": f"{name}@example.test", "password": make_password()}
        learners[str(subject)] = learner
        core_seed[name] = {**learner, "subject_ref": str(subject)}
        scope[str(subject)] = {"course_ids": [course.course_id for course in plan.courses], "tool_scopes": []}
    return learners, core_seed, scope


def bind_pg_url(original, port, database, role, *, analytics=False):
    url = urlsplit(original)
    if (url.hostname != "127.0.0.1" or url.port != 57392 or url.path != "/" + database
            or url.username != role or not url.password or url.query or url.fragment or port != bench_pg.PORT):
        raise ValueError("cannot bind noncanonical runtime database role to the benchmark PostgreSQL")
    if analytics:
        return urlunsplit(url._replace(netloc=url.netloc.rsplit("@", 1)[0] + f"@[::1]:{bench_pg.RELAY_PORT}"))
    return urlunsplit(url._replace(netloc=url.netloc.removesuffix(":57392") + f":{port}"))


def phase_cluster(local, folder):
    binding = json.loads(local._read(folder, "pg-benchmark-cluster.json", 4096))
    data, record, guarded = bench_pg.cluster_connection(UUID(binding["cluster"]))
    if binding != {"cluster": record["owner"], "port": record["port"], "id": record["id"]}:
        raise ValueError("phase dedicated PostgreSQL binding changed")
    with guarded(data):
        pass
    relay = verify_phase_alias(UUID(binding["cluster"]), UUID(folder.name))
    return {**record, "relay_pid": relay["relay_pid"]}


def verify_phase_alias(cluster, instance):
    import psycopg

    relay = bench_pg.verify_relay(cluster)
    data, _, guarded = bench_pg.cluster_connection(cluster)
    folder = local_server._instance_dir(str(instance))
    phase = json.loads(local_server._read(folder, "corp607-pending.json", 4096))
    name = local_live.database_name(str(instance))
    if (phase["owner"], phase["instance_id"], phase["analytics"], phase["analytics_owner"]) != (
        "607", str(instance), name, name + "_migrate"
    ):
        raise ValueError("dedicated relay phase owner mismatch")
    with guarded(data), psycopg.connect(host="::1", port=bench_pg.RELAY_PORT, dbname=name,
            user=bench_pg.BOOTSTRAP, password=data["password"], sslmode="disable", connect_timeout=3,
            options="-c default_transaction_read_only=on -c statement_timeout=1000") as conn:
        row = conn.execute("SELECT current_database(), pg_get_userbyid(datdba) FROM pg_database "
                           "WHERE datname=current_database()").fetchone()
        if row != (name, phase["analytics_owner"]) or conn.execute(
                "SELECT token FROM public.corp607_sentinel").fetchall() != [(phase["sentinel"]["analytics"],)]:
            raise ValueError("dedicated relay phase PostgreSQL sentinel mismatch")
    return relay


def stage_target(instance, learners, revision, cluster):
    import bootstrap_runtime
    import shadow_607_target as shared
    from pymentor_analytics.adapters.postgres.migrate import upgrade
    from pymentor_analytics.app.config import Settings

    bench_pg.verify_relay(cluster)
    data, record, connect = bench_pg.cluster_connection(cluster)
    local, folder, owner = prepare_folder(str(instance))
    analytics_name, core_name = local_live.database_name(str(instance)), f"core_607_{instance.hex}"
    analytics_owner, core_owner, core_app = analytics_name + "_migrate", core_name + "_owner", core_name + "_app"
    passwords = {role: secrets.token_urlsafe(32) for role in local_live.ROLES}
    core_passwords = {role: secrets.token_urlsafe(32) for role in (core_owner, core_app)}
    sentinels = {kind: secrets.token_hex(32) for kind in ("analytics", "core")}
    _write(local, folder, "corp607-pending.json", dict(owner="607", instance_id=str(instance),
        analytics=analytics_name, core=core_name, analytics_owner=analytics_owner, core_owner=core_owner,
        sentinel=sentinels))
    shared.provision(data, analytics_name, analytics_owner,
                     {analytics_name + "_" + role: password for role, password in passwords.items()},
                     sentinels["analytics"], bootstrap_connect=connect)
    shared.provision(data, core_name, core_owner, core_passwords, sentinels["core"], core=True,
                     bootstrap_connect=connect)

    keys, reader, source = local_live._pem_keys(), local_live._secret32(), local_live._secret32()
    config = local_live.build_configuration(folder, owner, keys, reader, source, passwords, core_name,
                                            projection_revision="a014")
    local_live._write_config_files(local, folder, owner, keys, reader, source, passwords, config)
    _write(local, folder, "pg-benchmark-cluster.json", dict(cluster=str(cluster), port=record["port"], id=record["id"]))
    verify_phase_alias(cluster, instance)
    migrate_url = bind_pg_url(config["urls"]["migrate"], record["port"], analytics_name, analytics_owner, analytics=True)
    direct_migrate_url = bind_pg_url(config["urls"]["migrate"], record["port"], analytics_name, analytics_owner)
    migration = Settings(**config["shared"], database_url=migrate_url)
    upgrade(migration, target_revision=revision)
    if local_live._db_revision(migrate_url) != revision:
        raise ValueError("expanded Analytics schema revision mismatch")
    local_live._begin_projection(migrate_url, owner["dataset_run_id"])
    local.initialize(str(instance))

    core_dsn = bind_pg_url(shared.dsn(core_name, core_owner, core_passwords[core_owner], "psycopg"),
                           record["port"], core_name, core_owner)
    app_dsn = bind_pg_url(shared.dsn(core_name, core_app, core_passwords[core_app], "psycopg"),
                          record["port"], core_name, core_app)
    core_env = {**config["core_env"], "DATABASE_URL": app_dsn,
                "REDIS_URL": shared.REDIS_URL, "KNOWI_REDIS_PREFIX": f"corp598-607-bench:{instance.hex}:",
                "KNOWI_LOGIN_RECOVERY_KEY": secrets.token_hex(32), "PYMENTOR_PROFILE": "postgres_redis",
                "PYMENTOR_SCHEMA_MODE": "managed", "PYMENTOR_COURSE_TRANSPORT": "local",
                "PYMENTOR_COURSES_ROOT": str(shared.REPOSITORY / "courses"), "PYMENTOR_COOKIE_SECURE": "0",
                "PYMENTOR_LOCAL_NOTIFICATIONS": "0", "PYMENTOR_EMAIL_TOKEN_SECRET": secrets.token_urlsafe(40),
                "PYMENTOR_ANALYTICS_LEARNER_READS_ENABLED": "1",
                "PYMENTOR_ANALYTICS_QUERY_URL": "http://127.0.0.1:18043",
                "PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(shared.CORE)}
    full = {**shared.base_env(), **core_env}
    core_migration = dict(full, KNOWI_MIGRATION_URL=core_dsn.replace("+psycopg:", "+asyncpg:", 1),
                          KNOWI_MIGRATION_DATABASE=core_name, KNOWI_MIGRATION_USER=core_owner,
                          KNOWI_MIGRATION_HOST="127.0.0.1", KNOWI_MIGRATION_OWNER=core_owner,
                          KNOWI_MIGRATION_APP_ROLE=core_app)
    python = str(shared.CORE_PYTHON)
    shared.run_private([python, "-B", "-m", "alembic", "-c", "alembic-runtime.ini", "upgrade", "head"],
                       shared.CORE, core_migration, folder / "core-migrate.log")
    _write(local, folder, "corp607-learners.json", learners)
    shared.run_private([python, "-B", "-c", shared.SEED], shared.CORE,
                       dict(full, CORP607_LEARNERS_FILE=str(folder / "corp607-learners.json")), folder / "core-seed.log")
    _write(local, folder, "corp607-core-runtime.json", core_env)
    _write(local, folder, "corp607-bench-owners.json", {
        "analytics": {"dsn": direct_migrate_url},
        "core": {"dsn": core_dsn.replace("postgresql+psycopg://", "postgresql://", 1)},
    })
    _write(local, folder, "corp607-schema.json", {"analytics_schema": revision})
    _write(local, folder, "prepared.json", dict(version=1, instance_id=str(instance), database=analytics_name,
        schema_revision="a014", history_sha256=bootstrap_runtime.schema_history("a014")[2],
        backfill="NOT_YET_VERIFIED", do_not_drop=True))


def stage_main(argv):
    cli = argparse.ArgumentParser(description="Provision one fresh owned benchmark target and seed its synthetic cohort.")
    cli.add_argument("--pair", type=UUID, required=True)
    cli.add_argument("--phase", choices=("v2_only", "shadow"), required=True)
    cli.add_argument("--schema", choices=("a015", "a016"), required=True)
    cli.add_argument("--cluster", required=True, type=UUID)
    args = cli.parse_args(argv)
    if os.environ.get("ANALYTICS_BENCHMARK_GO") != "1":
        cli.error("explicit ANALYTICS_BENCHMARK_GO=1 and owned resource slot required")
    try:
        folder = local_server._instance_dir(str(args.pair))
        template = json.loads(local_server._read(folder, "windows-benchmark.template.json", 131_072))
        if template["owner"] != "corp598-607-benchmark" or template["pair"] != str(args.pair):
            raise ValueError("unexpected pair owner")
        instance = UUID(template["targets"][args.phase]["instance"])
        seed = json.loads(local_server._read(folder, "windows-benchmark-core-seed.json", 131_072))
        observed = {row["subject_ref"]: {"email": row["email"], "password": row["password"]} for row in seed.values()}
        if observed != template["learners"] or not 10 <= len(seed) <= 50 or any(
            not row["email"].endswith("@example.test") or not name.startswith("corp607-bench-")
            or UUID(row["subject_ref"]).version != 4 for name, row in seed.items()
        ):
            raise ValueError("seed does not match the synthetic owner roster")
        if args.schema != template.get("schema", "a015"):
            raise ValueError("stage schema differs from the planned pair")
        if args.cluster in {args.pair, instance}:
            raise ValueError("dedicated PostgreSQL must have its own cluster identity")
        stage_target(instance, seed, args.schema, args.cluster)
    except Exception as error:
        cli.error("disposable staging stopped before completion, reconcile owner record: " + type(error).__name__)
    print(json.dumps({"pair": str(args.pair), "phase": args.phase, "instance": str(instance),
                      "synthetic_learners": len(seed), "schema": args.schema}))
    return 0


def release_core_env(core_env, enabled, t0):
    result = dict(core_env)
    for name in ("ANALYTICS_WINDOWS_COHORT", "ANALYTICS_WINDOWS_SCOPE_FILE", "ANALYTICS_WINDOWS_T0"):
        result.pop(name, None)
    flag = "1" if enabled else "0"
    result.update(ANALYTICS_WINDOWS_ALL_ELIGIBLE=flag, ANALYTICS_WINDOWS_INTAKE_ENABLED=flag,
                  ANALYTICS_WINDOWS_RECONCILIATION_ENABLED=flag)
    if enabled:
        result.update(ANALYTICS_WINDOWS_T0=t0,
                      ANALYTICS_WINDOWS_LOCAL_COLLECTOR_ORIGIN="http://127.0.0.1:18042")
    return result


def core_runtime_flags(env):
    return dict(all_eligible=env["ANALYTICS_WINDOWS_ALL_ELIGIBLE"] == "1",
                intake=env["ANALYTICS_WINDOWS_INTAKE_ENABLED"] == "1",
                reconciliation=env["ANALYTICS_WINDOWS_RECONCILIATION_ENABLED"] == "1",
                cohort_configured=bool(env.get("ANALYTICS_WINDOWS_COHORT")),
                scope_file_configured=bool(env.get("ANALYTICS_WINDOWS_SCOPE_FILE")),
                t0=env.get("ANALYTICS_WINDOWS_T0"))


def analytics_runtime_flags(settings):
    return dict(all_eligible=settings.windows_all_eligible, intake=settings.windows_intake_enabled,
                reconciliation=settings.windows_reconciliation_enabled, cohort_size=len(settings.windows_cohort),
                t0=settings.windows_t0.isoformat() if settings.windows_t0 else None)


def serve_main(argv):
    import local_personal_runtime as runtime
    import shadow_607_target as shared
    from pymentor_analytics.app.config import Settings

    cli = argparse.ArgumentParser(description="Serve one component of a previously staged benchmark target.")
    cli.add_argument("--pair", type=UUID, required=True)
    cli.add_argument("--phase", choices=("v2_only", "shadow"), required=True)
    cli.add_argument("--component", choices=("core", "analytics", "authority", "catalog"), required=True)
    args = cli.parse_args(argv)
    if os.environ.get("ANALYTICS_BENCHMARK_GO") != "1":
        cli.error("explicit ANALYTICS_BENCHMARK_GO=1 and owned resource slot required")
    try:
        pair_folder = local_server._instance_dir(str(args.pair))
        template = json.loads(local_server._read(pair_folder, "windows-benchmark.template.json", 131_072))
        if template["owner"] != "corp598-607-benchmark" or args.pair != UUID(template["pair"]):
            raise ValueError("unexpected pair owner")
        target = template["targets"][args.phase]
        instance = UUID(target["instance"])
        local, folder, _, configs = runtime.read_settings(str(instance))
        record = phase_cluster(local, folder)
        revision = json.loads(local._read(folder, "corp607-schema.json", 4096))["analytics_schema"]
        if revision not in {"a015", "a016"} or revision != template.get("schema", "a015"):
            raise ValueError("runtime schema differs from the staged pair")
        release_mode = revision == "a016"
        if args.component == "catalog":
            core_env = json.loads(local._read(folder, "corp607-core-runtime.json", 16_384))
            for index, course in enumerate(template["courses"]):
                command = [str(shared.CORE_PYTHON), "-B", "-m", "pymentor_core.app.analytics_catalog",
                           "--course-id", course["course_id"]]
                shared.run_private(command, shared.CORE, {**shared.base_env(), **core_env},
                                   folder / f"bench-catalog-{index}-{os.getpid()}.log")
                print(json.dumps({"course": course["course_id"], "status": "accepted"}), flush=True)
            return 0
        shadow = args.phase == "shadow"
        if args.component == "authority" or (not release_mode and not shadow and args.component == "core"):
            return shared.serve_or_publish(instance, args.component, "baseline", None)
        if not release_mode:
            scopes_path = local._file(pair_folder, "windows-benchmark-scope.json")
            scope = json.loads(local._read(pair_folder, scopes_path.name, 131_072))
            if set(scope) != set(template["learners"]) or not 10 <= len(scope) <= 50:
                raise ValueError("shadow scope and staged learner roster differ")
        t0 = target["t0"]
        if args.component == "core":
            core_env = json.loads(local._read(folder, "corp607-core-runtime.json", 16_384))
            if release_mode:
                core_env = release_core_env(core_env, shadow, t0)
                _write(local, folder, "benchmark-r1-core-flags.json", core_runtime_flags(core_env))
            else:
                core_env.update(ANALYTICS_WINDOWS_INTAKE_ENABLED="1", ANALYTICS_WINDOWS_RECONCILIATION_ENABLED="1",
                                ANALYTICS_WINDOWS_COHORT=",".join(scope), ANALYTICS_WINDOWS_T0=t0,
                                ANALYTICS_WINDOWS_SCOPE_FILE=str(scopes_path),
                                ANALYTICS_WINDOWS_LOCAL_COLLECTOR_ORIGIN="http://127.0.0.1:18042")
            command = [str(shared.CORE_PYTHON), "-B", "-m", "uvicorn", "pymentor_core.app.asgi:create_app",
                       "--factory", "--host", "127.0.0.1", "--port", "18041", "--no-access-log"]
            with (folder / f"bench-core-{os.getpid()}.log").open("xb") as log:
                child = subprocess.Popen(command, cwd=shared.CORE, env={**shared.base_env(), **core_env},
                                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                try:
                    print(json.dumps({"phase": args.phase, "component": "core", "pid": child.pid}), flush=True)
                    return child.wait()
                finally:
                    if child.poll() is None:
                        child.terminate()
                        child.wait(timeout=10)
        database = local_live.database_name(str(instance))
        profile = base.profile_load(base.ROOT / "bench/profile-v1.json")
        for role, original in configs.items():
            settings = original.model_dump()
            settings.update(pool_size=profile["pool_per_process"], pool_overflow=profile["pool_overflow"])
            settings["database_url"] = bind_pg_url(
                original.database_url.get_secret_value(), record["port"], database, f"{database}_{role}", analytics=True)
            if original.core_deletion_status_database_url is not None:
                settings["core_deletion_status_database_url"] = bind_pg_url(
                    original.core_deletion_status_database_url.get_secret_value(), record["port"], database,
                    f"{database}_transfer_status", analytics=True)
            if release_mode:
                settings.update(windows_intake_enabled=shadow, windows_reconciliation_enabled=shadow,
                                windows_all_eligible=shadow, windows_cohort=frozenset(),
                                windows_t0=t0 if shadow else None)
            elif shadow:
                settings.update(windows_intake_enabled=True, windows_reconciliation_enabled=True,
                                windows_cohort=frozenset(scope), windows_t0=t0)
            configs[role] = Settings.model_validate(settings)
        if release_mode:
            flags = {role: analytics_runtime_flags(configs[role]) for role in ("collector", "processor")}
            _write(local, folder, "benchmark-r1-analytics-flags.json", flags)
        asyncio.run(runtime._serve_all(str(instance), local, folder, configs))
        return 0
    except Exception as error:
        cli.error("owned component stopped: " + type(error).__name__)


def capture_main(argv):
    cli = argparse.ArgumentParser(description="Capture an owned 607 runtime into a private phase manifest.")
    cli.add_argument("--pair", type=UUID, required=True)
    cli.add_argument("--phase", choices=("v2_only", "shadow"), required=True)
    cli.add_argument("--builds", required=True, type=Path)
    cli.add_argument("--core-pid", type=int, required=True)
    cli.add_argument("--analytics-pid", type=int, required=True)
    cli.add_argument("--authority-pid", type=int, required=True)
    cli.add_argument("--worker-pid", type=int)
    cli.add_argument("--port-start", type=int, default=18041)
    cli.add_argument("--lock-probe", nargs=2, metavar=("PG_PID", "UTC_TIME"))
    cli.add_argument("--generation", type=int, default=1, choices=range(1, 1000), metavar="1..999")
    args = cli.parse_args(argv)
    try:
        folder = local_server._instance_dir(str(args.pair))
        record = json.loads(local_server._read(folder, "windows-benchmark.template.json", 131_072))
        phase = record["targets"][args.phase]
        target = local_server._instance_dir(phase["instance"])
        if record["owner"] != "corp598-607-benchmark" or record["pair"] != str(args.pair):
            raise ValueError("wrong benchmark owner")
        pg = phase_cluster(local_server, target)
        if record.get("schema") == "a016":
            core_flags = json.loads(local_server._read(target, "benchmark-r1-core-flags.json", 4096))
            analytics_flags = json.loads(local_server._read(target, "benchmark-r1-analytics-flags.json", 4096))
            shadow = args.phase == "shadow"
            expected = dict(all_eligible=shadow, intake=shadow, reconciliation=shadow, t0=phase["t0"] if shadow else None)
            if (core_flags != dict(expected, cohort_configured=False, scope_file_configured=False)
                    or analytics_flags != dict.fromkeys(("collector", "processor"), dict(expected, cohort_size=0))):
                raise ValueError("launched benchmark flags differ from R1 phase")
            phase["runtime_flags"] = {"core": core_flags, **analytics_flags}
        owners = json.loads(local_server._read(target, "corp607-bench-owners.json", 16_384))
        settings = json.loads(local_server._read(target, "role-settings.json", 131_072))
        trust = settings["roles"]["collector"]["trust_manifest_json"]
        pid_by_role = dict(core=args.core_pid, authority=args.authority_pid,
                           **dict.fromkeys(ANALYTICS_ROLES, args.analytics_pid))
        import httpx
        ports = dict(zip(LISTENERS, range(args.port_start, args.port_start + len(LISTENERS)), strict=True))
        with httpx.Client(timeout=5, trust_env=False) as client:
            pools = {role: measured_pool(client.get(f"http://127.0.0.1:{ports[role]}/metrics").raise_for_status().text)
                     for role in ANALYTICS_ROLES}
        process_ids = set(pid_by_role.values()) | {pg["relay_pid"]}
        if args.worker_pid is not None:
            process_ids.add(args.worker_pid)
        phase.update(analytics=owners["analytics"], core=owners["core"], issuer=json.loads(trust)["grant"]["issuer"],
                     ports=ports, analytics_pool_by_role=pools,
                     listener_pids=pid_by_role, processes={str(pid): process_info(pid)[0] for pid in process_ids},
                     trust_fingerprint=load_manifest(SecretStr(trust))[2])
        if args.lock_probe:
            phase["lock_probe"] = dict(pid=int(args.lock_probe[0]), at=datetime.fromisoformat(args.lock_probe[1]).isoformat())
        record["postgres"].update(id=pg["id"], project=pg["project"], owner=pg["owner"], port=pg["port"],
                                  relay_pid=pg["relay_pid"])
        record["versions"] = json.loads(args.builds.read_text(encoding="utf-8-sig"))
        suffix = "" if args.generation == 1 else f"-{args.generation}"
        _write(local_server, folder, f"windows-benchmark-{args.phase}{suffix}.json", record)
    except Exception as error:
        cli.error("private phase capture failed: " + type(error).__name__)
    print(json.dumps({"pair": str(args.pair), "phase": args.phase, "manifest_created": True}))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "capture":
        return capture_main(argv[1:])
    if argv and argv[0] == "stage":
        return stage_main(argv[1:])
    if argv and argv[0] == "serve":
        return serve_main(argv[1:])
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--pair", type=UUID, required=True)
    cli.add_argument("--anchor", type=datetime.fromisoformat, required=True)
    cli.add_argument("--stage", type=int, choices=base.STAGES, required=True)
    cli.add_argument("--mode", choices=("full", "smoke"), required=True)
    cli.add_argument("--schema", choices=("a015", "a016"), default="a015")
    cli.add_argument("--v2-instance", type=UUID, required=True)
    cli.add_argument("--shadow-instance", type=UUID, required=True)
    cli.add_argument("--course", nargs=3, action="append", required=True,
                     metavar=("COURSE_ID", "CATALOG_VERSION", "LESSON_ID"))
    args = cli.parse_args(argv)
    ids = (args.pair, args.v2_instance, args.shadow_instance)
    if len(set(ids)) != 3 or any(value.version != 4 for value in ids):
        cli.error("use three distinct UUIDv4 identities")
    if len(args.course) != 2 or len({course[0] for course in args.course}) != 2:
        cli.error("supply the exact receipt coordinates of two distinct published courses")
    plan = Plan(base.profile_load(base.ROOT / "bench/profile-v1.json"), args.stage, args.pair, args.anchor,
                args.mode, [Course(*row) for row in args.course])
    learners, core_seed, scope = fixture_inputs(plan, lambda: secrets.token_urlsafe(24))
    manifest = dict(
        owner="corp598-607-benchmark", pair=str(args.pair), learners=learners, schema=args.schema,
        courses=[dict(zip(("course_id", "catalog_version", "lesson_id"), row, strict=True)) for row in args.course],
        postgres=dict(context="desktop-linux", port=57392, id=None, project=None),
        versions=dict.fromkeys(("web", "core", "collector", "query", "worker", "privacy", "schema")),
        targets={
            mode: dict(instance=str(instance), t0=args.anchor.isoformat(), analytics={"dsn": None}, core={"dsn": None},
                       issuer=None, ports=None, listener_pids=None, processes=None, trust_fingerprint=None)
            for mode, instance in zip(("v2_only", "shadow"), ids[1:], strict=True)
        },
    )
    local, folder, _ = prepare_folder(str(args.pair))
    _write(local, folder, "windows-benchmark.template.json", manifest)
    _write(local, folder, "windows-benchmark-core-seed.json", core_seed)
    if args.schema == "a015":
        _write(local, folder, "windows-benchmark-scope.json", scope)
    print(json.dumps({"pair": str(args.pair), "profiles": len(learners), "schema": args.schema,
                      "files_created": 3 if args.schema == "a015" else 2}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

