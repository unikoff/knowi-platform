import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig, _DOMAIN
from pymentor_analytics.adapters.privacy_authority.journal import Journal, JournalConflict, JournalError
from pymentor_analytics.adapters.privacy_authority.server import create_authority_app, HEADER_TIMEOUT_SECONDS, MAX_CONNECTIONS
from pymentor_analytics.contracts.privacy_control_v1 import (AuthorityConflict, AuthorityProtocolError, AuthorityUnavailable,
    canonical_json, deletion_command_bytes, deletion_hash)
from pymentor_analytics.contracts.v2 import DeletionFence

ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = "synthetic-authority-1"
ISSUER = "synthetic-authority-issuer"
PRODUCER = "synthetic-producer"


def pem_public(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def pem_private(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def token(key, *, audience="analytics-control", scope="control:write", sub=PRODUCER):
    now = int(time.time())
    claims = {"grant_version": 1, "iss": "synthetic-control", "aud": audience, "sub": sub,
              "iat": now - 1, "nbf": now - 1, "exp": now + 50, "scope": scope}
    return jwt.encode(claims, key, algorithm="EdDSA", headers={"typ": "JWT", "kid": "producer"})


def control():
    raw = {"schema_version": 2, "kind": "deletion_fence", "control_id": str(uuid4()),
           "subject_ref": str(uuid4()), "epoch": 1, "occurred_at": datetime.now(timezone.utc).isoformat()}
    return DeletionFence.model_validate_json(json.dumps(raw))


def b64(data): return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def settings(port, signer, secret):
    return {"url": f"http://127.0.0.1:{port}", "authority_id": AUTHORITY, "issuer": ISSUER,
            "public_keys": {"authority": pem_public(signer)}, "reader_secret": secret, "fixture_mode": True}


@pytest.fixture
def private_tmp_path():
    """Own one explicitly scoped fixture directory; never remove other data."""
    local_root = ROOT.resolve() / ".local"
    local_root.mkdir(parents=True, exist_ok=True)
    if local_root.is_symlink() or local_root.is_junction() or local_root.resolve() != local_root:
        raise RuntimeError("unsafe fixture root")
    name = f"test-authority-{uuid4().hex}"
    target = local_root / name
    target.mkdir(mode=0o700, exist_ok=False)
    try:
        yield target
    finally:
        resolved = target.resolve(strict=True)
        if (target.is_symlink() or target.is_junction() or resolved.parent != local_root
                or resolved.name != name or resolved != target):
            raise RuntimeError("unsafe fixture cleanup target")
        shutil.rmtree(resolved)


@pytest.fixture
def fixture_server(private_tmp_path):
    journal = private_tmp_path / "authority.sqlite"
    Journal.initialize(journal, AUTHORITY)
    signer, producer, command_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    secret = b64(os.urandom(32))
    keyfile, secretfile, configfile = (private_tmp_path / "authority.key", private_tmp_path / "reader.key", private_tmp_path / "config.json")
    keyfile.write_bytes(pem_private(signer)); secretfile.write_text(secret, encoding="ascii")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    cfg = {"fixture_mode": True, "bind_host": "127.0.0.1", "port": port, "issuer": ISSUER,
           "kid": "authority", "signing_key_file": str(keyfile), "producer_issuer": "synthetic-control",
           "producer_public_keys": {"producer": pem_public(producer)}, "allowed_producers": [PRODUCER],
           "producer_command_public_keys": {PRODUCER: pem_public(command_key)},
           "reader_secret_file": str(secretfile)}
    configfile.write_text(json.dumps(cfg), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    command = [sys.executable, "-B", "-m", "pymentor_analytics.adapters.privacy_authority.server", "serve",
               "--journal", str(journal), "--authority-id", AUTHORITY, "--config", str(configfile)]
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    active = []
    def start():
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        active.append(child)
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if child.poll() is not None:
                out, err = child.communicate()
                pytest.fail(f"fixture authority exited without serving; stdout={out!r}, stderr={err!r}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.1): break
            except OSError:
                time.sleep(.05)
        else:
            pytest.fail("fixture authority did not listen")
        return child
    def stop(child):
        if child.poll() is None:
            child.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
            try: child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait(timeout=5)
        out, err = child.communicate()
        assert secret.encode() not in out + err and b"PRIVATE KEY" not in out + err
    yield {"journal": journal, "signer": signer, "producer": producer, "command_key": command_key, "secret": secret, "port": port,
           "config": settings(port, signer, secret), "start": start, "stop": stop}
    for child in active:
        if child.poll() is None: stop(child)


def test_live_http_signatures_journal_restart_and_separate_credentials(fixture_server):
    f = fixture_server
    child = f["start"]()
    first = control()
    raw_control = first.model_dump(mode="json")
    async def scenario():
        config = AuthorityConfig.from_json(json.dumps(f["config"]))
        assert f["secret"] not in repr(config) and "PUBLIC KEY" not in repr(config)
        client = AuthorityClient(config)
        attach = await client.attach(0)
        assert attach.is_fresh() and attach.frame.high_watermark == 0
        session = attach.frame.session
        def command(value):
            nonce = uuid4()
            return {"nonce": str(nonce), "control": value.model_dump(mode="json"),
                    "signature": b64(f["command_key"].sign(deletion_command_bytes(PRODUCER, nonce, value)))}
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{f['port']}", trust_env=False) as http:
            cmd = command(first)
            bad = await http.post("/v1/append", headers={"Authorization": "Bearer " + token(f["producer"], audience="analytics")}, json=cmd)
            assert bad.status_code == 401
            now = int(time.time())
            expired_claims = {"grant_version": 1, "iss": "synthetic-control", "aud": "analytics-control", "sub": PRODUCER,
                              "iat": now - 61, "nbf": now - 61, "exp": now - 1, "scope": "control:write"}
            expired_token = jwt.encode(expired_claims, f["producer"], algorithm="EdDSA", headers={"typ": "JWT", "kid": "producer"})
            assert (await http.post("/v1/append", headers={"Authorization": "Bearer " + expired_token}, json=cmd)).status_code == 401
            result = await http.post("/v1/append", headers={"Authorization": "Bearer " + token(f["producer"])}, json=cmd)
            assert result.status_code == 200
            envelope = result.json()
            f["signer"].public_key().verify(base64.urlsafe_b64decode(envelope["signature"] + "=="), _DOMAIN + canonical_json(envelope["frame"]))
            assert envelope["frame"]["status"] == "accepted" and envelope["frame"]["seq"] == 1
            assert envelope["frame"]["payload_hash"] == deletion_hash(PRODUCER, first)
            replay_nonce = await http.post("/v1/append", headers={"Authorization": "Bearer " + token(f["producer"])}, json=cmd)
            assert replay_nonce.status_code == 401
            repeated = await http.post("/v1/append", headers={"Authorization": "Bearer " + token(f["producer"])}, json=command(first))
            assert repeated.json()["frame"]["status"] == "duplicate" and repeated.json()["frame"]["seq"] == 1
            altered = {**raw_control, "epoch": 2}
            tampered = await http.post("/v1/append", headers={"Authorization": "Bearer " + token(f["producer"])}, json={**command(first), "control": altered})
            assert tampered.status_code == 401
            modified = DeletionFence.model_validate_json(json.dumps(altered))
            conflict = await http.post("/v1/append", headers={"Authorization": "Bearer " + token(f["producer"])}, json=command(modified))
            assert conflict.status_code == 409
            oversized_epoch = DeletionFence.model_validate_json(json.dumps({**raw_control, "control_id": str(uuid4()), "epoch": 1 << 63}))
            assert (await http.post("/v1/append", headers={"Authorization": "Bearer " + token(f["producer"])}, json=command(oversized_epoch))).status_code == 401
            unauthorized_read = await http.post("/v1/observe", headers={"Authorization": "Bearer " + token(f["producer"])}, json={})
            assert unauthorized_read.status_code == 401
            nonce = str(uuid4())
            read_command = {"nonce": nonce, "session": str(session), "after": 0, "through": None, "limit": 100}
            read_headers = {"Authorization": "Bearer " + f["secret"]}
            assert (await http.post("/v1/observe", headers=read_headers, json=read_command)).status_code == 200
            assert (await http.post("/v1/observe", headers=read_headers, json=read_command)).status_code == 401
            assert (await http.post("/v1/observe", headers=read_headers, content=b' ' * 8193)).status_code == 401
        barrier = await client.barrier(session)
        assert barrier.frame.high_watermark == 1
        page = await client.page(session, 0, 1)
        assert page.frame.scanned_through == 1 and page.frame.entries[0].control == first
        assert page.frame.entries[0].payload_hash == deletion_hash(PRODUCER, first)
        assert (await client.observe(session, 1)).frame.high_watermark == 1
        receipt = await client.append(first, producer=PRODUCER, producer_token=token(f["producer"]), command_key=f["command_key"])
        assert receipt.status == "duplicate" and receipt.seq == 1
        with pytest.raises(AuthorityConflict):
            await client.append(modified, producer=PRODUCER, producer_token=token(f["producer"]), command_key=f["command_key"])
        with pytest.raises(AuthorityProtocolError): await client.observe(uuid4(), 0)
        with pytest.raises((AuthorityProtocolError, AuthorityUnavailable)): await client.attach(2)
        with pytest.raises(AuthorityProtocolError): await client.observe(session, 0)
        session = (await client.attach(0)).frame.session
        lost = control()
        original_http = client._http
        async def lost_reply(*args, **kwargs):
            await original_http(*args, **kwargs)  # actual committed HTTP response is discarded
            raise AuthorityUnavailable()
        client._http = lost_reply
        try:
            with pytest.raises(AuthorityUnavailable):
                await client.append(lost, producer=PRODUCER, producer_token=token(f["producer"]), command_key=f["command_key"])
        finally:
            client._http = original_http
        replay = await client.append(lost, producer=PRODUCER, producer_token=token(f["producer"]), command_key=f["command_key"])
        assert replay.status == "duplicate" and replay.seq == 2
        await client.append(control(), producer=PRODUCER, producer_token=token(f["producer"]), command_key=f["command_key"])
        assert (await client.observe(session, 0)).frame.high_watermark == 3
        page1 = await client.page(session, 0, 3, limit=2)
        page2 = await client.page(session, 2, 3, limit=2)
        assert [item.seq for item in page1.frame.entries] == [1, 2]
        assert [item.seq for item in page2.frame.entries] == [3]
        with pytest.raises(AuthorityProtocolError): await client.page(session, 0, 3, limit=101)
        reattached = await client.attach(0)
        assert len(client._sessions) == 1
        with pytest.raises(AuthorityProtocolError): await client.observe(session, 0)
        return client, reattached.frame.session
    client, session = asyncio.run(scenario())
    f["stop"](child)
    child = f["start"]()
    async def after_restart():
        with pytest.raises((AuthorityProtocolError, AuthorityUnavailable)): await client.observe(session, 0)
        reopened = await client.attach(0)
        assert reopened.frame.high_watermark == 3 and (await client.page(reopened.frame.session, 0, 1)).frame.entries[0].control == first
    asyncio.run(after_restart())
    f["stop"](child)


def test_explicit_init_idempotency_and_corruption_detection(tmp_path):
    path = tmp_path / "journal.sqlite"
    with pytest.raises(JournalError): Journal(path, AUTHORITY)
    journal = Journal.initialize(path, AUTHORITY)
    with pytest.raises(FileExistsError): Journal.initialize(path, AUTHORITY)
    with pytest.raises(JournalError): Journal(path, "wrong-authority")
    fact = control()
    too_large = DeletionFence.model_validate_json(json.dumps({**fact.model_dump(mode="json"), "epoch": 1 << 63}))
    with pytest.raises(ValueError, match="epoch exceeds"):
        journal.append(PRODUCER, too_large)
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: journal.append(PRODUCER, fact), range(4)))
    assert sorted(status for status, _ in outcomes) == ["accepted", "duplicate", "duplicate", "duplicate"]
    assert {seq for _, seq in outcomes} == {1}
    with pytest.raises(JournalConflict):
        journal.append("another-producer", fact)
    with sqlite3.connect(str(path)) as db:
        db.execute("DROP TRIGGER journal_no_delete")
        db.execute("DELETE FROM journal WHERE seq=1")
        db.commit()
    with pytest.raises(JournalError): journal.snapshot(after=1, through=1)
    with pytest.raises(JournalError): Journal(path, AUTHORITY)


@pytest.mark.parametrize("mutation", ["tamper", "wrong_key", "wrong_nonce", "expired", "wrong_issuer", "wrong_audience", "unknown_kid", "overflow_hwm", "bool_version"])
def test_client_rejects_untrusted_frame(mutation):
    signer = Ed25519PrivateKey.generate()
    secret = b64(os.urandom(32))
    client = AuthorityClient(AuthorityConfig.from_json(json.dumps(settings(12345, signer, secret))))
    async def fake_http(path, request):
        parsed = json.loads(request)
        issued = int(time.time())
        body = {"version": 1, "authority_id": AUTHORITY, "issuer": ISSUER, "audience": "analytics-privacy-journal",
                "kid": "authority", "kind": "attach", "nonce": parsed["nonce"], "session": str(uuid4()),
                "high_watermark": 0, "after": 0, "through": None, "scanned_through": 0, "entries": [],
                "issued_at": issued, "expires_at": issued + 3}
        if mutation == "wrong_nonce": body["nonce"] = str(uuid4())
        if mutation == "expired": body.update(issued_at=issued - 4, expires_at=issued - 1)
        if mutation == "wrong_issuer": body["issuer"] = "other"
        if mutation == "wrong_audience": body["audience"] = "other"
        if mutation == "unknown_kid": body["kid"] = "unknown"
        if mutation == "overflow_hwm": body["high_watermark"] = 1 << 63
        if mutation == "bool_version": body["version"] = True
        key = Ed25519PrivateKey.generate() if mutation == "wrong_key" else signer
        signature = b64(key.sign(_DOMAIN + canonical_json(body)))
        if mutation == "tamper": body["high_watermark"] = 1
        return canonical_json({"frame": body, "signature": signature})
    client._http = fake_http
    async def scenario():
        with pytest.raises(AuthorityProtocolError): await client.attach(0)
    asyncio.run(scenario())


def test_config_rejects_external_url_and_no_implicit_fixture_mode():
    signer = Ed25519PrivateKey.generate()
    secret = b64(os.urandom(32))
    config = settings(1234, signer, secret)
    for update in ({"url": "http://169.254.169.254:80"}, {"url": "https://localhost:1234"},
                   {"url": "http://user:pass@localhost:1234"}, {"url": "http://localhost:1234/a"},
                   {"fixture_mode": 1}, {"reader_secret": "short"}):
        with pytest.raises(ValueError, match="invalid authority configuration"):
            AuthorityConfig.from_json(json.dumps({**config, **update}))


def test_client_rejects_sequence_gap_and_hwm_rollback():
    signer = Ed25519PrivateKey.generate()
    client = AuthorityClient(AuthorityConfig.from_json(json.dumps(settings(1234, signer, b64(os.urandom(32))))))
    subject = control()
    async def signed_http(path, raw):
        req = json.loads(raw); now = int(time.time()); kind = path.rsplit("/", 1)[1]
        session = req["session"] or str(uuid4())
        hwm = 2 if kind != "observe" else 1
        entries = []
        scanned = req["after"]
        if kind == "page":
            scanned = 2
            entries = [{"seq": 2, "producer": PRODUCER, "control": subject.model_dump(mode="json"),
                        "received_at": datetime.now(timezone.utc).isoformat(), "payload_hash": deletion_hash(PRODUCER, subject)}]
        body = {"version": 1, "authority_id": AUTHORITY, "issuer": ISSUER, "audience": "analytics-privacy-journal",
                "kid": "authority", "kind": kind, "nonce": req["nonce"], "session": session, "high_watermark": hwm,
                "after": req["after"], "through": req["through"], "scanned_through": scanned, "entries": entries,
                "issued_at": now, "expires_at": now + 3}
        return canonical_json({"frame": body, "signature": b64(signer.sign(_DOMAIN + canonical_json(body)))})
    client._http = signed_http
    async def scenario():
        attach = await client.attach(0)
        assert attach.frame.high_watermark == 2
        with pytest.raises(AuthorityProtocolError): await client.page(attach.frame.session, 0, 2)
        with pytest.raises(AuthorityProtocolError): await client.observe(attach.frame.session, 0)
    asyncio.run(scenario())


def test_signed_huge_page_span_is_rejected_without_allocating_range():
    signer = Ed25519PrivateKey.generate()
    client = AuthorityClient(AuthorityConfig.from_json(json.dumps(settings(1234, signer, b64(os.urandom(32))))))
    maximum = (1 << 63) - 1
    async def signed_http(path, raw):
        req = json.loads(raw); now = int(time.time()); kind = path.rsplit("/", 1)[1]
        body = {"version": 1, "authority_id": AUTHORITY, "issuer": ISSUER, "audience": "analytics-privacy-journal",
                "kid": "authority", "kind": kind, "nonce": req["nonce"], "session": req["session"] or str(uuid4()),
                "high_watermark": maximum, "after": req["after"], "through": req["through"],
                "scanned_through": maximum if kind == "page" else req["after"], "entries": [],
                "issued_at": now, "expires_at": now + 3}
        return canonical_json({"frame": body, "signature": b64(signer.sign(_DOMAIN + canonical_json(body)))})
    client._http = signed_http
    async def scenario():
        attach = await client.attach(0)
        with pytest.raises(AuthorityProtocolError): await client.page(attach.frame.session, 0, maximum)
    asyncio.run(scenario())


def test_client_refuses_redirect_and_bounds_concurrent_sockets():
    async def scenario():
        started = asyncio.Event(); finish = asyncio.Event(); connections = []
        async def handler(reader, writer):
            connections.append(1)
            try:
                await reader.readuntil(b"\r\n\r\n")
                started.set()
                await finish.wait()
                writer.write(b"HTTP/1.1 302 Found\r\nLocation: http://169.254.169.254/\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            key = Ed25519PrivateKey.generate()
            client = AuthorityClient(AuthorityConfig.from_json(json.dumps(settings(port, key, b64(os.urandom(32))))))
            first = asyncio.create_task(client._http("/v1/attach", b"{}"))
            await asyncio.wait_for(started.wait(), 2)
            with pytest.raises(AuthorityUnavailable): await client._http("/v1/attach", b"{}")
            finish.set()
            with pytest.raises(AuthorityProtocolError): await first
            assert len(connections) == 1
        finally:
            server.close(); await server.wait_closed()
    asyncio.run(scenario())


def test_fixture_cli_creates_files_and_submits_without_logging_credentials(fixture_server, private_tmp_path):
    f = fixture_server
    tmp_path = private_tmp_path
    child = f["start"]()
    command = [sys.executable, "-B", "-m", "pymentor_analytics.adapters.privacy_authority.server"]
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    def invoke(*args):
        return subprocess.run(command + list(args), cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
    keys = invoke("keygen", "--private-key", str(tmp_path / "new.key"), "--public-key", str(tmp_path / "new.pub"))
    assert keys.returncode == 0 and keys.stdout.strip() == "synthetic key pair created"
    assert "PRIVATE KEY" not in keys.stdout + keys.stderr
    same_keys = invoke("keygen", "--private-key", str(tmp_path / "new.key"), "--public-key", str(tmp_path / "new.pub"))
    assert same_keys.returncode != 0
    secret = invoke("reader-secret", "--output", str(tmp_path / "generated.secret"))
    assert secret.returncode == 0 and len((tmp_path / "generated.secret").read_bytes()) == 43
    init = invoke("init", "--journal", str(tmp_path / "new.sqlite"), "--authority-id", "synthetic-cli")
    assert init.returncode == 0
    assert invoke("init", "--journal", str(tmp_path / "new.sqlite"), "--authority-id", "synthetic-cli").returncode != 0
    event = control()
    (tmp_path / "client.json").write_text(json.dumps(f["config"]), encoding="utf-8")
    (tmp_path / "command.key").write_bytes(pem_private(f["command_key"]))
    (tmp_path / "control.json").write_text(event.model_dump_json(), encoding="utf-8")
    jwt_value = token(f["producer"])
    (tmp_path / "producer.token").write_text(jwt_value, encoding="ascii")
    args = ("append", "--client-config", str(tmp_path / "client.json"), "--control-file", str(tmp_path / "control.json"),
            "--producer", PRODUCER, "--producer-token-file", str(tmp_path / "producer.token"), "--command-key", str(tmp_path / "command.key"))
    first = invoke(*args); second = invoke(*args)
    assert first.returncode == 0 and first.stdout.strip() == "synthetic journal append accepted"
    assert second.returncode == 0 and second.stdout.strip() == "synthetic journal append duplicate"
    output = first.stdout + first.stderr + second.stdout + second.stderr
    assert jwt_value not in output and f["secret"] not in output and str(event.subject_ref) not in output
    f["stop"](child)


def test_journal_writer_has_reserved_capacity_during_slow_reader(tmp_path):
    journal = Journal.initialize(tmp_path / "reserved.sqlite", AUTHORITY)
    reader_started = threading.Event(); finish_reader = threading.Event()
    class SlowJournal:
        authority_id = AUTHORITY
        def snapshot(self, **kwargs):
            reader_started.set()
            if not finish_reader.wait(3): raise RuntimeError("test reader deadline")
            return journal.snapshot(**kwargs)
        def append(self, *args): return journal.append(*args)
    signer, issuer, command_key = (Ed25519PrivateKey.generate() for _ in range(3))
    secret = b64(os.urandom(32))
    app = create_authority_app(SlowJournal(), issuer=ISSUER, kid="authority", signing_key=signer,
        producer_issuer="synthetic-control", producer_public_keys={"producer": pem_public(issuer).encode()},
        allowed_producers={PRODUCER}, producer_command_public_keys={PRODUCER: pem_public(command_key).encode()}, reader_secret=secret)
    async def scenario():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
                reader = asyncio.create_task(http.post("/v1/attach", headers={"Authorization": "Bearer " + secret},
                    json={"nonce": str(uuid4()), "session": None, "after": 0, "through": None, "limit": 100}))
                try:
                    async with asyncio.timeout(2):
                        while not reader_started.is_set(): await asyncio.sleep(.01)
                    event = control(); nonce = uuid4()
                    body = {"nonce": str(nonce), "control": event.model_dump(mode="json"),
                            "signature": b64(command_key.sign(deletion_command_bytes(PRODUCER, nonce, event)))}
                    writer = await asyncio.wait_for(http.post("/v1/append", headers={"Authorization": "Bearer " + token(issuer)}, json=body), 1)
                    assert writer.status_code == 200 and writer.json()["frame"]["seq"] == 1
                finally:
                    finish_reader.set()
                    assert (await reader).status_code == 200
    asyncio.run(scenario())


def test_authority_rejects_overlapping_signing_domains(tmp_path):
    journal = Journal.initialize(tmp_path / "domains.sqlite", AUTHORITY)
    response, issuer, command = (Ed25519PrivateKey.generate() for _ in range(3))
    base = {"issuer": ISSUER, "kid": "authority", "signing_key": response,
            "producer_issuer": "synthetic-control", "producer_public_keys": {"producer": pem_public(issuer).encode()},
            "allowed_producers": {PRODUCER}, "producer_command_public_keys": {PRODUCER: pem_public(command).encode()},
            "reader_secret": b64(os.urandom(32))}
    for change in ({"signing_key": issuer}, {"producer_command_public_keys": {PRODUCER: pem_public(issuer).encode()}},
                   {"producer_command_public_keys": {PRODUCER: pem_public(response).encode()}}, {"producer_command_public_keys": {}}):
        with pytest.raises(ValueError): create_authority_app(journal, **{**base, **change})


def test_fixture_process_caps_idle_sockets_and_expires_incomplete_headers(fixture_server):
    f = fixture_server
    child = f["start"]()
    sockets = []
    try:
        time.sleep(.15)  # let the readiness probe's owned socket close
        first_connected = None
        for _ in range(MAX_CONNECTIONS):
            sockets.append(socket.create_connection(("127.0.0.1", f["port"]), timeout=1))
            if first_connected is None:
                first_connected = time.monotonic()
        sockets[0].sendall(b"GET / HTTP/1.1\r\nHost: fixture\r\nX-Dribble: ")
        time.sleep(.15)
        excess = socket.create_connection(("127.0.0.1", f["port"]), timeout=1)
        sockets.append(excess)
        excess.settimeout(1)
        try:
            excess.sendall(b"GET / HTTP/1.1\r\nHost: fixture\r\n\r\n")
            assert excess.recv(4096) == b""
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass
        # One more byte near the original deadline must not reset the timer.
        time.sleep(max(0, HEADER_TIMEOUT_SECONDS - 1 - (time.monotonic() - first_connected)))
        sockets[0].sendall(b"x")
        sockets[0].settimeout(2)
        try:
            assert sockets[0].recv(1) == b""
        except (ConnectionResetError, ConnectionAbortedError):
            pass
        assert time.monotonic() - first_connected < HEADER_TIMEOUT_SECONDS + 1
    finally:
        for sock in sockets: sock.close()
        f["stop"](child)
