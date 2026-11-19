import sys
from types import SimpleNamespace
import pytest
import pymentor_analytics.app.cli as cli


@pytest.mark.parametrize("host,allowed", [("127.0.0.1", True), ("::1", True), ("127.2.3.4", True),
    ("localhost", False), ("0.0.0.0", False), ("192.168.1.2", False), ("::", False), ("::1%lo", False)])
def test_local_cli_exposes_only_numeric_loopback(monkeypatch, host, allowed):
    monkeypatch.setattr(sys, "argv", ["analytics", "collector", "--host", host])
    monkeypatch.setattr(cli, "load_settings", lambda role: SimpleNamespace(real_collection=True, shutdown_timeout_seconds=1))
    monkeypatch.setattr(cli, "create_app", lambda role, settings: object())
    calls = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    assert cli.main() == (0 if allowed else 2)
    assert len(calls) == int(allowed)
