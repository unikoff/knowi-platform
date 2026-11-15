#!/usr/bin/env python3
"""Run a synthetic PostgreSQL 16 c503 backup/restore rehearsal.

The only database endpoint this helper accepts is the GitHub Actions/local
synthetic service at 127.0.0.1:57382 as pymentor_test. It never reads dotenv,
application settings, SQLite files, or production credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "services" / "core-api"
OLD = "c503_dual_email_change"
NEW = "c504_course_progress"
BASE_URL = "postgresql://pymentor_test:pymentor_test@127.0.0.1:57382/pymentor_test"
NAME_RE = re.compile(r"c504_(?:backup|restore)_ci_[0-9a-f]{32}\Z")


class RehearsalError(RuntimeError):
    """A safety guard or rehearsal assertion failed."""


def _allowlisted_url(raw: str):
    try:
        url = make_url(raw)
        valid = (
            url.drivername in {"postgresql", "postgresql+psycopg"}
            and (url.host, url.port, url.database, url.username)
            == ("127.0.0.1", 57382, "pymentor_test", "pymentor_test")
            and url.password == "pymentor_test"
            and not url.query
        )
    except Exception:
        valid = False
    if not valid:
        raise RehearsalError("PostgreSQL URL outside synthetic rehearsal allowlist") from None
    return url


def _target_metadata(inspect_result: Any) -> dict[str, Any]:
    """Validate only the explicitly selected Docker target, ignoring labels."""
    if not isinstance(inspect_result, list) or len(inspect_result) != 1:
        raise RehearsalError("Docker target must resolve to exactly one container")
    item = inspect_result[0]
    config = item.get("Config") or {}
    image = config.get("Image", "")
    if not re.fullmatch(r"postgres:16(?:[-@][A-Za-z0-9_.:+-]+)?", image):
        raise RehearsalError("Docker target image is not PostgreSQL 16")
    ports = ((item.get("NetworkSettings") or {}).get("Ports") or {}).get("5432/tcp")
    if not isinstance(ports, list) or len(ports) != 1:
        raise RehearsalError("Docker target must publish only 127.0.0.1:57382 to PostgreSQL")
    binding = ports[0]
    if (binding.get("HostIp"), str(binding.get("HostPort"))) != ("127.0.0.1", "57382"):
        raise RehearsalError("Docker target must publish PostgreSQL at 127.0.0.1:57382")
    return item


def _docker(args, *parts: str, input_bytes: bytes | None = None, timeout: int = 120):
    try:
        result = subprocess.run([args.docker, *parts], input=input_bytes,
                                capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RehearsalError(f"Docker command failed ({parts[0]}): {type(exc).__name__}") from None
    if result.returncode:
        # Never echo command output: database clients can include connection details.
        raise RehearsalError(f"Docker {parts[0]} failed (exit {result.returncode})")
    return result.stdout


def _inspect(args, target: str) -> dict[str, Any]:
    try:
        output = _docker(args, "inspect", target)
        parsed = json.loads(output)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RehearsalError("Docker inspect returned invalid metadata") from None
    return _target_metadata(parsed)


def _connect(url=None):
    target = url or _allowlisted_url(BASE_URL)
    if (target.drivername not in {"postgresql", "postgresql+psycopg"}
            or (target.host, target.port, target.username, target.password)
            != ("127.0.0.1", 57382, "pymentor_test", "pymentor_test")
            or target.query
            or (target.database != "pymentor_test" and not NAME_RE.fullmatch(target.database or ""))):
        raise RehearsalError("PostgreSQL connection target outside synthetic rehearsal allowlist")
    connection = psycopg.connect(target.render_as_string(hide_password=False), autocommit=True)
    try:
        _verify_server(connection, target.database)
    except Exception:
        connection.close()
        raise
    return connection


def _verify_server(connection, expected_database="pymentor_test") -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT current_database(), current_user, current_setting('server_version_num')::int")
        database, user, version = cur.fetchone()
    if (database, user) != (expected_database, "pymentor_test") or not 160000 <= version < 170000:
        raise RehearsalError("Connected target is not the synthetic PostgreSQL 16 database")


def _db_url(name: str):
    if not NAME_RE.fullmatch(name):
        raise RehearsalError("Refusing database name outside this rehearsal's generated-name pattern")
    return _allowlisted_url(BASE_URL).set(database=name)


def _migrate(url, action: str, revision: str) -> None:
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    env.update({
        "KNOWI_MIGRATION_URL": url.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False),
        "KNOWI_MIGRATION_HOST": "127.0.0.1",
        "KNOWI_MIGRATION_DATABASE": url.database,
        "KNOWI_MIGRATION_USER": "pymentor_test",
    })
    script = (
        "from alembic import command; from alembic.config import Config; import sys; "
        "c=Config(sys.argv[1]); c.set_main_option('core_runtime_migrations','true'); "
        "getattr(command,sys.argv[2])(c,sys.argv[3])"
    )
    try:
        result = subprocess.run([sys.executable, "-c", script, str(CORE / "alembic-runtime.ini"), action, revision],
                                env=env, cwd=CORE, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RehearsalError(f"Alembic {action} failed ({type(exc).__name__})") from None
    if result.returncode:
        raise RehearsalError(f"Alembic {action} failed (exit {result.returncode})")


def _snapshot(connection) -> dict[str, list[tuple]]:
    result = {}
    with connection.cursor() as cur:
        for table, columns in (
            ("users", "id, username, email, password_hash"),
            ("user_lesson_progress", "id, user_id, lesson_id, completed_at"),
            ("user_task_progress", "id, user_id, task_id, task_revision, completed_at"),
        ):
            cur.execute(sql.SQL("SELECT {} FROM {} ORDER BY id").format(
                sql.SQL(columns), sql.Identifier(table)))
            rows = cur.fetchall()
            result[table] = [tuple(value.isoformat() if hasattr(value, "isoformat") else value for value in row)
                             for row in rows]
    return result


def _digest(snapshot: dict[str, list[tuple]]) -> str:
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _seed(connection) -> None:
    with connection.cursor() as cur:
        cur.execute("INSERT INTO users (id,username,email,password_hash) VALUES (91,'Synthetic','c504@example.test','synthetic')")
        cur.execute("INSERT INTO user_lesson_progress (id,user_id,lesson_id,completed_at) VALUES (101,91,'historical-урок','2020-01-02 03:04:05+00')")
        cur.execute("INSERT INTO user_task_progress (id,user_id,task_id,task_revision,completed_at) VALUES (201,91,'historical-задача','revision-α','2021-02-03 04:05:06+00')")


def _add_copy_progress(connection) -> None:
    with connection.cursor() as cur:
        cur.execute("INSERT INTO user_lesson_progress (id,user_id,lesson_id,course_id) VALUES (102,91,'historical-урок','backend_copy')")
        cur.execute("INSERT INTO user_task_progress (id,user_id,task_id,task_revision,course_id) VALUES (202,91,'historical-задача','copy-revision','backend_copy')")


def _assert_backend_rows(connection, before: dict[str, list[tuple]]) -> None:
    current = _snapshot(connection)
    for table in ("user_lesson_progress", "user_task_progress"):
        if current[table] != before[table]:
            raise RehearsalError(f"Historical rows changed in {table}")
        with connection.cursor() as cur:
            cur.execute(sql.SQL("SELECT DISTINCT course_id FROM {}").format(sql.Identifier(table)))
            if cur.fetchall() != [("backend",)]:
                raise RehearsalError(f"Unexpected course assignment in {table}")


def _drop_created(admin, names: list[str]) -> list[str]:
    problems = []
    for name in reversed(names):
        if not NAME_RE.fullmatch(name):
            problems.append("generated database name failed cleanup guard")
            continue
        try:
            with admin.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
                exists = cur.fetchone() is not None
                if exists:
                    cur.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        except Exception as exc:
            # Reconcile the outcome once; never blindly retry a DROP.
            try:
                with admin.cursor() as cur:
                    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
                    remains = cur.fetchone() is not None
                if remains:
                    problems.append(f"generated database remains after failed cleanup ({type(exc).__name__})")
            except Exception as inspect_exc:
                problems.append(f"cleanup outcome unknown after inspection ({type(inspect_exc).__name__})")
    return problems


def run(args) -> dict[str, Any]:
    if not args.container or not args.container.strip():
        raise RehearsalError("--container is required")
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        raise RehearsalError("--output must name a new, nonexistent folder")
    docker_path = args.docker or shutil.which("docker")
    if not docker_path:
        raise RehearsalError("Docker executable was not found")
    args.docker = docker_path
    _inspect(args, args.container)
    client_version = _docker(args, "exec", args.container, "pg_dump", "--version").decode("ascii", "replace").strip()
    if not re.search(r"\b16\.", client_version):
        raise RehearsalError("Docker target pg_dump client is not PostgreSQL 16")

    admin = _connect()
    created: list[str] = []
    cleanup_problems: list[str] = []
    try:
        _verify_server(admin)
        # The output folder is created only after both container and live-server guards pass.
        output.mkdir(parents=True, exist_ok=False)
        names = ["c504_backup_ci_" + uuid4().hex, "c504_restore_ci_" + uuid4().hex]
        if any(not NAME_RE.fullmatch(name) for name in names):
            raise RehearsalError("Generated database name failed its strict guard")
        for name in names:
            try:
                with admin.cursor() as cur:
                    cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            except Exception as exc:
                # Reconcile a possibly lost CREATE result, but never adopt or drop an
                # ambiguously owned database merely because its random name exists.
                try:
                    with admin.cursor() as cur:
                        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
                        exists = cur.fetchone() is not None
                except Exception as inspect_exc:
                    raise RehearsalError(f"CREATE DATABASE outcome unknown after inspection ({type(inspect_exc).__name__})") from None
                state = "exists with ambiguous ownership" if exists else "was not created"
                raise RehearsalError(f"CREATE DATABASE {state} ({type(exc).__name__}); no retry attempted") from None
            created.append(name)
        backup_url, restore_url = map(_db_url, names)
        _migrate(backup_url, "upgrade", OLD)
        with _connect(backup_url) as source:
            _seed(source)
            baseline = _snapshot(source)
        if len(baseline["users"]) != 1 or len(baseline["user_lesson_progress"]) != 1 or len(baseline["user_task_progress"]) != 1:
            raise RehearsalError("Synthetic c503 seed counts did not match expected one-row fixtures")

        dump = _docker(args, "exec", "-i", args.container, "pg_dump", "-U", "pymentor_test", "-d", names[0],
                       "--format=custom", "--no-owner", "--no-acl", timeout=180)
        if not dump:
            raise RehearsalError("pg_dump returned an empty archive")
        dump_path = output / "c504-synthetic.dump"
        dump_path.write_bytes(dump)
        dump_sha256 = hashlib.sha256(dump).hexdigest()

        _migrate(backup_url, "upgrade", NEW)
        with _connect(backup_url) as source:
            _assert_backend_rows(source, baseline)
            _add_copy_progress(source)
            after_copy = _snapshot(source)
            if len(after_copy["user_lesson_progress"]) != 2 or len(after_copy["user_task_progress"]) != 2:
                raise RehearsalError("BackendCopy synthetic rows were not added after backup")

        _docker(args, "exec", "-i", args.container, "pg_restore", "--exit-on-error", "--no-owner", "--no-acl",
                "-U", "pymentor_test", "-d", names[1], input_bytes=dump, timeout=180)
        with _connect(restore_url) as restored:
            with restored.cursor() as cur:
                cur.execute("SELECT version_num FROM alembic_version")
                if cur.fetchone() != (OLD,):
                    raise RehearsalError("Restored backup did not preserve c503 revision")
            restored_rows = _snapshot(restored)
            if restored_rows != baseline or _digest(restored_rows) != _digest(baseline):
                raise RehearsalError("Restored c503 rows did not match the exact pre-backup snapshot")
        _migrate(restore_url, "upgrade", NEW)
        with _connect(restore_url) as restored:
            _assert_backend_rows(restored, baseline)
            _add_copy_progress(restored)
            with restored.cursor() as cur:
                cur.execute("SELECT DISTINCT course_id FROM user_lesson_progress ORDER BY course_id")
                lesson_courses = cur.fetchall()
                cur.execute("SELECT DISTINCT course_id FROM user_task_progress ORDER BY course_id")
                task_courses = cur.fetchall()
            if lesson_courses != [("backend",), ("backend_copy",)] or task_courses != [("backend",), ("backend_copy",)]:
                raise RehearsalError("Backend and BackendCopy progress isolation failed after restore upgrade")

        report = {
            "status": "PASS",
            "postgres_major": 16,
            "revisions": {"backup": OLD, "upgraded": NEW},
            "checks": ["container image and loopback port validated", "server database/user/version validated",
                       "pg_dump 16 client confirmed",
                       "c503 fixture snapshot preserved through c504 upgrade", "custom-format pg_dump created",
                       "pg_restore recreated exact c503 fixture snapshot", "restored database upgraded to c504",
                       "BackendCopy isolation checked in upgraded source and restored database"],
            "backup_sha256": dump_sha256,
            "fixture_row_hash_sha256": _digest(baseline),
            "post_backup_writes": "Synthetic BackendCopy lesson/task rows were added after pg_dump and are intentionally absent from the restored c503 snapshot; both databases were independently upgraded and isolation checked.",
            "synthetic_database_names": names,
        }
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report
    finally:
        # All per-database contexts leave before cleanup; admin stays on pymentor_test.
        cleanup_problems.extend(_drop_created(admin, created))
        admin.close()
        if cleanup_problems:
            raise RehearsalError("Cleanup needs inspection: " + "; ".join(cleanup_problems))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True, help="Existing PostgreSQL 16 Docker container id or name")
    parser.add_argument("--output", required=True, help="New local folder for the synthetic dump and report")
    parser.add_argument("--docker", help="Docker executable path; defaults to PATH lookup")
    args = parser.parse_args(argv)
    try:
        report = run(args)
    except Exception as exc:
        # Failures do not print the synthetic URL, password, or row data.
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

