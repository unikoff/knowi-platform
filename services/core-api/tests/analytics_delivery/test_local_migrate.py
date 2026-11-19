"""Offline checks for the explicit protected-local migration boundary."""
import importlib.util
import json
import socket
from types import SimpleNamespace

import pytest

from pymentor_core.adapters.sql.analytics_delivery_ops import addon_migration


def load():
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "tools/analytics_transfer/local_migrate.py"
    spec = importlib.util.spec_from_file_location("local_migrate_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DATASET = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DATABASE = "c527_role_ci_" + "a" * 32


def setup_target(monkeypatch):
    module = load()
    meta = {"database": DATABASE, "do_not_drop": True, "durable_postgres": True, "schema_revision": "c528_course_leaders"}
    owner = {"version": 1, "mode": "local", "do_not_drop": True, "instance_id": DATASET, "core_instance_id": DATASET}
    env = {"PYMENTOR_ANALYTICS_LOCAL_DATASET_ID": DATASET, "PYMENTOR_ANALYTICS_LOCAL_INSTANCE_ID": DATASET}
    monkeypatch.setattr(module, "_read_json", lambda path: {"browser-dataset.json": meta, "owner.json": owner, "core-env.json": env}[path.name])
    monkeypatch.setattr(module.local_collection, "configure", lambda env, url: SimpleNamespace(catalog_binding=SimpleNamespace(dataset_id=DATASET)))
    monkeypatch.setenv("DATABASE_URL", f"postgresql+psycopg://pymentor_test:secret@127.0.0.1:57382/{DATABASE}")
    return module, meta


def test_exact_target_and_protection_flags(monkeypatch):
    module, meta = setup_target(monkeypatch)
    assert module.target(DATASET)[0].database == DATABASE
    for field in ("do_not_drop", "durable_postgres", "schema_revision"):
        old = meta[field]
        meta[field] = False
        with pytest.raises(ValueError): module.target(DATASET)
        meta[field] = old
    for url in (f"postgresql+psycopg://pymentor_test@localhost:57382/{DATABASE}",
                f"postgresql+psycopg://wrong@127.0.0.1:57382/{DATABASE}",
                f"postgresql+psycopg://pymentor_test@127.0.0.1:5432/{DATABASE}",
                "postgresql+psycopg://pymentor_test@127.0.0.1:57382/other"):
        monkeypatch.setenv("DATABASE_URL", url)
        with pytest.raises(ValueError): module.target(DATASET)
    with pytest.raises(ValueError): module.target("not-a-uuid")


def test_writer_port_is_reserved_and_active_writer_blocks(monkeypatch):
    module = load()
    class FakeSocket:
        def setsockopt(self, *args): pass
        def bind(self, address):
            assert address == ("127.0.0.1", 18041)
            raise OSError("in use")
        def close(self): self.closed = True
    sock = FakeSocket()
    monkeypatch.setattr(module.socket, "socket", lambda *args: sock)
    with pytest.raises(OSError): module._quiescence_socket()
    assert sock.closed


def test_existing_migration_still_rejects_protected_local_name():
    class Dialect: name = "postgresql"
    class Connection: dialect = Dialect()
    with pytest.raises(ValueError, match="installation identity"):
        addon_migration.migrate(Connection(), expected_db=DATABASE, expected_owner="pymentor_test",
                                sentinel=DATASET, target_version=2)
