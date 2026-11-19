"""Opt-in PostgreSQL 16 regression; creates one isolated a011 target only.

PostgreSQL 16 role membership options and SET LOCAL ROLE:
https://www.postgresql.org/docs/16/sql-grant.html
https://www.postgresql.org/docs/16/sql-set-role.html
"""
import json
import os

import psycopg
from psycopg import sql
import pytest

from . import resources, restore
from tools.bootstrap_postgres import connect


GO = "ANALYTICS_RUN_CROSS_A011_MEMBERSHIP_SYNTHETIC"
pytestmark = pytest.mark.skipif(os.environ.get(GO) != "1", reason="separate disposable a011 membership PostgreSQL GO required")


def _verify_target(admin, target):
    identity = admin.execute("SELECT current_database(),pg_get_userbyid(datdba),current_setting('server_version_num')::int FROM pg_database WHERE datname=current_database()").fetchone()
    marker = admin.execute("SELECT token FROM public.analytics_test_sentinel").fetchall()
    if identity[:2] != (target["name"], target["owner"]) or not 160000 <= identity[2] < 170000 or marker != [(target["sentinel"],)]:
        raise ValueError("disposable a011 target guard failed")


def _membership(admin, target):
    processor = target["name"] + "_processor"
    collector = target["name"] + "_collector"
    return admin.execute("""SELECT pg_get_userbyid(m.roleid),m.inherit_option,m.set_option,m.admin_option
        FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles member ON member.oid=m.member
        WHERE member.rolname=%s ORDER BY 1""", (collector,)).fetchall(), processor, collector


@pytest.fixture
def fresh_membership_target():
    if os.environ.get(GO) != "1":
        pytest.skip("separate disposable a011 membership PostgreSQL GO required")
    folder, token = resources.private_folder()
    target = data = None
    ready = False
    try:
        target, urls, data = resources.create_analytics(folder, token)
        resources.record(folder, token, state="a011_migration_pending")
        resources.install_analytics(urls, full_chain=True, activity_projection=True, day_projection=True)
        resources.record(folder, token, state="a011_membership_test_ready")
        ready = True
        yield folder, token, target, urls, data
    finally:
        if not ready:
            # Creation/migration outcome can be unknown; preserve ownership record.
            print("Membership fixture requires reconciliation:", folder.name)
        else:
            # Reconcile the exact recorded edge before any role/database removal.
            try:
                with connect(data, target["name"]) as admin:
                    _verify_target(admin, target)
                    rows, processor, collector = _membership(admin, target)
                    if rows not in ([], [(processor, False, True, False)]):
                        raise ValueError("unexpected collector membership; reconcile manually")
                    if rows:
                        resources.record(folder, token, state="membership_revoke_pending")
                        admin.execute(sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(processor), sql.Identifier(collector)))
                        if _membership(admin, target)[0]:
                            raise ValueError("membership revoke not confirmed")
                resources.record(folder, token, state="analytics_cleanup_pending")
                resources.cleanup_analytics(data, target)
                with connect(data) as admin:
                    names = [target["name"] + "_" + role for role in resources.ROLES]
                    assert admin.execute("SELECT count(*) FROM pg_database WHERE datname=%s", (target["name"],)).fetchone()[0] == 0
                    assert admin.execute("SELECT count(*) FROM pg_roles WHERE rolname=ANY(%s)", (names,)).fetchone()[0] == 0
                resources.record(folder, token, state="cleanup_complete")
                resources.cleanup_private(folder, token)
                print("Membership fixture database and six roles cleaned")
            except Exception:
                # Do not guess/retry an ambiguous GRANT, REVOKE, or DROP outcome.
                print("Membership fixture requires reconciliation:", folder.name)
                raise


def test_collector_set_role_membership_is_rejected_before_transplant(fresh_membership_target, monkeypatch):
    folder, token, target, urls, data = fresh_membership_target
    with connect(data, target["name"]) as admin:
        _verify_target(admin, target)
        rows, processor, collector = _membership(admin, target)
        assert rows == []
        assert admin.execute("SELECT rolcanlogin,rolinherit FROM pg_roles WHERE rolname=%s", (collector,)).fetchone() == (True, False)
        resources.record(folder, token, state="membership_grant_pending")
        admin.execute(sql.SQL("GRANT {} TO {} WITH INHERIT FALSE, SET TRUE").format(sql.Identifier(processor), sql.Identifier(collector)))
        assert _membership(admin, target)[0] == [(processor, False, True, False)]
        resources.record(folder, token, state="membership_granted")

    with psycopg.connect(urls["collector"], autocommit=True, connect_timeout=3) as collector_conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with collector_conn.transaction():
                collector_conn.execute("SELECT count(*) FROM analytics.activity_projection_days")
        with collector_conn.transaction():
            collector_conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(processor)))
            assert collector_conn.execute("SELECT current_user").fetchone() == (processor,)
            assert collector_conn.execute("SELECT count(*) FROM analytics.activity_projection_days").fetchone() == (0,)
        assert collector_conn.execute("SELECT current_user").fetchone() == (collector,)

    # Same transplant entry point as the harness, with a structurally valid
    # synthetic source but no real rows. A read-only connection makes any
    # accidental write fail and ensures the expected ACL error is reached first.
    from uuid import uuid4
    old_run, new_run = uuid4(), uuid4()
    snapshot = {table: [] for table in (*restore.TABLES, "processing_results", "processing_leases", "processing_replays",
                                      "admission_subject_windows", "activity_projection_buckets", "privacy_state")}
    snapshot["activity_projection_backfill"] = [json.dumps({"run_id": str(old_run), "phase": "complete"})]
    original_connect = psycopg.connect
    def readonly_connect(*args, **kwargs):
        kwargs["options"] = kwargs.get("options", "") + " -c default_transaction_read_only=on"
        return original_connect(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(psycopg, "connect", readonly_connect)
        with pytest.raises(ValueError, match="unsafe day restore role"):
            restore.transplant_fresh(target, snapshot, day_projection=True, old_run_id=old_run, new_run_id=new_run)
    with connect(data, target["name"]) as admin:
        _verify_target(admin, target)
        assert admin.execute("SELECT run_id,phase,cursor_namespace,cursor_event_id FROM analytics.activity_projection_backfill").fetchall() == [(None, "scanning", None, None)]
        assert admin.execute("SELECT run_id,phase,cursor_subject,cursor_bucket,cursor_metric FROM analytics.activity_projection_day_backfill").fetchall() == [(None, "scanning", None, None, None)]
        for table in ("activity_facts", "activity_projection_buckets", "activity_projection_day_sources", "activity_projection_days"):
            assert admin.execute(sql.SQL("SELECT count(*) FROM analytics.{}").format(sql.Identifier(table))).fetchone()[0] == 0
