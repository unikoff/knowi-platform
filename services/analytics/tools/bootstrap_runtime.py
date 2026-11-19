"""Provision exactly one durable local synthetic DB; never delete or reseed."""
import json
import hashlib
import os
from contextlib import contextmanager
from pathlib import Path
import re
import secrets
import subprocess
import sys
from urllib.parse import quote, urlsplit
from psycopg import sql

import bootstrap_postgres as db
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
RECORD = LOCAL / "runtime.json"
CONNECTION = LOCAL / "postgres-connection.json"
PROJECT = "knowi-analytics-01a1df75"
LOCK = LOCAL / "runtime.operation.lock"


@contextmanager
def operation_lock():
    LOCAL.mkdir(exist_ok=True)
    if LOCAL.is_symlink():
        raise ValueError("unsafe metadata path")
    fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        yield
    finally:
        LOCK.unlink()


def schema_history(target_revision=None):
    """Return one linear, content-fingerprinted Alembic history, newest first."""
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    heads = scripts.get_heads()
    if len(heads) != 1:
        raise ValueError("migration history must have exactly one head")
    revisions = list(scripts.walk_revisions())
    if not revisions or revisions[0].revision != heads[0]:
        raise ValueError("invalid migration history")
    if target_revision is not None:
        matches = [i for i, revision in enumerate(revisions) if revision.revision == target_revision]
        if len(matches) != 1:
            raise ValueError("unknown target revision")
        revisions = revisions[matches[0]:]
    digest = hashlib.sha256()
    for index, revision in enumerate(revisions):
        expected = revisions[index + 1].revision if index + 1 < len(revisions) else None
        if revision.down_revision != expected or revision.dependencies:
            raise ValueError("migration history is not linear")
        digest.update(revision.revision.encode("utf-8") + b"\0")
        digest.update(Path(revision.path).read_bytes())
    return revisions[0].revision, tuple(rev.revision for rev in revisions), digest.hexdigest()


def current_revision(rows, revisions, *, allow_empty=False):
    if not rows and allow_empty:
        return None
    if len(rows) != 1 or len(rows[0]) != 1 or rows[0][0] not in revisions:
        raise ValueError("unknown or multiple database revisions")
    return rows[0][0]


def write_record(data, *, exclusive=False):
    LOCAL.mkdir(exist_ok=True)
    if LOCAL.is_symlink() or RECORD.is_symlink():
        raise ValueError("unsafe metadata path")
    if exclusive:
        fd = os.open(RECORD, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out)
            out.flush()
            os.fsync(out.fileno())
    else:
        temp = LOCAL / "runtime.json.pending"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, RECORD)


def validate_record(record):
    if (record.get("synthetic_only") is not True or record.get("do_not_drop") is not True
            or record.get("project") != PROJECT or record.get("state") not in {"migration_pending", "role_pending", "ready"}):
        raise ValueError("incomplete or unsafe runtime metadata; reconciliation required")
    name, token, urls = record.get("database"), record.get("nonce"), record.get("urls")
    if not isinstance(name, str) or not db.PATTERN.fullmatch(name) or not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ValueError("invalid runtime identity")
    if not isinstance(urls, dict) or set(urls) not in (set(db.ROLES), set(db.ROLES) | {db.TRANSFER_STATUS_ROLE}):
        raise ValueError("invalid runtime role set")
    for role, url in urls.items():
        try:
            p = urlsplit(url)
            if (p.scheme != "postgresql" or p.hostname != "127.0.0.1" or p.port != 57392
                    or p.username != f"{name}_{role}" or not p.password or p.path != f"/{name}"
                    or p.query or p.fragment):
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("invalid runtime role URL") from None
    return record


def read_record():
    if LOCAL.is_symlink() or RECORD.is_symlink():
        raise ValueError("unsafe metadata path")
    return validate_record(json.loads(RECORD.read_text(encoding="utf-8")))


def verify_database(config, record):
    validate_record(record)
    name = record["database"]
    with db.connect(config, name) as conn:
        if conn.execute("SELECT token FROM public.analytics_test_sentinel").fetchall() != [(record["nonce"],)]:
            raise ValueError("runtime sentinel mismatch")
        has_revision = conn.execute("SELECT to_regclass('public.alembic_version')").fetchone()[0]
        revision = conn.execute("SELECT version_num FROM public.alembic_version").fetchall() if has_revision else []
    with db.connect(config) as conn:
        owner = conn.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (name,)).fetchone()
    if owner != (f"{name}_migrate",):
        raise ValueError("runtime owner mismatch")
    return revision


def reconcile_pending(config, record, history):
    head, revisions, digest = history
    if record.get("target_revision") != head or record.get("history_sha256") != digest:
        raise ValueError("pending target or migration history differs; reconcile manually")
    revision = current_revision(verify_database(config, record), revisions, allow_empty=True)
    if revision != head:
        raise ValueError("pending migration outcome unresolved; reconcile manually")
    record["state"] = "ready"
    write_record(record)


def migrate_to_head(config, record, history):
    head, _, digest = history
    record.update(state="migration_pending", target_revision=head, history_sha256=digest)
    write_record(record)
    env = os.environ.copy()
    env["ANALYTICS_MIGRATE_DATABASE_URL"] = record["urls"]["migrate"]
    # Even a failed or timed-out invocation can have committed: reconcile first.
    try:
        subprocess.run([sys.executable, "-B", "-m", "pymentor_analytics.app.cli", "migrate", "--revision", head],
                       cwd=ROOT, env=env, capture_output=True, timeout=90)
    except subprocess.TimeoutExpired:
        pass
    if schema_history() != history:
        raise ValueError("migration history changed; reconcile manually")
    reconcile_pending(config, record, history)


def provision_transfer_status(config, record):
    """Provision once after durable intent; interrupted attempts require reconciliation."""
    if record["state"] != "ready" or db.TRANSFER_STATUS_ROLE in record["urls"]:
        raise ValueError("unexpected transfer status provisioning state")
    name = record["database"]
    role = f"{name}_{db.TRANSFER_STATUS_ROLE}"
    with db.connect(config) as conn:
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone() is not None:
            raise ValueError("transfer status role exists; reconcile manually")
    password = secrets.token_urlsafe(32)
    record["urls"][db.TRANSFER_STATUS_ROLE] = f"postgresql://{role}:{quote(password, safe='')}@127.0.0.1:57392/{name}"
    record["state"] = "role_pending"
    write_record(record)
    with db.connect(config) as conn:
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT")
                     .format(sql.Identifier(role), sql.Literal(password)))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}")
                     .format(sql.Identifier(name), sql.Identifier(role)))
    record["state"] = "ready"
    write_record(record)


def upgrade():
    config = db.config(CONNECTION)
    history = schema_history()
    record = read_record()
    if record["state"] == "role_pending":
        raise ValueError("role provisioning outcome unresolved; reconcile manually")
    if record["state"] == "migration_pending":
        reconcile_pending(config, record, history)
        return
    current = current_revision(verify_database(config, record), history[1])
    if current == history[0]:
        if db.TRANSFER_STATUS_ROLE in db.roles_for_revision(current) and db.TRANSFER_STATUS_ROLE not in record["urls"]:
            raise ValueError("current schema requires the transfer status role; reconcile manually")
        return
    if db.TRANSFER_STATUS_ROLE in db.roles_for_revision(history[0]) and db.TRANSFER_STATUS_ROLE not in record["urls"]:
        provision_transfer_status(config, record)
    migrate_to_head(config, record, history)


def init():
    config = db.config(CONNECTION)
    history = schema_history()
    if RECORD.exists():
        record = read_record()
        if record["state"] == "role_pending":
            raise ValueError("role provisioning outcome unresolved; reconcile manually")
        if record["state"] == "migration_pending":
            reconcile_pending(config, record, history)
        else:
            current_revision(verify_database(config, record), history[1])
        return
    # A refused connection must not create a misleading partial-provision marker.
    with db.connect(config):
        pass
    write_record({"synthetic_only": True, "do_not_drop": True, "project": PROJECT,
                  "state": "provisioning"}, exclusive=True)
    name, nonce, urls = db.create(config, revision=history[0])
    record = {"synthetic_only": True, "do_not_drop": True, "project": PROJECT,
              "state": "migration_pending", "database": name, "nonce": nonce, "urls": urls}
    write_record(record)
    current_revision(verify_database(config, record), history[1], allow_empty=True)
    migrate_to_head(config, record, history)


if __name__ == "__main__":
    try:
        with operation_lock():
            init()
        print("Synthetic runtime database initialized or safely reconciled.")
    except Exception:
        print("Initialization stopped; preserve metadata and reconcile before retry.", file=sys.stderr)
        raise SystemExit(2) from None
