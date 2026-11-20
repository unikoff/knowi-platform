"""Install the Core Analytics add-on after the operator has stopped the writers."""
from __future__ import annotations

import argparse
import json
import os
import re

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from pymentor_core.adapters.sql.analytics_delivery_ops import addon_migration

CORE_REVISION = 'c528_course_leaders'
ADDON_VERSION = 2
REQUIRED_PRIVILEGES = {
    'analytics_transfer_schema_version': 'SELECT',
    'analytics_transfer_outbox': 'SELECT, INSERT, UPDATE, DELETE',
    'analytics_transfer_checkpoints': 'SELECT, INSERT, UPDATE',
    'analytics_transfer_revisions': 'SELECT, INSERT, UPDATE',
    'analytics_transfer_activity_state': 'SELECT, INSERT, UPDATE, DELETE',
    'analytics_transfer_write_gate': 'SELECT, UPDATE',
    'analytics_transfer_snapshot_proofs': 'SELECT, INSERT, UPDATE, DELETE',
    'analytics_transfer_snapshot_barriers': 'SELECT, INSERT',
    'analytics_transfer_activity_preparation': 'SELECT, INSERT, UPDATE',
}


def lock_and_check(connection, args):
    connection.execute(text("SET LOCAL lock_timeout='3s'"))
    connection.execute(text("SET LOCAL statement_timeout='30s'"))
    identity = connection.execute(text('''SELECT d.oid::bigint, d.datname, current_user,
        pg_get_userbyid(d.datdba), pg_is_in_recovery(), current_setting('server_version_num')::int / 10000
        FROM pg_catalog.pg_database d WHERE d.datname=current_database()''')).one()
    if tuple(identity) != (args.database_oid, args.database, args.owner, args.owner, False, 16):
        raise ValueError('Core database identity mismatch')
    if connection.execute(text('SELECT version_num FROM public.alembic_version')).scalars().all() != [CORE_REVISION]:
        raise ValueError('Core revision mismatch')
    if not connection.execute(text('SELECT pg_try_advisory_xact_lock(1820814,917234)')).scalar_one():
        raise ValueError('Analytics add-on operation already active')
    writing = connection.execute(text('''SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_stat_activity
        WHERE datname=current_database() AND pid<>pg_backend_pid())
        OR EXISTS(SELECT 1 FROM pg_catalog.pg_prepared_xacts WHERE database=current_database())''')).scalar_one()
    if writing:
        raise ValueError('Core database connections or prepared transactions remain')
    role = connection.execute(text('''SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb,
        rolreplication, rolbypassrls FROM pg_catalog.pg_roles WHERE rolname=:role'''), {'role': args.app_role}).one_or_none()
    if role is None or tuple(role) != (True, False, False, False, False, False):
        raise ValueError('Core runtime role is missing or privileged')


def install(connection, args):
    if connection.dialect.name != 'postgresql' or not connection.in_transaction() or args.owner == args.app_role:
        raise ValueError('Require a PostgreSQL transaction with separated owner and runtime roles')
    lock_and_check(connection, args)
    current = addon_migration._version(connection, inspect(connection), allow_absent=True)
    if current:
        addon_migration._check_schema(connection, current)
        if current != ADDON_VERSION or connection.execute(text('''SELECT mode
                FROM public.analytics_transfer_write_gate WHERE singleton=1''')).scalar_one_or_none() != 'mirror':
            raise ValueError('Existing add-on is outside the supported preparation state')
    if args.apply:
        current = addon_migration._install_locked(connection, ADDON_VERSION)
        runtime = connection.dialect.identifier_preparer.quote_identifier(args.app_role)
        for table, privileges in REQUIRED_PRIVILEGES.items():
            connection.exec_driver_sql(f'GRANT {privileges} ON TABLE public.{table} TO {runtime}')
    return {'mode': 'apply' if args.apply else 'plan', 'addon_version': current}


def add_target_parameters(parser):
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--database', required=True)
    parser.add_argument('--database-oid', required=True, type=int)


def target_url(args, user, variable):
    url = make_url(os.environ[variable])
    if (url.drivername != 'postgresql+psycopg' or args.database_oid <= 0 or not 0 < args.port <= 65535
            or (url.host, url.port, url.database, url.username) != (args.host, args.port, args.database, user)
            or not all(re.fullmatch(r'[a-z_][a-z_0-9]{0,62}', name) for name in (args.database, user))):
        raise ValueError('Expected PostgreSQL URL and target parameters do not agree')
    loopback = args.host in {'localhost', '127.0.0.1', '::1'}
    if (not loopback or url.query) and (set(url.query) != {'sslmode', 'sslrootcert'}
            or url.query['sslmode'] != 'verify-full' or not isinstance(url.query['sslrootcert'], str)
            or not os.path.isfile(url.query['sslrootcert'])):
        raise ValueError('Remote operation requires a pinned CA and a verified TLS hostname')
    return url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_parameters(parser)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--app-role', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        url = target_url(args, args.owner, 'KNOWI_ANALYTICS_ADDON_MIGRATION_URL')
        engine = create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args={'connect_timeout': 3})
        try:
            with engine.begin() as connection:
                result = install(connection, args)
        finally:
            engine.dispose()
        print(json.dumps(result))
    except Exception:
        raise SystemExit('Core Analytics add-on installation failed; reconcile the exact target and writer state') from None


if __name__ == '__main__':
    main()
