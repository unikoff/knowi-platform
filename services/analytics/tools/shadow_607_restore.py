"""CORP-607-В on an isolated Core, Collector, Privacy and signed authority over HTTP."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import jwt
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from psycopg import sql

import shadow_607_failures as wire
import shadow_607_target as target
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
from pymentor_analytics.adapters.privacy_authority.journal import Journal
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.v2 import DeletionFence

CORE_PORT, COLLECTOR_PORT, SOURCE_PRIVACY_PORT, RESTORED_PRIVACY_PORT, AUTHORITY_PORT = 18141, 18142, 18144, 18145, 18146
REDIS_PORT = 63873
TIMES = ZoneInfo("Europe/Moscow")
COURSE = "backend"
CATALOG = "corp607-restore-fixture-v1"
ARCHIVE_TABLES = ("student_activity_window_control", "student_activity_windows",
                  "privacy_state", "privacy_receipts", "subject_fences")
COLLECTOR_ORIGIN = f"http://127.0.0.1:{COLLECTOR_PORT}"
CORE_ORIGIN = f"http://127.0.0.1:{CORE_PORT}"
AUTHORITY_ORIGIN = f"http://127.0.0.1:{AUTHORITY_PORT}"
ANALYTICS = target.ANALYTICS


def preflight():
    for port in (CORE_PORT, COLLECTOR_PORT, SOURCE_PRIVACY_PORT, RESTORED_PRIVACY_PORT, AUTHORITY_PORT, REDIS_PORT):
        with socket.socket() as probe:
            probe.settimeout(.3)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"reserved synthetic port {port} is already occupied")


def docker(*args, env=None, content=None):
    result = subprocess.run(["docker", "--context", "desktop-linux", *args], env=env, input=content, capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError("owned Docker operation failed")
    return result.stdout


@contextmanager
def redis(run):
    name = "corp607-restore-redis-" + run.hex
    role = "knowi.corp607.restore=" + str(run)
    image = docker("image", "inspect", "--format", "{{.Id}}", "redis:7-alpine").decode().strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("exact redis:7-alpine image required")
    identity = docker("run", "--pull=never", "--detach", "--name", name, "--label", role,
                      "--publish", f"127.0.0.1:{REDIS_PORT}:6379", "--memory", "64m",
                      image, "redis-server", "--save", "", "--appendonly", "no").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise RuntimeError("unknown Redis container outcome")
    try:
        for _ in range(50):
            try:
                if docker("exec", identity, "redis-cli", "ping").strip() == b"PONG":
                    break
            except RuntimeError:
                pass
            time.sleep(.1)
        else:
            raise RuntimeError("owned Redis not ready")
        yield f"redis://127.0.0.1:{REDIS_PORT}/0"
    finally:
        inspected = docker("inspect", "--format",
                           '{{.Id}} {{.Name}} {{index .Config.Labels "knowi.corp607.restore"}}', identity)
        if inspected.decode().strip() != f"{identity} /{name} {run}":
            raise RuntimeError("refusing cleanup of unverified Redis container")
        docker("rm", "--force", identity)


@contextmanager
def running(folder, name, cmd, workdir, *, env=None):
    private_log = folder / f"{name}.log"
    with private_log.open("xb") as log:
        child = subprocess.Popen(cmd, cwd=workdir, env={**target.base_env(), **(env or {})},
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print(json.dumps({"phase": "service_start", "service": name, "pid": child.pid}), flush=True)
        try:
            yield child
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=6)


def wait_status(proc, url, expected, *, timeout=18):
    until = time.monotonic() + timeout
    observed = None
    with httpx.Client(timeout=2, trust_env=False) as client:
        while time.monotonic() < until:
            if proc.poll() is not None:
                raise RuntimeError(f"owned service exited before response at {url}")
            try:
                observed = client.get(url).status_code
                if observed == expected:
                    return
            except httpx.TransportError:
                pass
            time.sleep(.1)
    raise RuntimeError(f"readiness failed for {url}: {observed}")


def pg_roles_removed(data, name, roles):
    with target.bootstrap.connect(data) as conn:
        assert conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (roles,)).fetchone() is None
    print(json.dumps({"phase": "cleanup", "database": name, "roles_removed": len(roles)}), flush=True)


@contextmanager
def analytics_target(data):
    name, sentinel, urls = target.bootstrap.create(data, revision="a015")
    try:
        upgrade(Settings(database_url=urls["migrate"]), target_revision="a015")
        yield {"name": name, "sentinel": sentinel, "urls": urls}
    finally:
        target.bootstrap.cleanup(data, name, sentinel, revision="a015")
        pg_roles_removed(data, name, [f"{name}_{role}" for role in target.bootstrap.roles_for_revision("a015")])


@contextmanager
def core_target(data, run):
    name = "core_607_" + run.hex
    owner, app = name + "_owner", name + "_app"
    sentinel = target.secrets.token_hex(32)
    passwords = {role: target.secrets.token_urlsafe(32) for role in (owner, app)}
    target.provision(data, name, owner, passwords, sentinel, core=True)
    try:
        yield name, owner, app, passwords
    finally:
        with target.connect_owned(data, name) as conn:
            target.observe_identity(conn, name, owner, sentinel)
        with target.bootstrap.connect(data) as conn:
            conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            for role in (app, owner):
                conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        pg_roles_removed(data, name, [app, owner])


def scope_env(local, folder, owner, keys, core, redis_url, learners, t0):
    name, migrate_role, app_role, passwords = core
    subjects = [learner["subject_ref"] for learner in learners.values()]
    scope = {subject: {"course_ids": [COURSE], "tool_scopes": []} for subject in subjects}
    target.live._write(local, folder, "restore-scope.json", scope)
    target.live._write(local, folder, "restore-learners.json", learners)
    target.live._write(local, folder, "restore-grant.pem", keys["g1"][0])

    result = {
        "DATABASE_URL": target.dsn(name, app_role, passwords[app_role], "psycopg"),
        "REDIS_URL": redis_url, "KNOWI_REDIS_PREFIX": f"corp607-restore:{owner['instance_id']}:",
        "KNOWI_LOGIN_RECOVERY_KEY": target.secrets.token_hex(32), "PYMENTOR_PROFILE": "postgres_redis",
        "PYMENTOR_SCHEMA_MODE": "managed", "PYMENTOR_COURSE_TRANSPORT": "local",
        "PYMENTOR_COURSES_ROOT": str(target.REPOSITORY / "courses"), "PYMENTOR_COOKIE_SECURE": "0",
        "PYMENTOR_LOCAL_NOTIFICATIONS": "0", "PYMENTOR_EMAIL_TOKEN_SECRET": target.secrets.token_urlsafe(40),
        "PYTHON_DOTENV_DISABLED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(target.CORE),
        "PYMENTOR_ANALYTICS_LOCAL_COLLECTION_ENABLED": "1", "PYMENTOR_ANALYTICS_GRANTS_ENABLED": "1",
        "PYMENTOR_ANALYTICS_CATALOG_METADATA_ENABLED": "1", "PYMENTOR_ANALYTICS_DELIVERY_ENABLED": "0",
        "PYMENTOR_ANALYTICS_LOCAL_EXPECTED_DATABASE": name,
        "PYMENTOR_ANALYTICS_LOCAL_INSTANCE_ID": owner["core_instance_id"],
        "PYMENTOR_ANALYTICS_LOCAL_DATASET_ID": owner["instance_id"],
        "PYMENTOR_ANALYTICS_LOCAL_SOURCE_KEY_FILE": str(folder / "source.key"),
        "PYMENTOR_ANALYTICS_LOCAL_CATALOG_NAMESPACE": "restore:" + owner["instance_id"],
        "PYMENTOR_ANALYTICS_LOCAL_CATALOG_URL": COLLECTOR_ORIGIN,
        "PYMENTOR_ANALYTICS_LOCAL_CATALOG_PRODUCER_KEY_SHA256": sha256(keys["p1"][2]).hexdigest(),
        "PYMENTOR_ANALYTICS_PRODUCER_ID": "607-restore-control",
        "PYMENTOR_ANALYTICS_PRODUCER_ISSUER": "607-restore-producer",
        "PYMENTOR_ANALYTICS_PRODUCER_KID": "p1",
        "PYMENTOR_ANALYTICS_GRANT_ISSUER": "607-restore-grants",
        "PYMENTOR_ANALYTICS_GRANT_KID": "g1",
        "PYMENTOR_ANALYTICS_GRANT_KEY_FILE": str(folder / "restore-grant.pem"),
        "ANALYTICS_WINDOWS_INTAKE_ENABLED": "1", "ANALYTICS_WINDOWS_RECONCILIATION_ENABLED": "0",
        "ANALYTICS_WINDOWS_COHORT": ",".join(subjects), "ANALYTICS_WINDOWS_T0": t0.isoformat(),
        "ANALYTICS_WINDOWS_SCOPE_FILE": str(folder / "restore-scope.json"),
        "ANALYTICS_WINDOWS_LOCAL_COLLECTOR_ORIGIN": COLLECTOR_ORIGIN,
    }
    migration = {**result, "KNOWI_MIGRATION_URL": target.dsn(name, migrate_role, passwords[migrate_role], "asyncpg"),
                 "KNOWI_MIGRATION_DATABASE": name, "KNOWI_MIGRATION_USER": migrate_role, "KNOWI_MIGRATION_HOST": "127.0.0.1",
                 "KNOWI_MIGRATION_OWNER": migrate_role, "KNOWI_MIGRATION_APP_ROLE": app_role}
    py = str(target.CORE_PYTHON)
    target.run_private([py, "-B", "-m", "alembic", "-c", "alembic-runtime.ini", "upgrade", "head"],
                       target.CORE, {**target.base_env(), **migration}, folder / "restore-core-migrate.log")
    seed_env = {**target.base_env(), **result, "CORP607_LEARNERS_FILE": str(folder / "restore-learners.json")}
    target.run_private([py, "-B", "-c", target.SEED], target.CORE, seed_env, folder / "restore-core-seed.log")
    return result


def authority_config(local, folder, owner, keys):
    reader = target.live._secret32()
    pub = {name: parts[1].decode("ascii") for name, parts in keys.items()}
    authority_id = owner["authority_id"]
    for name, value in (("restore-authority.pem", keys["authority"][0]), ("restore-reader", reader.encode("ascii")),
                        ("source.key", target.live._secret32().encode("ascii"))):
        target.live._write(local, folder, name, value)
    journal_path = folder / "restore-journal.sqlite"
    Journal.initialize(journal_path, authority_id)
    target.live._write(local, folder, "restore-authority-server.json", {
        "fixture_mode": True, "bind_host": "127.0.0.1", "port": AUTHORITY_PORT,
        "issuer": authority_id, "kid": "a1", "signing_key_file": str(folder / "restore-authority.pem"),
        "producer_issuer": "607-restore-producer", "producer_public_keys": {"p1": pub["p1"]},
        "allowed_producers": ["607-restore-control"], "producer_command_public_keys": {
            "607-restore-control": pub["command"],
        }, "reader_secret_file": str(folder / "restore-reader"),
    })
    authority = json.dumps({"url": AUTHORITY_ORIGIN, "authority_id": authority_id, "issuer": authority_id,
                           "public_keys": {"a1": pub["authority"]}, "reader_secret": reader, "fixture_mode": True})
    trust = json.dumps({"grant": {"issuer": "607-restore-grants", "keys": {"g1": pub["g1"]}},
                        "producer": {"issuer": "607-restore-producer", "keys": {"p1": pub["p1"]},
                                     "allowlist": ["607-restore-control"]}})
    app = [sys.executable, "-B", "-m", "pymentor_analytics.adapters.privacy_authority.server", "serve",
           "--journal", str(journal_path), "--authority-id", authority_id,
           "--config", str(folder / "restore-authority-server.json")]
    return authority, trust, app


def analytics_env(db, role, policy, authority, trust, *, lesson=None):
    prefix = f"ANALYTICS_{role.upper()}_"
    env = {**policy, prefix + "DATABASE_URL": db["urls"][role], "ANALYTICS_PRIVACY_AUTHORITY_JSON": authority,
           "ANALYTICS_TRUST_MANIFEST_JSON": trust, "PYTHONDONTWRITEBYTECODE": "1"}
    if role == "collector":
        env["ANALYTICS_FIXTURE_CATALOG_JSON"] = json.dumps([{
            "schema_version": 2, "kind": "catalog_snapshot", "control_id": str(uuid4()),
            "course_id": COURSE, "catalog_version": CATALOG, "track_id": "restore-fixture",
            "lessons": [{"lesson_id": lesson}],
        }])
    return env


def analytics_command(role, port):
    return [sys.executable, "-B", "-m", "pymentor_analytics.app.cli", role,
            "--host", "127.0.0.1", "--port", str(port)]


def connect_verified(data, db):
    return target.bootstrap.connect(data, db["name"])


def verify_analytics(conn, db):
    owner = conn.execute(
        "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()"
    ).fetchone()
    assert owner == (db["name"] + "_migrate",)
    assert conn.execute("SELECT token FROM public.analytics_test_sentinel").fetchall() == [(db["sentinel"],)]
    assert conn.execute("SELECT version_num FROM public.alembic_version").fetchall() == [("a015",)]


def evidence_state(data, db, subject):
    with connect_verified(data, db) as conn:
        verify_analytics(conn, db)
        rows = conn.execute("""SELECT w.activity_date::text,w.slot_no,w.stats,w.revision,c.seq,c.payload_hash,c.server_epoch
            FROM analytics.student_activity_windows w
            JOIN analytics.student_activity_window_control c USING (student_id,activity_date,slot_no)
            WHERE w.student_id=%s ORDER BY w.activity_date,w.slot_no""", (subject,)).fetchall()
        controls = conn.execute("""SELECT count(*) FROM analytics.student_activity_window_control
            WHERE student_id=%s""", (subject,)).fetchone()[0]
        fences = conn.execute("""SELECT epoch,deleted,control_id::text FROM analytics.subject_fences
            WHERE subject_ref=%s""", (subject,)).fetchall()
        receipts = conn.execute("""SELECT seq,phase,control_id::text FROM analytics.privacy_receipts
            WHERE subject_ref=%s""", (subject,)).fetchall()
        cursor = conn.execute("SELECT applied_through FROM analytics.privacy_state").fetchone()
        return {"business": [dict(zip(("activity_date", "slot_no", "stats", "revision", "seq", "hash", "epoch"),
                                      row, strict=True)) for row in rows],
                "control_count": controls, "fences": fences, "receipts": receipts,
                "cursor": cursor[0] if cursor else None}


def pg_archive(db, data, container, *, content=None):
    with connect_verified(data, db) as conn:
        verify_analytics(conn, db)
    flags = ("--data-only", "--no-owner", "--no-acl")
    if content is None:
        tool = "pg_dump"
        flags += ("--format=custom", *(f"--table=analytics.{table}" for table in ARCHIVE_TABLES))
    else:
        tool = "pg_restore"
        flags += ("--exit-on-error", "--single-transaction")
    env = {**target.base_env(), "PGPASSWORD": data["password"]}
    return docker("exec", "-i", "--env", "PGPASSWORD", container, tool, "--host", "127.0.0.1",
                  "--username", data["user"], "--dbname", db["name"], *flags, env=env, content=content)


async def signed_control(authority, keys, subject):
    client = AuthorityClient(AuthorityConfig.from_json(authority))
    at = datetime.now(timezone.utc)
    control = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(),
                            subject_ref=UUID(subject), epoch=1, occurred_at=at)
    ts = int(at.timestamp())
    producer_key = load_pem_private_key(keys["p1"][0], password=None)
    command_key = load_pem_private_key(keys["command"][0], password=None)
    token = jwt.encode({"grant_version": 1, "iss": "607-restore-producer", "aud": "analytics-control",
                        "sub": "607-restore-control", "iat": ts, "nbf": ts, "exp": ts + 40,
                        "scope": "control:write"}, producer_key, algorithm="EdDSA", headers={"kid": "p1"})
    receipt = await client.append(control, producer="607-restore-control", producer_token=token, command_key=command_key)
    assert (receipt.seq, receipt.status, receipt.control_id) == (1, "accepted", control.control_id)
    attach = await client.attach(0)
    page = await client.page(attach.frame.session, 0, 1, 100)
    entry, = page.frame.entries
    assert page.frame.high_watermark == 1 and entry.control == control
    return str(control.control_id), receipt.seq


def wait_purge(data, db, subject, control):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        observed = evidence_state(data, db, subject)
        if observed["cursor"] == 1:
            assert observed == {"business": [], "control_count": 0, "cursor": 1,
                                "fences": [(1, True, control)], "receipts": [(1, "complete", control)]}
            return observed
        time.sleep(.15)
    raise RuntimeError("privacy cursor did not catch up")


def snapshot(issued, slot_time, lesson, active_ms):
    return {"protocol_version": 1, "windows": [{
        "activity_date": slot_time.date().isoformat(), "slot_no": slot_time.hour * 4 + slot_time.minute // 15 + 1,
        "writer_id": issued["writer_id"], "writer_generation": 1, "server_epoch": 1, "seq": 1,
        "client_final": False, "catalog_versions": {COURSE: CATALOG},
        "stats": [{"course_id": COURSE, "lesson_id": lesson, "tool_id": "theory", "active_ms": active_ms}],
    }]}


def logged_in(client, learner):
    reply = client.post("/api/user/login", headers={"Origin": CORE_ORIGIN, "Idempotency-Key": str(uuid4())},
                        json={"email": learner["email"], "password": learner["password"]})
    if reply.status_code != 200:
        raise RuntimeError(f"ordinary Core login returned {reply.status_code}")
    assert client.get("/api/user/me").json()["user"]["account_ref"] == learner["subject_ref"]


def lesson_id(client):
    response = client.get("/api/learning/lessons", params={"course_id": COURSE, "track": wire.TRACK})
    assert response.status_code == 200
    lesson, = [item for item in response.json()["lessons"] if item["title"].startswith("71. ")]
    assert lesson["is_available"]
    return lesson["id"]


def assert_live_grant(issued):
    remaining = (datetime.fromisoformat(issued["expires_at"]) - datetime.now(timezone.utc)).total_seconds()
    assert remaining > 2, "old grant must still be valid when it is refused"
    return round(remaining, 3)


def expect_response(reply, status, code):
    actual = (reply["status"], reply["body"].get("code"))
    if actual != (status, code):
        raise RuntimeError(f"expected HTTP {status}/{code}, got {actual[0]}/{actual[1]}")
    return reply


def run_case(data, source, restored, keys, folder, authority, trust, policy, learners, container):
    deleted, replacement = learners["deleted"], learners["replacement"]
    with httpx.Client(base_url=CORE_ORIGIN, timeout=10, trust_env=False) as old_client, httpx.Client(
        base_url=CORE_ORIGIN, timeout=10, trust_env=False,
    ) as new_client:
        logged_in(old_client, deleted)
        logged_in(new_client, replacement)
        lesson = lesson_id(old_client)
        old_ticket = wire.grant(old_client, str(uuid4()), deleted)
        slot = datetime.now(TIMES) - timedelta(minutes=20)
        original = snapshot(old_ticket, slot, lesson, 60_000)
        old_raw = wire.wire(original)
        old_token = old_ticket["grant"]
        env = analytics_env(source, "collector", policy, authority, trust, lesson=lesson)
        with running(folder, "source-collector", analytics_command("collector", COLLECTOR_PORT), ANALYTICS, env=env) as proc:
            wait_status(proc, COLLECTOR_ORIGIN + "/ready", 200)
            result = wire.send(old_client, old_token, old_raw)
            committed = wire.assert_commit(result)
            initial = evidence_state(data, source, deleted["subject_ref"])
            assert initial["control_count"] == 1 and initial["cursor"] == 0
            row, = initial["business"]
            assert row["stats"] == original["windows"][0]["stats"] and row["revision"] == committed["revision"] == 1
            assert row["seq"] == 1 and row["hash"] == committed["payload_hash"]

            archive = pg_archive(source, data, container)
            assert archive.startswith(b"PGDMP")
            control, seq = asyncio.run(signed_control(authority, keys, deleted["subject_ref"]))
            source_purged = wait_purge(data, source, deleted["subject_ref"], control)
            wait_status(proc, COLLECTOR_ORIGIN + "/ready", 200)
            old_before_restore = expect_response(wire.send(old_client, old_token, old_raw), 403, "forbidden")
            assert_live_grant(old_ticket)

        assert evidence_state(data, restored, deleted["subject_ref"]) == {
            "business": [], "control_count": 0, "fences": [], "receipts": [], "cursor": None,
        }
        pg_archive(restored, data, container, content=archive)
        restored_before_replay = evidence_state(data, restored, deleted["subject_ref"])
        assert restored_before_replay == initial
        print(json.dumps({"phase": "restored_before_replay", "target": restored["name"], "cursor": 0,
                          "control_seq": seq, "sha256": sha256(archive).hexdigest()}), flush=True)

        env = analytics_env(restored, "collector", policy, authority, trust, lesson=lesson)
        with running(folder, "restored-collector", analytics_command("collector", COLLECTOR_PORT), ANALYTICS, env=env) as proc:
            wait_status(proc, COLLECTOR_ORIGIN + "/live", 200)
            wait_status(proc, COLLECTOR_ORIGIN + "/ready", 503)
            closed = expect_response(wire.send(old_client, old_token, old_raw), 503, "privacy_unavailable")
            new_ticket = wire.grant(new_client, str(uuid4()), replacement)
            new_raw = wire.wire(snapshot(new_ticket, slot, lesson, 30_000))
            replacement_closed = expect_response(wire.send(new_client, new_ticket["grant"], new_raw),
                                                 503, "privacy_unavailable")
            assert_live_grant(old_ticket)
            assert evidence_state(data, restored, deleted["subject_ref"]) == initial
            assert not evidence_state(data, restored, replacement["subject_ref"])["business"]

            env = analytics_env(restored, "privacy", policy, authority, trust)
            with running(folder, "restored-privacy", analytics_command("privacy", RESTORED_PRIVACY_PORT),
                         ANALYTICS, env=env) as privacy:
                wait_status(privacy, f"http://127.0.0.1:{RESTORED_PRIVACY_PORT}/ready", 200)
                after = wait_purge(data, restored, deleted["subject_ref"], control)
                wait_status(proc, COLLECTOR_ORIGIN + "/ready", 200)
                remaining = assert_live_grant(old_ticket)
                exact_replay = expect_response(wire.send(old_client, old_token, old_raw), 403, "forbidden")
                late = original["windows"][0]
                late["seq"] += 4
                late["stats"][0]["active_ms"] += 1_000
                late_replay = expect_response(wire.send(old_client, old_token, wire.wire(original)), 403, "forbidden")
                assert evidence_state(data, restored, deleted["subject_ref"]) == after
                new_result = wire.send(new_client, new_ticket["grant"], new_raw)
                new_confirmed = wire.assert_commit(new_result)
                new_state = evidence_state(data, restored, replacement["subject_ref"])
                new_row, = new_state["business"]
                assert new_state["control_count"] == 1 and new_row["stats"][0]["active_ms"] == 30_000
                assert new_row["revision"] == new_confirmed["revision"] == 1
                return {"subjects": {"deleted": deleted["subject_ref"], "replacement": replacement["subject_ref"]},
                        "committed_initial": result, "source_before_archive": initial, "source_purged": source_purged,
                        "archive": {"bytes": len(archive), "sha256": sha256(archive).hexdigest(), "tables": ARCHIVE_TABLES},
                        "journal": {"seq": seq, "control_id": control, "signed_append_and_page_verified": True},
                        "old_grant_source_after_purge": old_before_restore,
                        "restored_before_replay": restored_before_replay, "collector_ready_before": 503,
                        "old_grant_blocked_until_replay": closed, "replacement_blocked_until_replay": replacement_closed,
                        "restored_after_replay": after, "collector_ready_after": 200,
                        "old_grant_seconds_remaining": remaining, "old_exact_replay": exact_replay,
                        "old_higher_seq_replay": late_replay, "replacement_commit": new_result,
                        "replacement_postgres": new_state}


def source_revisions():
    files = {
        "core": target.CORE / "pymentor_core/app/factory.py",
        "core_gateway": target.CORE / "pymentor_core/app/analytics_windows_gateway.py",
        "collector_factory": ANALYTICS / "pymentor_analytics/app/factory.py",
        "collector_route": ANALYTICS / "pymentor_analytics/modules/collector/api/windows_router.py",
        "privacy_runtime": ANALYTICS / "pymentor_analytics/app/privacy.py",
        "privacy_store": ANALYTICS / "pymentor_analytics/adapters/postgres/privacy.py",
        "a015": ANALYTICS / "migrations/versions/a015_student_activity_windows.py",
    }
    return {name: sha256(file.read_bytes()).hexdigest() for name, file in files.items()}


def plan():
    return {"status": "PROPOSED", "ports": {"Core": CORE_PORT, "Collector": COLLECTOR_PORT,
                                            "source_Privacy": SOURCE_PRIVACY_PORT, "restored_Privacy": RESTORED_PRIVACY_PORT,
                                            "authority": AUTHORITY_PORT, "Redis": REDIS_PORT},
            "databases": {"Core": "core_607_<run>", "Analytics": "two fresh analytics_test_<uuid> databases at a015"},
            "archive": {"format": "custom data-only", "tables": ARCHIVE_TABLES},
            "fixtures": ["two real synthetic Core accounts", "Collector catalog configured from Core lesson ID",
                         "synthetic signed deletion producer"],
            "readiness": "ordinary network authority and Privacy service; restored consumer starts after observing 503"}


def run(run_id, output):
    if os.environ.get("CORP607_RUN_RESTORE") != "1":
        raise RuntimeError("explicit 607-В synthetic execution opt-in required")
    destination = (target.REPOSITORY / "docs/analytics/evidence-598/607/restore").resolve()
    if not output.is_absolute() or output.resolve().parent != destination or output.suffix != ".json" or output.exists():
        raise ValueError("a fresh JSON output inside the 607 restore evidence directory is required")
    destination.mkdir(parents=True, exist_ok=True)
    preflight()
    pg_identity = docker("inspect", "--format",
        '{{.Id}} {{index .Config.Labels "com.docker.compose.project"}} {{index .Config.Labels "knowi.analytics.synthetic"}}',
        "knowi-analytics-01a1df75-analytics-postgres-1").decode().strip().split()
    if len(pg_identity) != 3 or not re.fullmatch(r"[0-9a-f]{64}", pg_identity[0]) or pg_identity[1:] != [
        "knowi-analytics-01a1df75", "true",
    ]:
        raise ValueError("unexpected synthetic PostgreSQL container")
    data = target.bootstrap.config(target.CONNECTION)
    local, folder, owner = target.live.prepare_folder(str(run_id))
    print(json.dumps({"phase": "prepared_private_namespace", "run": str(run_id), "folder": str(folder)}), flush=True)
    keys = target.live._pem_keys()
    authority, trust, commands = authority_config(local, folder, owner, keys)
    t0 = datetime.now(timezone.utc) - timedelta(hours=2)
    learners = {alias: {"subject_ref": str(uuid4()), "email": f"corp607-restore-{alias}@example.test",
                        "password": target.secrets.token_urlsafe(24)}
                for alias in ("deleted", "replacement")}
    versions = source_revisions()
    with ExitStack() as stack:
        redis_url = stack.enter_context(redis(run_id))
        core = stack.enter_context(core_target(data, run_id))
        source = stack.enter_context(analytics_target(data))
        restored = stack.enter_context(analytics_target(data))
        print(json.dumps({"phase": "database_targets", "core": core[0], "source": source["name"],
                          "restored": restored["name"]}), flush=True)
        core_env = scope_env(local, folder, owner, keys, core, redis_url, learners, t0)
        policy = {name: value for name, value in core_env.items() if name in {
            "ANALYTICS_WINDOWS_INTAKE_ENABLED", "ANALYTICS_WINDOWS_RECONCILIATION_ENABLED",
            "ANALYTICS_WINDOWS_COHORT", "ANALYTICS_WINDOWS_T0",
        }}
        authority_proc = stack.enter_context(running(folder, "authority", commands, ANALYTICS))
        wait_status(authority_proc, AUTHORITY_ORIGIN + "/unused", 404)
        env = analytics_env(source, "privacy", policy, authority, trust)
        source_privacy = stack.enter_context(running(folder, "source-privacy",
            analytics_command("privacy", SOURCE_PRIVACY_PORT), ANALYTICS, env=env))
        wait_status(source_privacy, f"http://127.0.0.1:{SOURCE_PRIVACY_PORT}/ready", 200)
        core_command = [str(target.CORE_PYTHON), "-B", "-m", "uvicorn", "pymentor_core.app.asgi:create_app",
                        "--factory", "--host", "127.0.0.1", "--port", str(CORE_PORT), "--no-access-log"]
        core_proc = stack.enter_context(running(folder, "core", core_command, target.CORE, env=core_env))
        wait_status(core_proc, CORE_ORIGIN + "/health/ready", 200, timeout=30)
        result = run_case(data, source, restored, keys, folder, authority, trust, policy, learners, pg_identity[0])
        assert source_revisions() == versions, "source digests changed during the flow"
    wire.safe_output(output, {**plan(), **result, "status": "PASS", "run": str(run_id), "versions": versions,
                              "observation_start": t0.isoformat(), "postgres_container": pg_identity[0],
                              "targets": {"source": source["name"], "restored": restored["name"], "core": core[0]},
                              "cleanup": "three PostgreSQL databases and their roles, owned Redis and HTTP processes"})
    print(json.dumps({"phase": "complete", "run": str(run_id), "evidence": str(output)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true")
    action.add_argument("--run", type=UUID)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.plan:
        print(json.dumps(plan(), indent=2))
    elif not args.output:
        parser.error("--run requires --output")
    else:
        run(args.run, args.output)


if __name__ == "__main__":
    main()
