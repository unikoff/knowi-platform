import asyncio
import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import ssl
from uuid import uuid4
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import NameOID

from pymentor_analytics.adapters.core_privacy.client import SourceConfig, CoreDeletionClient, REQUEST_DOMAIN, RESPONSE_DOMAIN
from pymentor_analytics.contracts.privacy_control_v1 import canonical_json
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.factory import create_app
from pymentor_analytics.contracts.privacy_control_v1 import AuthorityUnavailable


@pytest.fixture
def owned_tmp():
    root = Path(__file__).resolve().parents[2] / ".local"
    root.mkdir(exist_ok=True)
    path = root / ("tls-client-test-" + uuid4().hex)
    path.mkdir()
    try:
        yield path
    finally:
        for entry in path.iterdir():
            entry.unlink()
        path.rmdir()


def credentials(tmp_path):
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
          .not_valid_after(now + timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
          .sign(ca_key, hashes.SHA256()))
    def issue(label):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, label)]))
                .issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(label)]), False)
                .sign(ca_key, hashes.SHA256()))
        cert_path, key_path = tmp_path / f"{label}.crt", tmp_path / f"{label}.key"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        return str(cert_path), str(key_path)
    ca_path = tmp_path / 'ca.crt'
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    return str(ca_path), issue('localhost'), issue('client')


def config(ca, client, url='https://localhost:1234'):
    signer = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return dict(url=url, authority_id='authority', issuer='issuer', public_keys={'key': signer},
                reader_secret=base64.urlsafe_b64encode(b'r' * 32).rstrip(b'=').decode(), fixture_mode=False,
                runtime_mode='production', tls_ca_file=ca, tls_cert_file=client[0], tls_key_file=client[1])


def test_production_requires_complete_tls_and_no_plaintext_or_fixture(owned_tmp):
    tmp_path = owned_tmp
    ca, _, client = credentials(tmp_path)
    raw = config(ca, client)
    AuthorityConfig(**raw)
    for change in ({'url': 'http://localhost:1234'}, {'fixture_mode': True}, {'tls_ca_file': None},
                   {'tls_cert_file': ''}, {'tls_key_file': None}, {'runtime_mode': 'local'}):
        with pytest.raises(ValueError):
            AuthorityConfig(**(raw | change))
    with pytest.raises(ValueError, match='TLS configuration'):
        AuthorityClient(AuthorityConfig(**(raw | {'tls_ca_file': str(tmp_path / 'missing')})))


def test_real_mtls_rejects_wrong_server_name_and_requires_client_cert(owned_tmp):
    tmp_path = owned_tmp
    ca, server, client = credentials(tmp_path)
    async def scenario():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*server)
        ctx.load_verify_locations(cafile=ca)
        ctx.verify_mode = ssl.CERT_REQUIRED
        seen = []
        async def handle(reader, writer):
            seen.append(writer.get_extra_info('peercert'))
            await reader.readuntil(b'\r\n\r\n')
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}')
            await writer.drain()
            writer.close()
        listener = await asyncio.start_server(handle, '127.0.0.1', 0, ssl=ctx)
        port = listener.sockets[0].getsockname()[1]
        try:
            good = AuthorityClient(AuthorityConfig(**config(ca, client, f'https://localhost:{port}')))
            assert await good._http('/v1/attach', b'{}') == b'{}'
            assert seen and seen[0]
            bad = AuthorityClient(AuthorityConfig(**config(ca, client, f'https://127.0.0.1:{port}')))
            with pytest.raises(AuthorityUnavailable):
                await bad._http('/v1/attach', b'{}')
            no_cert = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            no_cert.load_verify_locations(cafile=ca)
            reader, writer = await asyncio.open_connection('127.0.0.1', port, ssl=no_cert, server_hostname='localhost')
            writer.write(b'GET / HTTP/1.1\r\n\r\n')
            try:
                await writer.drain()
                assert await reader.read(1) == b''
            except (ConnectionError, ssl.SSLError):
                pass
            writer.close()
        finally:
            listener.close()
            await listener.wait_closed()
    asyncio.run(scenario())


def test_production_profile_requires_authority_and_disables_browser_route(owned_tmp):
    tmp_path = owned_tmp
    ca, _, client = credentials(tmp_path)
    raw = config(ca, client)
    source = dict(url='https://localhost:8443/api/internal/analytics/deletions', instance_id=str(uuid4()),
                  secret=base64.urlsafe_b64encode(b's' * 32).rstrip(b'=').decode(), runtime_mode='production',
                  tls_ca_file=ca, tls_cert_file=client[0], tls_key_file=client[1])
    opts = dict(core_deletion_source_json=json.dumps(source),
                core_deletion_status_database_url='postgresql://analytics_test_transfer_status@127.0.0.1:57392/analytics_test', database_url='postgresql://synthetic@127.0.0.1:57392/analytics_test', issuer_mode='production',
                trust_manifest_json='{}', privacy_authority_json=json.dumps(raw),
                processor_bucket_worker_enabled=False, processor_generic_worker_enabled=False)
    settings = Settings(**opts)
    app = create_app('collector', settings)
    assert '/api/analytics/events/batch' not in {route.path for route in app.routes}
    with pytest.raises(ValueError):
        create_app('processor', settings.model_copy(update={'processor_generic_worker_enabled': True}))
    with pytest.raises(ValueError):
        create_app('processor', settings.model_copy(update={'core_deletion_status_database_url': None}))
    with pytest.raises(ValueError):
        create_app('privacy', settings.model_copy(update={'core_deletion_status_database_url': None}))
    for change in ({'privacy_authority_json': None},
                   {'fixture_catalog_json': '{}'}, {'core_deletion_source_json': None}, {'core_deletion_source_json': '{}'},
                   {'privacy_authority_json': json.dumps(raw | {'runtime_mode': 'local'})}):
        with pytest.raises(ValueError):
            Settings(**(opts | change))


def test_core_source_real_mtls_and_authenticated_snapshot(owned_tmp, monkeypatch):
    ca, server, client = credentials(owned_tmp)
    secret = b's' * 32
    instance = str(uuid4())
    async def scenario():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*server)
        ctx.load_verify_locations(cafile=ca)
        ctx.verify_mode = ssl.CERT_REQUIRED
        async def handle(reader, writer):
            request = (await reader.readuntil(b'\r\n\r\n')).decode('ascii')
            params = parse_qs(urlsplit(request.split(' ')[1]).query)
            nonce = params['nonce'][0]
            challenge = {'version': 1, 'instance_id': instance, 'nonce': nonce}
            expected = hmac.new(secret, REQUEST_DOMAIN + canonical_json(challenge), hashlib.sha256).hexdigest()
            assert f'X-Analytics-Request-MAC: {expected}\r\n' in request
            response = challenge | {'as_of': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'events': []}
            response['mac'] = hmac.new(secret, RESPONSE_DOMAIN + canonical_json(response), hashlib.sha256).hexdigest()
            payload = canonical_json(response)
            writer.write(f'HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\n\r\n'.encode() + payload)
            await writer.drain()
            writer.close()
        listener = await asyncio.start_server(handle, '127.0.0.1', 0, ssl=ctx)
        loop = asyncio.get_running_loop()
        resolve = loop.getaddrinfo
        async def ipv4(host, port, **kwargs):
            return await resolve('127.0.0.1' if host == 'localhost' else host, port, **kwargs)
        monkeypatch.setattr(loop, 'getaddrinfo', ipv4)
        port = listener.sockets[0].getsockname()[1]
        raw = dict(url=f'https://localhost:{port}/api/internal/analytics/deletions', instance_id=instance,
                   secret=base64.urlsafe_b64encode(secret).rstrip(b'=').decode(), runtime_mode='production',
                   tls_ca_file=ca, tls_cert_file=client[0], tls_key_file=client[1])
        try:
            source = SourceConfig.from_json(json.dumps(raw))
            assert await CoreDeletionClient(source).snapshot() == ()
            wrong_host = SourceConfig.from_json(json.dumps(raw | {'url': raw['url'].replace('localhost', '127.0.0.1')}))
            with pytest.raises(PrivacyUnavailable):
                await CoreDeletionClient(wrong_host).snapshot()
            for change in ({'url': raw['url'].replace('https:', 'http:')}, {'tls_key_file': ''}, {'runtime_mode': 'local'}):
                with pytest.raises(ValueError):
                    SourceConfig.from_json(json.dumps(raw | change))
        finally:
            listener.close()
            await listener.wait_closed()
    asyncio.run(scenario())
