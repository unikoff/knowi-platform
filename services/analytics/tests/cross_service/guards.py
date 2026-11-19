"""Fail-closed target checks for the explicitly prepared local HTTP scenario."""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

CORE_NAME = re.compile(r"analytics_delivery_test_[0-9a-f]{32}\Z")
ANALYTICS_NAME = re.compile(r"analytics_test_[0-9a-f]{32}\Z")
TOKEN = re.compile(r"[0-9a-f]{64}\Z")


def loopback_url(value: str, port: int) -> None:
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.port != port
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("invalid local service address")


def validate(config: dict) -> dict:
    if set(config) != {"core_url", "collector_url", "query_url", "privacy_url", "databases", "users", "browser_users", "course_id", "catalog_version", "roster"}:
        raise ValueError("invalid fixture configuration")
    for name, port in (("core_url", 18151), ("collector_url", 18152), ("query_url", 18153), ("privacy_url", 18154)):
        loopback_url(config[name], port)
    if set(config["databases"]) != {"core", "analytics"}:
        raise ValueError("both disposable databases required")
    for key, pattern, port in (("core", CORE_NAME, 57382), ("analytics", ANALYTICS_NAME, 57392)):
        item = config["databases"][key]
        if set(item) != {"dsn", "name", "owner", "sentinel"} or not pattern.fullmatch(item["name"]) or not TOKEN.fullmatch(item["sentinel"]):
            raise ValueError("invalid disposable database identity")
        parsed = urlsplit(item["dsn"])
        if (parsed.scheme != "postgresql" or parsed.hostname != "127.0.0.1" or parsed.port != port
                or parsed.path != "/" + item["name"] or not parsed.username or parsed.query or parsed.fragment):
            raise ValueError("invalid disposable database address")
        if item["owner"] != (item["name"] + "_migrate" if key == "analytics" else "pymentor_test"):
            raise ValueError("invalid disposable database owner")
    if set(config["users"]) != {"admin", "leader", "student", "other"}:
        raise ValueError("four distinct synthetic users required")
    if set(config["browser_users"]) != {"admin", "leader", "student"}:
        raise ValueError("three separate browser users required")
    if config["browser_users"]["leader"] != config["users"]["leader"]:
        raise ValueError("browser leader must be the configured synthetic leader")
    refs, ids = set(), set()
    for user in (*config["users"].values(), config["browser_users"]["admin"], config["browser_users"]["student"]):
        if set(user) != {"id", "email", "password", "subject_ref"}:
            raise ValueError("invalid user fixture")
        if type(user["id"]) is not int or user["id"] <= 0 or not user["email"].endswith("@example.test") or not user["password"]:
            raise ValueError("invalid synthetic user")
        if str(UUID(user["subject_ref"])) != user["subject_ref"]:
            raise ValueError("invalid subject")
        refs.add(user["subject_ref"]); ids.add(user["id"])
    if len(refs) != 6 or len(ids) != 6 or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", config["course_id"]):
        raise ValueError("fixture identities must be distinct")
    if not isinstance(config["catalog_version"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", config["catalog_version"]):
        raise ValueError("invalid fixture catalog version")
    roster = config["roster"]
    expected = {config["users"]["student"]["subject_ref"], config["browser_users"]["student"]["subject_ref"]}
    allowed = (expected, expected | {config["browser_users"]["admin"]["subject_ref"]})
    if not isinstance(roster, list) or len(roster) != len(set(roster)) or set(roster) not in allowed:
        raise ValueError("unexpected complete synthetic roster")
    return config


def load(path: str, root: Path) -> dict:
    file = Path(path).resolve(strict=True)
    private = (root / ".local").resolve()
    if private.parent != root.resolve(strict=True) or not file.is_relative_to(private) or file == private or not file.is_file():
        raise ValueError("fixture must be in Analytics private local directory")
    if file.stat().st_size > 65536:
        raise ValueError("fixture configuration too large")
    return validate(json.loads(file.read_text(encoding="utf-8")))


def verify_databases(config: dict) -> None:
    # Read-only checks precede every HTTP mutation. This does not create or remove
    # a database; the process that created it retains ownership of cleanup.
    import psycopg
    for kind in ("core", "analytics"):
        target = config["databases"][kind]
        with psycopg.connect(target["dsn"], connect_timeout=3, options="-c statement_timeout=3000 -c default_transaction_read_only=on") as conn:
            actual = conn.execute("SELECT current_database(), pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone()
            marker = conn.execute("SELECT token FROM public.cross_service_sentinel").fetchall()
            if actual != (target["name"], target["owner"]) or marker != [(target["sentinel"],)]:
                raise ValueError("disposable database owner or sentinel mismatch")
