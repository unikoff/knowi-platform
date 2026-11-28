"""Prepare one disposable CORP-607 runtime with canonical Core and Analytics schemas."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import quote
from uuid import UUID

from psycopg import sql

import bootstrap_postgres as bootstrap
import local_personal_live as live
import local_personal_runtime as runtime
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.app.config import Settings


ANALYTICS = Path(__file__).resolve().parents[1]
REPOSITORY = ANALYTICS.parents[1]
CORE = ANALYTICS.parent / "core-api"
CONNECTION = Path("C:/Project Python/Pymentor_platform/services/analytics/.local/postgres-connection.json")
CORE_PYTHON = Path("C:/Project Python/Pymentor_platform/services/core-api/.venv/Scripts/python.exe")
FIXTURE = ANALYTICS / "tests/student_windows/fixtures/corp_607_expected.json"
REDIS_URL = "redis://127.0.0.1:63707/0"


def connect_owned(data, name):
    import psycopg
    return psycopg.connect(
        host=data["host"], port=data["port"], user=data["user"], password=data["password"],
        dbname=name, connect_timeout=5, autocommit=True,
        options="-c statement_timeout=5000 -c lock_timeout=1000",
    )


def provision(data, name, owner, passwords, sentinel, *, core=False, bootstrap_connect=bootstrap.connect):
    with bootstrap_connect(data) as conn:
        if conn.execute("SELECT current_database(),current_user,pg_is_in_recovery()").fetchone() != (
            "analytics_bootstrap", "analytics_bootstrap", False
        ):
            raise ValueError("incorrect synthetic PostgreSQL instance")
        if conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
            raise ValueError("potentially existing database; reconcile before retry")
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (list(passwords),)).fetchone():
            raise ValueError("potentially existing role; reconcile before retry")
        for role, password in passwords.items():
            verifier = live._password_verifier(conn, role, password)
            conn.execute(sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
            ).format(sql.Identifier(role), sql.Literal(verifier)))
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(owner)))
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
        for role in passwords:
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(name), sql.Identifier(role)))

    with connect_owned(data, name) as conn:
        conn.execute("CREATE TABLE public.corp607_sentinel (token text NOT NULL)")
        conn.execute("INSERT INTO public.corp607_sentinel VALUES (%s)", (sentinel,))
        conn.execute("REVOKE ALL ON public.corp607_sentinel FROM PUBLIC")
        conn.execute(sql.SQL("GRANT SELECT ON public.corp607_sentinel TO {}").format(sql.Identifier(owner)))
        if core:
            app = sql.Identifier(name + "_app")
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(app))
            conn.execute(sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
            ).format(sql.Identifier(owner), app))
            conn.execute(sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT USAGE ON SEQUENCES TO {}"
            ).format(sql.Identifier(owner), app))
        return observe_identity(conn, name, owner, sentinel)


def observe_identity(conn, name, owner, sentinel):
    actual = conn.execute("""
        SELECT current_database(), pg_get_userbyid(datdba), current_setting('server_version'),
               current_setting('fsync'), current_setting('synchronous_commit'),
               current_setting('full_page_writes'), current_setting('max_connections')::integer
        FROM pg_database WHERE datname = current_database()
    """).fetchone()
    if actual is None or actual[:2] != (name, owner):
        raise ValueError("unexpected PostgreSQL database identity")
    if conn.execute("SELECT token FROM public.corp607_sentinel").fetchall() != [(sentinel,)]:
        raise ValueError("CORP-607 sentinel mismatch")
    return dict(zip(("database", "owner", "postgres", "fsync", "synchronous_commit", "full_page_writes",
                     "max_connections"), actual, strict=True))


def dsn(name, role, password, driver):
    return f"postgresql+{driver}://{role}:{quote(password, safe='')}@127.0.0.1:57392/{name}"


def base_env():
    return {name: value for name, value in os.environ.items() if name.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
    }}


def run_private(command, cwd, env, log_path):
    with log_path.open("xb") as log:
        subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log,
                       stderr=subprocess.STDOUT, check=True, timeout=90,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def prepare(instance, analytics_revision):
    data = bootstrap.config(CONNECTION)
    base = ANALYTICS / ".local"
    base.mkdir(exist_ok=True)
    local, folder, owner = live.prepare_folder(str(instance))
    analytics_name = live.database_name(str(instance))
    core_name = "core_607_" + instance.hex
    analytics_owner = analytics_name + "_migrate"
    core_owner = core_name + "_owner"
    core_app = core_name + "_app"
    passwords = {role: secrets.token_urlsafe(32) for role in live.ROLES}
    analytics_passwords = {analytics_name + "_" + role: password for role, password in passwords.items()}
    core_passwords = {role: secrets.token_urlsafe(32) for role in (core_owner, core_app)}
    sentinel = {"analytics": secrets.token_hex(32), "core": secrets.token_hex(32)}
    live._write(local, folder, "corp607-pending.json", {
        "owner": "607", "instance_id": str(instance), "analytics": analytics_name, "core": core_name,
        "analytics_owner": analytics_owner, "core_owner": core_owner, "sentinel": sentinel,
    })
    analytics_identity = provision(data, analytics_name, analytics_owner, analytics_passwords, sentinel["analytics"])
    core_identity = provision(data, core_name, core_owner, core_passwords, sentinel["core"], core=True)

    keys, reader, source_secret = live._pem_keys(), live._secret32(), live._secret32()
    config = live.build_configuration(folder, owner, keys, reader, source_secret, passwords, core_name,
                                      projection_revision="a014")
    live._write_config_files(local, folder, owner, keys, reader, source_secret, passwords, config)
    runtime.read_settings(str(instance), require_prepared=False, projection_revision="a014")
    finish_preparation(instance, data, local, folder, owner, analytics_identity, core_identity,
                       config, core_passwords, analytics_revision=analytics_revision)


def finish_preparation(instance, data, local, folder, owner, analytics_identity, core_identity, config, core_passwords, *, analytics_revision="a014", apply_analytics_schema=True):
    current_schema = None
    with connect_owned(data, analytics_identity["database"]) as conn:
        current_schema, = conn.execute("SELECT to_regclass('public.alembic_version')").fetchone()
    if (current_schema is None) != apply_analytics_schema:
        raise ValueError("reconcile schema before continuing setup")
    core_name, core_owner = core_identity["database"], core_identity["owner"]
    analytics_name = analytics_identity["database"]
    core_app = core_name + "_app"
    if apply_analytics_schema:
        migrate_settings = Settings(**config["shared"], database_url=config["urls"]["migrate"])
        upgrade(migrate_settings, target_revision=analytics_revision)
    if live._db_revision(config["urls"]["migrate"]) != analytics_revision:
        raise ValueError("Analytics migration revision differs from the explicit target")
    live._begin_projection(config["urls"]["migrate"], owner["dataset_run_id"])
    local.initialize(str(instance))

    core_env = {**config["core_env"],
        "DATABASE_URL": dsn(core_name, core_app, core_passwords[core_app], "psycopg"),
        "REDIS_URL": REDIS_URL, "KNOWI_REDIS_PREFIX": "corp598-607:" + instance.hex + ":",
        "KNOWI_LOGIN_RECOVERY_KEY": secrets.token_hex(32), "PYMENTOR_PROFILE": "postgres_redis",
        "PYMENTOR_SCHEMA_MODE": "managed", "PYMENTOR_COURSE_TRANSPORT": "local",
        "PYMENTOR_COURSES_ROOT": str(REPOSITORY / "courses"), "PYMENTOR_COOKIE_SECURE": "0",
        "PYMENTOR_LOCAL_NOTIFICATIONS": "0", "PYMENTOR_EMAIL_TOKEN_SECRET": secrets.token_urlsafe(40),
        "PYMENTOR_ANALYTICS_LEARNER_READS_ENABLED": "1", "PYMENTOR_ANALYTICS_QUERY_URL": "http://127.0.0.1:18043",
        "PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(CORE),
    }
    full_core_env = {**base_env(), **core_env}
    migration_env = {**full_core_env,
        "KNOWI_MIGRATION_URL": dsn(core_name, core_owner, core_passwords[core_owner], "asyncpg"),
        "KNOWI_MIGRATION_DATABASE": core_name, "KNOWI_MIGRATION_USER": core_owner,
        "KNOWI_MIGRATION_HOST": "127.0.0.1", "KNOWI_MIGRATION_OWNER": core_owner,
        "KNOWI_MIGRATION_APP_ROLE": core_app,
    }
    run_private([str(CORE_PYTHON), "-B", "-m", "alembic", "-c", "alembic-runtime.ini", "upgrade", "head"],
                CORE, migration_env, folder / "core-migrate.log")

    subjects = json.loads(FIXTURE.read_text(encoding="utf-8-sig"))["subjects"]
    learners = {
        name: {"subject_ref": value, "email": "corp607-" + name.replace("_", "-") + "@example.test",
               "password": secrets.token_urlsafe(24)}
        for name, value in subjects.items()
    }
    live._write(local, folder, "corp607-learners.json", learners)
    seed_env = dict(full_core_env, CORP607_LEARNERS_FILE=str(folder / "corp607-learners.json"))
    run_private([str(CORE_PYTHON), "-B", "-c", SEED], CORE, seed_env, folder / "core-seed.log")
    live._write(local, folder, "corp607-core-runtime.json", core_env)
    history = __import__("bootstrap_runtime").schema_history("a014")
    live._write(local, folder, "prepared.json", {
        "version": 1, "instance_id": str(instance), "database": analytics_name, "schema_revision": "a014",
        "history_sha256": history[2], "backfill": "NOT_YET_VERIFIED", "do_not_drop": True,
    })
    live._write(local, folder, "corp607-schema.json", {"analytics_schema": analytics_revision})
    with connect_owned(data, core_name) as conn:
        core_revision, = conn.execute("SELECT version_num FROM public.alembic_version").fetchone()
    print(json.dumps({
        "instance_id": str(instance), "analytics": {**analytics_identity, "revision": analytics_revision},
        "core": {**core_identity, "revision": core_revision}, "synthetic_subjects": len(learners),
    }))


def serve_or_publish(instance, component, phase, catalog_course):
    local, folder, owner, role_settings = runtime.read_settings(str(instance))
    core_env = json.loads(local._read(folder, "corp607-core-runtime.json", 16384))
    web_flags = {}
    entrypoint = ["-m", "uvicorn", "pymentor_core.app.asgi:create_app", "--factory", "--host", "127.0.0.1",
                  "--port", "18041", "--no-access-log"]
    if phase != "baseline":
        schema = json.loads(local._read(folder, "corp607-schema.json", 4096))
        if schema != {"analytics_schema": "a015"}:
            raise ValueError("shadow requires the separately prepared a015 database")
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8-sig"))
        integration = phase in {"integration", "integration-rerun", "integration-final"}
        names = {
            "first": ("first",),
            "failures": ("multi", "scope", "privacy"),
            "integration": ("first", "multi", "lost", "new_lifecycle"),
            "integration-rerun": ("first", "multi", "lost", "new_lifecycle", "scope"),
            "integration-final": ("first", "multi", "lost", "new_lifecycle", "scope", "privacy", "outside_cohort"),
        }[phase]
        subjects = {fixture["subjects"][name]: {
            "course_ids": ["backend", "backend_copy"] if name == "scope" and phase == "failures" else ["backend"],
            "tool_scopes": [{"tool_id": "catalog", "course_id": None}] if name == "multi" and integration else [],
        } for name in names}
        t0 = "2026-11-26T20:45:00Z" if integration else "2026-11-27T07:00:00Z"
        if component == "core":
            scope_path = local._file(folder, f"corp607-scope-{phase}.json", required=False)
            if not scope_path.exists():
                live._write(local, folder, scope_path.name, subjects)
            core_env.update(ANALYTICS_WINDOWS_INTAKE_ENABLED="1", ANALYTICS_WINDOWS_RECONCILIATION_ENABLED="0",
                ANALYTICS_WINDOWS_COHORT=",".join(subjects), ANALYTICS_WINDOWS_T0=t0,
                ANALYTICS_WINDOWS_SCOPE_FILE=str(scope_path),
                ANALYTICS_WINDOWS_LOCAL_COLLECTOR_ORIGIN="http://127.0.0.1:18042")
            core_env["CORP607_VIRTUAL_GRANT_CLOCK"] = "2026-11-27T06:59:59.500+00:00"
            entrypoint = ["-c", CONTROLLED_CORE]
            if integration:
                for name, variable in (("corp607-v2-clock.json", "CORP607_V2_GRANT_CLOCK_FILE"),
                                       ("corp607-windows-clock.json", "CORP607_WINDOWS_GRANT_CLOCK_FILE")):
                    clock_file = local._file(folder, name, required=False)
                    if not clock_file.exists():
                        live._write(local, folder, name, {"at": core_env["CORP607_VIRTUAL_GRANT_CLOCK"]})
                    core_env[variable] = str(clock_file)
        elif component == "analytics":
            import asyncio
            role_settings["collector"] = Settings.model_validate({
                **role_settings["collector"].model_dump(),
                "windows_intake_enabled": True, "windows_reconciliation_enabled": False,
                "windows_cohort": frozenset(subjects), "windows_t0": t0,
            })
            if integration:
                role_settings["processor"] = Settings.model_validate({
                    **role_settings["processor"].model_dump(),
                    "windows_intake_enabled": True, "windows_reconciliation_enabled": True,
                    "windows_cohort": frozenset(subjects), "windows_t0": t0,
                })
            print(json.dumps({"run": str(instance), "component": "analytics", "pid": os.getpid()}), flush=True)
            asyncio.run(runtime._serve_all(str(instance), local, folder, role_settings))
            return 0
        web_flags["VITE_ANALYTICS_WINDOWS_ENABLED"] = "true"

    commands = {
        "core": ([str(CORE_PYTHON), "-B", *entrypoint], CORE, core_env),
        "catalog": ([str(CORE_PYTHON), "-B", "-m", "pymentor_core.app.analytics_catalog",
                     "--course-id", catalog_course], CORE, core_env),
        "authority": ([sys.executable, "-B", "-m", "pymentor_analytics.adapters.privacy_authority.local_server",
                       "serve", "--instance-id", str(instance)], ANALYTICS, {"PYTHONPATH": str(ANALYTICS)}),
        "analytics": ([sys.executable, "-B", "tools/local_personal_runtime.py", "--instance-id", str(instance),
                       "--serve"], ANALYTICS, {"PYTHONPATH": str(ANALYTICS)}),
        "web": (["node", "node_modules/vite/bin/vite.js", "--config", "config/vite.config.ts",
                 "--host", "127.0.0.1", "--port", "52707", "--strictPort", "--configLoader", "bundle"],
                REPOSITORY / "apps/web", {
                    "PYMENTOR_DEV_API_TARGET": "http://127.0.0.1:18041",
                    "PYMENTOR_DEV_ANALYTICS_COLLECTOR_TARGET": "http://127.0.0.1:18042",
                    "PYMENTOR_DEV_ANALYTICS_QUERY_TARGET": "http://127.0.0.1:18043",
                    "VITE_ANALYTICS_TRACKER_ENABLED": "true",
                    "VITE_ANALYTICS_LEGACY_VISIT_CUTOVER": "false",
                    "VITE_ANALYTICS_TOOLS_ENABLED": "false",
                    "VITE_ANALYTICS_REPORTS_ENABLED": "true",
                    "VITE_ANALYTICS_PERSONAL_MEASUREMENTS_ENABLED": "true",
                    **web_flags,
                }),
    }
    command, workdir, additions = commands[component]
    env = {**base_env(), **additions}
    log_path = folder / f"{component}-{os.getpid()}.log"
    with log_path.open("xb") as log:
        child = subprocess.Popen(command, cwd=workdir, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print(json.dumps({"run": str(instance), "component": component, "pid": child.pid}), flush=True)
        try:
            return child.wait()
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)

 
CONTROLLED_CORE = """
from datetime import datetime, timedelta
import json
import os
from pathlib import Path

import uvicorn
from pymentor_core.app.asgi import create_app
from pymentor_core.modules.analytics_delivery import router as v2_router
from pymentor_core.modules.analytics_delivery import windows_grants

def clock_from_file(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(257)
    if len(raw) > 256:
        raise ValueError("Unexpected acceptance clock size")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.keys() != {"at"}:
        raise ValueError("Expected acceptance clock object with UTC at")
    clock = datetime.fromisoformat(value["at"])
    if clock.utcoffset() != timedelta():
        raise ValueError("Acceptance clock must be UTC")
    return clock

clock = datetime.fromisoformat(os.environ["CORP607_VIRTUAL_GRANT_CLOCK"])
windows_file = os.environ.get("CORP607_WINDOWS_GRANT_CLOCK_FILE")
windows_grants.window_clock = lambda: clock_from_file(windows_file) if windows_file else clock
issue = windows_grants.issue

def issue_with_controlled_browser_clock(*args):
    grant = issue(*args)
    server_time = datetime.fromisoformat(grant["server_time"])
    grant["expires_at"] = (server_time + timedelta(seconds=windows_grants.MAX_GRANT_SECONDS)).isoformat()
    return grant

windows_grants.issue = issue_with_controlled_browser_clock
if v2_file := os.environ.get("CORP607_V2_GRANT_CLOCK_FILE"):
    issue_v2 = v2_router._issue_grant

    def issue_v2_with_clock(signer, claims, purpose):
        grant = issue_v2(signer, claims, purpose)
        if purpose == "collect":
            server_time = clock_from_file(v2_file)
            grant["server_time"] = server_time
            grant["expires_at"] = server_time + timedelta(seconds=300)
        return grant

    v2_router._issue_grant = issue_v2_with_clock

uvicorn.run(create_app(), host="127.0.0.1", port=18041, access_log=False)
"""

SEED = """
import json
import os
from pathlib import Path
from pymentor_core.modules.identity.passwords import hash_password
from pymentor_core.app.database import engine
from sqlalchemy import text
learners = json.loads(Path(os.environ["CORP607_LEARNERS_FILE"]).read_text(encoding="utf-8"))
with engine.begin() as connection:
    database, role = connection.execute(text("SELECT current_database(),current_user")).one()
    if not database.startswith("core_607_") or role != database + "_app":
        raise ValueError("synthetic database required")
    for name, learner in learners.items():
        user_id = connection.execute(text(
            "INSERT INTO users(username,email,password_hash,verified,subscription_until,subscription_plan) "
            "VALUES (:name,:email,:password,true,now()+interval '30 days','premium') RETURNING id"
        ), {"name": name, "email": learner["email"], "password": hash_password(learner["password"])}).scalar_one()
        connection.execute(text("INSERT INTO account_lifecycles(user_id,lifecycle_id) VALUES (:id,:ref)"),
                           {"id": user_id, "ref": learner["subject_ref"]})
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--prepare", type=UUID, metavar="FRESH_RUN_UUID")
    actions.add_argument("--serve", type=UUID, metavar="PREPARED_RUN_UUID")
    parser.add_argument("--component", choices=("core", "catalog", "authority", "analytics", "web"))
    parser.add_argument("--schema", choices=("a014", "a015"), default="a014")
    parser.add_argument("--phase", choices=("baseline", "first", "failures", "integration", "integration-rerun", "integration-final"), default="baseline")
    parser.add_argument("--catalog-course", choices=("backend", "backend_copy"), default="backend")
    args = parser.parse_args()
    try:
        if args.prepare:
            prepare(args.prepare, args.schema)
        elif not args.component:
            parser.error("--serve requires --component")
        else:
            raise SystemExit(serve_or_publish(args.serve, args.component, args.phase, args.catalog_course))
    except Exception as error:
        raise SystemExit(f"CORP-607 failed for {args.prepare or args.serve}: {type(error).__name__}") from None

