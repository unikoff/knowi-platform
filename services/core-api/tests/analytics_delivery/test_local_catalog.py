import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from pymentor_core.adapters.analytics_catalog import publication
from pymentor_core.app import analytics_catalog as local_catalog


@pytest.fixture
def local_tmp():
    Path("output").mkdir(exist_ok=True)
    folder = (Path("output") / ("local-catalog-" + uuid4().hex)).resolve()
    assert folder.parent == Path("output").resolve()
    folder.mkdir()
    yield folder
    for path in sorted(folder.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        assert path.resolve().is_relative_to(folder)
        if path.is_dir(): path.rmdir()
        else: path.unlink()
    folder.rmdir()


def test_lost_ack_reconciles_exact_pending_before_any_resend(monkeypatch, local_tmp):
    fake_file = local_tmp / "core" / "pymentor_core" / "adapters" / "analytics_catalog" / "publication.py"
    fake_file.parent.mkdir(parents=True)
    monkeypatch.setattr(publication, "__file__", str(fake_file))
    binding = local_catalog.Binding(str(uuid4()), str(uuid4()), "core-local:test", "http://127.0.0.1:18042",
                                    "a" * 64, "core-local", "issuer", "kid")
    snapshot = SimpleNamespace(context=SimpleNamespace(course_id="backend"))
    message = {"catalog_version": "sha256:" + publication.digest({"course_id": "backend"}),
               "catalog": {"course_id": "backend"}}
    monkeypatch.setattr(publication, "project", lambda snap, prior: (message, "b" * 64))
    monkeypatch.setattr(publication, "current_digest", lambda snap: "b" * 64)
    results = iter([(200, b'{"status":"absent"}'), None, None, (200, b'{"status":"accepted"}')])
    calls = []
    async def request(method, url, **kwargs):
        calls.append((method, url, kwargs.get("body")))
        return next(results)
    monkeypatch.setattr(local_catalog, "request", request)
    assert asyncio.run(local_catalog.publish(binding, snapshot, lambda: "private-token")) == "unknown"
    root = local_catalog.verified_root(binding)
    pending = publication.read_receipt("backend", pending=True, root=root)
    assert pending["fingerprint"] == publication.digest(message)
    assert asyncio.run(local_catalog.publish(binding, snapshot, lambda: "private-token")) == "accepted"
    assert [call[0] for call in calls] == ["GET", "POST", "GET", "GET"]
    assert publication.read_receipt("backend", root=root) == pending
    assert not publication.receipt_path("backend", root=root).with_suffix(".pending.json").exists()
    different = local_catalog.Binding(binding.dataset_id, binding.instance_id, binding.namespace,
        "http://127.0.0.1:18043", binding.producer_sha256, binding.producer_id, binding.producer_issuer, binding.producer_kid)
    with pytest.raises(ValueError):
        local_catalog.verified_root(different)
