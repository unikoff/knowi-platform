"""Loopback mTLS rehearsal; Core source DB and production anchor are fixtures.

Run explicitly against the guarded disposable Analytics PostgreSQL cluster.
This does not test the private Core source's PostgreSQL credentials or TLS.
"""
import asyncio
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from uuid import uuid4

import pytest
import uvicorn
from psycopg import sql
from sqlalchemy import text
from fastapi import FastAPI
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from test_production_authority import _certs, journal, owned_path
from pymentor_analytics.adapters.core_privacy.client import CoreDeletionClient, SourceConfig
from pymentor_analytics.adapters.core_privacy.relay import CoreDeletionRelay, RelayConfig
from pymentor_analytics.adapters.postgres.activity import ActivityStorage
from pymentor_analytics.adapters.postgres.activity_reports import PostgresActivityReport
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
from pymentor_analytics.adapters.privacy_authority.server import create_authority_app
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.privacy import PrivacyRuntime
from pymentor_analytics.contracts.auth_v1 import VerifiedGrant
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.reports_v1 import ActivityQuery
from pymentor_analytics.contracts.v2 import AcceptedEvent, BrowserEvent, EmptyPayload
from tools.bootstrap_postgres import cleanup, config, connect, create

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT.parent / 'core-api'))
from pymentor_core.adapters.analytics_http import privacy as core_privacy
from pymentor_core.modules.analytics_delivery import local_collection
from pymentor_core.modules.analytics_delivery.contracts import deletion


def pem(key):
    return key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()


def archive_command(container, data, database, command, *arguments, content=None):
    if not re.fullmatch(r'[a-f0-9]{64}', container) or not re.fullmatch(r'analytics_test_[a-f0-9]{32}', database):
        raise ValueError('disposable archive identities required')
    inspection = json.loads(subprocess.run(['docker', 'inspect', container], check=True, capture_output=True,
                                           timeout=10).stdout)[0]
    if (inspection['Id'] != container or inspection['Config']['Labels'].get('knowi.scope') != 'a014-rollback-qa'
            or inspection['NetworkSettings']['Ports'].get('5432/tcp') != [{'HostIp': '127.0.0.1', 'HostPort': '57392'}]):
        raise ValueError('unrecognized disposable Analytics archive container')
    env = {**os.environ, 'PGPASSWORD': data['password']}
    return subprocess.run(['docker', 'exec', '-i', '--env', 'PGPASSWORD', container, command,
                           '--host', '127.0.0.1', '--username', data['user'], '--dbname', database, *arguments],
                          input=content, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, check=True, timeout=60).stdout


def contents(connection):
    names = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='analytics'").fetchall()
    return {table: connection.execute(sql.SQL('''SELECT count(*), md5(COALESCE(string_agg(row_to_json(t)::text,
        E'\\n' ORDER BY row_to_json(t)::text), '')) FROM analytics.{} t''').format(sql.Identifier(table))).fetchone()
            for (table,) in names}


async def replay_restored_archive(restore_urls, client_cfg, control, query):
    readers = {}
    writer = Database(Settings(database_url=restore_urls['privacy']))
    try:
        for role in ('collector', 'processor', 'query'):
            db = Database(Settings(database_url=restore_urls[role]))
            runtime = PrivacyRuntime(AuthorityClient(client_cfg), db)
            db.transaction_guard = runtime.check_in_transaction
            readers[role] = (db, runtime)
            await runtime.tick()
            assert not await runtime.is_ready()
            with pytest.raises(PrivacyUnavailable):
                async with db.transaction() as session:
                    await session.execute(text('SELECT 1'))
        replay = PrivacyRuntime(AuthorityClient(client_cfg), writer, store=PostgresPrivacyStore(writer))
        for _ in range(30):
            await replay.tick()
            if await replay.is_ready():
                break
        assert await replay.is_ready()
        for db, runtime in readers.values():
            await runtime.tick()
            assert await runtime.is_ready()
            async with db.transaction() as session:
                assert (await session.execute(text('SELECT 1'))).scalar_one() == 1
        async with writer.sessions.begin() as session:
            assert (await session.execute(text('SELECT applied_through FROM analytics.privacy_state'))).scalar_one() == 2
            assert (await session.execute(text('SELECT count(*) FROM analytics.privacy_receipts WHERE phase=\'complete\''))).scalar_one() == 2
        grant = VerifiedGrant(control, 'user', 'report:read', ('backend',), (), False, int(time.time()) + 120)
        report = await PostgresActivityReport(readers['query'][0], None).read_activity(grant, query)
        assert sum(row['active_ms'] for row in report['daily']) == 10_000
        assert sum(row['unique_visitors'] for row in report['daily']) == 1
    finally:
        for db, _ in readers.values():
            await db.close()
        await writer.close()


@contextmanager
def tls_server(app, ca, cert, key):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        cfg = uvicorn.Config(app, host='127.0.0.1', port=port, ws='none', log_level='error', access_log=False,
                             proxy_headers=False, ssl_certfile=str(cert), ssl_keyfile=str(key),
                             ssl_ca_certs=str(ca), ssl_cert_reqs=ssl.CERT_REQUIRED)
        server = uvicorn.Server(cfg)
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        deadline = time.monotonic() + 8
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        try:
            yield f'https://127.0.0.1:{port}'
        finally:
            server.should_exit = True
            thread.join(timeout=8)
            assert not thread.is_alive()


def test_protected_dual_delivery_mtls_without_database(owned_path, monkeypatch):
    """Network-only evidence; this test does not establish local purge."""
    ca, (cert, key), (client_cert, client_key) = _certs(owned_path)
    signer, producer, command = (Ed25519PrivateKey.generate() for _ in range(3))
    secret = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=').decode()
    j = journal(owned_path)
    authority_app = create_authority_app(j, issuer=j.authority_id, kid='a', signing_key=signer,
        producer_issuer='core', producer_public_keys={'p': pem(producer).encode()}, allowed_producers={'core'},
        producer_command_public_keys={'core': pem(command).encode()}, reader_secret=secret)
    target, instance, source_key = uuid4(), uuid4(), os.urandom(32)
    occurred = datetime.now(timezone.utc)
    stamp = local_collection._stamp(occurred)
    monkeypatch.setattr(local_collection, 'read_snapshot', lambda _: (local_collection._stamp(datetime.now(timezone.utc)),
                        [{'subject_ref': str(target), 'deleted_at': stamp}]))
    source_app = FastAPI()
    source_app.state.analytics_local_collection = local_collection.LocalCollection(str(instance), 'fixture', '', source_key)
    source_app.include_router(local_collection.router, prefix='/api')
    with tls_server(authority_app, ca, cert, key) as authority_url, tls_server(source_app, ca, cert, key) as source_url:
        ac = AuthorityConfig.from_json(json.dumps({'url': authority_url, 'authority_id': j.authority_id,
            'issuer': j.authority_id, 'public_keys': {'a': pem(signer)}, 'reader_secret': secret,
            'fixture_mode': False, 'runtime_mode': 'production', 'tls_ca_file': str(ca),
            'tls_cert_file': str(client_cert), 'tls_key_file': str(client_key)}))
        sc = SourceConfig.from_json(json.dumps({'url': source_url + '/api/internal/analytics/deletions',
            'instance_id': str(instance), 'secret': base64.urlsafe_b64encode(source_key).rstrip(b'=').decode(),
            'runtime_mode': 'production', 'tls_ca_file': str(ca), 'tls_cert_file': str(client_cert), 'tls_key_file': str(client_key)}))
        async def scenario():
            cfg = RelayConfig('core', 'core', 'p', producer, command)
            ctx = ssl.create_default_context(cafile=str(ca)); ctx.load_cert_chain(str(client_cert), str(client_key))
            core = core_privacy.AuthorityClient(endpoint=authority_url, authority_id=j.authority_id,
                issuer=j.authority_id, public_keys={'a': pem(signer).encode()}, producer='core',
                producer_token=cfg.token, command_key=command, verify=ctx)
            event = deletion(target, revision=1)
            original = core_privacy.request
            async def lost(*args, **kwargs):
                response = await original(*args, **kwargs)
                assert response is not None and response[0] == 200
                assert json.loads(response[1])['frame']['status'] == 'accepted'
                assert j.snapshot(after=0)[0] == 1
                return None
            monkeypatch.setattr(core_privacy, 'request', lost)
            assert await core.append(event, occurred, timeout_seconds=3) is None
            monkeypatch.setattr(core_privacy, 'request', original)
            assert await core.append(event, occurred, timeout_seconds=3) == 1
            await CoreDeletionRelay(CoreDeletionClient(sc), AuthorityClient(ac), cfg).tick()
            high, entries = j.snapshot(after=0)
            assert high == 2 and len(entries) == 2
            assert len({str(entry.control.control_id) for entry in entries}) == 2
            assert all(entry.control.subject_ref == target for entry in entries)
        asyncio.run(scenario())


def test_protected_dual_delivery_and_purge(owned_path, monkeypatch):
    if os.environ.get('ANALYTICS_RUN_SYNTHETIC_PRIVACY_INTEGRATION') != '1':
        pytest.skip('explicit synthetic integration opt-in required')
    data = config(ROOT / '.local/postgres-connection.json')
    name, sentinel, urls = create(data, revision='a014')
    try:
        upgrade(Settings(database_url=urls['migrate']), target_revision='a014')
        target, control, instance = uuid4(), uuid4(), uuid4()
        with connect(data, name) as conn:
            for subject in (target, control):
                conn.execute('INSERT INTO analytics.admission_subject_windows VALUES (%s, 1, 1)', (subject,))
        archive_container = os.environ.get('ANALYTICS_SYNTHETIC_ARCHIVE_CONTAINER')
        ca, (cert, key), (client_cert, client_key) = _certs(owned_path)
        signer, producer, command = (Ed25519PrivateKey.generate() for _ in range(3))
        secret = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=').decode()
        j = journal(owned_path)
        authority_app = create_authority_app(j, issuer=j.authority_id, kid='a', signing_key=signer,
            producer_issuer='core', producer_public_keys={'p': pem(producer).encode()}, allowed_producers={'core'},
            producer_command_public_keys={'core': pem(command).encode()}, reader_secret=secret)
        source_key = os.urandom(32)
        occurred = datetime.now(timezone.utc)
        stamp = local_collection._stamp(occurred)
        monkeypatch.setattr(local_collection, 'read_snapshot', lambda _: (local_collection._stamp(datetime.now(timezone.utc)),
                            [{'subject_ref': str(target), 'deleted_at': stamp}]))
        source_app = FastAPI()
        source_app.state.analytics_local_collection = local_collection.LocalCollection(str(instance), 'fixture', '', source_key)
        source_app.include_router(local_collection.router, prefix='/api')
        with tls_server(authority_app, ca, cert, key) as authority_url, tls_server(source_app, ca, cert, key) as source_url:
            ac = AuthorityConfig.from_json(json.dumps({'url': authority_url, 'authority_id': j.authority_id,
                'issuer': j.authority_id, 'public_keys': {'a': pem(signer)}, 'reader_secret': secret,
                'fixture_mode': False, 'runtime_mode': 'production', 'tls_ca_file': str(ca),
                'tls_cert_file': str(client_cert), 'tls_key_file': str(client_key)}))
            sc = SourceConfig.from_json(json.dumps({'url': source_url + '/api/internal/analytics/deletions',
                'instance_id': str(instance), 'secret': base64.urlsafe_b64encode(source_key).rstrip(b'=').decode(),
                'runtime_mode': 'production', 'tls_ca_file': str(ca), 'tls_cert_file': str(client_cert), 'tls_key_file': str(client_key)}))
            if archive_container:
                start = (occurred - timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
                query = ActivityQuery(from_time=start, to_time=start + timedelta(hours=1), course_id='backend', subject_ref=None)

                async def bind_initial_authority():
                    db = Database(Settings(database_url=urls['privacy']))
                    collector = Database(Settings(database_url=urls['collector']))
                    try:
                        runtime = PrivacyRuntime(AuthorityClient(ac), db, store=PostgresPrivacyStore(db))
                        await runtime.tick()
                        assert await runtime.is_ready()
                        async with collector.transaction() as session:
                            for subject in (target, control):
                                event = BrowserEvent(schema_version=2, event_id=uuid4(), type='activity_segment',
                                    course_id='backend', catalog_version='archive-fixture', lesson_id='fixture', section='theory',
                                    session_id=uuid4(), started_at=start, ended_at=start + timedelta(seconds=10),
                                    last_action_at=start, visible=True, focused=True, active_ms=10_000, payload=EmptyPayload())
                                record = AcceptedEvent(producer_namespace='archive-fixture', subject_ref=subject,
                                                       source='browser', event=event, received_at=occurred)
                                assert await ActivityStorage(session).insert(record) == 'inserted'
                    finally:
                        await collector.close()
                        await db.close()

                asyncio.run(bind_initial_authority())
                with connect(data, name) as conn:
                    assert conn.execute('SELECT token FROM public.analytics_test_sentinel').fetchall() == [(sentinel,)]
                    assert conn.execute('SELECT applied_through FROM analytics.privacy_state').fetchone() == (0,)
                    archived_contents = contents(conn)
                archive = archive_command(archive_container, data, name, 'pg_dump', '--data-only', '--format=custom',
                                          '--schema=analytics', '--no-owner', '--no-acl')
                assert archive.startswith(b'PGDMP')

            async def scenario():
                relay_cfg = RelayConfig('core', 'core', 'p', producer, command)
                ctx = ssl.create_default_context(cafile=str(ca)); ctx.load_cert_chain(str(client_cert), str(client_key))
                core = core_privacy.AuthorityClient(endpoint=authority_url, authority_id=j.authority_id,
                    issuer=j.authority_id, public_keys={'a': pem(signer).encode()}, producer='core',
                    producer_token=relay_cfg.token, command_key=command, verify=ctx)
                event = deletion(target, revision=1)
                original = core_privacy.request
                async def lost(*args, **kwargs):
                    response = await original(*args, **kwargs)
                    assert response is not None and response[0] == 200
                    assert json.loads(response[1])['frame']['status'] == 'accepted'
                    assert j.snapshot(after=0)[0] == 1
                    return None
                monkeypatch.setattr(core_privacy, 'request', lost)
                assert await core.append(event, occurred, timeout_seconds=3) is None
                monkeypatch.setattr(core_privacy, 'request', original)
                assert await core.append(event, occurred, timeout_seconds=3) == 1
                relay = CoreDeletionRelay(CoreDeletionClient(sc), AuthorityClient(ac), relay_cfg)
                await relay.tick()
                assert j.snapshot(after=0)[0] == 2
                db = Database(Settings(database_url=urls['privacy'], pool_size=2, pool_overflow=0))
                try:
                    runtime = PrivacyRuntime(AuthorityClient(ac), db, store=PostgresPrivacyStore(db))
                    first_applied = False
                    for _ in range(30):
                        await runtime.tick()
                        with connect(data, name) as conn:
                            prefix = conn.execute('SELECT applied_through FROM analytics.privacy_state').fetchone()[0]
                            if prefix == 1:
                                assert conn.execute('SELECT count(*) FROM analytics.admission_subject_windows WHERE subject_ref=%s', (target,)).fetchone() == (0,)
                                assert conn.execute("SELECT phase FROM analytics.privacy_receipts WHERE seq=1").fetchone() == ('complete',)
                                first_applied = True
                        if await runtime.is_ready(): break
                    assert first_applied and await runtime.is_ready()
                finally:
                    await db.close()
            asyncio.run(scenario())
            if archive_container:
                restored, restore_token, restore_urls = create(data, revision='a014')
                try:
                    upgrade(Settings(database_url=restore_urls['migrate']), target_revision='a014')
                    seeds = {'admission_budget', 'activity_projection_backfill', 'activity_projection_day_backfill'}
                    with connect(data, restored) as conn:
                        assert conn.execute('SELECT token FROM public.analytics_test_sentinel').fetchall() == [(restore_token,)]
                        assert conn.execute('SELECT version_num FROM public.alembic_version').fetchall() == [('a014',)]
                        present = {table: row[0] for table, row in contents(conn).items() if row[0]}
                        assert present == dict.fromkeys(seeds, 1)
                        for table in sorted(seeds):
                            conn.execute(sql.SQL('DELETE FROM analytics.{} WHERE singleton=true').format(sql.Identifier(table)))
                    archive_command(archive_container, data, restored, 'pg_restore', '--data-only', '--disable-triggers',
                                    '--no-owner', '--no-acl', '--single-transaction', '--exit-on-error', content=archive)
                    with connect(data, restored) as conn:
                        assert contents(conn) == archived_contents
                        assert conn.execute('SELECT count(*) FROM analytics.admission_subject_windows').fetchone() == (2,)
                    asyncio.run(replay_restored_archive(restore_urls, ac, control, query))
                    with connect(data, restored) as conn:
                        assert conn.execute('SELECT subject_ref FROM analytics.admission_subject_windows').fetchall() == [(control,)]
                        assert conn.execute('SELECT subject_ref FROM analytics.activity_facts').fetchall() == [(control,)]
                        assert conn.execute('SELECT deleted FROM analytics.subject_fences WHERE subject_ref=%s', (target,)).fetchone() == (True,)
                finally:
                    cleanup(data, restored, restore_token, revision='a014')
            with connect(data, name) as conn:
                assert conn.execute('SELECT applied_through FROM analytics.privacy_state').fetchone() == (2,)
                rows = conn.execute('SELECT control_id,subject_ref,phase FROM analytics.privacy_receipts ORDER BY seq').fetchall()
                assert len(rows) == 2 and len({row[0] for row in rows}) == 2
                assert all(row[1] == target and row[2] == 'complete' for row in rows)
                assert conn.execute('SELECT subject_ref FROM analytics.admission_subject_windows').fetchall() == [(control,)]
                assert conn.execute('SELECT deleted FROM analytics.subject_fences WHERE subject_ref=%s', (target,)).fetchone() == (True,)
    finally:
        cleanup(data, name, sentinel, revision='a014')
