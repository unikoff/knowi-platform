"""Local profile validation; no authority process or real journal is started."""
import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from pymentor_analytics.adapters.privacy_authority.client import AuthorityConfig
from pymentor_analytics.adapters.privacy_authority import local_server


def public(key):
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode("ascii")


def client_config(**changes):
    key = Ed25519PrivateKey.generate()
    config = {"url": "http://127.0.0.1:18046", "authority_id": "local-authority:test",
              "issuer": "local-authority:test", "public_keys": {"a1": public(key)},
              "reader_secret": base64.urlsafe_b64encode(b"a" * 32).rstrip(b"=").decode("ascii"),
              "fixture_mode": False, "runtime_mode": "local"}
    return {**config, **changes}


def test_fixture_default_and_local_mode_require_explicit_boolean():
    assert AuthorityConfig.from_json(json.dumps(client_config(fixture_mode=True, runtime_mode="synthetic"))).runtime_mode == "synthetic"
    old = client_config(fixture_mode=True)
    old.pop("runtime_mode")
    assert AuthorityConfig.from_json(json.dumps(old)).runtime_mode == "synthetic"
    assert AuthorityConfig.from_json(json.dumps(client_config())).runtime_mode == "local"
    for change in ({"fixture_mode": False, "runtime_mode": "synthetic"}, {"fixture_mode": True, "runtime_mode": "local"},
                   {"fixture_mode": 1}, {"fixture_mode": "false"}, {"runtime_mode": "LOCAL"}):
        with pytest.raises(ValueError): AuthorityConfig.from_json(json.dumps(client_config(**change)))
    no_explicit_mode = client_config()
    no_explicit_mode.pop("runtime_mode")
    with pytest.raises(ValueError): AuthorityConfig.from_json(json.dumps(no_explicit_mode))


@pytest.mark.parametrize("url", ["http://localhost:18046", "http://0.0.0.0:18046", "http://192.168.1.2:18046",
    "http://privacy-authority:18046", "https://127.0.0.1:18046", "http://127.0.0.1:18046/path",
    "http://127.0.0.1:18046/?x=1", "http://u:p@127.0.0.1:18046"])
def test_local_client_rejects_remote_dns_and_url_components(url):
    with pytest.raises(ValueError): AuthorityConfig.from_json(json.dumps(client_config(url=url)))


@pytest.fixture
def owned(tmp_path, monkeypatch):
    instance_id = str(uuid4())
    root = tmp_path / ".local" / "local-live"
    instance = root / instance_id
    instance.mkdir(parents=True)
    monkeypatch.setattr(local_server, "SERVICE", tmp_path)
    monkeypatch.setattr(local_server, "LOCAL_ROOT", root)
    # ACL semantics have their own focused tests; tmp_path inherits CI ACL.
    monkeypatch.setattr(local_server, "_windows_private", lambda _: None)
    if os.name != "nt":
        root.chmod(0o700)
        instance.chmod(0o700)
    owner = {"version": 1, "mode": "local", "do_not_drop": True, "instance_id": instance_id,
             "core_instance_id": str(uuid4()), "dataset_run_id": str(uuid4()), "authority_id": "local-authority:" + instance_id}
    def write(name, data):
        path = instance / name
        path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
        if os.name != "nt": path.chmod(0o600)
        return path
    write("owner.json", owner)
    return instance_id, instance, owner, write


def test_init_is_exclusive_and_never_reinitializes(owned, monkeypatch):
    identity, instance, owner, _write = owned
    calls = []
    class Journal:
        @staticmethod
        def initialize(path, authority_id):
            calls.append((path, authority_id))
            with path.open("xb") as stream: stream.write(b"fixture")
    monkeypatch.setattr(local_server, "Journal", Journal)
    local_server.initialize(identity)
    assert calls == [(instance / "authority.sqlite", owner["authority_id"])]
    with pytest.raises(ValueError, match="already exists"): local_server.initialize(identity)
    assert len(calls) == 1


@pytest.mark.parametrize("name", ["../escape", "/tmp/key", "nested/key", "..", "", "name\\outside"])
def test_local_paths_reject_escape(owned, name):
    _, instance, _, _ = owned
    with pytest.raises(ValueError): local_server._file(instance, name, required=False)


def test_hardlinked_secret_and_journal_are_rejected(owned):
    identity, instance, _, write = owned
    original = write("private", b"fixture")
    linked = instance / "link"
    try: os.link(original, linked)
    except OSError: pytest.skip("hardlinks unavailable")
    with pytest.raises(ValueError): local_server._read(instance, "private", 100)
    with pytest.raises(ValueError): local_server._read(instance, "link", 100)
    linked.unlink()
    original.unlink()
    journal = write("authority.sqlite", b"fixture")
    try: os.link(journal, linked)
    except OSError: pytest.skip("hardlinks unavailable")
    with pytest.raises(ValueError): local_server.initialize(identity)


def test_symlinked_instance_is_rejected(owned, monkeypatch):
    identity, instance, _, _ = owned
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == instance or original(self))
    with pytest.raises(ValueError): local_server._instance_dir(identity)


def test_owner_mismatch_and_sidecar_block_initialization(owned, monkeypatch):
    identity, instance, owner, write = owned
    monkeypatch.setattr(local_server, "Journal", SimpleNamespace(initialize=lambda *_: pytest.fail("must not initialize")))
    write("owner.json", {**owner, "instance_id": str(uuid4())})
    with pytest.raises(ValueError): local_server.initialize(identity)
    write("owner.json", owner)
    write("authority.sqlite-journal", b"fixture")
    with pytest.raises(ValueError, match="unresolved"): local_server.initialize(identity)


@pytest.mark.parametrize("change", ["authority", "core_identity", "dataset_identity", "mode", "drop_flag"])
def test_invalid_owner_never_initializes(owned, monkeypatch, change):
    identity, _, owner, write = owned
    updates = {"authority": {"authority_id": "other"}, "core_identity": {"core_instance_id": identity},
               "dataset_identity": {"dataset_run_id": "00000000-0000-0000-0000-000000000000"},
               "mode": {"mode": "synthetic"}, "drop_flag": {"do_not_drop": False}}
    write("owner.json", {**owner, **updates[change]})
    monkeypatch.setattr(local_server, "Journal", SimpleNamespace(initialize=lambda *_: pytest.fail("must not initialize")))
    with pytest.raises(ValueError): local_server.initialize(identity)


def server_config(identity, producer, command):
    return {"mode": "local", "fixture_mode": False, "bind_host": "127.0.0.1", "port": 18046,
            "issuer": "local-authority:" + identity, "kid": "a1", "signing_key_file": "authority.private.pem",
            "producer_issuer": "local-producer:" + identity, "producer_public_keys": {"p1": public(producer)},
            "allowed_producers": ["core-local"], "producer_command_public_keys": {"core-local": public(command)},
            "reader_secret_file": "authority.reader"}


def test_serve_loads_only_private_owned_configuration(owned, monkeypatch):
    identity, instance, owner, write = owned
    authority, producer, command = (Ed25519PrivateKey.generate() for _ in range(3))
    write("authority.private.pem", authority.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    write("authority.reader", base64.urlsafe_b64encode(b"r" * 32).rstrip(b"="))
    write("authority.sqlite", b"not-a-real-journal")
    config = server_config(identity, producer, command)
    write("authority-server.json", config)
    received = []
    monkeypatch.setattr(local_server, "Journal", lambda path, authority_id: received.append((path, authority_id)) or object())
    monkeypatch.setattr(local_server, "create_authority_app", lambda *_a, **_k: "local-app")
    assert local_server.load_application(identity) == "local-app"
    assert received == [(instance / "authority.sqlite", owner["authority_id"])]
    for change in ({"bind_host": "0.0.0.0"}, {"bind_host": "localhost"}, {"port": 57392},
                   {"fixture_mode": True}, {"mode": "synthetic"}, {"signing_key_file": "../authority.private.pem"},
                   {"issuer": "other"}, {"producer_issuer": "other"}, {"allowed_producers": ["other"]}):
        write("authority-server.json", {**config, **change})
        with pytest.raises(ValueError): local_server.load_application(identity)
    assert len(received) == 1


@pytest.mark.parametrize("collision", ["producer", "command"])
def test_local_server_rejects_shared_signing_domains(owned, monkeypatch, collision):
    identity, _, _owner, write = owned
    authority, producer, command = (Ed25519PrivateKey.generate() for _ in range(3))
    if collision == "producer": producer = authority
    if collision == "command": command = producer
    write("authority.private.pem", authority.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    write("authority.reader", base64.urlsafe_b64encode(b"r" * 32).rstrip(b"="))
    write("authority.sqlite", b"not-a-real-journal")
    write("authority-server.json", server_config(identity, producer, command))
    monkeypatch.setattr(local_server, "Journal", lambda *_: object())
    with pytest.raises(ValueError): local_server.load_application(identity)


@pytest.mark.parametrize("other", ["S-1-1-0", "S-1-5-32-545"])
def test_windows_acl_rejects_broad_principals(monkeypatch, other):
    sid = "S-1-5-21-100-200-300-1000"
    body = {"user": sid, "owner": sid, "rules": [
        {"sid": sid, "allow": True, "rights": 0x1F01FF},
        {"sid": other, "allow": True, "rights": 1}]}
    monkeypatch.setattr(local_server.subprocess, "run", lambda *_a, **_k: SimpleNamespace(stdout=json.dumps(body).encode()))
    with pytest.raises(ValueError, match="ACL"): local_server._windows_private(Path("owned"))


def test_windows_acl_accepts_only_owner_and_system(monkeypatch):
    sid = "S-1-5-21-100-200-300-1000"
    body = {"user": sid, "owner": sid, "rules": [
        {"sid": sid, "allow": True, "rights": 0x1F01FF},
        {"sid": "S-1-5-18", "allow": True, "rights": 0x1F01FF}]}
    monkeypatch.setattr(local_server.subprocess, "run", lambda *_a, **_k: SimpleNamespace(stdout=json.dumps(body).encode()))
    local_server._windows_private(Path("owned"))
