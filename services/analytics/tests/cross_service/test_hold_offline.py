"""Offline exercise of bounded private hold and token release; no services."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import threading
import time

import pytest

from . import resources


@pytest.fixture
def owned():
    folder, token = resources.private_folder()
    try:
        yield folder, token
    finally:
        parent = (resources.ROOT / ".local/cross-service").resolve(strict=True)
        assert folder.resolve(strict=True).parent == parent
        assert json.loads((folder / "owner.json").read_text(encoding="utf-8"))["token"] == token
        shutil.rmtree(folder)


@pytest.fixture
def runner():
    path = Path(__file__).resolve().parents[2] / "tools/test_cross_service.py"
    spec = importlib.util.spec_from_file_location("cross_service_test_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_private_release_ends_hold(owned, runner):
    folder, token = owned
    failures = []
    def hold():
        try:
            runner.browser_hold(folder, token, 2, [])
        except Exception as exc:
            failures.append(exc)
    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if json.loads((folder / "owner.json").read_text())["state"] == "browser_hold":
            break
        time.sleep(.01)
    else:
        thread.join(timeout=3)
        pytest.fail("hold did not start")
    assert json.loads((folder / "owner.json").read_text())["browser_holder_pid"] > 0
    runner.release_hold(folder.name)
    thread.join(timeout=3)
    assert not thread.is_alive() and not failures


def test_chain_hold_preserves_browser_student(runner):
    fixture = runner.make_fixture({}, {}, preserve_browser_student=True)
    assert fixture["users"]["admin"]["id"] == 1
    assert fixture["users"]["leader"]["id"] == 2
    assert fixture["browser_users"]["student"]["id"] == 7
    assert fixture["browser_users"]["admin"]["id"] == 5
    review_password = "Analytics-Local-Review-20261117!"
    assert fixture["users"]["admin"]["password"] == fixture["users"]["leader"]["password"] == fixture["browser_users"]["student"]["password"] == review_password
    assert fixture["browser_users"]["leader"]["password"] == review_password
    assert fixture["users"]["student"]["password"] != review_password and fixture["browser_users"]["admin"]["password"] != review_password
    ordinary = runner.make_fixture({}, {})
    assert all(user["password"] != review_password for group in (ordinary["users"], ordinary["browser_users"]) for user in group.values())
    assert set(fixture["roster"]) == {fixture["users"]["student"]["subject_ref"], fixture["browser_users"]["student"]["subject_ref"], fixture["browser_users"]["admin"]["subject_ref"]}


@pytest.mark.parametrize("seconds", [60, 900, 3600])
def test_hold_duration_accepts_explicit_bound_without_opening_resources(runner, monkeypatch, capsys, seconds):
    monkeypatch.delenv("ANALYTICS_CROSS_SERVICE_GO", raising=False)
    monkeypatch.setattr(sys, "argv", ["test_cross_service.py", "--run", "--hold-seconds", str(seconds)])
    assert runner.main() == 2
    assert "no resources opened" in capsys.readouterr().err


@pytest.mark.parametrize("seconds", [1, 59, 3601])
def test_hold_duration_rejects_out_of_bound_before_admission(runner, monkeypatch, seconds):
    monkeypatch.setattr(sys, "argv", ["test_cross_service.py", "--run", "--hold-seconds", str(seconds)])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2


@pytest.mark.parametrize("args", [
    ["--run", "--publisher"], ["--run", "--chain", "--publisher"],
    ["--run", "--chain", "--a010", "--publisher", "--hold-seconds", "60"],
])
def test_publisher_requires_fresh_full_chain_without_hold(runner, monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["test_cross_service.py", *args])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2


def test_publisher_still_requires_runtime_go(runner, monkeypatch, capsys):
    monkeypatch.delenv("ANALYTICS_CROSS_SERVICE_GO", raising=False)
    monkeypatch.setattr(sys, "argv", ["test_cross_service.py", "--run", "--chain", "--a010", "--publisher"])
    assert runner.main() == 2
    assert "no resources opened" in capsys.readouterr().err


@pytest.mark.parametrize("args", [
    ["--run", "--publisher-image", "sha256:" + "f" * 64],
    ["--run", "--chain", "--a010", "--publisher", "--publisher-image", "example:latest"],
])
def test_publisher_image_rejects_missing_publisher_or_mutable_tag(runner, monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["test_cross_service.py", *args])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2


@pytest.mark.parametrize("args", [
    ["--run", "--browser-pages"],
    ["--run", "--chain", "--a010", "--hold-seconds", "900", "--browser-pages"],
    ["--run", "--chain", "--a010", "--hold-seconds", "3600", "--publisher", "--browser-pages"],
])
def test_browser_pages_require_full_hour_hold_without_publisher(runner, monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["test_cross_service.py", *args])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2


def test_browser_pages_still_require_go(runner, monkeypatch, capsys):
    monkeypatch.delenv("ANALYTICS_CROSS_SERVICE_GO", raising=False)
    monkeypatch.setattr(sys, "argv", ["test_cross_service.py", "--run", "--chain", "--a010", "--hold-seconds", "3600", "--browser-pages"])
    assert runner.main() == 2
    assert "no resources opened" in capsys.readouterr().err


@pytest.mark.parametrize("args", [["--run", "--a011"], ["--run", "--chain", "--a011"], ["--run", "--a010", "--a011"]])
def test_a011_requires_explicit_full_chain(runner, monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["test_cross_service.py", *args])
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2


def test_a011_still_requires_runtime_go(runner, monkeypatch, capsys):
    monkeypatch.delenv("ANALYTICS_CROSS_SERVICE_GO", raising=False)
    monkeypatch.setattr(sys, "argv", ["test_cross_service.py", "--run", "--chain", "--a010", "--a011"])
    assert runner.main() == 2
    assert "no resources opened" in capsys.readouterr().err


def test_redis_prefix_recorded_before_owned_process_setup(owned, runner, monkeypatch):
    folder, token = owned
    core_dir = folder / "synthetic-core"
    core_python = core_dir / ".venv/Scripts/python.exe"
    core_python.parent.mkdir(parents=True)
    core_python.touch()
    monkeypatch.setattr(runner, "CORE", core_dir)
    monkeypatch.setenv("ANALYTICS_CROSS_SERVICE_GO", "1")
    monkeypatch.setattr(sys, "argv", ["test_cross_service.py", "--run"])
    monkeypatch.setattr(runner.r, "available_memory_mib", lambda: 4096)
    monkeypatch.setattr(runner.r, "local_ports_free", lambda ports: None)
    monkeypatch.setattr(runner.r, "private_folder", lambda: (folder, token))

    def fail_before_job_starts():
        state = json.loads((folder / "owner.json").read_text(encoding="utf-8"))
        assert state["redis_prefix"].startswith("cross-service-")
        raise RuntimeError("synthetic interruption before process setup")

    monkeypatch.setattr(runner.r, "OwnedJob", fail_before_job_starts)
    assert runner.main() == 1
    state = json.loads((folder / "owner.json").read_text(encoding="utf-8"))
    assert len(state["redis_prefix"]) == len("cross-service-") + 32
    assert state["state"] == "reconciliation_required"


def test_invalid_token_does_not_release(owned, runner):
    folder, token = owned
    (folder / "release.request").write_bytes(b"x" * 64)
    with pytest.raises(ValueError, match="invalid release"):
        runner.browser_hold(folder, token, 1, [])


def test_hold_expires(owned, runner):
    folder, token = owned
    start = time.monotonic()
    runner.browser_hold(folder, token, .01, [])
    assert time.monotonic() - start < 1


def test_private_cleanup_retries_transient_lock_and_keeps_marker(monkeypatch):
    folder, token = resources.private_folder()
    item = folder / "synthetic-secret"
    item.write_text("fixture", encoding="utf-8")
    original = Path.unlink
    attempts = []
    def flaky(path, *args, **kwargs):
        if path == item and len(attempts) < 2:
            assert (folder / "owner.json").is_file()
            attempts.append(1)
            raise PermissionError("synthetic lock")
        return original(path, *args, **kwargs)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", flaky)
            patch.setattr(resources.time, "sleep", lambda delay: None)
            resources.cleanup_private(folder, token)
        assert len(attempts) == 2 and not folder.exists()
    finally:
        if folder.exists():
            assert folder.resolve().parent == (resources.ROOT / ".local/cross-service").resolve()
            shutil.rmtree(folder)


def test_private_cleanup_preserves_marker_on_persistent_lock(monkeypatch):
    folder, token = resources.private_folder()
    item = folder / "synthetic-secret"
    item.write_text("fixture", encoding="utf-8")
    original = Path.unlink
    def locked(path, *args, **kwargs):
        if path == item:
            raise PermissionError("synthetic lock")
        return original(path, *args, **kwargs)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", locked)
            patch.setattr(resources.time, "sleep", lambda delay: None)
            with pytest.raises(PermissionError):
                resources.cleanup_private(folder, token)
        assert (folder / "owner.json").is_file()
    finally:
        if folder.exists():
            assert folder.resolve().parent == (resources.ROOT / ".local/cross-service").resolve()
            shutil.rmtree(folder)
