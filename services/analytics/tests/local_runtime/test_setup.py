import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import setup_local_postgres as postgres
import setup_synthetic_trust as trust


def snapshot(folder):
    return {path.name: path.read_bytes() for path in folder.iterdir() if path.is_file()}


def test_postgres_initial_setup_and_repeat_preserve_files(tmp_path, capsys):
    postgres.setup(tmp_path)
    before = snapshot(tmp_path)
    postgres.setup(tmp_path)
    assert snapshot(tmp_path) == before
    metadata = json.loads(before["postgres-connection.json"])
    postgres.validate(metadata)
    assert set(before) == {"postgres-connection.json", "postgres.env"}
    assert capsys.readouterr().out == ""


def test_postgres_rejects_orphan_or_conflicting_env(tmp_path):
    env = tmp_path / "postgres.env"
    env.write_text("unknown state", encoding="utf-8")
    with pytest.raises(ValueError):
        postgres.setup(tmp_path)
    assert snapshot(tmp_path) == {"postgres.env": b"unknown state"}
    env.unlink()
    postgres.setup(tmp_path)
    metadata = (tmp_path / "postgres-connection.json").read_bytes()
    env.write_text("different credentials", encoding="utf-8")
    with pytest.raises(ValueError):
        postgres.setup(tmp_path)
    assert (tmp_path / "postgres-connection.json").read_bytes() == metadata
    assert env.read_text(encoding="utf-8") == "different credentials"


@pytest.mark.parametrize("field,value", [("project", "core"), ("port", 57382),
                                         ("host", "localhost"), ("database", "core"),
                                         ("user", "core"), ("password", "line\nbreak")])
def test_postgres_rejects_unsafe_existing_metadata_without_creating_env(tmp_path, field, value):
    data = {"project": postgres.PROJECT, "synthetic_only": True, "host": "127.0.0.1", "port": 57392,
            "user": "analytics_bootstrap", "database": "analytics_bootstrap", "password": "a" * 43}
    data[field] = value
    path = tmp_path / "postgres-connection.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        postgres.setup(tmp_path)
    assert snapshot(tmp_path) == before


def test_exclusive_create_cannot_overwrite_existing_file(tmp_path):
    target = tmp_path / "existing"
    target.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        postgres.create_exclusive(target, b"replacement")
    assert snapshot(tmp_path) == {"existing": b"keep"}


def test_missing_postgres_env_is_recreated_only_from_valid_metadata(tmp_path):
    postgres.setup(tmp_path)
    before = snapshot(tmp_path)
    (tmp_path / "postgres.env").unlink()
    postgres.setup(tmp_path)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("setup,pending", [
    (postgres.setup, ".postgres-connection.json.interrupted.pending"),
    (trust.setup, ".synthetic-grant-private.pem.interrupted.pending"),
])
def test_interrupted_atomic_write_blocks_regeneration(tmp_path, setup, pending):
    (tmp_path / pending).write_bytes(b"preserve")
    with pytest.raises(ValueError):
        setup(tmp_path)
    assert snapshot(tmp_path) == {pending: b"preserve"}


def test_symbolic_link_targets_are_refused(tmp_path, monkeypatch):
    target = tmp_path / "postgres-connection.json"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == target or original(path))
    with pytest.raises(ValueError):
        postgres.setup(tmp_path)
    assert snapshot(tmp_path) == {}


def test_trust_initial_setup_repeat_and_public_private_consistency(tmp_path, capsys):
    trust.setup(tmp_path)
    before = snapshot(tmp_path)
    trust.setup(tmp_path)
    assert snapshot(tmp_path) == before
    assert set(before) == {"synthetic-grant-private.pem", "synthetic-producer-private.pem", "trust-manifest.json"}
    assert b"PRIVATE KEY" not in before["trust-manifest.json"]
    assert capsys.readouterr().out == ""


def test_trust_refuses_changed_key_or_manifest_without_overwrite(tmp_path):
    trust.setup(tmp_path)
    original = snapshot(tmp_path)
    manifest = json.loads(original["trust-manifest.json"])
    manifest["grant"]["issuer"] = "unknown"
    (tmp_path / "trust-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        trust.setup(tmp_path)
    assert snapshot(tmp_path) == before


def test_trust_refuses_public_key_mismatch(tmp_path):
    trust.setup(tmp_path)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    path = tmp_path / "trust-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["grant"]["keys"]["g1"] = Ed25519PrivateKey.generate().public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("ascii")
    path.write_text(json.dumps(manifest), encoding="utf-8")
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        trust.setup(tmp_path)
    assert snapshot(tmp_path) == before


def test_trust_refuses_changed_private_key(tmp_path):
    trust.setup(tmp_path)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    path = tmp_path / "synthetic-producer-private.pem"
    path.write_bytes(Ed25519PrivateKey.generate().private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        trust.setup(tmp_path)
    assert snapshot(tmp_path) == before


def test_trust_partial_state_fails_without_generating_keys(tmp_path):
    path = tmp_path / "synthetic-grant-private.pem"
    path.write_bytes(b"preserve")
    with pytest.raises(ValueError):
        trust.setup(tmp_path)
    assert snapshot(tmp_path) == {path.name: b"preserve"}
