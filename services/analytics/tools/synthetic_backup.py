"""Guarded local synthetic fixture backup. Never targets the retained runtime."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from uuid import UUID, uuid4

import bootstrap_postgres as db
import bootstrap_runtime as runtime
import check_k3s
import run_local
from psycopg import sql

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
from pymentor_analytics.adapters.backup import crypto
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.app.config import Settings

BASE = SERVICE / ".local" / "synthetic-backup"
CONTAINER = "knowi-analytics-01a1df75-analytics-postgres-1"
MAX_SECONDS = 120
FACT_TRIGGER_DEFINITION = ("CREATE TRIGGER activity_projection_fact_inserted AFTER INSERT ON analytics.activity_facts "
                           "FOR EACH ROW WHEN (new.source_kind = 'browser_v2'::text) "
                           "EXECUTE FUNCTION analytics.activity_projection_fact_inserted()")
# PostgreSQL 16 decompiles the entire trigger, including the NEW-qualified WHEN.
# A mismatch (including a changed search_path or PostgreSQL rendering) is closed.
FACT_DEFINITION_SQL = "pg_get_triggerdef(oid,true) = " + sql.Literal(FACT_TRIGGER_DEFINITION).as_string()


def identifier(value):
    parsed = UUID(value)
    if str(parsed) != value.lower():
        raise ValueError("invalid identifier")
    return str(parsed)


def tree():
    for path in (BASE.parent, BASE, BASE / "keys", BASE / "operations", BASE / "artifacts", BASE / "staging"):
        if path.is_symlink() or path.is_junction():
            raise ValueError("unsafe path")
        path.mkdir(exist_ok=True)
        if path.is_symlink() or path.is_junction() or not path.is_dir():
            raise ValueError("unsafe path")


def path_for(group, identity, suffix):
    identity = identifier(identity)
    path = BASE / group / (identity + suffix)
    if path.is_symlink() or path.is_junction():
        raise ValueError("unsafe path")
    return path


def new_file(path, data):
    if any(path.parent.glob("." + path.name + ".*.pending")):
        raise ValueError("prior file creation is unresolved")
    temporary = path.with_name("." + path.name + "." + uuid4().hex + ".pending")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_record(identity, data, *, create=False):
    path = path_for("operations", identity, ".json")
    content = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if create:
        new_file(path, content)
        return
    tmp = path.with_name(path.name + ".pending")
    new_file(tmp, content)
    os.replace(tmp, path)


def read_record(identity, kind, state):
    path = path_for("operations", identity, ".json")
    if path.stat().st_size > 64 * 1024 or path.stat().st_nlink != 1:
        raise ValueError("unsafe operation file")
    record = json.loads(path.read_text(encoding="utf-8"))
    if (record.get("operation_id") != identity or record.get("kind") != kind
            or record.get("state") != state or record.get("synthetic_only") is not True
            or record.get("do_not_drop") is not True):
        raise ValueError("unrecognized or incomplete operation")
    return record


def key_for(identity):
    path = path_for("keys", identity, ".key")
    if path.stat().st_size != 32 or path.stat().st_nlink != 1:
        raise ValueError("invalid backup key")
    value = path.read_bytes()
    if len(value) != 32:
        raise ValueError("invalid backup key")
    return value


def connection_config():
    verify_container()
    return db.config(runtime.CONNECTION)


def validate_db(record, config):
    runtime_record = {"synthetic_only": True, "do_not_drop": True, "project": runtime.PROJECT,
                      "state": "ready", "database": record["database"], "nonce": record["nonce"],
                      "urls": record["urls"]}
    return runtime.verify_database(config, runtime_record)


def history():
    head, revisions, digest = runtime.schema_history()
    return {"head": head, "chain": list(revisions), "sha256": digest,
            "runtime_sha256": run_local.runtime_files_fingerprint(SERVICE)}


def history_for_revision(revision):
    current_history = history()
    if revision == current_history["head"]:
        return current_history
    head, revisions, digest = runtime.schema_history(revision)
    return {"head": head, "chain": list(revisions), "sha256": digest,
            "runtime_sha256": current_history["runtime_sha256"]}


def current(record, config, expected):
    rows = validate_db(record, config)
    if runtime.current_revision(rows, tuple(expected["chain"])) != expected["head"]:
        raise ValueError("database schema differs")


def reject_retained(record):
    if runtime.RECORD.exists():
        retained = runtime.read_record()
        if record["database"] == retained["database"]:
            raise ValueError("retained runtime is outside this operation")


def provision(identity, kind, config, expected, context=None):
    record = {"operation_id": identity, "kind": kind, "state": "provisioning",
              "synthetic_only": True, "do_not_drop": True, "privacy_opened": False}
    if context is not None:
        record.update(context)
    write_record(identity, record, create=True)
    name, nonce, urls = db.create(config, revision=expected["head"])
    record.update(database=name, nonce=nonce, urls=urls, state="migration_pending", history=expected)
    write_record(identity, record)
    reject_retained(record)
    upgrade(Settings(database_url=urls["migrate"], pool_size=1, pool_overflow=0), target_revision=expected["head"])
    if history_for_revision(expected["head"]) != expected:
        raise ValueError("source changed during migration")
    current(record, config, expected)
    record["state"] = "fixture_ready" if kind == "fixture" else "fresh_migrated"
    write_record(identity, record)
    return record


def docker(command, *, env=None, timeout=10):
    result = subprocess.run(["docker", *command], env=env, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Docker operation failed")
    return result.stdout.decode("utf-8").strip()


def verify_container():
    check_k3s.local_docker_env()
    template = '{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "knowi.analytics.synthetic"}}|{{.State.Running}}'
    actual = docker(["inspect", "--format", template, CONTAINER])
    if actual != "knowi-analytics-01a1df75|analytics-postgres|true|true":
        raise ValueError("unexpected PostgreSQL container")
    bindings = json.loads(docker(["inspect", "--format", '{{json (index .NetworkSettings.Ports "5432/tcp")}}', CONTAINER]))
    if bindings != [{"HostIp": "127.0.0.1", "HostPort": "57392"}]:
        raise ValueError("unexpected PostgreSQL port binding")
    version = docker(["exec", CONTAINER, "pg_dump", "--version"])
    restore_version = docker(["exec", CONTAINER, "pg_restore", "--version"])
    if (not re.fullmatch(r"pg_dump \(PostgreSQL\) 16\.16(?: \([^\r\n]+\))?", version)
            or not re.fullmatch(r"pg_restore \(PostgreSQL\) 16\.16(?: \([^\r\n]+\))?", restore_version)):
        raise ValueError("unexpected pg_dump version")


def docker_db_args(program, name, user, options):
    if not db.PATTERN.fullmatch(name) or user not in {"analytics_bootstrap", f"{name}_migrate"}:
        raise ValueError("unsafe database target")
    return ["docker", "exec", "-i", "--env", "PGPASSWORD", CONTAINER, program,
            "--host", "127.0.0.1", "--username", user, "--dbname", name, *options]


def password_env(password):
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    return env


def dump(record, config, path):
    verify_container()
    args = docker_db_args("pg_dump", record["database"], "analytics_bootstrap",
                          ["--format=custom", "--data-only", "--schema=analytics", "--no-owner", "--no-acl"])
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
        process = subprocess.Popen(args, env=password_env(config["password"]), stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        failures = []
        def stream():
            try:
                total = 0
                while chunk := process.stdout.read(crypto.CHUNK):
                    total += len(chunk)
                    if total > crypto.MAX_BYTES:
                        raise ValueError("dump exceeds limit")
                    output.write(chunk)
            except Exception as exc:
                failures.append(exc)
                process.kill()
        reader = threading.Thread(target=stream, daemon=True)
        reader.start()
        try:
            process.wait(timeout=MAX_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RuntimeError("dump timeout") from None
        finally:
            reader.join(timeout=10)
        if reader.is_alive() or failures or process.returncode:
            raise RuntimeError("dump failed")
        output.flush()
        os.fsync(output.fileno())


def backup(source_id, backup_id, key_id):
    config = connection_config()
    source = read_record(source_id, "fixture", "fixture_ready")
    reject_retained(source)
    expected = history_for_revision(source["history"]["head"])
    if source["history"] != expected:
        raise ValueError("fixture history differs")
    current(source, config, expected)
    artifact = path_for("artifacts", backup_id, ".enc")
    operation = {"operation_id": backup_id, "kind": "backup", "state": "backup_pending",
                 "synthetic_only": True, "do_not_drop": True, "source_id": source_id,
                 "privacy_opened": False}
    write_record(backup_id, operation, create=True)
    stage = BASE / "staging" / (uuid4().hex + ".dump")
    started = datetime.now(timezone.utc).isoformat()
    try:
        before = table_digests(config, source)
        dump(source, config, stage)
        after = table_digests(config, source)
        if history_for_revision(expected["head"]) != expected or before != after:
            raise ValueError("source changed during backup")
        metadata = {"synthetic_only": True, "source_id": source_id, "source_database": source["database"],
                    "snapshot_started_at": started, "snapshot_finished_at": datetime.now(timezone.utc).isoformat(),
                    "history": expected, "format": "pg16-custom-analytics-data-only",
                    "fixture_state_sha256": state_digest(before)}
        crypto.encrypt_file(stage, artifact, key_for(key_id), metadata)
        _, verified = crypto.decrypt_verified(artifact, BASE / "staging", key_for(key_id))
        verified.unlink()
        operation.update(state="crypto_verified", crypto_verified=True)
        write_record(backup_id, operation)
    finally:
        stage.unlink(missing_ok=True)


def clear_fresh_defaults(config, record):
    """Delete only the known migration seed after verifying all other tables empty."""
    if record.get("kind") != "restore" or record.get("state") != "restore_pending":
        raise ValueError("target is not a new restore")
    target_history = record.get("history", {})
    if target_history.get("head") == "a011" or "a011" in target_history.get("chain", ()):
        raise ValueError("a011 restore is not supported")
    reject_retained(record)
    validate_db(record, config)
    with db.connect(config, record["database"]) as conn:
        with conn.transaction():
            tables = conn.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='analytics' ORDER BY tablename").fetchall()
            if not tables or ("admission_budget",) not in tables:
                raise ValueError("unexpected fresh schema")
            projection_seed = record.get("history", {}).get("head") == "a010"
            if projection_seed and ("activity_projection_backfill",) not in tables:
                raise ValueError("missing projection backfill seed")
            seeds = {"admission_budget"} | ({"activity_projection_backfill"} if projection_seed else set())
            for (name,) in tables:
                count = conn.execute(sql.SQL("SELECT count(*) FROM analytics.{}").format(sql.Identifier(name))).fetchone()[0]
                if count != (1 if name in seeds else 0):
                    raise ValueError("target schema is not fresh")
            default = conn.execute("SELECT singleton,subject_per_minute,global_per_minute,max_outstanding_jobs,"
                                   "max_database_bytes,warning_percent,critical_percent,window_epoch,accepted_count,lock_nonce "
                                   "FROM analytics.admission_budget").fetchall()
            if default != [(True, 60, 600, 10000, 536870912, 80, 90, 0, 0, False)]:
                raise ValueError("unexpected default budget")
            if projection_seed:
                projection = conn.execute("SELECT singleton,run_id,phase,cursor_namespace,cursor_event_id "
                                          "FROM analytics.activity_projection_backfill").fetchall()
                if projection != [(True, None, "scanning", None, None)]:
                    raise ValueError("unexpected projection seed")
                visibility = conn.execute("SELECT rolsuper OR pg_has_role(current_user,'pg_read_all_stats','MEMBER') "
                                          "FROM pg_roles WHERE rolname=current_user").fetchone()
                sessions = conn.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                                        "AND pid<>pg_backend_pid()").fetchone()
                if visibility != (True,) or sessions != (0,):
                    raise ValueError("fresh restore target has other or unobservable sessions")
                name = record["database"]
                for role in ("collector", "processor", "query", "transfer_status"):
                    conn.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM {}")
                                 .format(sql.Identifier(name), sql.Identifier(name + "_" + role)))
                for role in ("collector", "processor", "query", "transfer_status"):
                    privilege = conn.execute("SELECT has_database_privilege(%s,current_database(),'CONNECT')",
                                             (name + "_" + role,)).fetchone()
                    if privilege != (False,):
                        raise ValueError("fresh restore quarantine not established")
                conn.execute("DELETE FROM analytics.activity_projection_backfill WHERE singleton=true")
            conn.execute("DELETE FROM analytics.admission_budget WHERE singleton=true")


def assert_a011_privileges(conn, name):
    """Fail closed on unexpected effective or PUBLIC a011 privileges."""
    if not db.PATTERN.fullmatch(name):
        raise ValueError("invalid disposable target")
    columns = {
        "activity_projection_day_sources": ("subject_ref", "local_day", "metric_version", "generation", "state", "claim_id", "owner", "lease_expires_at", "claim_generation", "claim_epoch", "claim_run_id", "attempts", "next_attempt_at", "last_failure"),
        "activity_projection_days": ("subject_ref", "local_day", "metric_version", "generation", "subject_epoch", "run_id", "contributions", "presence_labels", "source_bucket_count", "source_compact_items", "source_compact_bytes"),
        "activity_projection_day_backfill": ("singleton", "run_id", "phase", "cursor_subject", "cursor_bucket", "cursor_metric"),
    }
    updates = {
        "activity_projection_day_sources": {"state", "claim_id", "owner", "lease_expires_at", "claim_generation", "claim_epoch", "claim_run_id", "attempts", "next_attempt_at", "last_failure"},
        "activity_projection_day_backfill": {"phase", "cursor_subject", "cursor_bucket", "cursor_metric"},
    }
    functions = ("activity_projection_fact_inserted()", "activity_projection_bucket_changed()", "register_activity_day(uuid,timestamptz,text,uuid)",
                 "begin_activity_day_backfill(uuid)", "invalidate_stale_activity_day(uuid,date,uuid)")
    roles = ("collector", "processor", "query", "privacy", "transfer_status")
    expressions, params, expected = [], [], []
    def check(expression, args, permitted):
        expressions.append(expression)
        params.extend(args)
        expected.append(permitted)
    for table, names in columns.items():
        qualified = "analytics." + table
        rows = conn.execute("SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass "
                            "AND attnum>0 AND NOT attisdropped ORDER BY attnum", (qualified,)).fetchall()
        if tuple(x[0] for x in rows) != names:
            raise ValueError("a011 table shape differs")
        for role in roles:
            role_name = name + "_" + role
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                allowed = ((privilege == "SELECT" and (role in ("processor", "query") or
                            (role == "privacy" and table != "activity_projection_day_backfill"))) or
                           (role == "processor" and table == "activity_projection_days" and privilege in ("INSERT", "UPDATE")) or
                           (role == "privacy" and table != "activity_projection_day_backfill" and privilege == "DELETE"))
                check("has_table_privilege(%s,%s,%s)", (role_name, qualified, privilege), allowed)
            for col in names:
                for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
                    allowed = ((privilege == "SELECT" and (role in ("processor", "query") or
                                (role == "privacy" and table != "activity_projection_day_backfill") or
                                (role == "transfer_status" and table != "activity_projection_day_backfill" and col == "subject_ref"))) or
                               (role == "processor" and table == "activity_projection_days" and privilege in ("INSERT", "UPDATE")) or
                               (role == "processor" and privilege == "UPDATE" and col in updates.get(table, set())))
                    check("has_column_privilege(%s,%s,%s,%s)", (role_name, qualified, col, privilege), allowed)
    for signature in functions:
        for role in roles:
            check("has_function_privilege(%s,%s,%s)",
                  (name + "_" + role, "analytics." + signature, "EXECUTE"),
                  role == "processor" and signature.startswith(("register_activity_day(", "begin_activity_day_backfill(", "invalidate_stale_activity_day(")))
    actual = conn.execute("SELECT " + ",".join(expressions), params).fetchone()
    if actual != tuple(expected):
        raise ValueError("a011 runtime privileges differ")
    public = conn.execute("""SELECT EXISTS(
        SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
          LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) a
        WHERE n.nspname='analytics' AND c.relname=ANY(%s) AND a.grantee=0
        UNION ALL SELECT 1 FROM pg_attribute x JOIN pg_class c ON c.oid=x.attrelid
          JOIN pg_namespace n ON n.oid=c.relnamespace, LATERAL aclexplode(x.attacl) a
        WHERE n.nspname='analytics' AND c.relname=ANY(%s) AND a.grantee=0
        UNION ALL SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace,
          LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
        WHERE n.nspname='analytics' AND p.oid=ANY(%s::regprocedure[]) AND a.grantee=0
        UNION ALL SELECT 1 FROM pg_namespace n,
          LATERAL aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) a
        WHERE n.nspname IN ('analytics','pg_catalog') AND a.grantee=0 AND a.privilege_type='CREATE')""",
        (list(columns), list(columns), ["analytics." + f for f in functions])).fetchone()
    if public != (False,):
        raise ValueError("a011 public privileges differ")
    for role in roles:
        unsafe = conn.execute("SELECT has_schema_privilege(%s,'analytics','CREATE') OR "
                              "has_schema_privilege(%s,'pg_catalog','CREATE') OR pg_has_role(%s,%s,'MEMBER')",
                              (name + "_" + role, name + "_" + role, name + "_" + role, name + "_migrate")).fetchone()
        if unsafe != (False,):
            raise ValueError("a011 unsafe role ownership")
        membership = conn.execute("SELECT EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member "
                                  "WHERE r.rolname=%s)", (name + "_" + role,)).fetchone()
        if membership != (False,):
            raise ValueError("a011 runtime role has memberships")
    function_rows = conn.execute("""SELECT pg_get_userbyid(p.proowner),p.prosecdef,
        EXISTS(SELECT 1 FROM unnest(p.proconfig) c WHERE replace(c,' ','')='search_path=pg_catalog,analytics')
        FROM pg_proc p WHERE p.oid=ANY(%s::regprocedure[])""", (["analytics." + f for f in functions],)).fetchall()
    if len(function_rows) != len(functions) or any(row != (name + "_migrate", True, True) for row in function_rows):
        raise ValueError("a011 function ownership differs")
    owners = conn.execute("SELECT pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                          "WHERE n.nspname='analytics' AND c.relname=ANY(%s)", (list(columns),)).fetchall()
    if len(owners) != len(columns) or any(row != (name + "_migrate",) for row in owners):
        raise ValueError("a011 table ownership differs")
    triggers = conn.execute("SELECT "
        "(SELECT count(*) FROM pg_trigger WHERE tgrelid='analytics.activity_facts'::regclass "
        "AND tgname='activity_projection_fact_inserted' AND NOT tgisinternal AND tgenabled='O' "
        "AND tgtype=5 AND tgfoid='analytics.activity_projection_fact_inserted()'::regprocedure AND " + FACT_DEFINITION_SQL + ")=1, "
        "(SELECT count(*) FROM pg_trigger WHERE tgrelid='analytics.activity_projection_buckets'::regclass "
        "AND tgname='activity_projection_bucket_changed' AND NOT tgisinternal AND tgenabled='O' "
        "AND tgtype=29 AND tgqual IS NULL AND tgfoid='analytics.activity_projection_bucket_changed()'::regprocedure)=1").fetchone()
    if triggers != (True, True):
        raise ValueError("a011 trigger definition differs")


def clear_fresh_defaults_a011(config, record):
    """Prepare an owned a011 target; this helper is not wired to restore."""
    if (record.get("kind") != "restore" or record.get("state") != "restore_pending"
            or record.get("history", {}).get("head") != "a011"):
        raise ValueError("unsafe a011 restore target")
    reject_retained(record)
    validate_db(record, config)
    name = record["database"]
    with db.connect(config, name) as conn:
        assert_a011_privileges(conn, name)
        # Commit revocation before seed deletion. A transactional REVOKE does
        # not protect writes while its privilege change is still uncommitted.
        with conn.transaction():
            visibility = conn.execute("SELECT rolsuper OR pg_has_role(current_user,'pg_read_all_stats','MEMBER') "
                                      "FROM pg_roles WHERE rolname=current_user").fetchone()
            if visibility != (True,):
                raise ValueError("fresh restore sessions are unobservable")
            for role in ("collector", "processor", "query", "transfer_status", "privacy"):
                conn.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM {}")
                             .format(sql.Identifier(name), sql.Identifier(name + "_" + role)))
        with conn.transaction():
            roles = ("collector", "processor", "query", "transfer_status", "privacy")
            for role in roles:
                if conn.execute("SELECT has_database_privilege(%s,current_database(),'CONNECT')", (name + "_" + role,)).fetchone() != (False,):
                    raise ValueError("fresh restore quarantine not established")
            if conn.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                            "AND pid<>pg_backend_pid()").fetchone() != (0,):
                raise ValueError("fresh restore target has other sessions")
            tables = {row[0] for row in conn.execute(
                "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='analytics'").fetchall()}
            seeds = {"admission_budget", "activity_projection_backfill", "activity_projection_day_backfill"}
            if not seeds <= tables:
                raise ValueError("missing fresh restore seeds")
            for table in sorted(tables):
                count = conn.execute(sql.SQL("SELECT count(*) FROM analytics.{}").format(sql.Identifier(table))).fetchone()[0]
                if count != (1 if table in seeds else 0):
                    raise ValueError("target schema is not fresh")
            if conn.execute("SELECT singleton,subject_per_minute,global_per_minute,max_outstanding_jobs,"
                            "max_database_bytes,warning_percent,critical_percent,window_epoch,accepted_count,lock_nonce "
                            "FROM analytics.admission_budget").fetchall() != [(True, 60, 600, 10000, 536870912, 80, 90, 0, 0, False)]:
                raise ValueError("unexpected default budget")
            if conn.execute("SELECT singleton,run_id,phase,cursor_namespace,cursor_event_id "
                            "FROM analytics.activity_projection_backfill").fetchall() != [(True, None, "scanning", None, None)]:
                raise ValueError("unexpected projection seed")
            if conn.execute("SELECT singleton,run_id,phase,cursor_subject,cursor_bucket,cursor_metric "
                            "FROM analytics.activity_projection_day_backfill").fetchall() != [(True, None, "scanning", None, None, None)]:
                raise ValueError("unexpected day projection seed")
            for table in sorted(seeds):
                conn.execute(sql.SQL("DELETE FROM analytics.{} WHERE singleton=true").format(sql.Identifier(table)))


def classify_psql_failure(diagnostic):
    """Return only a fixed category; never persist SQL, values, or stderr."""
    message = diagnostic.lower()
    if b"permission denied for table analytics_test_sentinel" in message:
        return "sentinel permission denied"
    if b"restore guard failed" in message:
        return "restore guard failed"
    if b"permission denied" in message:
        return "permission denied"
    if b"duplicate key value" in message:
        return "duplicate key"
    if b"violates check constraint" in message:
        return "check constraint"
    if b"syntax error" in message:
        return "syntax error"
    return "unknown"


def restore_a010_transaction(stage, record, config, password):
    """Restore only a fresh isolated a010 target; keep app roles quarantined."""
    _restore_projection_transaction(stage, record, config, password, revision="a010")


def restore_a011_transaction(stage, record, config, password):
    """Unwired a011 restore, atomically suspending only the two known triggers."""
    _restore_projection_transaction(stage, record, config, password, revision="a011")


def _restore_projection_transaction(stage, record, config, password, *, revision):
    name, token = record["database"], record["nonce"]
    if (record.get("kind") != "restore" or record.get("state") != "restore_pending"
            or revision not in ("a010", "a011") or record.get("history", {}).get("head") != revision
            or not db.PATTERN.fullmatch(name) or not re.fullmatch(r"[0-9a-f]{64}", token)):
        raise ValueError(f"unsafe {revision} restore target")
    reject_retained(record)
    validate_db(record, config)
    if revision == "a011":
        with db.connect(config, name) as acl_conn:
            assert_a011_privileges(acl_conn, name)
    owner = name + "_migrate"
    trigger = "activity_projection_fact_inserted"
    extra_guard = ""
    extra_disable = ""
    extra_enable = ""
    if revision == "a011":
        # Check function identity, not merely a trigger with the expected name.
        extra_guard = ("OR (SELECT count(*) FROM pg_trigger WHERE tgrelid='analytics.activity_facts'::regclass "
                       "AND tgname='activity_projection_fact_inserted' AND NOT tgisinternal AND tgenabled='O' "
                       "AND tgtype=5 AND " + FACT_DEFINITION_SQL + " "
                       "AND tgfoid='analytics.activity_projection_fact_inserted()'::regprocedure) <> 1 "
                       "OR (SELECT count(*) FROM pg_trigger WHERE tgrelid='analytics.activity_projection_buckets'::regclass "
                       "AND tgname='activity_projection_bucket_changed' AND NOT tgisinternal AND tgenabled='O' "
                       "AND tgtype=29 AND tgqual IS NULL "
                       "AND tgfoid='analytics.activity_projection_bucket_changed()'::regprocedure) <> 1 ")
        for role in ("collector", "processor", "query", "transfer_status", "privacy"):
            extra_guard += f"OR has_database_privilege('{name}_{role}',current_database(),'CONNECT') "
        extra_disable = "ALTER TABLE analytics.activity_projection_buckets DISABLE TRIGGER activity_projection_bucket_changed;\n"
        extra_enable = "ALTER TABLE analytics.activity_projection_buckets ENABLE TRIGGER activity_projection_bucket_changed;\n"
    header = ("DO $guard$ BEGIN "
              f"IF current_database() <> '{name}' OR current_user <> '{owner}' "
              "OR (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()) <> current_user "
              f"OR (SELECT count(*) FROM public.analytics_test_sentinel WHERE token='{token}') <> 1 "
              "OR (SELECT count(*) FROM public.analytics_test_sentinel) <> 1 "
              "OR (SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()) <> 0 "
              "OR (SELECT count(*) FROM pg_trigger WHERE tgrelid='analytics.activity_facts'::regclass "
              f"AND tgname='{trigger}' AND NOT tgisinternal AND tgenabled='O') <> 1 "
              + extra_guard +
              "THEN RAISE EXCEPTION 'restore guard failed'; END IF; END $guard$;\n"
              f"ALTER TABLE analytics.activity_facts DISABLE TRIGGER {trigger};\n" + extra_disable)
    footer = f"\nALTER TABLE analytics.activity_facts ENABLE TRIGGER {trigger};\n" + extra_enable
    for role in ("collector", "processor", "query", "transfer_status") + (("privacy",) if revision == "a011" else ()):
        footer += f'REVOKE CONNECT ON DATABASE "{name}" FROM "{name}_{role}";\n'
    spool = BASE / "staging" / ("." + uuid4().hex + ".restore.sql")
    if spool.is_symlink() or spool.is_junction():
        raise ValueError("unsafe restore spool")
    fd = os.open(spool, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    failures = []
    try:
        with os.fdopen(fd, "wb") as output, stage.open("rb") as archive:
            output.write(header.encode("ascii"))
            command = ["docker", "exec", "-i", CONTAINER, "pg_restore", "--data-only", "--no-owner", "--no-acl", "--file=-"]
            process = subprocess.Popen(command, stdin=archive, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            diagnostic = bytearray()
            def copy_diagnostic():
                try:
                    while block := process.stderr.read(4096):
                        if len(diagnostic) < 4096:
                            diagnostic.extend(block[:4096 - len(diagnostic)])
                except Exception:
                    failures.append("restore diagnostic failed")
                    process.kill()
            def copy_output():
                try:
                    total = 0
                    while block := process.stdout.read(1024 * 1024):
                        total += len(block)
                        if total > crypto.MAX_BYTES:
                            failures.append("restore stream too large")
                            process.kill()
                            break
                        output.write(block)
                except Exception:
                    failures.append("restore stream failed")
                    process.kill()
            reader = threading.Thread(target=copy_output, daemon=True)
            error_reader = threading.Thread(target=copy_diagnostic, daemon=True)
            reader.start()
            error_reader.start()
            try:
                process.wait(timeout=MAX_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise RuntimeError("restore extraction timeout") from None
            finally:
                reader.join(timeout=10)
                error_reader.join(timeout=10)
            if reader.is_alive() or error_reader.is_alive() or failures or process.returncode:
                if b"one of -d/--dbname and -f/--file must be specified" in diagnostic:
                    reason = "output destination required"
                elif b"non-seekable input file" in diagnostic:
                    reason = "non-seekable archive"
                else:
                    reason = "unknown"
                raise RuntimeError("restore extraction failed: " + reason)
            output.write(footer.encode("ascii"))
            output.flush()
            os.fsync(output.fileno())
        command = docker_db_args("psql", name, owner,
                                 ["--no-psqlrc", "--set", "ON_ERROR_STOP=on", "--single-transaction", "--file=-"])
        with spool.open("rb") as source:
            process = subprocess.Popen(command, stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                       env=password_env(password))
            diagnostic = bytearray()
            def read_psql_diagnostic():
                while block := process.stderr.read(4096):
                    if len(diagnostic) < 4096:
                        diagnostic.extend(block[:4096 - len(diagnostic)])
            reader = threading.Thread(target=read_psql_diagnostic, daemon=True)
            reader.start()
            try:
                process.wait(timeout=MAX_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise RuntimeError("restore outcome needs reconciliation: timeout") from None
            finally:
                reader.join(timeout=10)
            if reader.is_alive():
                raise RuntimeError("restore outcome needs reconciliation: diagnostic incomplete")
        if process.returncode:
            raise RuntimeError("restore outcome needs reconciliation: " + classify_psql_failure(diagnostic))
        with db.connect(config, name) as conn:
            fact_function = (" AND tgtype=5 AND " + FACT_DEFINITION_SQL + " AND tgfoid='analytics.activity_projection_fact_inserted()'::regprocedure"
                             if revision == "a011" else "")
            state = conn.execute("SELECT tgenabled FROM pg_trigger WHERE tgrelid='analytics.activity_facts'::regclass "
                                 "AND tgname=%s AND NOT tgisinternal" + fact_function, (trigger,)).fetchall()
            day_state = (conn.execute("SELECT tgenabled FROM pg_trigger WHERE tgrelid='analytics.activity_projection_buckets'::regclass "
                                      "AND tgname='activity_projection_bucket_changed' AND NOT tgisinternal "
                                      "AND tgtype=29 AND tgqual IS NULL "
                                      "AND tgfoid='analytics.activity_projection_bucket_changed()'::regprocedure").fetchall()
                         if revision == "a011" else [("O",)])
            roles = ("collector", "processor", "query", "transfer_status") + (("privacy",) if revision == "a011" else ())
            privileges = [conn.execute("SELECT has_database_privilege(%s,current_database(),'CONNECT')", (name + "_" + role,)).fetchone()[0]
                          for role in roles]
        if state != [("O",)] or day_state != [("O",)] or any(privileges):
            raise ValueError("post-restore quarantine or trigger unverified")
    finally:
        spool.unlink(missing_ok=True)


def restore(backup_id, key_id, restore_id):
    artifact = path_for("artifacts", backup_id, ".enc")
    # Authentication completes before target creation, migrations or restore SQL.
    header, stage = crypto.decrypt_verified(artifact, BASE / "staging", key_for(key_id))
    metadata = header["metadata"]
    recorded_history = metadata.get("history")
    if not isinstance(recorded_history, dict) or not isinstance(recorded_history.get("head"), str):
        stage.unlink(missing_ok=True)
        raise ValueError("incompatible backup")
    try:
        expected = history_for_revision(recorded_history["head"])
    except Exception:
        stage.unlink(missing_ok=True)
        raise ValueError("incompatible backup") from None
    if (metadata.get("synthetic_only") is not True or metadata.get("history") != expected
            or metadata.get("format") != "pg16-custom-analytics-data-only"
            or not db.PATTERN.fullmatch(metadata.get("source_database", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", metadata.get("fixture_state_sha256", ""))):
        stage.unlink(missing_ok=True)
        raise ValueError("incompatible backup")
    # Only exact a011 has a reviewed day recovery protocol. A later revision
    # may introduce new tables or triggers and must not take the generic path.
    if expected["head"] != "a011" and "a011" in expected.get("chain", ()):
        stage.unlink(missing_ok=True)
        raise ValueError("restore revision is not supported")
    identifier(metadata["source_id"])
    config = connection_config()
    context = {"artifact_id": backup_id, "source_id": metadata["source_id"], "stage": stage.name,
               "crypto_verified": True, "data_restored": False,
               "fixture_state_sha256": metadata["fixture_state_sha256"]}
    record = provision(restore_id, "restore", config, expected, context=context)
    if record["database"] == metadata["source_database"]:
        raise ValueError("source and target must differ")
    record.update(state="restore_pending", artifact_id=backup_id, source_id=metadata["source_id"],
                  stage=stage.name, crypto_verified=True, data_restored=False, privacy_opened=False,
                  fixture_state_sha256=metadata["fixture_state_sha256"])
    write_record(restore_id, record)
    if expected["head"] == "a011":
        clear_fresh_defaults_a011(config, record)
    else:
        clear_fresh_defaults(config, record)
    if expected["head"] in {"a010", "a011"}:
        # A failed extraction must remain recorded as quarantined, not merely
        # be expected to reach the later restore transaction's revocation.
        record.update(application_access="revoked", projection_rebuild_required=True)
        write_record(restore_id, record)
    verify_container()
    from urllib.parse import unquote, urlsplit
    password = unquote(urlsplit(record["urls"]["migrate"]).password)
    if expected["head"] in {"a010", "a011"}:
        try:
            restore_transaction = restore_a011_transaction if expected["head"] == "a011" else restore_a010_transaction
            restore_transaction(stage, record, config, password)
        except Exception as error:
            reason = str(error) if type(error) is RuntimeError and str(error) in {
                "restore extraction failed: output destination required",
                "restore extraction failed: non-seekable archive",
                "restore extraction failed: unknown",
                "restore extraction timeout",
                "restore outcome needs reconciliation: sentinel permission denied",
                "restore outcome needs reconciliation: restore guard failed",
                "restore outcome needs reconciliation: permission denied",
                "restore outcome needs reconciliation: duplicate key",
                "restore outcome needs reconciliation: check constraint",
                "restore outcome needs reconciliation: syntax error",
                "restore outcome needs reconciliation: unknown",
                "restore outcome needs reconciliation: timeout",
                "restore outcome needs reconciliation: diagnostic incomplete"} else "unclassified"
            record["restore_failure"] = {"type": type(error).__name__, "category": reason}
            write_record(restore_id, record)
            raise
        record.update(projection_rebuild_required=True, application_access="revoked")
    else:
        args = docker_db_args("pg_restore", record["database"], f"{record['database']}_migrate",
                              ["--data-only", "--single-transaction", "--exit-on-error", "--no-owner", "--no-acl"])
        with stage.open("rb") as source:
            result = subprocess.run(args, stdin=source, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    env=password_env(password), timeout=MAX_SECONDS)
        if result.returncode:
            raise RuntimeError("restore outcome needs reconciliation")
    current(record, config, expected)
    record.update(state="restored_unverified", data_restored=True)
    write_record(restore_id, record)


def table_digests(config, record):
    """Bounded client memory: server cursor streams deterministic synthetic rows."""
    reject_retained(record)
    validate_db(record, config)
    result = {}
    with db.connect(config, record["database"]) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            tables = conn.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='analytics' ORDER BY tablename").fetchall()
            if not tables:
                raise ValueError("no analytics tables")
            for (name,) in tables:
                digest, count, size = hashlib.sha256(), 0, 0
                with conn.cursor(name="backup_parity_" + uuid4().hex) as cursor:
                    cursor.execute(sql.SQL("SELECT to_jsonb(t)::text FROM analytics.{} t ORDER BY 1").format(sql.Identifier(name)))
                    while rows := cursor.fetchmany(256):
                        for (text,) in rows:
                            encoded = text.encode("utf-8")
                            size += len(encoded)
                            count += 1
                            if count > 1_000_000 or size > crypto.MAX_BYTES:
                                raise ValueError("parity scan exceeds limit")
                            digest.update(len(encoded).to_bytes(8, "big"))
                            digest.update(encoded)
                result[name] = (count, digest.hexdigest())
    return result


def state_digest(tables):
    encoded = json.dumps(tables, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parity(restore_id):
    config = connection_config()
    restored = read_record(restore_id, "restore", "restored_unverified")
    expected = history_for_revision(restored["history"]["head"])
    if restored.get("history") != expected or not re.fullmatch(r"[0-9a-f]{64}", restored.get("fixture_state_sha256", "")):
        raise ValueError("restore metadata mismatch")
    current(restored, config, expected)
    target = table_digests(config, restored)
    if state_digest(target) != restored["fixture_state_sha256"] or history_for_revision(expected["head"]) != expected:
        raise ValueError("fixture parity is not established")
    restored.update(state="parity_verified", parity_verified=True, privacy_opened=False)
    write_record(restore_id, restored)
    name = restored.get("stage", "")
    if not re.fullmatch(r"\.verified-dump-[0-9a-f]{32}\.tmp", name):
        raise ValueError("unsafe staging path")
    stage = BASE / "staging" / name
    if stage.is_symlink() or stage.is_junction():
        raise ValueError("unsafe staging path")
    stage.unlink()


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid command arguments")


def main():
    parser = SafeParser()
    sub = parser.add_subparsers(dest="action", required=True, parser_class=SafeParser)
    for name, options in (("key-create", ("key",)), ("fixture-create", ("fixture",)),
                          ("backup", ("source", "backup", "key")),
                          ("verify", ("backup", "key")), ("restore", ("backup", "key", "restore")),
                          ("parity", ("restore",))):
        command = sub.add_parser(name)
        for option in options:
            command.add_argument("--" + option, required=True, type=identifier)
        if name == "fixture-create":
            command.add_argument("--revision", default=None)
    args = parser.parse_args()
    tree()
    if args.action == "key-create":
        new_file(path_for("keys", args.key, ".key"), os.urandom(32))
    elif args.action == "fixture-create":
        expected = history() if args.revision is None else history_for_revision(args.revision)
        provision(args.fixture, "fixture", connection_config(), expected)
    elif args.action == "backup":
        backup(args.source, args.backup, args.key)
    elif args.action == "verify":
        _, stage = crypto.decrypt_verified(path_for("artifacts", args.backup, ".enc"), BASE / "staging", key_for(args.key))
        stage.unlink()
    elif args.action == "restore":
        restore(args.backup, args.key, args.restore)
    else:
        parity(args.restore)
    print(json.dumps({"operation": args.action, "completed": True,
                      "crypto_verified": args.action in {"backup", "verify", "restore", "parity"},
                      "data_restored": args.action in {"restore", "parity"},
                      "parity_verified": args.action == "parity", "privacy_opened": False}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Operation failed or outcome is unknown; preserve state and reconcile before retry.", file=sys.stderr)
        raise SystemExit(2) from None
