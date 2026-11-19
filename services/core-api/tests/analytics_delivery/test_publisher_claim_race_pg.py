"""Opt-in concurrent claims on a disposable, sentinel-protected PostgreSQL DB."""
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4
import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools' / 'analytics_transfer'))
from run import _synthetic_database
from pymentor_core.adapters.sql.analytics_delivery import claim, enqueue, finish, mark_sending, outbox
from pymentor_core.modules.analytics_delivery.contracts import activity

def test_two_publishers_race_and_expired_claim_cannot_ack():
    keys = ('CORP428_TEST_DATABASE_URL', 'CORP428_TEST_EXPECTED_DB', 'CORP428_TEST_EXPECTED_OWNER', 'CORP428_TEST_SENTINEL')
    if not all(os.environ.get(key) for key in keys):
        pytest.skip('requires prepared disposable PostgreSQL addon database and sentinel')
    url, expected, owner, sentinel = (os.environ[key] for key in keys)
    parsed = _synthetic_database(url, expected)
    assert str(UUID(sentinel)) == sentinel
    engine = create_engine(parsed, poolclass=NullPool, connect_args={'connect_timeout': 3, 'options': '-c statement_timeout=5000 -c lock_timeout=3000'})
    sessions = sessionmaker(engine)
    barrier = threading.Barrier(2, timeout=4)
    try:
        with engine.begin() as conn:
            identity = conn.execute(text('SELECT current_database(),current_user,pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()')).one()
            assert tuple(identity) == (expected, owner, owner)
            assert [str(x) for x in conn.execute(text('SELECT token FROM public.analytics_delivery_install_sentinel LIMIT 2')).scalars()] == [sentinel]
            assert conn.execute(select(outbox.c.event_id).limit(1)).first() is None, 'outbox must be empty'
        now = datetime.now(timezone.utc)
        item = activity(uuid4(), date(2026, 11, 19), None, revision=0)
        with sessions.begin() as db: assert enqueue(db, item, now)
        @event.listens_for(engine, 'before_cursor_execute')
        def synchronize(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith('UPDATE ANALYTICS_TRANSFER_OUTBOX') and 'attempts' in statement:
                barrier.wait()
        def contender():
            with sessions.begin() as db: return claim(db, now, limit=1, lease_seconds=5, max_attempts=5)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: contender(), range(2)))
        event.remove(engine, 'before_cursor_execute', synchronize)
        assert sorted(map(len, results)) == [0, 1]
        first = next(row for rows in results for row in rows)
        with sessions.begin() as db: mark_sending(db, item['event_id'], first['claim_token'])
        with sessions.begin() as db:
            second = claim(db, now + timedelta(seconds=6), limit=1, lease_seconds=5, max_attempts=5)[0]
            assert second['uncertain'] is True and second['claim_token'] != first['claim_token']
        with sessions.begin() as db:
            with pytest.raises(ValueError, match='stale claim'): finish(db, item['event_id'], first['claim_token'], 'ack', now)
            finish(db, item['event_id'], second['claim_token'], 'ack', now)
        with sessions() as db: assert db.execute(select(outbox.c.status, outbox.c.attempts)).one() == ('delivered', 2)
    finally:
        engine.dispose()
