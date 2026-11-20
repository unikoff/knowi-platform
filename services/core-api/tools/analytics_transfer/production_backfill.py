"""One bounded production Core Analytics operation after capture is active in mirror mode."""
from __future__ import annotations

import argparse
import json
import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from tools.analytics_transfer.production_migrate import CORE_REVISION, add_target_parameters, target_url


def dispatch(db, args):
    from pymentor_core.adapters.sql.analytics_delivery_ops import addon_migration, export, integration
    from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_export import proof_enabled

    db.execute(text("SET LOCAL statement_timeout='30s'"))
    db.execute(text("SET LOCAL lock_timeout='3s'"))
    identity = db.execute(text('''SELECT d.oid::bigint, current_database(), current_user, pg_is_in_recovery(),
        current_user=pg_get_userbyid(d.datdba) FROM pg_catalog.pg_database d WHERE d.datname=current_database()''')).one()
    revision = db.execute(text('SELECT version_num FROM public.alembic_version')).scalars().all()
    if tuple(identity) != (args.database_oid, args.database, args.app_role, False, False) or revision != [CORE_REVISION]:
        raise ValueError('Runtime identity or revision mismatch')
    if not integration.enabled() or not proof_enabled() or not 1 <= args.limit <= 100:
        raise ValueError('Capture and proofs must be active and the page bounded')
    if not db.execute(text('SELECT pg_try_advisory_xact_lock(1820814,917234)')).scalar_one():
        raise ValueError('Another Analytics add-on operation is active')
    addon_migration.check_version(db.connection(), 2)
    integration.require_write_gate(db, 'mirror')
    if args.operation == 'status':
        rows = db.execute(text('''SELECT c.stream, c.high_watermark, c.cursor, b.stream IS NOT NULL AS sealed
            FROM public.analytics_transfer_checkpoints c LEFT JOIN public.analytics_transfer_snapshot_barriers b
            ON c.stream = b.stream AND c.high_watermark=b.high_watermark ORDER BY c.stream''')).mappings()
        upper_id = db.execute(text('SELECT COALESCE(MAX(id),0) FROM public.users')).scalar_one()
        return {'user_high_watermark': upper_id, 'checkpoints': [dict(row) for row in rows]}
    if args.operation == 'provision':
        if args.high_watermark is None:
            raise ValueError('A fixed user high watermark is required')
        return export.provision_lifecycles_page(db, cursor=args.cursor, high_watermark=args.high_watermark, limit=args.limit)
    if not args.stream:
        raise ValueError('A stream is required')
    if args.operation == 'initialize':
        state = db.execute(text('SELECT high_watermark FROM public.analytics_transfer_checkpoints WHERE stream=:stream'),
                           {'stream': args.stream}).scalar_one_or_none()
        return {'high_watermark': state if state is not None else export.initialize(db, args.stream)}
    if args.operation == 'page':
        return export.export_page(db, args.stream, limit=args.limit)
    export.seal_backfill(db, args.stream)
    return {'sealed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_target_parameters(parser)
    parser.add_argument('--app-role', required=True)
    parser.add_argument('operation', choices=('status', 'provision', 'initialize', 'page', 'seal'))
    parser.add_argument('--stream', choices=('activity', 'progress', 'tasks'))
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--cursor', type=int, default=0)
    parser.add_argument('--high-watermark', type=int)
    args = parser.parse_args()
    try:
        url = target_url(args, args.app_role, 'KNOWI_ANALYTICS_BACKFILL_URL')
        # ORM metadata still imports the app bridge. All operator queries must use
        # the explicit Session below, including its verified TLS settings.
        os.environ['DATABASE_URL'] = 'postgresql+psycopg://unused@127.0.0.1:1/backfill_metadata_only'
        engine = create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args={'connect_timeout': 3})
        try:
            with Session(engine) as db, db.begin():
                result = dispatch(db, args)
        finally:
            engine.dispose()
        print(json.dumps(result))
    except Exception:
        raise SystemExit('Core Analytics backfill stopped; reconcile the exact operation from status') from None


if __name__ == '__main__':
    main()
