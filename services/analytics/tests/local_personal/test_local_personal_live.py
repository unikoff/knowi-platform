"""Offline local-live identity, isolation and recovery contracts."""
import json
from pathlib import Path
import socket
import subprocess
import sys
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SERVICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVICE))
sys.path.insert(0, str(SERVICE / "tools"))
import bootstrap_postgres
import local_personal_live as live
import local_personal_runtime as runtime

INSTANCE = "11111111-1111-4111-8111-111111111111"
CORE = "22222222-2222-4222-8222-222222222222"
RUN = "33333333-3333-4333-8333-333333333333"


def fixture_config(projection_revision="a011"):
    keys = {}
    for index, name in enumerate(("g1", "p1", "authority", "command"), 1):
        key = Ed25519PrivateKey.from_private_bytes(bytes([index]) * 32)
        keys[name] = (key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
                      key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo),
                      key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    owner = {"version": 1, "mode": "local", "do_not_drop": True, "instance_id": INSTANCE,
             "core_instance_id": CORE, "dataset_run_id": RUN, "authority_id": "local-authority:" + INSTANCE}
    passwords = {role: "test-password-" + role for role in live.ROLES}
    config = live.build_configuration(Path("private"), owner, keys, "A" * 43, "A" * 42 + "E", passwords, "core_test",
                                      projection_revision=projection_revision)
    return owner, config


def test_default_plans_do_not_open_files_or_processes(monkeypatch, capsys):
    monkeypatch.setattr("builtins.open", lambda *a, **kw: pytest.fail("unexpected file IO"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("unexpected process"))
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("unexpected network"))
    assert live.main([]) == 0 and json.loads(capsys.readouterr().out)["status"] == "UNRUN"
    assert runtime.main([]) == 0 and json.loads(capsys.readouterr().out)["status"] == "UNRUN"


def test_windows_acl_is_new_protected_two_principal_dacl(monkeypatch):
    calls = []
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs)))
    path = Path(r"C:\private-parent\new-owned-empty")
    live._set_new_windows_acl(path)
    command = calls[0][0][-1]
    assert "[System.Security.AccessControl.DirectorySecurity]::new()" in command
    assert "$acl.SetOwner($user)" in command
    assert "$acl.SetAccessRuleProtection($true, $false)" in command
    assert "foreach ($sid in @($user, $system))" in command
    assert "'S-1-5-18'" in command and "::FullControl" in command
    assert "::ContainerInherit" in command and "::ObjectInherit" in command
    assert "Get-Acl" not in command and "GetAccessControl" not in command
    assert "[System.IO.Directory]::SetAccessControl" in command
    assert calls[0][1]["env"]["ANALYTICS_NEW_PRIVATE_DIRECTORY"] == str(path)
    assert calls[0][1]["check"] is True


def test_existing_directory_is_never_resecured(monkeypatch):
    class Existing:
        def exists(self): return True
    monkeypatch.setattr(live, "_set_new_windows_acl", lambda path: pytest.fail("existing directory modified"))
    with pytest.raises(ValueError): live._private_directory(Existing(), None)


def test_apply_reports_safe_known_phase_without_exception_contents(monkeypatch, capsys):
    def fail(instance, *, projection_revision):
        assert projection_revision == "a011"
        live._phase("private-parent")
        raise ValueError("sensitive detail must not be shown")
    monkeypatch.setattr(live, "apply", fail)
    assert live.main(["--instance-id", INSTANCE, "--apply"]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "LOCAL_LIVE_OPERATION_FAILED phase=private-parent" in output.err
    assert "sensitive detail" not in output.err


def test_live_database_and_six_roles_are_separate_from_fixture_cleanup():
    name = live.database_name(INSTANCE)
    assert name == "analytics_live_" + UUID(INSTANCE).hex
    assert len(name + "_transfer_status") == 63
    assert not bootstrap_postgres.PATTERN.fullmatch(name)
    sys.path.insert(0, str(SERVICE / "tests"))
    from cross_service.guards import ANALYTICS_NAME
    assert not ANALYTICS_NAME.fullmatch(name)
    assert len({name + "_" + role for role in live.ROLES}) == 6
    for invalid in (None, "not-a-uuid", "{" + INSTANCE + "}", "00000000-0000-0000-0000-000000000000"):
        with pytest.raises((ValueError, TypeError)): live.database_name(invalid)


def test_configuration_builds_isolated_real_roles_and_core_binding(monkeypatch):
    owner, config = fixture_config()
    core = config["core_env"]
    assert core["PYMENTOR_ANALYTICS_LOCAL_EXPECTED_DATABASE"] == "core_test"
    assert core["PYMENTOR_ANALYTICS_LOCAL_INSTANCE_ID"] == CORE
    assert core["PYMENTOR_ANALYTICS_LOCAL_CATALOG_NAMESPACE"] == "core-local:" + CORE
    assert core["PYMENTOR_ANALYTICS_PRODUCER_ID"] == "core-local"
    assert core["PYMENTOR_ANALYTICS_DELIVERY_ENABLED"] == "0"
    assert config["authority_server"]["issuer"] == "local-authority:" + INSTANCE
    assert config["authority_server"]["fixture_mode"] is False
    class Local:
        _unique = staticmethod(lambda pairs: dict(pairs))
        _read = staticmethod(lambda folder, name, limit: json.dumps(config["roles"]).encode())
    monkeypatch.setattr(runtime, "_identity", lambda instance: (Local, Path("private"), owner))
    _, _, _, roles = runtime.read_settings(INSTANCE, require_prepared=False)
    assert all(role.pool_size == 1 and role.pool_overflow == 0 and role.real_collection for role in roles.values())
    assert roles["processor"].activity_day_worker_enabled and roles["query"].activity_day_query_enabled
    assert roles["processor"].processor_bucket_worker_enabled and not roles["processor"].processor_generic_worker_enabled
    assert roles["processor"].activity_projection_run_id == UUID(RUN) == roles["query"].activity_projection_run_id
    assert roles["collector"].catalog_namespace == "core-local:" + CORE
    assert len({role.database_url.get_secret_value() for role in roles.values()}) == 4


def test_transfer_overlay_preserves_local_deletion_and_private_settings(monkeypatch):
    from pymentor_analytics.app.config import core_transfer_config, local_deletion_config
    owner, config = fixture_config()
    class Local:
        _unique = staticmethod(lambda pairs: dict(pairs))
        _read = staticmethod(lambda folder, name, limit: json.dumps(config["roles"]).encode())
    monkeypatch.setattr(runtime, "_identity", lambda instance: (Local, Path("private"), owner))
    _, _, _, baseline = runtime.read_settings(INSTANCE, require_prepared=False)
    overlay = runtime.enable_core_transfer(owner, baseline)
    for role in ("collector", "processor", "query"):
        assert overlay[role].core_transfer_enabled and overlay[role].core_transfer_proof_enabled
        assert not baseline[role].core_transfer_enabled and not baseline[role].core_transfer_proof_enabled
        assert local_deletion_config(overlay[role], role) == local_deletion_config(baseline[role], role)
    assert core_transfer_config(overlay["collector"], "collector") == {"core-local": "core-local:" + CORE}
    assert overlay["collector"].core_transfer_status_database_url == baseline["collector"].core_deletion_status_database_url
    assert core_transfer_config(overlay["query"], "query") == {"namespace": "core-local:" + CORE, "roster": None}
    assert overlay["privacy"] is baseline["privacy"]


def test_transfer_overlay_rejects_namespace_drift(monkeypatch):
    owner, config = fixture_config()
    class Local:
        _unique = staticmethod(lambda pairs: dict(pairs))
        _read = staticmethod(lambda folder, name, limit: json.dumps(config["roles"]).encode())
    monkeypatch.setattr(runtime, "_identity", lambda instance: (Local, Path("private"), owner))
    _, _, _, baseline = runtime.read_settings(INSTANCE, require_prepared=False)
    baseline["collector"] = baseline["collector"].model_copy(update={"core_transfer_namespaces_json":
        baseline["collector"].core_transfer_namespaces_json.__class__('{ "core-local": "other" }')})
    with pytest.raises(ValueError): runtime.enable_core_transfer(owner, baseline)


@pytest.mark.parametrize("change", ["wrong_database", "wrong_role", "wrong_projection", "pool", "nonlocal", "shared_source"])
def test_runtime_rejects_role_or_shared_identity_drift(monkeypatch, change):
    owner, config = fixture_config()
    roles = config["roles"]["roles"]
    if change == "wrong_database": roles["query"]["database_url"] = roles["query"]["database_url"].replace("analytics_live_", "analytics_test_")
    if change == "wrong_role": roles["query"]["database_url"] = roles["collector"]["database_url"]
    if change == "wrong_projection": roles["query"]["activity_projection_run_id"] = CORE
    if change == "pool": roles["processor"]["pool_size"] = 2
    if change == "nonlocal": roles["privacy"]["database_url"] = roles["privacy"]["database_url"].replace("127.0.0.1", "10.0.0.1")
    if change == "shared_source": roles["processor"]["core_deletion_source_json"] = roles["processor"]["core_deletion_source_json"].replace(CORE, RUN)
    class Local:
        _unique = staticmethod(lambda pairs: dict(pairs))
        _read = staticmethod(lambda folder, name, limit: json.dumps(config["roles"]).encode())
    monkeypatch.setattr(runtime, "_identity", lambda instance: (Local, Path("private"), owner))
    with pytest.raises(ValueError): runtime.read_settings(INSTANCE, require_prepared=False)


def test_pending_record_survives_unknown_no_retry(monkeypatch):
    events = []
    monkeypatch.setattr(live, "_write", lambda local, folder, name, value: events.append(name))
    def action():
        events.append("mutation")
        raise TimeoutError("unknown")
    with pytest.raises(TimeoutError): live._pending(None, None, "database-create", "target", action, lambda: True)
    assert events == ["database-create.pending.json", "mutation"]


def test_stop_is_bound_to_original_process_identity(monkeypatch):
    marker = {"version": 1, "instance_id": INSTANCE, "pid": 42, "started": 123, "nonce": "a" * 32}
    class Local:
        _unique = staticmethod(lambda pairs: dict(pairs))
        _read = staticmethod(lambda *args: json.dumps(marker).encode())
    writes = []
    monkeypatch.setattr(runtime, "_identity", lambda instance: (Local, Path("private"), {}))
    monkeypatch.setattr(runtime, "_write_new", lambda local, folder, name, data: writes.append((name, data)))
    monkeypatch.setattr(runtime, "_process_start_identity", lambda pid: 999)
    with pytest.raises(ValueError): runtime.request_stop(INSTANCE)
    assert writes == []
    monkeypatch.setattr(runtime, "_process_start_identity", lambda pid: 123)
    runtime.request_stop(INSTANCE)
    assert writes == [("runtime.stop.json", {"version": 1, "instance_id": INSTANCE, "nonce": "a" * 32})]


def test_runtime_requires_prepared_history(monkeypatch):
    owner, config = fixture_config()
    class Local:
        _unique = staticmethod(lambda pairs: dict(pairs))
        _read = staticmethod(lambda folder, name, limit: json.dumps({"version": 1, "instance_id": INSTANCE}).encode())
    monkeypatch.setattr(runtime, "_identity", lambda instance: (Local, Path("private"), owner))
    with pytest.raises(ValueError): runtime.read_settings(INSTANCE)


@pytest.mark.parametrize(("revision", "course", "boundary"), [
    ("a011", False, False), ("a012", True, False), ("a013", True, True),
    ("a014", True, True),
])
def test_prepared_revision_requires_matching_projection_flags(monkeypatch, revision, course, boundary):
    import bootstrap_runtime

    owner, config = fixture_config(revision)
    prepared = {
        "version": 1, "instance_id": INSTANCE, "database": live.database_name(INSTANCE),
        "schema_revision": revision, "history_sha256": bootstrap_runtime.schema_history(revision)[2],
        "backfill": "NOT_YET_VERIFIED", "do_not_drop": True,
    }

    class Local:
        _unique = staticmethod(dict)

        @staticmethod
        def _read(folder, name, limit):
            return json.dumps(prepared if name == "prepared.json" else config["roles"]).encode()

    monkeypatch.setattr(runtime, "_identity", lambda instance: (Local, Path("private"), owner))
    _, _, _, roles = runtime.read_settings(INSTANCE)
    assert roles["processor"].activity_course_worker_enabled is course
    assert roles["processor"].activity_boundary_credit_enabled is boundary
    assert roles["query"].activity_course_query_enabled is course

    config["roles"]["roles"]["query"]["activity_course_query_enabled"] = not course
    with pytest.raises(ValueError, match="projection configuration"):
        runtime.read_settings(INSTANCE)


def test_projection_revision_option_is_apply_only(monkeypatch, capsys):
    calls = []

    def record(instance, *, projection_revision):
        calls.append((instance, projection_revision))
        return {"status": "prepared"}

    monkeypatch.setattr(live, "apply", record)
    assert live.main(["--instance-id", INSTANCE, "--apply", "--projection-revision", "a013"]) == 0
    assert calls == [(INSTANCE, "a013")]
    assert json.loads(capsys.readouterr().out) == {"status": "prepared"}
    with pytest.raises(SystemExit) as error:
        live.main(["--instance-id", INSTANCE, "--status", "--projection-revision", "a013"])
    assert error.value.code == 2
    assert calls == [(INSTANCE, "a013")]


def test_core_preflight_program_compiles_and_admin_identity_fails_closed():
    compile(live.CORE_PREFLIGHT, "<core-read-only-preflight>", "exec")
    class Connection:
        def __init__(self, row): self.row = row
        def execute(self, query): return type("Result", (), {"fetchone": lambda _: self.row})()
    live._verify_admin(Connection(("analytics_bootstrap", "analytics_bootstrap", 160001, False)))
    for row in (("analytics_bootstrap", "analytics_bootstrap", 170000, False),
                ("analytics_bootstrap", "analytics_bootstrap", 160001, True), ("other", "analytics_bootstrap", 160001, False)):
        with pytest.raises(ValueError): live._verify_admin(Connection(row))
