"""Create isolated synthetic test DBs only. No Core connections or generic deletion."""
import json
import re
import secrets
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import psycopg
from psycopg import sql

ROLES = ("migrate", "collector", "processor", "query", "privacy")
TRANSFER_STATUS_ROLE = "transfer_status"
PATTERN = re.compile(r"analytics_test_[0-9a-f]{32}\Z")


def config(path: Path, *, benchmark=False):
    data = json.loads(path.read_text(encoding="utf-8"))
    validate(data, benchmark=benchmark)
    return data


def validate(data, *, benchmark=False):
    if type(benchmark) is not bool: raise ValueError("invalid benchmark option")
    allowed = {("knowi-analytics-s16-benchmark",62332), ("knowi-analytics-s16-benchmark-f",62333)} if benchmark else {("knowi-analytics-01a1df75",57392)}
    if (not isinstance(data,dict) or (benchmark and set(data)!={"project","synthetic_only","host","port","user","database","password"})
            or data.get("synthetic_only") is not True or data.get("host") != "127.0.0.1"
            or type(data.get("port")) is not int or (data.get("project"),data.get("port")) not in allowed or data.get("user") != "analytics_bootstrap"
            or data.get("database") != "analytics_bootstrap"
            or not isinstance(data.get("password"), str) or not data["password"]):
        raise ValueError("only the designated synthetic Analytics cluster is permitted")


def connect(data, dbname=None, *, benchmark=False):
    validate(data, benchmark=benchmark)
    if dbname is not None and not PATTERN.fullmatch(dbname):
        raise ValueError("invalid disposable database")
    return psycopg.connect(host=data["host"], port=data["port"], user=data["user"],
                           password=data["password"], dbname=dbname or data["database"],
                           connect_timeout=5, autocommit=True)


def roles_for_revision(revision):
    if not isinstance(revision, str) or not re.fullmatch(r"a[0-9]{3}", revision):
        raise ValueError("explicit migration revision required")
    return ROLES + ((TRANSFER_STATUS_ROLE,) if int(revision[1:]) >= 7 else ())


def create(data, *, revision="a006"):
    name = "analytics_test_" + uuid4().hex
    token = secrets.token_hex(32)
    roles = roles_for_revision(revision)
    passwords = {role: secrets.token_urlsafe(32) for role in roles}
    with connect(data) as conn:
        for role in roles:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT")
                         .format(sql.Identifier(f"{name}_{role}"), sql.Literal(passwords[role])))
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}")
                     .format(sql.Identifier(name), sql.Identifier(f"{name}_migrate")))
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
        for role in roles:
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}")
                         .format(sql.Identifier(name), sql.Identifier(f"{name}_{role}")))
    with connect(data, name) as conn:
        conn.execute("CREATE TABLE public.analytics_test_sentinel (token text NOT NULL)")
        conn.execute("INSERT INTO public.analytics_test_sentinel VALUES (%s)", (token,))
        conn.execute("REVOKE ALL ON public.analytics_test_sentinel FROM PUBLIC")
        conn.execute(sql.SQL("GRANT SELECT ON public.analytics_test_sentinel TO {}")
                     .format(sql.Identifier(f"{name}_migrate")))
    urls = {role: f"postgresql://{name}_{role}:{quote(passwords[role], safe='')}@127.0.0.1:57392/{name}"
            for role in roles}
    return name, token, urls


def cleanup(data, name, token, *, revision="a006"):
    """Delete only the DB we just created, after exact-name and sentinel checks."""
    if not PATTERN.fullmatch(name) or not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ValueError("invalid disposable target")
    with connect(data, name) as conn:
        result = conn.execute("SELECT token FROM public.analytics_test_sentinel").fetchall()
        if result != [(token,)]:
            raise ValueError("disposable database sentinel mismatch")
    with connect(data) as conn:
        owner = conn.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (name,)).fetchone()
        if owner != (f"{name}_migrate",):
            raise ValueError("disposable database owner mismatch")
        conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        for role in roles_for_revision(revision):
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(f"{name}_{role}")))
