import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import time
from uuid import uuid4

import pytest
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ed25519
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

from pymentor_analytics.adapters.privacy_authority.journal import JournalError
from pymentor_analytics.adapters.privacy_authority.production_journal import ProductionJournal
from pymentor_analytics.adapters.privacy_authority.production_server import load_config
from pymentor_analytics.contracts.v2 import DeletionFence
from pymentor_analytics.contracts.privacy_control_v1 import canonical_json, deletion_command_bytes
from pymentor_analytics.adapters.privacy_authority.client import _DOMAIN


def fence():
    return DeletionFence.model_validate_json(json.dumps({"schema_version": 2, "kind": "deletion_fence", "control_id": str(uuid4()),
        "subject_ref": str(uuid4()), "epoch": 1, "occurred_at": datetime.now(timezone.utc).isoformat()}))


@pytest.fixture
def owned_path():
    root = Path(__file__).resolve().parents[2] / ".local"
    root.mkdir(exist_ok=True)
    path = root / ("test-production-" + uuid4().hex)
    path.mkdir()
    yield path
    if path.parent == root and path.name.startswith("test-production-") and not path.is_symlink():
        shutil.rmtree(path)


def journal(tmp_path):
    a, b = tmp_path / "journal", tmp_path / "anchor"
    a.mkdir(); b.mkdir()
    return ProductionJournal.initialize(a / "journal.db", b / "anchor.db", "authority:test")


def test_concurrent_retries_are_serialized_and_conflicts_fail(owned_path):
    tmp_path = owned_path
    j = journal(tmp_path)
    event = fence()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: j.append("producer", event), range(8)))
    assert results.count(("accepted", 1)) == 1
    assert results.count(("duplicate", 1)) == 7
    assert j.snapshot(after=0)[0] == 1


def test_independent_rollback_and_same_hwm_divergence_block_every_operation(owned_path):
    tmp_path = owned_path
    j = journal(tmp_path)
    original = tmp_path / "old.db"
    shutil.copyfile(j.path, original)
    j.append("producer", fence())
    backup = tmp_path / "latest.db"
    shutil.copyfile(j.path, backup)
    shutil.copyfile(original, j.path)
    with pytest.raises(JournalError): j.snapshot(after=0)
    with pytest.raises(JournalError): j.append("producer", fence())
    shutil.copyfile(backup, j.path)
    import sqlite3
    with closing(sqlite3.connect(j.path)) as db:
        db.execute("DROP TRIGGER journal_no_update")
        db.execute("UPDATE journal SET received_at='2000-01-01T00:00:00+00:00'")
        db.commit()
    with pytest.raises(JournalError): ProductionJournal(j.path, j.anchor_path, j.authority_id)


def test_interrupted_anchor_update_blocks_restart(owned_path, monkeypatch):
    j = journal(owned_path)
    original = j._digest
    calls = 0
    def interrupted():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise JournalError("simulated interruption")
        return original()
    monkeypatch.setattr(j, "_digest", interrupted)
    with pytest.raises(JournalError): j.append("producer", fence())
    with pytest.raises(JournalError): ProductionJournal(j.path, j.anchor_path, j.authority_id)

def test_missing_or_existing_state_never_reinitialized(owned_path):
    tmp_path = owned_path
    j = journal(tmp_path)
    with pytest.raises(JournalError): ProductionJournal.initialize(j.path, j.anchor_path, j.authority_id)
    j.anchor_path.unlink()
    with pytest.raises(JournalError): ProductionJournal(j.path, j.anchor_path, j.authority_id)
    assert j.path.exists()


def _key(path, key):
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))


def _certs(tmp_path):
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic test ca")])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(ca_key, hashes.SHA256()))
    ca_path = tmp_path / "ca.pem"; ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    def issued(label, usage):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, label)]))
          .issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
          .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
          .add_extension(x509.ExtendedKeyUsage([usage]), critical=False).sign(ca_key, hashes.SHA256()))
        cp, kp = tmp_path / (label + ".pem"), tmp_path / (label + ".key")
        cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM)); _key(kp, key)
        return cp, kp
    return ca_path, issued("server", ExtendedKeyUsageOID.SERVER_AUTH), issued("client", ExtendedKeyUsageOID.CLIENT_AUTH)


def test_live_production_server_requires_mtls_and_reader_credential(owned_path):
    tmp_path = owned_path
    import httpx
    ca, (cert, key), (client_cert, client_key) = _certs(tmp_path)
    j = journal(tmp_path)
    signer, producer, command = (ed25519.Ed25519PrivateKey.generate() for _ in range(3))
    signing = tmp_path / "signing.key"; _key(signing, signer)
    secret = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    secret_path = tmp_path / "reader"; secret_path.write_text(secret)
    def public(k): return k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    cfg = {"mode": "production", "fixture_mode": False, "authority_id": j.authority_id, "issuer": j.authority_id,
        "kid": "a", "bind_host": "127.0.0.1", "port": port, "journal_path": str(j.path), "anchor_path": str(j.anchor_path),
        "signing_key_file": str(signing), "producer_issuer": "core", "producer_public_keys": {"p": public(producer)},
        "allowed_producers": ["core"], "producer_command_public_keys": {"core": public(command)}, "reader_secret_file": str(secret_path),
        "tls_cert_file": str(cert), "tls_key_file": str(key), "tls_client_ca_file": str(ca)}
    config = tmp_path / "server.json"; config.write_text(json.dumps(cfg))
    assert load_config(config)["mode"] == "production"
    cfg["fixture_mode"] = True; config.write_text(json.dumps(cfg))
    with pytest.raises(ValueError): load_config(config)
    cfg["fixture_mode"] = False; config.write_text(json.dumps(cfg))
    service = Path(__file__).resolve().parents[2]
    cli = [sys.executable, "-m", "pymentor_analytics.adapters.privacy_authority.production_server"]
    env = {**os.environ, "PYTHONPATH": str(service)}
    for action in ("init", "serve"):
        result = subprocess.run(cli + [action, "--config", str(config)], cwd=service, env=env, capture_output=True, timeout=5)
        assert result.returncode != 0
    cfg["bind_host"] = "0.0.0.0"; config.write_text(json.dumps(cfg))
    for action in ("init", "serve"):
        result = subprocess.run(cli + [action, "--config", str(config), "--allow-local-anchor-fixture"], cwd=service, env=env, capture_output=True, timeout=5)
        assert result.returncode != 0
    cfg["bind_host"] = "127.0.0.1"; config.write_text(json.dumps(cfg))
    proc = subprocess.Popen([sys.executable, "-m", "pymentor_analytics.adapters.privacy_authority.production_server", "serve", "--config", str(config), "--allow-local-anchor-fixture"],
                            cwd=service, env={**os.environ, "PYTHONPATH": str(service)}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    url = f"https://127.0.0.1:{port}/v1/attach"
    try:
        end = time.monotonic() + 10
        while True:
            if proc.poll() is not None: pytest.fail(f"server exited: {proc.communicate()}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.2): break
            except OSError:
                if time.monotonic() >= end: pytest.fail("server did not listen")
                time.sleep(.05)
        body = {"nonce": str(uuid4()), "session": None, "after": 0, "through": None, "limit": 100}
        with pytest.raises(httpx.TransportError):
            httpx.post(url, json=body, verify=ssl.create_default_context(cafile=str(ca)), trust_env=False)
        context = ssl.create_default_context(cafile=str(ca)); context.load_cert_chain(str(client_cert), str(client_key))
        with httpx.Client(verify=context, trust_env=False) as client:
            assert client.post(url, json=body).status_code == 401
            response = client.post(url, json=body, headers={"Authorization": "Bearer " + secret})
            assert response.status_code == 200 and response.json()["frame"]["high_watermark"] == 0
            now = int(time.time())
            claims = {"grant_version": 1, "iss": "core", "aud": "analytics-control", "sub": "core",
                      "iat": now - 1, "nbf": now - 1, "exp": now + 50, "scope": "control:write"}
            token = jwt.encode(claims, producer, algorithm="EdDSA", headers={"typ": "JWT", "kid": "p"})
            event, nonce = fence(), uuid4()
            signature = base64.urlsafe_b64encode(command.sign(deletion_command_bytes("core", nonce, event))).rstrip(b"=").decode()
            append_body = {"nonce": str(nonce), "control": event.model_dump(mode="json"), "signature": signature}
            receipt = client.post(url.replace("attach", "append"), json=append_body, headers={"Authorization": "Bearer " + token})
            assert receipt.status_code == 200
            frame = receipt.json()["frame"]
            assert frame["status"] == "accepted" and frame["seq"] == 1
            encoded = receipt.json()["signature"]
            signer.public_key().verify(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)), _DOMAIN + canonical_json(frame))
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)






def test_postgres_anchor_configuration_rejects_identity_and_tls_overrides(owned_path):
    from pymentor_analytics.adapters.privacy_authority.postgres_anchor import PostgresAnchorJournal
    ca = owned_path / "ca.pem"; ca.write_text("synthetic placeholder")
    dsn = "host=anchor.example dbname=privacy user=anchor password=synthetic"
    def config(value=dsn, **changes):
        args = {"ca_file": str(ca), "host": "anchor.example", "database": "privacy", "user": "anchor"}
        return PostgresAnchorJournal._validate_connection(value, **{**args, **changes})
    result = config()
    assert result["sslmode"] == "verify-full" and result["sslrootcert"] == str(ca) and result["gssencmode"] == "disable"
    for invalid in (dsn + " sslmode=disable", dsn + " hostaddr=127.0.0.1", dsn + " options=-cfoo=bar", dsn.replace("anchor.example", "other.example")):
        with pytest.raises(ValueError): config(invalid)
    with pytest.raises(ValueError): config(database="other")


def test_postgres_checkpoint_transaction_fail_closed_without_startup_ddl(owned_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    from pymentor_analytics.adapters.privacy_authority import postgres_anchor as module
    ca = owned_path / "ca.pem"; ca.write_text("placeholder")
    db_state = {"row": ("authority:test", 1, 0, hashlib.sha256(b"").hexdigest()), "commit_error": False, "queries": []}
    class Cursor:
        def __init__(self, row=None, rowcount=1): self.row, self.rowcount = row, rowcount
        def fetchone(self): return self.row
    class Connection:
        pgconn = SimpleNamespace(ssl_in_use=True)
        def __init__(self): self.pending = None
        def execute(self, sql, params=None):
            db_state["queries"].append(sql)
            if sql.startswith("SELECT current_database"):
                return Cursor(("privacy", "anchor", False))
            if sql.startswith("SELECT authority_id"):
                return Cursor(db_state["row"])
            if sql.startswith("UPDATE"):
                high, digest, identity, old_high, old_digest = params
                if db_state["row"] != (identity, 1, old_high, old_digest): return Cursor(rowcount=0)
                self.pending = (identity, 1, high, digest)
            return Cursor()
        def commit(self):
            if db_state["commit_error"]: raise RuntimeError("unknown commit")
            if self.pending: db_state["row"] = self.pending
        def rollback(self): pass
        def close(self): pass
    connections = []
    def connect(**kwargs):
        connections.append(kwargs)
        return Connection()
    monkeypatch.setattr(module.psycopg, "connect", connect)
    opts = {"dsn": "host=anchor.example dbname=privacy user=anchor password=synthetic", "ca_file": str(ca),
            "host": "anchor.example", "database": "privacy", "user": "anchor"}
    journal_path = owned_path / "journal.db"
    j = module.PostgresAnchorJournal.initialize(journal_path, "authority:test", **opts)
    assert j.append("core", fence()) == ("accepted", 1)
    assert j.snapshot(after=0)[0] == 1
    assert all("CREATE " not in query for query in db_state["queries"])
    assert any("FOR UPDATE" in query for query in db_state["queries"])
    assert "SET LOCAL synchronous_commit = 'on'" in db_state["queries"]
    assert all(c["sslmode"] == "verify-full" and c["gssencmode"] == "disable" for c in connections)
    db_state["commit_error"] = True
    with pytest.raises(JournalError): j.append("core", fence())
    db_state["commit_error"] = False
    with pytest.raises(JournalError): module.PostgresAnchorJournal(journal_path, "authority:test", **opts)


def test_postgres_anchor_rejects_missing_tls_and_database_identity(monkeypatch):
    from types import SimpleNamespace
    from pymentor_analytics.adapters.privacy_authority import postgres_anchor as module
    config = {"dbname": "privacy", "user": "anchor"}
    class Connection:
        pgconn = SimpleNamespace(ssl_in_use=False)
        def execute(self, sql):
            return SimpleNamespace(fetchone=lambda: ("wrong", "anchor", False))
        def close(self): pass
    monkeypatch.setattr(module.psycopg, "connect", lambda **kwargs: Connection())
    with pytest.raises(JournalError):
        with module._PostgresLock(config): pass
    Connection.pgconn = SimpleNamespace(ssl_in_use=True)
    with pytest.raises(JournalError):
        with module._PostgresLock(config): pass



def test_postgres_anchor_rejects_standby(monkeypatch):
    from types import SimpleNamespace
    from pymentor_analytics.adapters.privacy_authority import postgres_anchor as module
    class Connection:
        pgconn = SimpleNamespace(ssl_in_use=True)
        def execute(self, sql):
            return SimpleNamespace(fetchone=lambda: ("privacy", "anchor", True))
        def close(self): pass
    monkeypatch.setattr(module.psycopg, "connect", lambda **kwargs: Connection())
    with pytest.raises(JournalError):
        with module._PostgresLock({"dbname": "privacy", "user": "anchor"}): pass
