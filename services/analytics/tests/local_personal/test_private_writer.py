from pathlib import Path

import pytest

from pymentor_analytics.adapters.privacy_authority import local_server


def test_private_write_roundtrip_is_exclusive_and_synced(tmp_path, monkeypatch):
    checks = []
    syncs = []
    monkeypatch.setattr(local_server, "_private", lambda path, **kw: checks.append((Path(path), kw)))
    original_sync = local_server.os.fsync

    def sync(fd):
        syncs.append(fd)
        original_sync(fd)

    monkeypatch.setattr(local_server.os, "fsync", sync)
    path = tmp_path.resolve() / "owner.json"
    payload = b'{"mode":"local","do_not_drop":true}'
    local_server._write_new(path, payload)
    assert path.read_bytes() == payload
    assert len(syncs) == 1
    assert checks == [(path.parent, {"directory": True}), (path, {})]
    with pytest.raises(FileExistsError):
        local_server._write_new(path, b"replace")
    assert path.read_bytes() == payload


@pytest.mark.parametrize("path,data", [(Path("relative.json"), b"x"), (Path("/absolute.json"), "text")])
def test_invalid_private_write_does_not_touch_files(path, data, monkeypatch):
    monkeypatch.setattr(local_server, "_private", lambda *a, **kw: pytest.fail("invalid write reached filesystem"))
    with pytest.raises(ValueError):
        local_server._write_new(path, data)
