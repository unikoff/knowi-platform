"""Prepare only the independent synthetic PostgreSQL credentials; never overwrite."""
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from uuid import uuid4

LOCAL = Path(__file__).resolve().parents[1] / ".local"
PROJECT = "knowi-analytics-01a1df75"
FIELDS = {"project", "synthetic_only", "host", "port", "user", "database", "password"}


def ensure_directory(folder):
    if folder.is_symlink() or folder.is_junction():
        raise ValueError("unsafe directory")
    folder.mkdir(exist_ok=True)
    if folder.is_symlink() or folder.is_junction() or not folder.is_dir():
        raise ValueError("unsafe directory")


def file_exists(path):
    if path.is_symlink() or path.is_junction():
        raise ValueError("unsafe file")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("unsafe file")
    return True


def create_exclusive(path, content):
    # Link a fully written private temporary file: no partial target or replacement.
    temporary = path.with_name("." + path.name + "." + uuid4().hex + ".pending")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def reject_pending(folder, paths):
    for path in paths:
        if any(folder.glob("." + path.name + ".*.pending")):
            raise ValueError("unfinished atomic creation requires reconciliation")


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate metadata field")
        result[key] = value
    return result


def validate(data):
    if (not isinstance(data, dict) or set(data) != FIELDS or data["project"] != PROJECT
            or data["synthetic_only"] is not True or data["host"] != "127.0.0.1"
            or type(data["port"]) is not int or data["port"] != 57392
            or data["user"] != "analytics_bootstrap" or data["database"] != "analytics_bootstrap"
            or not isinstance(data["password"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", data["password"])):
        raise ValueError("unknown PostgreSQL metadata")


def setup(folder=LOCAL):
    ensure_directory(folder)
    target, env = folder / "postgres-connection.json", folder / "postgres.env"
    reject_pending(folder, (target, env))
    has_metadata, has_env = file_exists(target), file_exists(env)
    if has_env and not has_metadata:
        raise ValueError("environment has no matching metadata")
    if has_metadata:
        data = json.loads(target.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)
        validate(data)
    else:
        data = {"project": PROJECT, "synthetic_only": True, "host": "127.0.0.1", "port": 57392,
                "user": "analytics_bootstrap", "database": "analytics_bootstrap",
                "password": secrets.token_urlsafe(32)}
    expected = (f"POSTGRES_USER={data['user']}\nPOSTGRES_PASSWORD={data['password']}\n"
                f"POSTGRES_DB={data['database']}\n")
    if has_env and env.read_text(encoding="utf-8") != expected:
        raise ValueError("environment differs from metadata")
    if not has_metadata:
        create_exclusive(target, (json.dumps(data, indent=2) + "\n").encode("utf-8"))
    if not has_env:
        create_exclusive(env, expected.encode("utf-8"))


if __name__ == "__main__":
    try:
        setup()
        print("Synthetic PostgreSQL credentials prepared or verified.")
    except Exception:
        print("PostgreSQL setup refused; preserve existing files and reconcile.", file=sys.stderr)
        raise SystemExit(2) from None
