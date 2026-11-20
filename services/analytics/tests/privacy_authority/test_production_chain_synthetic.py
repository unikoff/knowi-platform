"""Loopback mTLS rehearsal; Core source DB and production anchor are fixtures.

Run explicitly against the guarded disposable Analytics PostgreSQL cluster.
This does not test the private Core source's PostgreSQL credentials or TLS.
"""
import asyncio
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import ssl
import sys
import threading
import time
from uuid import uuid4

import pytest
import uvicorn
from fastapi import FastAPI
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from test_production_authority import _certs, journal, owned_path
from pymentor_analytics.adapters.core_privacy.client import CoreDeletionClient, SourceConfig
from pymentor_analytics.adapters.core_privacy.relay import CoreDeletionRelay, RelayConfig
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
from pymentor_analytics.adapters.privacy_authority.server import create_authority_app
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.privacy import PrivacyRuntime
from tools.bootstrap_postgres import cleanup, config, connect, create

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT.parent / 'core-api'))
from pymentor_core.adapters.analytics_http import privacy as core_privacy
from pymentor_core.modules.analytics_delivery import local_collection
from pymentor_core.modules.analytics_delivery.contracts import deletion


def pem(key):
    return key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()


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
            with connect(data, name) as conn:
                assert conn.execute('SELECT applied_through FROM analytics.privacy_state').fetchone() == (2,)
                rows = conn.execute('SELECT control_id,subject_ref,phase FROM analytics.privacy_receipts ORDER BY seq').fetchall()
                assert len(rows) == 2 and len({row[0] for row in rows}) == 2
                assert all(row[1] == target and row[2] == 'complete' for row in rows)
                assert conn.execute('SELECT subject_ref FROM analytics.admission_subject_windows').fetchall() == [(control,)]
                assert conn.execute('SELECT deleted FROM analytics.subject_fences WHERE subject_ref=%s', (target,)).fetchone() == (True,)
    finally:
        cleanup(data, name, sentinel, revision='a014')
