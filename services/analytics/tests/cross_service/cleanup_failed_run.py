"""Reconcile and remove a known interrupted, owned a010 fixture."""
import json
import os
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

import psycopg
from tools.bootstrap_postgres import config, connect
from cross_service import resources as r
from cross_service.guards import verify_databases


def main(operation):
    if not re.fullmatch(r"[0-9a-f]{32}", operation):
        raise ValueError("invalid fixture identity")
    root = (r.ROOT / ".local/cross-service").resolve(strict=True)
    folder = (root / operation).resolve(strict=True)
    if folder.parent != root:
        raise ValueError("fixture outside owned root")
    owner = json.loads((folder / "owner.json").read_text(encoding="utf-8"))
    fixture = json.loads((folder / "fixture.json").read_text(encoding="utf-8"))
    if owner["state"] not in ("memory_reconciliation_required", "reconciliation_required") or len(owner["analytics_resources"]) != 2:
        raise ValueError("unexpected cleanup state")
    restore_rollback = owner["state"] == "reconciliation_required"
    verify_databases(fixture)
    core, analytics = fixture["databases"]["core"], fixture["databases"]["analytics"]
    if owner["core_name"] != core["name"] or owner["core_sentinel"] != core["sentinel"]:
        raise ValueError("Core owner record mismatch")
    if {"name": analytics["name"], "sentinel": analytics["sentinel"]} not in owner["analytics_resources"]:
        raise ValueError("Analytics owner record mismatch")
    with psycopg.connect(core["dsn"], connect_timeout=3) as conn:
        if conn.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()").fetchone()[0]:
            raise ValueError("Core fixture connections are still active")
        if conn.execute("SELECT count(*) FROM analytics_transfer_outbox WHERE status<>'delivered' OR uncertain").fetchone()[0] != 0:
            raise ValueError("Core raw delivery outcome not reconciled")
        proof_guard = "status<>'verified' OR NOT accepted OR uncertain" if restore_rollback else "status<>'pending' OR accepted OR uncertain"
        if conn.execute("SELECT count(*) FROM analytics_transfer_snapshot_proofs WHERE " + proof_guard).fetchone()[0] != 0:
            raise ValueError("Core proof outcome requires additional reconciliation")
        if restore_rollback and conn.execute("SELECT count(*) FROM analytics_transfer_snapshot_proofs").fetchone()[0] != 6:
            raise ValueError("unexpected Core proof count")
        if conn.execute("SELECT mode FROM analytics_transfer_write_gate WHERE singleton=1").fetchone() != ("mirror",):
            raise ValueError("Core write gate is not mirror")
    with psycopg.connect(analytics["dsn"], connect_timeout=3) as conn:
        if not restore_rollback and conn.execute("SELECT count(*) FROM analytics.core_snapshot_proofs").fetchone()[0] != 0:
            raise ValueError("Analytics proof acceptance requires reconciliation")
        if restore_rollback:
            with closing(sqlite3.connect("file:" + (folder / "journal.sqlite").as_posix() + "?mode=ro", uri=True, timeout=3)) as journal:
                head = journal.execute("SELECT coalesce(max(seq),0) FROM journal").fetchone()[0]
            if conn.execute("SELECT applied_through FROM analytics.privacy_state").fetchone() != (head,):
                raise ValueError("source privacy journal is not reconciled")
    data = config(r.ROOT / ".local/postgres-connection.json")
    names = [item["name"] for item in owner["analytics_resources"]]
    with connect(data) as conn:
        if conn.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=ANY(%s)", (names,)).fetchone()[0] != 0:
            raise ValueError("Analytics fixture connections are still active")
    # The fresh restore target must still carry its own exact marker.
    restored = next(item for item in owner["analytics_resources"] if item["name"] != analytics["name"])
    with connect(data, restored["name"]) as conn:
        if conn.execute("SELECT token FROM public.analytics_test_sentinel").fetchall() != [(restored["sentinel"],)]:
            raise ValueError("restore target sentinel mismatch")
        if restore_rollback:
            for table in ("activity_facts", "activity_projection_buckets", "core_snapshot_proofs", "privacy_state"):
                if conn.execute("SELECT count(*) FROM analytics." + table).fetchone()[0]:
                    raise ValueError("restore transaction did not roll back")
            if conn.execute("SELECT tgenabled FROM pg_trigger WHERE tgrelid='analytics.activity_facts'::regclass AND tgname='activity_projection_fact_inserted'").fetchall() != [("O",)]:
                raise ValueError("restore trigger state unexpected")
    # Redis cleanup is bounded to the recorded synthetic prefix, never FLUSH.
    prefix = owner["redis_prefix"]
    env = {key: val for key, val in os.environ.items() if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC"}}
    env.update(CROSS_SERVICE_PRIVATE_DIR=str(folder), KNOWI_REDIS_PREFIX=prefix, REDIS_URL="redis://127.0.0.1:57381/13")
    core_python = r.CORE / ".venv/Scripts/python.exe"
    result = subprocess.run([str(core_python), "-B", str(r.ROOT / "tests/cross_service/core_fixture.py"), "clean-redis"],
                            cwd=r.CORE, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    if result.returncode:
        raise RuntimeError("owned Redis cleanup failed")
    # Verify exact generated DB owner and sentinel inside each cleanup method.
    from urllib.parse import urlsplit, urlunsplit
    base = urlsplit(core["dsn"])
    admin = urlunsplit((base.scheme, base.netloc, "/pymentor_test", "", ""))
    r.record(folder, owner["token"], state="core_cleanup_pending")
    r.cleanup_core(admin, core)
    for item in owner["analytics_resources"]:
        r.record(folder, owner["token"], state="analytics_cleanup_pending", cleanup_name=item["name"])
        r.cleanup_analytics(data, item)
    r.record(folder, owner["token"], state="cleanup_complete")
    r.cleanup_private(folder, owner["token"])
    print("Reconciled synthetic targets and private fixture cleaned")


if __name__ == "__main__":
    main(sys.argv[1])
