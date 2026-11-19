"""Offline safety checks: no Docker, SQLite source, process or app is started."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import types
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("legacy_preservation", ROOT / "tools/test_legacy_preservation.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
runtime_spec = importlib.util.spec_from_file_location("legacy_preservation_runtime", ROOT / "tools/legacy_preservation/runtime.py")
runtime = importlib.util.module_from_spec(runtime_spec)
runtime_spec.loader.exec_module(runtime)
reuse_spec = importlib.util.spec_from_file_location("legacy_reuse", ROOT / "tools/legacy_preservation/reuse_image.py")
reuse = importlib.util.module_from_spec(reuse_spec)
reuse_spec.loader.exec_module(reuse)


@pytest.fixture
def tmp_path():
    # Inherit the workspace ACL: pytest's 0700 temp directories are denied on
    # this Windows installation. Every child holds only generated test bytes.
    parent = ROOT / "tests/.legacy-offline-scratch"
    parent.mkdir(exist_ok=True)
    folder = parent / uuid4().hex
    folder.mkdir()
    yield folder
    if folder.resolve().parent != parent.resolve() or folder.is_symlink():
        raise RuntimeError("unsafe test cleanup target")
    shutil.rmtree(folder)


def test_default_plan_never_reads_source_or_invokes_docker(monkeypatch, capsys):
    def forbidden(*_args, **_kwargs): pytest.fail("plan had side effects")
    monkeypatch.setattr(runner, "call", forbidden)
    monkeypatch.setattr(runner, "source_observations", forbidden)
    assert runner.main([]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "UNRUN"


def test_run_without_go_does_not_read_or_create(monkeypatch):
    monkeypatch.delenv("LEGACY_PRESERVATION_RUN_GO", raising=False)
    monkeypatch.setattr(runner, "source_observations", lambda: pytest.fail("source read before GO"))
    with pytest.raises(ValueError, match="GO"):
        runner.run_case(uuid4(), "docker", "0" * 64, "default", "sha256:" + "a" * 64)


def test_dependency_network_is_explicit_and_proxy_args_are_blank(tmp_path):
    offline = runner.build_command(["docker"], tmp_path, tmp_path / "iid", "none")
    online = runner.build_command(["docker"], tmp_path, tmp_path / "iid", "default")
    assert "--network=none" in offline and "--network=default" in online
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        assert name + "=" in online
    with pytest.raises(ValueError): runner.build_command(["docker"], tmp_path, tmp_path / "iid", "host")


def test_manifest_pin_requires_one_local_digest_bound_to_expected_image():
    image_id = "sha256:" + "a" * 64
    manifest = "sha256:" + "b" * 64
    info = {"Id": image_id, "RepoDigests": ["python@" + manifest]}
    assert runner.select_base_reference(info, image_id) == "python@" + manifest
    info["RepoDigests"].append("docker.io/library/python@" + manifest)
    assert runner.select_base_reference(info, image_id).endswith(manifest)
    for value in ({"Id": image_id, "RepoDigests": []},
                  {"Id": image_id, "RepoDigests": ["python@" + manifest, "python@sha256:" + "c" * 64]},
                  {"Id": "sha256:" + "d" * 64, "RepoDigests": ["python@" + manifest]}):
        with pytest.raises(ValueError): runner.select_base_reference(value, image_id)


@pytest.mark.parametrize("outcome", ["success", "failure", "timeout", "not_started"])
def test_docker_operation_private_diagnostics_and_unknown_outcome(tmp_path, outcome):
    class Process:
        def __init__(self):
            self.stdout = io.BytesIO(b"build output\npassword=secret\n")
            self.stderr = io.BytesIO(b"error detail\nAuthorization: Bearer token\n")
            self.calls = 0
        def wait(self, timeout=None):
            self.calls += 1
            if outcome == "timeout" and self.calls == 1:
                raise subprocess.TimeoutExpired("docker", timeout)
            return 1 if outcome == "failure" else 0
        def kill(self): pass
    def spawn(*_args, **_kwargs):
        if outcome == "not_started": raise OSError("private details")
        return Process()
    runner.mark(tmp_path, "build-pending", {"operation": "test"})
    if outcome == "success":
        assert runner.docker_operation(tmp_path, "build", ["docker"], timeout=1, env={}, spawn=spawn).startswith(b"build")
    else:
        with pytest.raises(RuntimeError): runner.docker_operation(tmp_path, "build", ["docker"], timeout=1, env={}, spawn=spawn)
    info = json.loads((tmp_path / "build-diagnostics.json").read_text())
    assert info["outcome"] == {"success": "cli_success", "failure": "cli_failed_reconcile", "timeout": "timeout_unknown", "not_started": "not_started"}[outcome]
    assert (tmp_path / "build-pending.json").exists()
    assert b"secret" not in (tmp_path / "build.stdout.log").read_bytes()
    assert b"Bearer" not in (tmp_path / "build.stderr.log").read_bytes()


def test_runtime_unrecognized_output_is_never_logged(tmp_path):
    class Process:
        stdout = io.BytesIO(b'{"body":"private source content"}')
        stderr = io.BytesIO(b"untrusted private body")
        def wait(self, timeout=None): return 0
    runner.docker_operation(tmp_path, "start", ["docker"], timeout=1, env={}, spawn=lambda *_a, **_k: Process())
    for name in ("start.stdout.log", "start.stderr.log"):
        value = (tmp_path / name).read_bytes()
        assert b"private" not in value and b"suppressed" in value


def test_runtime_failure_classification_is_allowlisted_and_redacted(monkeypatch, capsys):
    def fail():
        runtime._phase = "seed_copy"
        raise RuntimeError("secret@example.invalid token=PRIVATE")
    monkeypatch.setattr(runtime, "run", fail)
    assert runtime.main() == 1
    raw = capsys.readouterr().out
    assert json.loads(raw) == {"status": "FAIL", "phase": "seed_copy", "error": "runtime_error"}
    assert "secret" not in raw and "PRIVATE" not in raw
    assert json.loads(runner._safe_diagnostic(raw.encode(), "start")) == json.loads(raw)
    unsafe = b'{"status":"FAIL","phase":"secret","error":"runtime_error"}'
    assert b"suppressed" in runner._safe_diagnostic(unsafe, "start")


def test_server_diagnostic_maps_known_patterns_without_raw_text():
    assert runtime.classify_server_error(b"private email; Permission denied /data/private.db") == "permission_denied"
    assert runtime.classify_server_error(b"private token without known pattern") == "server_error"


def test_docker_environment_does_not_forward_credentials_or_proxies():
    env = runner.docker_environment({"PATH": "path", "USERPROFILE": "profile", "HTTPS_PROXY": "secret",
                                     "PIP_INDEX_URL": "secret", "AWS_SECRET_ACCESS_KEY": "secret", "DOCKER_HOST": "secret"})
    assert env == {"PATH": "path", "USERPROFILE": "profile"}


@pytest.mark.parametrize("endpoint,allowed", [("npipe:////./pipe/dockerDesktopLinuxEngine", True),
                                                ("tcp://remote.example:2375", False)])
def test_only_local_pipe_context_is_accepted(monkeypatch, tmp_path, endpoint, allowed):
    seen = []
    def fake_call(args, **_kwargs):
        seen.append(args)
        return json.dumps(endpoint).encode()
    monkeypatch.setattr(runner, "call", fake_call)
    if allowed:
        assert runner.local_docker("docker", "legacy-smoke", {}, config_dir=tmp_path) == endpoint
        assert seen[0][1:3] == ["--config", str(tmp_path)]
    else:
        with pytest.raises(ValueError, match="local Windows pipe"):
            runner.local_docker("docker", "legacy-smoke", {}, config_dir=tmp_path)


@pytest.mark.parametrize("extra", ["test.db", ".env", "secret.key", "unlisted.py"])
def test_context_rejects_secrets_and_unlisted_files(tmp_path, extra):
    (tmp_path / "requirements.txt").write_text("a2wsgi>=1.10\n")
    assert len(runner.validate_context(tmp_path)) == 64
    (tmp_path / extra).write_text("synthetic")
    with pytest.raises(ValueError, match="unsafe build context"):
        runner.validate_context(tmp_path)


@pytest.fixture
def sources(tmp_path, monkeypatch):
    body = b"synthetic bytes"
    digest = hashlib.sha256(body).hexdigest()
    original = tmp_path / "original.db"
    original.write_bytes(body)
    archives = (tmp_path / "archive1", tmp_path / "archive2")
    for archive in archives:
        (archive / "before/backend").mkdir(parents=True)
        (archive / "before/backend/test.db").write_bytes(body)
        (archive / "snapshot.json").write_text(json.dumps({"files": [{"path": "backend/test.db", "bytes": len(body), "sha256": digest}]}))
    monkeypatch.setattr(runner, "ORIGINAL", original)
    monkeypatch.setattr(runner, "ARCHIVES", archives)
    monkeypatch.setattr(runner, "SIZE", len(body))
    monkeypatch.setattr(runner, "EXPECTED", digest)
    return original, archives


def test_manifest_and_all_three_files_are_checked_before_and_after(sources):
    original, archives = sources
    before = runner.source_observations()
    assert len(before) == 3
    runner.verify_after(before)
    (archives[1] / "before/backend/test.db").write_bytes(b"modified")
    with pytest.raises(ValueError): runner.verify_after(before)
    assert original.read_bytes() == b"synthetic bytes"


def test_sidecar_and_bad_manifest_fail_closed(sources):
    original, archives = sources
    Path(str(original) + "-wal").write_bytes(b"")
    with pytest.raises(ValueError, match="sidecar"): runner.source_observations()
    Path(str(original) + "-wal").unlink()
    (archives[1] / "snapshot.json").write_text('{"files":[]}')
    with pytest.raises(ValueError, match="manifest"): runner.source_observations()


@pytest.mark.parametrize("extra", [False, True])
def test_private_acl_rejects_access_by_other_principal(monkeypatch, tmp_path, extra):
    sid = "S-1-5-21-1-2-3-1001"
    rules = [{"sid": identity, "allow": True, "inherited": False, "rights": 0x1F01FF, "inheritance": 3}
             for identity in (sid, "S-1-5-18")]
    if extra:
        rules.append({"sid": "S-1-1-0", "allow": True, "inherited": False, "rights": 1, "inheritance": 3})
    def fake_call(args, **_kwargs):
        if args[0].endswith("whoami.exe"): return ('"user","' + sid + '"').encode()
        if args[0].endswith("icacls.exe"): return b""
        return json.dumps({"protected": True, "rules": rules}).encode()
    monkeypatch.setenv("SystemRoot", "C:/Windows")
    monkeypatch.setattr(runner, "call", fake_call)
    if extra:
        with pytest.raises(ValueError, match="ACL"): runner.secure_private(tmp_path)
    else:
        runner.secure_private(tmp_path)


def test_private_acl_rejects_null_powershell_result(monkeypatch, tmp_path):
    def fake_call(args, **_kwargs):
        if args[0].endswith("whoami.exe"):
            return b'"user","S-1-5-21-1-2-3-1001"'
        if args[0].endswith("icacls.exe"):
            return b""
        return b'{"protected":null,"rules":[{"sid":null,"allow":false,"inherited":null,"rights":0,"inheritance":0}]}'
    monkeypatch.setenv("SystemRoot", "C:/Windows")
    monkeypatch.setattr(runner, "call", fake_call)
    with pytest.raises(ValueError, match="ACL"):
        runner.secure_private(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell ACL integration")
def test_real_private_acl_with_inherited_module_path(tmp_path):
    runner.secure_private(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell ACL integration")
def test_powershell_acl_nonterminating_error_is_failure(monkeypatch, tmp_path):
    real_call = runner.call
    def missing_acl(args, **kwargs):
        if args[0].lower().endswith("powershell.exe"):
            kwargs["env"] = dict(kwargs["env"], LEGACY_SMOKE_ACL_PATH=str(tmp_path / "missing"))
        return real_call(args, **kwargs)
    monkeypatch.setattr(runner, "call", missing_acl)
    with pytest.raises(RuntimeError, match="operation failed"):
        runner.secure_private(tmp_path)


def archive_with(name, *, symlink=False):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        item = tarfile.TarInfo(name)
        item.type = tarfile.SYMTYPE if symlink else tarfile.REGTYPE
        item.linkname = "other" if symlink else ""
        data = b"FROM python:3.12.16-slim-trixie\n" * 2
        item.size = 0 if symlink else len(data)
        tar.addfile(item, None if symlink else io.BytesIO(data))
    return buffer.getvalue()


@pytest.mark.parametrize("name,symlink", [("../../escape", False), ("backend/content/.env", False),
                                          ("backend/content/test.db", False), ("backend/content/link", True),
                                          ("unrelated/file", False)])
def test_frozen_archive_rejects_secrets_traversal_and_links(tmp_path, monkeypatch, name, symlink):
    monkeypatch.setattr(runner, "call", lambda *_a, **_k: archive_with(name, symlink=symlink))
    with pytest.raises(ValueError, match="unsafe frozen"): runner.extract_frozen(tmp_path)


def good_container(folder):
    return {"Id": "a" * 64, "Image": "sha256:" + "b" * 64,
            "Config": {"User": "10001:10001"},
            "HostConfig": {"NetworkMode": "none", "PortBindings": {}, "ReadonlyRootfs": True,
                           "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"],
                           "Memory": 512 * 1024 * 1024, "NanoCpus": 1_000_000_000, "PidsLimit": 128},
            "Mounts": [{"Type": "bind", "Source": str(folder), "Destination": "/data", "RW": True}]}


@pytest.mark.parametrize("case", ["network", "port", "user", "caps", "memory", "mount", "image"])
def test_container_requires_isolation_and_owned_mount(tmp_path, case):
    obj = good_container(tmp_path)
    runner.validate_container(obj, "a" * 64, "sha256:" + "b" * 64, tmp_path)
    obj = copy.deepcopy(obj)
    if case == "network": obj["HostConfig"]["NetworkMode"] = "bridge"
    if case == "port": obj["HostConfig"]["PortBindings"] = {"18791/tcp": [{"HostPort": "1"}]}
    if case == "user": obj["Config"]["User"] = "0"
    if case == "caps": obj["HostConfig"]["CapDrop"] = []
    if case == "memory": obj["HostConfig"]["Memory"] *= 2
    if case == "mount": obj["Mounts"][0]["Source"] = str(tmp_path.parent)
    if case == "image": obj["Image"] = "sha256:" + "c" * 64
    with pytest.raises(ValueError, match="isolation"): runner.validate_container(obj, "a" * 64, "sha256:" + "b" * 64, tmp_path)


@pytest.mark.parametrize('case', ['good', 'writable_runtime', 'other_runtime', 'extra_mount', 'network'])
def test_reuse_requires_readonly_owned_runtime_and_all_existing_isolation(tmp_path, case):
    data, script = tmp_path / 'data', tmp_path / 'smoke_runtime.py'
    obj = good_container(data)
    obj['Mounts'].append({'Type': 'bind', 'Source': str(script), 'Destination': '/smoke_runtime.py', 'RW': False})
    if case == 'writable_runtime': obj['Mounts'][1]['RW'] = True
    if case == 'other_runtime': obj['Mounts'][1]['Source'] = str(tmp_path / 'other.py')
    if case == 'extra_mount': obj['Mounts'].append({'Destination': '/elsewhere'})
    if case == 'network': obj['HostConfig']['NetworkMode'] = 'bridge'
    if case == 'good':
        reuse.validate_mounts(obj, 'a' * 64, 'sha256:' + 'b' * 64, data, script)
    else:
        with pytest.raises(ValueError, match='isolation'):
            reuse.validate_mounts(obj, 'a' * 64, 'sha256:' + 'b' * 64, data, script)


def test_reuse_without_go_has_no_side_effects(monkeypatch):
    monkeypatch.delenv('LEGACY_PRESERVATION_REUSE_GO', raising=False)
    monkeypatch.setattr(reuse.r, 'sha', lambda *_: pytest.fail('must stop before any external work'))
    with pytest.raises(ValueError, match='GO'):
        reuse.run(uuid4())


def test_runtime_refuses_noncopy_database_before_open(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:////original/test.db")
    monkeypatch.setattr(runtime.sqlite3, "connect", lambda *_: pytest.fail("unsafe open"))
    with pytest.raises(RuntimeError, match="database identity"): runtime._seed_on_copy()


@pytest.mark.parametrize('missing', [None, 'activity', 'progress'])
def test_historical_admin_records_seed_synthetic_student(monkeypatch, tmp_path, missing):
    database = tmp_path / "synthetic.db"
    connect = runtime.sqlite3.connect
    db = connect(database)
    db.executescript('''
        CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, email TEXT, password_hash TEXT, is_admin INTEGER, verified INTEGER);
        CREATE TABLE user_activity_days(user_id INTEGER, activity_date TEXT, visited_at TEXT);
        CREATE TABLE user_lesson_progress(user_id INTEGER, lesson_id TEXT, completed_at TEXT);
        CREATE TABLE user_task_progress(user_id INTEGER, task_id TEXT, task_revision TEXT, completed_at TEXT);
        INSERT INTO users VALUES(1, 'fixture-admin', 'fixture@example.invalid', 'hash', 1, 1);
        INSERT INTO user_activity_days VALUES(1, '2026-01-02', '2026-01-02');
        INSERT INTO user_lesson_progress VALUES(1, 'fixture-lesson', '2026-01-02');
        INSERT INTO user_task_progress VALUES(1, 'fixture-task', 'v1', '2026-01-02');
    ''')
    if missing == 'activity':
        db.execute('DELETE FROM user_activity_days')
    if missing == 'progress':
        db.execute('DELETE FROM user_lesson_progress')
    db.commit()
    db.close()
    passwords = types.ModuleType('pymentor_core.modules.identity.passwords')
    passwords.hash_password = lambda _password: 'synthetic-hash'
    monkeypatch.setitem(sys.modules, 'pymentor_core.modules.identity.passwords', passwords)
    monkeypatch.setenv('DATABASE_URL', 'sqlite:////data/test.db')
    monkeypatch.setattr(runtime.sqlite3, 'connect', lambda _path: connect(database))
    if missing:
        with pytest.raises(RuntimeError, match='historical calendar/progress fixture unavailable'):
            runtime._seed_on_copy()
        db = connect(database)
        try:
            assert db.execute('SELECT count(*) FROM users').fetchone() == (1,)
        finally:
            db.close()
        return
    actors, _, student, year, month, days, lessons, _ = runtime._seed_on_copy()
    assert len(actors) == 2 and (year, month) == (2026, 1)
    assert days == ['2026-01-02'] and lessons == {'fixture-lesson'}
    db = connect(database)
    try:
        assert db.execute('SELECT is_admin FROM users WHERE id=?', (student,)).fetchone() == (0,)
        assert db.execute('SELECT count(*) FROM user_task_progress WHERE user_id=?', (student,)).fetchone() == (1,)
    finally:
        db.close()


@pytest.mark.parametrize("mismatch", [False, True])
def test_runtime_compares_progress_without_leaking(monkeypatch, capsys, mismatch):
    class Process:
        def poll(self): return None
        def terminate(self): pass
        def wait(self, timeout): pass
    monkeypatch.setenv("PYMENTOR_PROFILE", "legacy_sqlite")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("PYMENTOR_LOCAL_NOTIFICATIONS", "0")
    monkeypatch.setattr(runtime, "dependency_fingerprint", lambda: "d" * 64)
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *_a, **_k: Process())
    monkeypatch.setattr(runtime, "_seed_on_copy", lambda: ([('student', 1), ('admin', 2)], 'password', 1, 2026, 1, ['2026-01-01'], {'old-lesson'}, 2))
    monkeypatch.setattr(runtime, "_login", lambda user, _: user)
    def request(_method, path, body=None, cookie=None):
        if path == "/health/ready": return 200, None, {}
        if path == "/api/admin/overview":
            return (401 if not cookie else 403 if cookie == "student" else 200), None, {"total_students": 2}
        if path.startswith("/api/user/activity?"): return 200, None, {"days": ["2026-01-01"]}
        if path.startswith("/api/admin/users/"):
            return 200, None, {"profile": {"activity_days": ["2026-01-01"], "learning_progress": [{"modules": [{"lessons": [{"id": "old-lesson", "completed": True}]}]}]}}
        if path == "/api/learning/catalog":
            return 200, None, {"courses": {"track": [{"id": "old-lesson", "completed": not mismatch}]}}
        return 200, None, {}
    monkeypatch.setattr(runtime, "request", request)
    if mismatch:
        with pytest.raises(RuntimeError, match="progress mismatch"):
            runtime.run()
        assert capsys.readouterr().out == ""
        return
    runtime.run()
    out = capsys.readouterr().out
    assert json.loads(out)["status"] == "PASS"
    assert "old-lesson" not in out and "student" not in out and "password" not in out
