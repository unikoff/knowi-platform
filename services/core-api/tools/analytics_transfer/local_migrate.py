"""Explicit, guarded installation into the protected local Core database only."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from pymentor_core.adapters.sql.analytics_delivery_ops import addon_migration
from pymentor_core.modules.analytics_delivery import local_collection

CORE = Path(__file__).resolve().parents[2]
ANALYTICS = CORE.parent / "analytics"


def _read_json(path):
    if path.is_symlink() or os.path.isjunction(path) or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("unsafe local metadata")
    return json.loads(path.read_text(encoding="utf-8"))


def target(dataset_id):
    if str(UUID(dataset_id)) != dataset_id or UUID(dataset_id).int == 0:
        raise ValueError("invalid dataset")
    base = CORE / ".local" / "role-admin"
    for path in (base,):
        if path.is_symlink() or os.path.isjunction(path):
            raise ValueError("unsafe local metadata")
    meta = _read_json(base / "browser-dataset.json")
    name = meta.get("database")
    if (meta.get("do_not_drop") is not True or meta.get("durable_postgres") is not True
            or meta.get("schema_revision") != "c528_course_leaders"
            or not isinstance(name, str) or not re.fullmatch(r"c527_role_ci_[0-9a-f]{32}", name)):
        raise ValueError("invalid protected database")
    parsed = make_url(os.environ["DATABASE_URL"])
    if (parsed.drivername != "postgresql+psycopg" or parsed.host != "127.0.0.1" or parsed.port != 57382
            or parsed.username != "pymentor_test" or parsed.database != name or parsed.query):
        raise ValueError("database target mismatch")
    folder = ANALYTICS / ".local" / "local-live" / dataset_id
    owner = _read_json(folder / "owner.json")
    if (owner.get("version") != 1 or owner.get("mode") != "local" or owner.get("do_not_drop") is not True
            or owner.get("instance_id") != dataset_id):
        raise ValueError("invalid local dataset owner")
    env = _read_json(folder / "core-env.json")
    if (env.get("PYMENTOR_ANALYTICS_LOCAL_DATASET_ID") != dataset_id
            or env.get("PYMENTOR_ANALYTICS_LOCAL_INSTANCE_ID") != owner.get("core_instance_id")):
        raise ValueError("dataset source mismatch")
    config = local_collection.configure(env, parsed.render_as_string(hide_password=False))
    if config is None or config.catalog_binding.dataset_id != dataset_id:
        raise ValueError("local source unavailable")
    return parsed, config


def _quiescence_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", 18041))
        sock.listen(1)
        return sock
    except BaseException:
        sock.close()
        raise


def run(dataset_id, *, apply=False):
    parsed, config = target(dataset_id)
    # Keep the listening port reserved through commit, preventing the normal Core writer from starting.
    with _quiescence_socket():
        engine = create_engine(parsed, poolclass=NullPool, hide_parameters=True,
                               connect_args={"connect_timeout": 3, "options": "-c statement_timeout=30000 -c lock_timeout=3000"})
        try:
            with engine.begin() as connection:
                identity = connection.execute(text("""SELECT current_database(),current_user,
                    pg_get_userbyid(datdba),pg_is_in_recovery() FROM pg_database WHERE datname=current_database()""")).one()
                if tuple(identity) != (parsed.database, "pymentor_test", "pymentor_test", False):
                    raise ValueError("database identity mismatch")
                if connection.execute(text("SELECT version_num FROM public.alembic_version")).scalars().all() != ["c528_course_leaders"]:
                    raise ValueError("Core schema revision mismatch")
                if not local_collection._schema_ok(connection):
                    raise ValueError("Core deletion source mismatch")
                if not connection.execute(text("SELECT pg_try_advisory_xact_lock(1820814, 917234)")).scalar_one():
                    raise ValueError("addon migration already running")
                current = addon_migration._version(connection, addon_migration.inspect(connection), allow_absent=True)
                if current:
                    addon_migration._check_schema(connection, current)
                if apply:
                    current = addon_migration._install_locked(connection, 2)
                return {"status": "applied" if apply else "ready", "addon_version": current}
        finally:
            engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(run(args.dataset_id, apply=args.apply))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Local addon migration failed; check protected target, writer and schema") from None
