import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from pymentor_core.modules.analytics_delivery import local_source_key as source


@pytest.fixture
def isolated(monkeypatch):
    base = Path("output").resolve()
    base.mkdir(exist_ok=True)
    folder = base / ("source-key-" + uuid4().hex)
    assert folder.parent == base
    folder.mkdir()
    service = folder / "analytics"
    local = service / ".local"
    root = local / "local-live"
    dataset = str(uuid4())
    instance = root / dataset
    for item in (service, local, root, instance):
        item.mkdir()
        if os.name != "nt": item.chmod(0o700)
    path = instance / "source.key"
    path.write_bytes(base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"="))
    if os.name != "nt": path.chmod(0o600)
    monkeypatch.setattr(source, "_SERVICE", service)
    monkeypatch.setattr(source, "_windows_private", lambda path: None)
    yield path, dataset, root, instance
    path.unlink(missing_ok=True)
    for item in (instance, root, local, service, folder): item.rmdir()


def test_source_path_size_links_and_permissions_are_checked(isolated, monkeypatch):
    path, dataset, root, instance = isolated
    assert len(source.read_source_key(str(path), dataset)) == 43
    with pytest.raises(ValueError): source.read_source_key(str(instance / "other.key"), dataset)
    with pytest.raises(ValueError): source.read_source_key(str(path), str(uuid4()))
    path.write_bytes(b"a" * 257)
    with pytest.raises(ValueError): source.read_source_key(str(path), dataset)
    path.write_bytes(b"a" * 42 + b".")
    with pytest.raises(ValueError): source.read_source_key(str(path), dataset)
    path.write_bytes(b"a" * 43)
    with pytest.raises(ValueError): source.read_source_key(str(path), dataset)
    path.write_bytes(base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"="))
    original = Path.is_symlink
    with monkeypatch.context() as m:
        m.setattr(Path, "is_symlink", lambda self: self == path or original(self))
        with pytest.raises(ValueError): source.read_source_key(str(path), dataset)
    original_junction = os.path.isjunction
    with monkeypatch.context() as m:
        m.setattr(os.path, "isjunction", lambda p: p == root or original_junction(p))
        with pytest.raises(ValueError): source.read_source_key(str(path), dataset)
    original_lstat = Path.lstat
    with monkeypatch.context() as m:
        def hardlink(self, *args, **kwargs):
            info = original_lstat(self, *args, **kwargs)
            if self == path:
                return SimpleNamespace(st_mode=info.st_mode, st_nlink=2, st_size=info.st_size)
            return info
        m.setattr(Path, "lstat", hardlink)
        with pytest.raises(ValueError): source.read_source_key(str(path), dataset)
    if os.name == "nt":
        with monkeypatch.context() as m:
            m.setattr(source, "_windows_private", lambda path: (_ for _ in ()).throw(ValueError("ACL")))
            with pytest.raises(ValueError): source.read_source_key(str(path), dataset)
    else:
        root.chmod(0o755)
        with pytest.raises(ValueError): source.read_source_key(str(path), dataset)
        root.chmod(0o700)


def test_windows_acl_rejects_broad_owner_and_missing_user_control(monkeypatch):
    user = "S-1-5-21-1-2-3-1001"
    acl = {"user": user, "owner": user, "rules": [{"sid": user, "allow": True, "rights": 0x1F01FF}]}
    monkeypatch.setenv("SystemRoot", str(Path("output").resolve()))
    def result(*args, **kwargs):
        return SimpleNamespace(stdout=json.dumps(acl).encode())
    monkeypatch.setattr(source.subprocess, "run", result)
    source._windows_private(Path("somewhere"))
    acl["rules"].append({"sid": "S-1-1-0", "allow": True, "rights": 1})
    with pytest.raises(ValueError): source._windows_private(Path("somewhere"))
    acl["rules"].pop()
    acl["owner"] = "S-1-1-0"
    with pytest.raises(ValueError): source._windows_private(Path("somewhere"))
    acl["owner"] = user
    acl["rules"][0]["rights"] = 1
    with pytest.raises(ValueError): source._windows_private(Path("somewhere"))
