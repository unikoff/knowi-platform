import os
import asyncio
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from pymentor_analytics.app.config import Settings, load_settings
from pymentor_analytics.app.factory import create_app


URL = "postgresql://analytics_user@localhost:57392/analytics_test"
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("role", ["collector", "processor", "query", "privacy"])
def test_isolated_lifespan_and_probes(role):
    app = create_app(role, Settings(database_url=URL))
    with TestClient(app) as client:
        assert client.get("/live").json() == {"status": "live"}
        assert client.get("/ready").status_code == 503
        assert client.post("/api/analytics/events/batch").status_code == (503 if role == "collector" else 404)
        assert client.get("/api/analytics/reports/test").status_code == 404
        expected_paths = {
            "collector": {"/api/analytics/events/batch"},
            "query": {
                "/api/analytics/reports/activity",
                "/api/analytics/reports/activity/details",
                "/api/analytics/reports/activity/chapters",
                "/api/analytics/reports/activity/outline",
                "/api/analytics/reports/activity/outline/lifetime",
                "/api/analytics/reports/measured-calendar",
            },
        }.get(role, set())
        assert set(app.openapi()["paths"]) == expected_paths
    assert not hasattr(app.state, "settings")


def test_injected_readiness_and_failure():
    class Probe:
        async def is_ready(self):
            return True

    class FailedProbe:
        async def is_ready(self):
            raise RuntimeError("sensitive detail")

    for probe, expected in [(Probe(), 200), (FailedProbe(), 503)]:
        with TestClient(create_app("query", Settings(database_url=URL), probe)) as client:
            response = client.get("/ready")
            assert response.status_code == expected
            assert "sensitive" not in response.text
            assert client.get("/live").status_code == 200


def test_readiness_timeout_cancels_probe():
    class SlowProbe:
        cancelled = False

        async def is_ready(self):
            try:
                await asyncio.sleep(10)
            finally:
                self.cancelled = True
            return True

    probe = SlowProbe()
    with TestClient(create_app("query", Settings(database_url=URL, db_timeout_seconds=0.02), probe)) as client:
        assert client.get("/ready").status_code == 503
        assert probe.cancelled
        assert client.get("/live").status_code == 200


@pytest.mark.parametrize("url", ["", "sqlite:///analytics_test", "postgresql://u@localhost:57382/analytics_test",
    "postgresql://u@localhost:57392/core", "postgresql://u@localhost:5432/analytics_test",
    "postgresql://u@localhost:57392/analytics_test?host=other", "postgresql://u@localhost:abc/analytics_test"])
def test_reject_unsafe_urls(url):
    with pytest.raises(ValidationError):
        Settings(database_url=url)


def test_configuration_is_role_specific_and_bounded():
    with pytest.raises(ValidationError):
        load_settings("query", {"ANALYTICS_COLLECTOR_DATABASE_URL": URL})
    for field, value in [("pool_size", 0), ("pool_overflow", 11), ("concurrency", 101),
                         ("db_timeout_seconds", float("nan")), ("shutdown_timeout_seconds", 61),
                         ("real_collection", True), ("issuer_mode", "core")]:
        with pytest.raises(ValidationError):
            Settings(database_url=URL, **{field: value})
    assert "password" not in repr(Settings(database_url=URL.replace("analytics_user@", "analytics_user:password@")))


def clean_env(role):
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if key in os.environ}
    env[f"ANALYTICS_{role.upper()}_DATABASE_URL"] = URL
    return env


def get_status(url):
    try:
        with urlopen(url, timeout=0.5) as response:
            return response.status
    except HTTPError as exc:
        return exc.code


def _peek_pipe(pipe, limit=8192):
    """Read only already-available child output, without waiting for EOF."""
    if os.name == "nt":
        import ctypes
        import msvcrt
        available = ctypes.c_ulong()
        handle = msvcrt.get_osfhandle(pipe.fileno())
        ok = ctypes.windll.kernel32.PeekNamedPipe(ctypes.c_void_p(handle), None, 0, None, ctypes.byref(available), None)
        if not ok:
            return "<pipe peek unavailable>"
        count = min(available.value, limit)
    else:
        import select
        count = limit if select.select([pipe], [], [], 0)[0] else 0
    return os.read(pipe.fileno(), count).decode("utf-8", errors="replace") if count else "<no output available>"


@pytest.mark.parametrize("role", ["collector", "processor", "query", "privacy"])
def test_real_process_probe_and_shutdown(role):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    process = subprocess.Popen([sys.executable, "-m", "pymentor_analytics.app.cli", role,
                                "--port", str(port)], cwd=ROOT, env=clean_env(role),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail(f"process exited: {process.communicate()}")
            try:
                if get_status(f"http://127.0.0.1:{port}/live") == 200:
                    break
            except (URLError, OSError):
                time.sleep(0.05)
        else:
            pytest.fail("process did not become live")
        assert get_status(f"http://127.0.0.1:{port}/ready") == 503
        ready_elapsed = time.monotonic() - started
        process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        signaled = time.monotonic()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # Capture the child's current output before the existing cleanup
            # kills this owned process; no timeout or assertion is relaxed.
            stdout = _peek_pipe(process.stdout)
            stderr = _peek_pipe(process.stderr)
            pytest.fail(f"{role} shutdown timeout; started_utc={started_at}, pid={process.pid}, "
                        f"ready_after={ready_elapsed:.3f}s, signal_after={signaled-started:.3f}s, "
                        f"signal_wait={time.monotonic()-signaled:.3f}s, "
                        f"stdout_before_cleanup={stdout!r}, stderr_before_cleanup={stderr!r}")
        _, stderr = process.communicate()
        # Uvicorn reraises the captured signal after lifespan shutdown.
        assert b"Application shutdown complete" in stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.communicate()


def test_migration_and_invalid_configuration_fail_closed():
    command = [sys.executable, "-m", "pymentor_analytics.app.cli"]
    migrate = subprocess.run(command + ["migrate"], cwd=ROOT, env=clean_env("migrate"), capture_output=True, text=True)
    assert migrate.returncode != 0 and "migration failed" in migrate.stderr
    env = clean_env("collector")
    env["ANALYTICS_COLLECTOR_DATABASE_URL"] = "postgresql://u:secret@localhost:57382/core"
    invalid = subprocess.run(command + ["collector"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert invalid.returncode != 0 and "secret" not in invalid.stderr
