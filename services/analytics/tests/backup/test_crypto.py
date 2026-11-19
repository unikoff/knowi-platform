import json
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from pymentor_analytics.adapters.backup import crypto


KEY = bytes(range(32))


def test_stream_roundtrip_and_authenticated_metadata(tmp_path):
    source = tmp_path / "fixture.dump"
    source.write_bytes((b"synthetic\0" * 240_000) + b"end")
    artifact = tmp_path / "snapshot.enc"
    result = crypto.encrypt_file(source, artifact, KEY, {"revision": "a006", "synthetic_only": True})
    header, stage = crypto.decrypt_verified(artifact, tmp_path, KEY)
    assert result["dump_bytes"] == source.stat().st_size
    assert header["metadata"]["revision"] == "a006"
    assert stage.read_bytes() == source.read_bytes()
    stage.unlink()


@pytest.mark.parametrize("damage", ["ciphertext", "metadata", "tag", "truncate", "wrong_key"])
def test_tampering_never_returns_plaintext_and_cleans_stage(tmp_path, damage):
    source = tmp_path / "fixture.dump"
    source.write_bytes(b"synthetic payload" * 100)
    artifact = tmp_path / "snapshot.enc"
    crypto.encrypt_file(source, artifact, KEY, {"revision": "a006"})
    data = bytearray(artifact.read_bytes())
    if damage == "ciphertext":
        data[-17] ^= 1
    elif damage == "tag":
        data[-1] ^= 1
    elif damage == "metadata":
        i = data.index(b"a006")
        data[i] = ord("b")
    elif damage == "truncate":
        data.pop()
    artifact.write_bytes(data)
    with pytest.raises((InvalidTag, ValueError)):
        crypto.decrypt_verified(artifact, tmp_path, bytes(reversed(KEY)) if damage == "wrong_key" else KEY)
    assert not list(tmp_path.glob(".verified-dump-*.tmp"))
    assert artifact.exists()


def test_existing_artifact_never_overwritten(tmp_path):
    source = tmp_path / "fixture.dump"
    source.write_bytes(b"payload")
    artifact = tmp_path / "snapshot.enc"
    artifact.write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        crypto.encrypt_file(source, artifact, KEY, {})
    assert artifact.read_bytes() == b"preserve"
    assert not list(tmp_path.glob("*.pending"))


def test_stream_size_and_key_bounds(tmp_path, monkeypatch):
    source = tmp_path / "fixture.dump"
    source.write_bytes(b"12345")
    monkeypatch.setattr(crypto, "MAX_BYTES", 4)
    with pytest.raises(ValueError):
        crypto.encrypt_file(source, tmp_path / "large.enc", KEY, {})
    with pytest.raises(ValueError):
        crypto.encrypt_file(source, tmp_path / "bad.enc", b"short", {})
