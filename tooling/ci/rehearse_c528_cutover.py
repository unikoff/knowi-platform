#!/usr/bin/env python3
"""Disposable PostgreSQL 16 c504 -> c528 backup/return rehearsal.

Creates its own UUID-labelled Docker container; never connects to an existing DB.
Requires the Core virtualenv and a locally available postgres:16 image. No action
unless --execute is given. It does not exercise Kubernetes or HTTP traffic.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / 'services/core-api'
OLD = 'c504_course_progress'
NEW = 'c528_course_leaders'


def command(*args, input=None, check=True, timeout=120):
    p = subprocess.run(args, input=input, capture_output=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError(f'{args[0]} failed: {p.stderr.decode(errors="replace")[-3000:]}')
    return p


def migrate(port, database, login, owner, app, action, target, check=True):
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')}
    env.update(KNOWI_MIGRATION_URL=f'postgresql+asyncpg://{login}:synthetic@127.0.0.1:{port}/{database}',
               KNOWI_MIGRATION_HOST='127.0.0.1', KNOWI_MIGRATION_DATABASE=database,
               KNOWI_MIGRATION_USER=login, KNOWI_MIGRATION_OWNER=owner, KNOWI_MIGRATION_APP_ROLE=app)
    snippet = ("from alembic import command; from alembic.config import Config; import sys; "
               "c=Config(sys.argv[1]); c.set_main_option('core_runtime_migrations','true'); "
               "getattr(command,sys.argv[2])(c,sys.argv[3])")
    p = subprocess.run([sys.executable, '-c', snippet, str(CORE / 'alembic-runtime.ini'), action, target],
                       cwd=CORE, env=env, capture_output=True, timeout=120)
    if check and p.returncode:
        raise RuntimeError('Migration failed: ' + p.stderr.decode(errors='replace')[-4000:])
    return p


def snapshot(conn):
    with conn.cursor() as c:
        c.execute("""SELECT c.relname, pg_get_userbyid(c.relowner), coalesce(c.relacl::text,'')
                     FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relkind IN ('r','S') ORDER BY 1""")
        acl = c.fetchall()
        rows = []
        for table in ('users', 'user_lesson_progress', 'user_task_progress', 'account_deletion_events', 'account_deletion_redis_keys'):
            c.execute(sql.SQL('SELECT row_to_json(t)::text FROM (SELECT * FROM {} ORDER BY 1) t').format(sql.Identifier(table)))
            rows.append((table, c.fetchall()))
        c.execute('SELECT version_num FROM alembic_version')
        return acl, rows, c.fetchone()


def run():
    token = uuid.uuid4().hex
    name = 'c528-rehearsal-' + token
    owner, login, app = ('owner_' + token, 'login_' + token, 'app_' + token)
    source, restored = 'source_' + token, 'restore_' + token
    container = None
    try:
        result = command('docker', 'run', '-d', '--name', name, '--label', 'knowi.c528=' + token,
                         '-e', 'POSTGRES_PASSWORD=synthetic', '-p', '127.0.0.1::5432', 'postgres:16',
                         '-c', 'listen_addresses=*')
        container = result.stdout.decode().strip()
        port_info = command('docker', 'port', container, '5432/tcp').stdout.decode().strip()
        if not port_info.startswith('127.0.0.1:') or not port_info[10:].isdigit():
            raise RuntimeError('Unexpected container port binding')
        port = int(port_info[10:])
        def connect(database, user='postgres'):
            return psycopg.connect(host='127.0.0.1', port=port, dbname=database, user=user,
                                   password='synthetic', autocommit=True)
        for _ in range(60):
            try:
                with connect('postgres'):
                    break
            except psycopg.OperationalError:
                time.sleep(1)
        else:
            raise RuntimeError('PostgreSQL failed to start')
        with connect('postgres') as admin, admin.cursor() as c:
            c.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(owner)))
            c.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD 'synthetic'").format(sql.Identifier(login)))
            c.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD 'synthetic'").format(sql.Identifier(app)))
            c.execute(sql.SQL('GRANT {} TO {}').format(sql.Identifier(owner), sql.Identifier(login)))
            for database in (source, restored):
                c.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(database), sql.Identifier(owner)))
        migrate(port, source, login, owner, app, 'upgrade', OLD)
        with connect(source) as db, db.cursor() as c:
            c.execute("INSERT INTO users (id, username, email, password_hash) VALUES (91,'Synthetic','s@example.test','synthetic'), (92,'Deleted','d@example.test','synthetic')")
            c.execute("INSERT INTO user_lesson_progress (id,user_id,lesson_id,course_id) VALUES (101,91,'lesson','backend')")
            c.execute("INSERT INTO user_task_progress (id,user_id,task_id,task_revision,course_id) VALUES (201,91,'task','r1','backend')")
            c.execute('DELETE FROM users WHERE id=92')
            c.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(app)))
            c.execute(sql.SQL('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(app)))
            c.execute(sql.SQL('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(sql.Identifier(app)))
            baseline = snapshot(db)
            assert len(dict(baseline[1])['account_deletion_events']) == 1
        dump = command('docker', 'exec', container, 'pg_dump', '-U', 'postgres', '-Fc', source).stdout
        assert dump
        failed_grant = migrate(port, source, login, owner, 'missing_' + token, 'upgrade', NEW, check=False)
        assert failed_grant.returncode, 'migration with nonexistent application role unexpectedly succeeded'
        with connect(source) as db, db.cursor() as c:
            c.execute("SELECT version_num, to_regclass('public.course_leadership') FROM alembic_version")
            assert c.fetchone() == (OLD, None), 'failed migration did not roll back'
        migrate(port, source, login, owner, app, 'upgrade', NEW)
        with connect(source) as db, db.cursor() as c:
            c.execute("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='course_leadership'::regclass")
            assert c.fetchone() == (owner,)
            c.execute("SELECT has_table_privilege(%s, 'course_leadership', 'INSERT'), has_table_privilege(%s, 'course_leadership', 'DELETE')", (app, app))
            assert c.fetchone() == (True, False)
            c.execute('SELECT version_num FROM alembic_version')
            assert c.fetchone() == (NEW,)
            assert snapshot(db)[1] == baseline[1], 'pre-cutover rows changed'
        with connect(source, app) as db, db.cursor() as c:
            c.execute('SELECT username FROM users WHERE id=91')
            assert c.fetchone() == ('Synthetic',)
            c.execute("INSERT INTO course_leadership(course_id,user_id,account_ref,revision) VALUES ('backend',91,'synthetic',1)")
            c.execute('SELECT revision FROM role_admin_revisions WHERE user_id=91')
            assert c.fetchone() == (1,)
            c.execute('SELECT scoped_enabled FROM role_admin_cutover')
            assert c.fetchone() == (False,)
            c.execute("INSERT INTO users (id,username,email,password_hash) VALUES (93,'New','new@example.test','synthetic')")
            c.execute('DELETE FROM users WHERE id=93')
        failure = migrate(port, source, login, owner, app, 'downgrade', OLD, check=False)
        assert failure.returncode and b'Refusing downgrade' in failure.stderr
        # Do not reopen restored writes while the new leadership write is unreconciled.
        with connect(source) as db, db.cursor() as c:
            c.execute('SELECT count(*) FROM course_leadership')
            assert c.fetchone() == (1,)
            c.execute('SELECT count(*) FROM account_deletion_events')
            assert c.fetchone() == (2,)
        command('docker', 'exec', '-i', container, 'pg_restore', '--exit-on-error', '-U', 'postgres', '-d', restored, input=dump)
        with connect(restored) as db:
            assert snapshot(db) == baseline, 'restored rows, deletion events, owners or ACL differ'
        print(json.dumps({'status': 'PASS', 'postgres': 16, 'from': OLD, 'to': NEW,
                          'checks': ['seed and deletion event', 'failed grant rolls back DDL', 'pre-cutover rows preserved', 'owner/app grants and trigger', 'downgrade rejected',
                                     'fresh restore matches rows, owners and ACL'],
                          'boundary': 'post-snapshot leadership write is absent after restore; reconcile before reopening writes',
                          'not_tested': ['Kubernetes', 'HTTP application', 'legacy binary startup',
                                         'stopping a real legacy writer']}, indent=2))
    finally:
        if container:
            label = command('docker', 'inspect', '--format', '{{ index .Config.Labels "knowi.c528" }}', container, check=False)
            if label.returncode or label.stdout.decode().strip() != token:
                raise RuntimeError('Container ownership cannot be confirmed; manual cleanup required')
            command('docker', 'rm', '-f', '-v', container)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        parser.exit(2, 'No resources created; pass --execute for disposable rehearsal.\n')
    run()
