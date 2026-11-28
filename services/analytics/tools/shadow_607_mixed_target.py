"""Run a version-specific 607.C component against an allocated synthetic run."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import secrets
import subprocess
import sys
from uuid import UUID, uuid4

from psycopg import Error as PsycopgError

import shadow_607_target as target
from shadow_607_mixed_loopback import ALLOWED_OFFSET, install

FROZEN = Path("C:/Temp/corp598-399df854-baseline-01a21e19")
BASE_SHA = "399df8548093609eaf168441be8679c8b6d80b37"
CORE_ORIGIN = "http://127.0.0.1:18041"
COLLECTOR_ORIGIN = "http://127.0.0.1:18042"
QUERY_ORIGIN = "http://127.0.0.1:18043"
WEB_PORT = 52707
TOOLS = Path(__file__).resolve().parent
ROLE_PORTS = dict(target.runtime.ROLES)
MAPPING_ENTRY = """
import os
import sys
sys.path.insert(0, os.environ["CORP607_MIXED_TOOLS"])
from shadow_607_mixed_loopback import install
offset = int(os.environ["CORP607_MIXED_OFFSET"])
install(offset)
if os.environ.get("CORP607_MIXED_LOCAL_KEY_ROOT"):
    from pathlib import Path
    from pymentor_core.modules.analytics_delivery import local_source_key
    local_source_key._SERVICE = Path(os.environ["CORP607_MIXED_LOCAL_KEY_ROOT"])
if os.environ.get("CORP607_MIXED_SPA_DIST"):
    from pathlib import Path
    from pymentor_core.app import web
    web.DIST_DIR = Path(os.environ["CORP607_MIXED_SPA_DIST"])
"""
AUTHORITY_RUN = """
from pymentor_analytics.adapters.privacy_authority import local_server
import uvicorn
app = local_server.load_application(os.environ["CORP607_MIXED_RUN"])
uvicorn.run(app, host="127.0.0.1", port=18046 + offset, http=local_server.BoundedFixtureProtocol,
            ws="none", backlog=16, limit_concurrency=local_server.MAX_CONNECTIONS,
            timeout_keep_alive=2, h11_max_incomplete_event_size=8192,
            proxy_headers=False, access_log=False, log_level="critical", log_config=None)
"""
CATALOG_RUN = """
import runpy
sys.argv = ["pymentor_core.app.analytics_catalog", "--course-id", "backend"]
runpy.run_module("pymentor_core.app.analytics_catalog", run_name="__main__")
"""
PROTECTED_RUNS = {
    "8ccbbb8f-b2d7-41fb-a710-60ed577e41db",
    "d31b4903-03f7-4194-9aeb-b6b24ab81d61",
    "0cae39d1-83a3-4a90-9785-c376ea5e9f21",
    "320e5a2b-c526-4904-985b-a8a411a54c99",
}


def selected(run):
    if run in PROTECTED_RUNS:
        raise ValueError("607.A and 607.B runs are reserved to their owner")
    local, folder, owner, configs = target.runtime.read_settings(run)
    marker = json.loads(local._read(folder, "corp607-pending.json", 8192))
    if (marker["owner"], marker["instance_id"]) != ("607", run):
        raise ValueError("unexpected target owner")
    analytics = marker["analytics"]
    if analytics != "analytics_live_" + UUID(run).hex:
        raise ValueError("unexpected database alias")
    data = target.bootstrap.config(target.CONNECTION)
    with target.connect_owned(data, analytics) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            identity = target.observe_identity(conn, analytics, marker["analytics_owner"], marker["sentinel"]["analytics"])
            revision, = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    if revision not in {"a014", "a015"}:
        raise ValueError("unexpected analytics schema")
    learners = json.loads(local._read(folder, "corp607-learners.json", 16384))
    extras = local._file(folder, "corp607-mixed-learners.json", required=False)
    if extras.exists():
        learners.update(json.loads(local._read(folder, extras.name, 16384)))
    return local, folder, owner, configs, data, identity, revision, learners


def no_listener(*ports):
    for port in ports:
        with socket.socket() as conn:
            conn.settimeout(0.3)
            if conn.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError("required loopback port is occupied")


def open_state(run, fixture):
    _, _, _, _, data, identity, revision, learners = selected(run)
    subject = UUID(learners[fixture]["subject_ref"])
    with target.connect_owned(data, identity["database"]) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            facts = conn.execute("""
                SELECT event_id, course_id, lesson_id, started_at,
                       accepted_event #>> '{event,ended_at}',
                       accepted_event #>> '{event,section}',
                       (accepted_event #>> '{event,active_ms}')::integer
                FROM analytics.activity_facts WHERE subject_ref = %s
                ORDER BY started_at, event_id LIMIT 257
            """, (subject,)).fetchall()
            table, = conn.execute("SELECT to_regclass('analytics.student_activity_windows')").fetchone()
            if bool(table) != (revision == "a015") or len(facts) > 256:
                raise ValueError("unexpected subject evidence")
            if table:
                windows = conn.execute("""
                    SELECT activity_date, slot_no, stats, revision
                    FROM analytics.student_activity_windows WHERE student_id = %s
                    ORDER BY activity_date, slot_no LIMIT 97
                """, (subject,)).fetchall()
                control = conn.execute("""
                    SELECT activity_date, slot_no, phase, source_generation, reconciled_generation,
                           server_epoch, revision, coverage_reasons
                    FROM analytics.student_activity_window_control WHERE student_id = %s
                    ORDER BY activity_date, slot_no LIMIT 97
                """, (subject,)).fetchall()
                if len(windows) > 96 or len(control) > 96:
                    raise ValueError("unexpected projection evidence")
            else:
                windows, control = [], []
    return {
        "run": run, "database": identity["database"], "postgres": identity["postgres"],
        "schema": revision, "subject_ref": str(subject), "window_table": bool(table),
        "facts": [dict(zip(
            ("event_id", "course_id", "lesson_id", "started_at", "ended_at", "section", "active_ms"), row, strict=True,
        )) for row in facts],
        "windows": [dict(zip(("activity_date", "slot_no", "stats", "revision"), row, strict=True)) for row in windows],
        "control": [dict(zip(
            ("activity_date", "slot_no", "phase", "source_generation", "reconciled_generation", "server_epoch",
             "revision", "coverage_reasons"), row, strict=True,
        )) for row in control],
    }


OLD_ANALYTICS = """
import asyncio
import os
from pathlib import Path
import sys
base = Path(os.environ["CORP607_MIXED_SOURCE"])
sys.path.insert(0, str(base / "tools"))
from pymentor_analytics.app import factory
from pymentor_analytics.adapters.privacy_authority import local_server
import local_personal_runtime as runtime
assert Path(factory.__file__).resolve().is_relative_to(base.resolve())
runtime.ROLES = {key: port + offset for key, port in runtime.ROLES.items()}
local_server.LOCAL_ROOT = Path(os.environ["CORP607_MIXED_LOCAL_ROOT"])
local_server.SERVICE = local_server.LOCAL_ROOT.parents[1]
run = os.environ["CORP607_MIXED_RUN"]
local, folder, _, settings = runtime.read_settings(run)
asyncio.run(runtime._serve_all(run, local, folder, settings))
"""

CORE_RUN = """
import os
from pathlib import Path
import uvicorn
from pymentor_core.app import asgi
assert Path(asgi.__file__).resolve().is_relative_to(Path(os.environ["CORP607_MIXED_SOURCE"]).resolve())
uvicorn.run(asgi.create_app(), host="127.0.0.1", port=18041 + offset, access_log=False)
"""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed_extra(run):
    local, folder, _, _, _, _, revision, _ = selected(run)
    if revision != "a015":
        raise ValueError("extra learners require the dedicated a015 fixture")
    samples = {name: {
        "subject_ref": str(uuid4()), "email": f"corp607-{name.replace('_', '-')}@example.test",
        "password": secrets.token_urlsafe(24),
    } for name in ("rollback_retry", "current_final", "old_web_final")}
    file = "corp607-mixed-learners.json"
    target.live._write(local, folder, file, samples)
    env = json.loads(local._read(folder, "corp607-core-runtime.json", 16384))
    env["CORP607_LEARNERS_FILE"] = str(folder / file)
    target.run_private([str(target.CORE_PYTHON), "-B", "-c", target.SEED], target.CORE,
                       {**target.base_env(), **env}, folder / "mixed-seed-extra.log")
    print(json.dumps({"run": run, "learners": sorted(samples)}))

def clock_files(args, local, folder):
    values = {}
    for filename, setting in ((args.windows_clock_file, "CORP607_WINDOWS_GRANT_CLOCK_FILE"),
                              (args.v2_clock_file, "CORP607_V2_GRANT_CLOCK_FILE")):
        if filename is None:
            continue
        checked = filename.resolve(strict=True)
        if checked.parent != folder.resolve(strict=True):
            raise ValueError("clock path must be inside the synthetic owner folder")
        local._file(folder, checked.name)
        data = json.loads(local._read(folder, checked.name, 256))
        if set(data) != {"at"} or datetime.fromisoformat(data["at"]).utcoffset() != timezone.utc.utcoffset(None):
            raise ValueError("clock file must contain only an aware UTC at field")
        values[setting] = str(checked)
    return values


def mapped_port_env(args):
    return {"CORP607_MIXED_OFFSET": str(args.offset), "CORP607_MIXED_TOOLS": str(TOOLS),
            "CORP607_MIXED_RUN": args.run}


def run_component(args):
    local, folder, _, configs, _, database, revision, learners = selected(args.run)
    clock_env = clock_files(args, local, folder)
    if clock_env and not args.clock:
        raise ValueError("controlled clock files require --clock")
    cohort_names = tuple(args.cohort.split(",")) if args.cohort else ()
    scope = {
        learners[name]["subject_ref"]: {
            "course_ids": ["backend"],
            "tool_scopes": [{"tool_id": "catalog", "course_id": None}] if name == args.catalog_tool_for else [],
        } for name in cohort_names
    }
    frozen_server = args.server == "frozen"
    intake = args.server == "current-on"
    if intake and (not scope or revision != "a015"):
        raise ValueError("enabled intake requires explicitly scoped a015 runtime")
    if (bool(scope) != bool(args.t0) or args.proof and (not intake or args.component != "analytics")
            or args.clock and (args.component != "core" or frozen_server or (not intake and not args.v2_clock_file))
            or args.catalog_tool_for and (not intake or args.component != "core" or args.catalog_tool_for not in cohort_names)):
        raise ValueError("invalid mixed phase options")
    if args.t0 and (args.t0.utcoffset() is None or args.t0 > datetime.now(timezone.utc)):
        raise ValueError("cohort T0 must be UTC-aware and already reached")
    if args.clock and args.clock.utcoffset() is None:
        raise ValueError("grant clock requires an aware timestamp")
    if args.component == "web":
        workspace = (FROZEN if args.web == "frozen" else target.REPOSITORY) / "apps/web"
        no_listener(WEB_PORT + args.offset)
        flags = {
            "PYMENTOR_DEV_API_TARGET": f"http://127.0.0.1:{18041 + args.offset}",
            "PYMENTOR_DEV_ANALYTICS_COLLECTOR_TARGET": f"http://127.0.0.1:{18042 + args.offset}",
            "PYMENTOR_DEV_ANALYTICS_QUERY_TARGET": f"http://127.0.0.1:{18043 + args.offset}",
            "CORP607_MIXED_WEB_PORT": str(WEB_PORT + args.offset),
            "VITE_CACHE_DIR": str(workspace / ".cache" / ("corp607-mixed-" + args.run)),
            "VITE_ANALYTICS_TRACKER_ENABLED": "true",
            "VITE_ANALYTICS_LEGACY_VISIT_CUTOVER": "false",
            "VITE_ANALYTICS_TOOLS_ENABLED": "false",
            "VITE_ANALYTICS_REPORTS_ENABLED": "true",
            "VITE_ANALYTICS_PERSONAL_MEASUREMENTS_ENABLED": "true",
            "VITE_ANALYTICS_WINDOWS_ENABLED": str(args.web == "on").lower(),
        }
        if args.web_dist:
            dist = args.web_dist.resolve(strict=True)
            if not dist.is_relative_to(workspace.resolve()) or not (dist / "index.html").is_file():
                raise ValueError("provide an actual Web build under the selected worktree")
            flags["CORP607_MIXED_WEB_DIST"] = str(dist)
        command = ["node", str(Path(__file__).with_name("shadow_607_mixed_vite.mjs"))]
        source = workspace / "src/features/analytics-tracker/controller.ts"
        return spawn(args, folder, database, source, command, workspace, flags)
    if args.component == "authority":
        no_listener(18046 + args.offset)
        workspace = target.ANALYTICS
        command = [sys.executable, "-B", "-c", MAPPING_ENTRY + AUTHORITY_RUN]
        source = workspace / "pymentor_analytics/adapters/privacy_authority/local_server.py"
        return spawn(args, folder, database, source, command, workspace, {"PYTHONPATH": str(workspace)})
    if args.component in {"core", "catalog"}:
        if args.component == "core":
            no_listener(18041 + args.offset)
        workspace = FROZEN / "services/core-api" if frozen_server else target.CORE
        env = json.loads(local._read(folder, "corp607-core-runtime.json", 16384))
        env.update(PYTHONPATH=str(workspace), PYMENTOR_COURSES_ROOT=str(workspace.parents[1] / "courses"),
                   CORP607_MIXED_SOURCE=str(workspace))
        if frozen_server:
            env["CORP607_MIXED_LOCAL_KEY_ROOT"] = str(target.ANALYTICS)
        if intake:
            scope_name = "corp607-mixed-scope-" + hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()[:12] + ".json"
            scope_file = local._file(folder, scope_name, required=False)
            if not scope_file.exists():
                target.live._write(local, folder, scope_name, scope)
            if json.loads(local._read(folder, scope_name, 16384)) != scope:
                raise ValueError("unexpected stored scope")
            env.update(ANALYTICS_WINDOWS_INTAKE_ENABLED="1", ANALYTICS_WINDOWS_RECONCILIATION_ENABLED="0",
                       ANALYTICS_WINDOWS_COHORT=",".join(scope), ANALYTICS_WINDOWS_T0=args.t0.isoformat(),
                       ANALYTICS_WINDOWS_SCOPE_FILE=str(scope_file),
                       ANALYTICS_WINDOWS_LOCAL_COLLECTOR_ORIGIN=COLLECTOR_ORIGIN)
        if args.spa_dist:
            dist = args.spa_dist.resolve(strict=True)
            source_web = (workspace.parents[1] / "apps/web").resolve(strict=True)
            if (args.component != "core" or not dist.is_relative_to(source_web)
                    or not (dist / "index.html").is_file() or not (dist / "assets").is_dir()):
                raise ValueError("provide an actual dist under the selected Web worktree")
            env["CORP607_MIXED_SPA_DIST"] = str(dist)
        entrypoint = CATALOG_RUN if args.component == "catalog" else CORE_RUN
        if args.clock:
            env.update(clock_env, CORP607_VIRTUAL_GRANT_CLOCK=args.clock.isoformat())
            entrypoint = target.CONTROLLED_CORE.replace("port=18041,", "port=18041 + offset,")
        source = workspace / "pymentor_core/app/factory.py"
        return spawn(args, folder, database, source, [str(target.CORE_PYTHON), "-B", "-c", MAPPING_ENTRY + entrypoint], workspace, env)

    no_listener(*target.runtime.ROLES.values())
    if frozen_server:
        workspace = FROZEN / "services/analytics"
        env = {"PYTHONPATH": str(workspace), "CORP607_MIXED_SOURCE": str(workspace),
               "CORP607_MIXED_LOCAL_ROOT": str(folder.parent), "CORP607_MIXED_RUN": args.run}
        source = workspace / "pymentor_analytics/app/factory.py"
        return spawn(args, folder, database, source, [sys.executable, "-B", "-c", MAPPING_ENTRY + OLD_ANALYTICS], workspace, env)
    if scope:
        overlay = {"windows_cohort": frozenset(scope), "windows_t0": args.t0,
                   "windows_intake_enabled": intake, "windows_reconciliation_enabled": bool(args.proof)}
        configs["collector"] = target.Settings.model_validate({**configs["collector"].model_dump(), **overlay})
        if args.proof:
            from pymentor_analytics.modules.analytics.activity.student_windows_evidence import load_trusted_evidence
            proof = args.proof.resolve(strict=True)
            load_trusted_evidence(proof, cohort=tuple(map(UUID, scope)), cohort_t0=args.t0)
            configs["processor"] = target.Settings.model_validate({
                **configs["processor"].model_dump(), **overlay, "windows_reconciliation_proof_file": proof,
            })
    report_identity(args, database, target.ANALYTICS / "pymentor_analytics/app/factory.py", os.getpid())
    install(args.offset)
    asyncio.run(target.runtime._serve_all(args.run, local, folder, configs))
    return 0


def report_identity(args, database, source, pid):
    print(json.dumps({
        "run": args.run, "database": database["database"], "component": args.component,
        "server": args.server, "web": args.web, "baseline_commit": BASE_SHA, "physical_port_offset": args.offset,
        "pid": pid, "source": str(source), "source_sha256": digest(source),
        "reconciliation_proof": digest(args.proof) if args.proof else None,
    }), flush=True)


def spawn(args, folder, database, source, command, workspace, env):
    log = folder / f"mixed-{args.component}-{os.getpid()}.log"
    with log.open("xb") as output:
        child = subprocess.Popen(command, cwd=workspace, env={**target.base_env(), **mapped_port_env(args), **env},
                                 stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            report_identity(args, database, source, child.pid)
            return child.wait()
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("observe", "serve", "stop-analytics", "seed-extra"))
    parser.add_argument("--run", required=True, type=lambda value: str(UUID(value)))
    parser.add_argument("--learner")
    parser.add_argument("--component", choices=("core", "analytics", "authority", "web", "catalog"))
    parser.add_argument("--server", choices=("frozen", "current-off", "current-on"), default="current-off")
    parser.add_argument("--web", choices=("frozen", "off", "on"), default="off")
    parser.add_argument("--cohort")
    parser.add_argument("--t0", type=datetime.fromisoformat)
    parser.add_argument("--clock", type=datetime.fromisoformat)
    parser.add_argument("--proof", type=Path)
    parser.add_argument("--catalog-tool-for", choices=("multi",))
    parser.add_argument("--offset", type=int, choices=(0, ALLOWED_OFFSET), default=0)
    parser.add_argument("--v2-clock-file", type=Path)
    parser.add_argument("--windows-clock-file", type=Path)
    parser.add_argument("--spa-dist", type=Path)
    parser.add_argument("--web-dist", type=Path)
    args = parser.parse_args()
    target.runtime.ROLES = {role: port + args.offset for role, port in ROLE_PORTS.items()}
    try:
        if args.action == "seed-extra":
            seed_extra(args.run)
        elif args.action == "observe" and args.learner:
            print(json.dumps(open_state(args.run, args.learner), default=str))
        elif args.action == "stop-analytics":
            selected(args.run)
            target.runtime.request_stop(args.run)
        elif args.action == "serve" and args.component:
            return run_component(args)
        else:
            parser.error("provide a component for serve or a learner for observe")
    except (OSError, RuntimeError, ValueError, KeyError, PsycopgError) as error:
        raise SystemExit(f"CORP-607 mixed target {args.action} failed for {args.run}: {type(error).__name__}") from None


if __name__ == "__main__":
    raise SystemExit(main())
