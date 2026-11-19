"""Restore the expanded role schema and synthetic paid/history state in an isolated DB."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, text

_spec = importlib.util.spec_from_file_location(
    "role_restore_migration_helpers", Path(__file__).with_name("test_role_admin_migration.py")
)
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)
database = _helpers.database
ROOT = Path(__file__).resolve().parents[4]


def _pg16_container():
    """Locate only the container publishing the already allowlisted test endpoint."""
    result = subprocess.run(["docker", "ps", "--filter", "publish=57382", "--format", "{{.ID}}"],
                            capture_output=True, check=True, text=True, timeout=10)
    ids = result.stdout.split()
    assert len(ids) == 1 and re.fullmatch(r"[0-9a-f]{12,64}", ids[0])
    metadata = json.loads(subprocess.run(["docker", "inspect", ids[0]], capture_output=True,
                                        check=True, text=True, timeout=10).stdout)
    backup_spec = importlib.util.spec_from_file_location("role_restore_container_guard", ROOT / "tooling/ci/rehearse_c504_backup.py")
    backup = importlib.util.module_from_spec(backup_spec)
    backup_spec.loader.exec_module(backup)
    backup._target_metadata(metadata)
    return ["docker", "exec", "-i", ids[0]]


def _snapshot(dsn):
    with psycopg.connect(dsn) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        tables = conn.execute("""SELECT tablename FROM pg_tables
            WHERE schemaname='public' ORDER BY tablename""").fetchall()
        return {name: conn.execute(sql.SQL(
            "SELECT row_to_json(t)::text FROM {} AS t ORDER BY row_to_json(t)::text"
        ).format(sql.Identifier(name))).fetchall() for (name,) in tables}


def test_dump_restore_preserves_roles_paid_data_history_and_privacy_triggers(database, tmp_path):
    assert os.environ.get("KNOWI_PG02_INTEGRATION") == "1"
    docker = _pg16_container()
    _helpers.migrate(database, "c504_course_progress")
    engine = create_engine(database.set(drivername="postgresql+psycopg"))
    restored = "c527_role_ci_" + uuid4().hex
    assert re.fullmatch(r"c527_role_ci_[0-9a-f]{32}", restored)
    base = _helpers._helpers._allowlisted_url(os.environ["KNOWI_TEST_PG_URL"])
    admin_dsn = base.set(drivername="postgresql").render_as_string(hide_password=False)
    source_dsn = database.render_as_string(hide_password=False)
    restored_dsn = database.set(database=restored).render_as_string(hide_password=False)
    try:
        with engine.begin() as conn:
            conn.execute(text("""INSERT INTO users(id,username,email,password_hash,verified,is_admin,subscription_plan,subscription_until)
                VALUES (1,'Student','student@example.test','synthetic',true,false,'pro','2099-12-01T00:00:00Z'),
                (7,'Leader','kiryukhin_nik@bk.ru','synthetic',true,false,NULL,NULL),
                (9,'Admin','admin@example.test','synthetic',true,true,NULL,NULL)"""))
            conn.execute(text("""INSERT INTO booking_slots(id,slot_date,start_time,duration_minutes,student_id,created_at)
                VALUES (10,'2099-11-20','10:00',60,1,'2020-01-02T03:04:05Z')"""))
            conn.execute(text("INSERT INTO booking_quotas(id,user_id,year,month,granted_slots) VALUES (20,1,2099,11,3)"))
            conn.execute(text("""INSERT INTO user_lesson_progress(id,user_id,lesson_id,course_id,completed_at)
                VALUES (30,1,'lesson-a','backend','2021-01-01T00:00:00Z')"""))
            slot = conn.execute(text("SELECT * FROM booking_slots WHERE id=10")).mappings().one()
            quota = conn.execute(text("SELECT * FROM booking_quotas WHERE id=20")).mappings().one()
            manifest = {"slots": [{"expected": _helpers.slot_snapshot(slot), "course_id": "backend", "owner_id": 7}],
                        "quotas": [{"expected": _helpers.quota_snapshot(quota), "course_id": "backend"}]}
        _helpers.migrate(database, "c528_course_leaders")
        with engine.begin() as conn:
            _helpers.reconcile(conn, manifest, apply=True, course_enabled=lambda course: course == "backend")
            assert _helpers.bootstrap(conn, apply=True)["status"] == "applied"
            conn.execute(text("""INSERT INTO course_leader_bindings(course_id,user_id,account_ref)
                SELECT 'backend',7,lifecycle_id FROM account_lifecycles WHERE user_id=7"""))
            conn.execute(text("""INSERT INTO role_admin_commands(actor_id,actor_account_ref,target_user_id,target_account_ref,
                operation,command_id,request_hash,response_status,response_body)
                SELECT 7,lifecycle_id,1,'00000000-0000-4000-8000-000000000001','quota_grant',
                    '00000000-0000-4000-8000-000000000010',repeat('a',64),200,json_build_object('applied',true)
                FROM account_lifecycles WHERE user_id=7"""))
            conn.execute(text("""INSERT INTO booking_notification_outbox(event_id,event_type,slot_id,username,email,
                slot_date,start_time,status,attempts,next_attempt_at,sent_at,course_id,calendar_owner_id,booking_id)
                SELECT '00000000-0000-4000-8000-000000000011','booked',10,'Student','student@example.test',
                    '2099-11-20','10:00','sent',1,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','backend',7,id
                FROM booking_history WHERE slot_id=10"""))
        before = _snapshot(source_dsn)
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            identity = admin.execute("SELECT current_database(),current_user,current_setting('server_version_num')::int").fetchone()
            assert identity[:2] == ("pymentor_test", "pymentor_test") and 160000 <= identity[2] < 170000
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(restored)))
            try:
                archive = subprocess.run(docker + ["pg_dump", "-U", "pymentor_test", "-d", database.database,
                    "--format=custom", "--no-owner", "--no-acl"], capture_output=True, check=True, timeout=60).stdout
                assert archive.startswith(b"PGDMP")
                (tmp_path / "synthetic-role-admin.dump").write_bytes(archive)
                subprocess.run(docker + ["pg_restore", "-U", "pymentor_test", "-d", restored,
                    "--no-owner", "--no-acl", "--exit-on-error"], input=archive, capture_output=True, check=True, timeout=60)
                assert _snapshot(restored_dsn) == before
                with psycopg.connect(restored_dsn) as conn:
                    assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("c528_course_leaders",)
                    revision = conn.execute("SELECT revision FROM role_admin_revisions WHERE user_id=7").fetchone()[0]
                    conn.execute("UPDATE course_leadership SET revoked_at=now() WHERE user_id=7")
                    assert conn.execute("SELECT revision FROM role_admin_revisions WHERE user_id=7").fetchone()[0] == revision + 1
                    # Existing deletion service owns these pre-c527 dependencies.
                    conn.execute("DELETE FROM booking_quotas WHERE user_id=1")
                    conn.execute("DELETE FROM user_lesson_progress WHERE user_id=1")
                    conn.execute("DELETE FROM booking_notification_outbox WHERE slot_id=10")
                    conn.execute("UPDATE booking_slots SET student_id=NULL WHERE student_id=1")
                    conn.execute("DELETE FROM users WHERE id=1")
                    assert conn.execute("SELECT target_user_id,target_account_ref,redacted,response_body FROM role_admin_commands").fetchone() == (None, None, True, {})
                    assert conn.execute("SELECT count(*) FROM booking_history WHERE student_id=1").fetchone() == (0,)
                    pinned_ref = conn.execute("SELECT account_ref FROM course_leader_bindings WHERE course_id='backend'").fetchone()[0]
                    conn.execute("DELETE FROM users WHERE id=7")
                    assert conn.execute("SELECT user_id,account_ref FROM course_leader_bindings WHERE course_id='backend'").fetchone() == (None, pinned_ref)
            finally:
                assert re.fullmatch(r"c527_role_ci_[0-9a-f]{32}", restored)
                admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(restored)))
    finally:
        engine.dispose()
