import copy
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import bootstrap_runtime as runtime
import check_k3s
import run_local


NAME = "analytics_test_" + "a" * 32
RECORD = {"synthetic_only": True, "do_not_drop": True, "project": runtime.PROJECT,
          "state": "ready", "database": NAME, "nonce": "b" * 64,
          "urls": {role: f"postgresql://{NAME}_{role}:synthetic@127.0.0.1:57392/{NAME}" for role in runtime.db.ROLES}}
HISTORY = ("a003", ("a003", "a002", "a001"), "a" * 64)


@pytest.mark.parametrize("change", [
    {"project": "core"}, {"synthetic_only": False}, {"do_not_drop": False},
    {"state": "provisioning"}, {"database": "core"}, {"nonce": "wrong"},
])
def test_refuses_nonmatching_or_incomplete_metadata(change):
    with pytest.raises(ValueError):
        runtime.validate_record({**RECORD, **change})


@pytest.mark.parametrize("bad_url", [
    f"postgresql://{NAME}_query:synthetic@127.0.0.1:57382/{NAME}",
    f"postgresql://{NAME}_query:synthetic@remote:57392/{NAME}",
    f"postgresql://{NAME}_collector:synthetic@127.0.0.1:57392/{NAME}",
    f"postgresql://{NAME}_query:synthetic@127.0.0.1:57392/{NAME}?host=core",
])
def test_refuses_cross_role_or_unsafe_database_url(bad_url):
    record = copy.deepcopy(RECORD)
    record["urls"]["query"] = bad_url
    with pytest.raises(ValueError):
        runtime.validate_record(record)


def test_container_addresses_and_project_scope():
    env = run_local.compose_env(RECORD)
    assert "@analytics-postgres:5432/" in env["ANALYTICS_QUERY_DATABASE_URL"]
    with pytest.raises(ValueError):
        run_local.docker_url("postgresql://u:p@localhost:57382/core")
    fake = [{"Config": {"Labels": {"com.docker.compose.project": run_local.PROJECT,
                                    "com.docker.compose.service": "analytics-postgres",
                                    "com.docker.compose.project.working_dir": str(run_local.COMPOSE_DIR)}}}]
    with patch.object(run_local, "docker", side_effect=["container-id\n", __import__("json").dumps(fake)]):
        with pytest.raises(ValueError):
            run_local.verify_project()


def test_stop_does_not_depend_on_database_metadata():
    with patch.object(sys, "argv", ["run_local.py", "stop"]), \
         patch.object(runtime, "read_record", side_effect=AssertionError("must not read metadata")), \
         patch.object(run_local.check_k3s, "local_docker_env"), patch.object(run_local, "verify_project"), \
         patch.object(run_local, "compose") as compose:
        run_local.main()
        args, kwargs = compose.call_args
        assert args[0] == ["stop"]
        assert "disabled@127.0.0.1:57392" in args[1]["ANALYTICS_QUERY_DATABASE_URL"]


def test_memory_admission_is_fail_closed():
    assert not check_k3s.admission(check_k3s.MIN_FREE - 1)
    assert check_k3s.admission(check_k3s.MIN_FREE)


def test_compose_admission_uses_actual_limits_and_refuses_unbounded():
    def configuration(app_mib):
        return {"services": {role: {"mem_limit": str((128 if role == "ingress" else app_mib) * 1024**2)}
                             for role in ("collector", "processor", "query", "ingress")}}
    assert run_local.required_app_memory(configuration(128)) == 1536 * 1024**2
    assert run_local.required_app_memory(configuration(256)) == 1792 * 1024**2
    invalid = configuration(128)
    del invalid["services"]["query"]["mem_limit"]
    with pytest.raises(ValueError):
        run_local.required_app_memory(invalid)


def test_remote_docker_context_is_refused(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    result = SimpleNamespace(stdout='[{"Endpoints":{"docker":{"Host":"tcp://remote:2375"}}}]')
    with patch.object(check_k3s.subprocess, "run", return_value=result):
        with pytest.raises(ValueError):
            check_k3s.local_docker_env()


def test_preflight_failure_does_not_create_marker_or_database():
    with patch.object(runtime.db, "config", return_value={}), patch.object(runtime.RECORD.__class__, "exists", return_value=False), \
         patch.object(runtime, "schema_history", return_value=HISTORY), \
         patch.object(runtime.db, "connect", side_effect=OSError), patch.object(runtime, "write_record") as write, \
         patch.object(runtime.db, "create") as create:
        with pytest.raises(OSError):
            runtime.init()
        write.assert_not_called()
        create.assert_not_called()


def test_incomplete_provision_is_not_reseeded():
    with patch.object(runtime.db, "config", return_value={}), patch.object(runtime.RECORD.__class__, "exists", return_value=True), \
         patch.object(runtime, "schema_history", return_value=HISTORY), \
         patch.object(runtime, "read_record", side_effect=ValueError), patch.object(runtime.db, "create") as create:
        with pytest.raises(ValueError):
            runtime.init()
        create.assert_not_called()


def test_sentinel_mismatch_refuses_reuse():
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, *args):
            return SimpleNamespace(fetchall=lambda: [("different-sentinel",)])

    with patch.object(runtime.db, "connect", return_value=FakeConnection()):
        with pytest.raises(ValueError, match="sentinel mismatch"):
            runtime.verify_database({}, RECORD)


def test_pending_migration_reconciles_without_reseeding():
    record = {**RECORD, "state": "migration_pending", "target_revision": HISTORY[0], "history_sha256": HISTORY[2]}
    with patch.object(runtime.db, "config", return_value={}), patch.object(runtime.RECORD.__class__, "exists", return_value=True), \
         patch.object(runtime, "schema_history", return_value=HISTORY), \
         patch.object(runtime, "read_record", return_value=record), \
         patch.object(runtime, "verify_database", return_value=[("a003",)]), \
         patch.object(runtime, "write_record") as write, patch.object(runtime.db, "create") as create:
        runtime.init()
        create.assert_not_called()
        assert write.call_args.args[0]["state"] == "ready"


def test_record_precedes_migration_and_unknown_outcome_stays_pending():
    events = []
    def write(record, **kwargs):
        events.append(record["state"])
    def migrate(*args, **kwargs):
        assert events == ["provisioning", "migration_pending", "migration_pending"]
        return SimpleNamespace(returncode=1)
    with patch.object(runtime.db, "config", return_value={}), patch.object(runtime.db, "connect"), \
         patch.object(runtime, "schema_history", return_value=HISTORY), \
         patch.object(runtime.RECORD.__class__, "exists", return_value=False), \
         patch.object(runtime, "write_record", side_effect=write), \
         patch.object(runtime.db, "create", return_value=(NAME, "b" * 64, RECORD["urls"])), \
         patch.object(runtime, "verify_database", return_value=[]), patch.object(runtime.subprocess, "run", side_effect=migrate):
        with pytest.raises(ValueError):
            runtime.init()
    assert events == ["provisioning", "migration_pending", "migration_pending"]


@pytest.mark.parametrize("revision", [[("future",)], [("a001",), ("a002",)], []])
def test_upgrade_rejects_unknown_or_multiple_revisions(revision):
    with patch.object(runtime.db, "config", return_value={}), patch.object(runtime, "schema_history", return_value=HISTORY), \
         patch.object(runtime, "read_record", return_value=copy.deepcopy(RECORD)), \
         patch.object(runtime, "verify_database", return_value=revision), \
         patch.object(runtime, "migrate_to_head") as migration:
        with pytest.raises(ValueError):
            runtime.upgrade()
        migration.assert_not_called()


def test_upgrade_records_target_and_preserves_identity_before_migration():
    record = copy.deepcopy(RECORD)
    saved = []
    def save(value):
        saved.append(copy.deepcopy(value))
    def migrate(*args, **kwargs):
        assert saved[-1]["state"] == "migration_pending"
        assert saved[-1]["target_revision"] == "a003"
        assert saved[-1]["history_sha256"] == HISTORY[2]
        assert args[0][-2:] == ["--revision", "a003"]
        return SimpleNamespace(returncode=0)
    with patch.object(runtime.db, "config", return_value={}), patch.object(runtime, "schema_history", return_value=HISTORY), \
         patch.object(runtime, "read_record", return_value=record), \
         patch.object(runtime, "verify_database", side_effect=[[('a001',)], [('a003',)]]), \
         patch.object(runtime, "write_record", side_effect=save), patch.object(runtime.subprocess, "run", side_effect=migrate):
        runtime.upgrade()
    assert [x["state"] for x in saved] == ["migration_pending", "ready"]
    assert saved[-1]["nonce"] == RECORD["nonce"] and saved[-1]["urls"] == RECORD["urls"]


def test_unresolved_pending_never_retries_migration():
    record = {**copy.deepcopy(RECORD), "state": "migration_pending", "target_revision": "a003", "history_sha256": HISTORY[2]}
    with patch.object(runtime.db, "config", return_value={}), patch.object(runtime, "schema_history", return_value=HISTORY), \
         patch.object(runtime, "read_record", return_value=record), \
         patch.object(runtime, "verify_database", return_value=[("a001",)]), \
         patch.object(runtime.subprocess, "run") as migration:
        with pytest.raises(ValueError):
            runtime.upgrade()
        migration.assert_not_called()


def test_start_refuses_old_schema_without_running_migrations():
    with patch.object(runtime, "operation_lock", return_value=nullcontext()), \
         patch.object(runtime, "read_record", return_value=copy.deepcopy(RECORD)), \
         patch.object(runtime, "schema_history", return_value=HISTORY), \
         patch.object(runtime.db, "config", return_value={}), \
         patch.object(runtime, "verify_database", return_value=[("a001",)]), \
         patch.object(runtime, "migrate_to_head") as migration, patch.object(run_local, "compose") as compose:
        with pytest.raises(ValueError):
            run_local.start()
        migration.assert_not_called()
        compose.assert_not_called()


def test_changed_history_refuses_pending_reconciliation():
    record = {**copy.deepcopy(RECORD), "state": "migration_pending", "target_revision": "a003", "history_sha256": "b" * 64}
    with patch.object(runtime.db, "config", return_value={}), patch.object(runtime, "schema_history", return_value=HISTORY), \
         patch.object(runtime, "read_record", return_value=record), patch.object(runtime.subprocess, "run") as migration:
        with pytest.raises(ValueError):
            runtime.upgrade()
        migration.assert_not_called()


def test_upgrade_at_head_is_noop():
    with patch.object(runtime.db, "config", return_value={}), patch.object(runtime, "schema_history", return_value=HISTORY), \
         patch.object(runtime, "read_record", return_value=copy.deepcopy(RECORD)), \
         patch.object(runtime, "verify_database", return_value=[("a003",)]), \
         patch.object(runtime, "migrate_to_head") as migration:
        runtime.upgrade()
        migration.assert_not_called()


def test_alembic_multiple_heads_are_rejected():
    scripts = SimpleNamespace(get_heads=lambda: ["a002", "a003"])
    with patch.object(runtime.ScriptDirectory, "from_config", return_value=scripts):
        with pytest.raises(ValueError):
            runtime.schema_history()


def test_override_contains_only_common_public_manifest():
    text = '{"public":"contains$variable"}'
    doc = run_local.trust_override_document(text)
    assert set(doc["services"]) == {"collector", "processor", "query"}
    for entry in doc["services"].values():
        assert entry["environment"] == {"ANALYTICS_TRUST_MANIFEST_JSON": text.replace("$", "$$")}
    assert "DATABASE_URL" not in json.dumps(doc)


def test_conflicting_role_specific_trust_is_rejected(monkeypatch):
    monkeypatch.setenv("ANALYTICS_QUERY_GRANT_KEYS_JSON", "independent")
    with pytest.raises(ValueError):
        run_local.trust_manifest()


def test_missing_manifest_keeps_trust_unconfigured(monkeypatch):
    fake = SimpleNamespace(parent=SimpleNamespace(is_symlink=lambda: False), is_symlink=lambda: False,
                           exists=lambda: False)
    monkeypatch.setattr(run_local, "MANIFEST", fake)
    assert run_local.trust_manifest() is None


def test_public_manifest_validation_and_private_key_rejection(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("ascii")
    producer_public = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("ascii")
    private = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii")
    data = {"grant": {"issuer": "synthetic-grant", "keys": {"g1": public}},
            "producer": {"issuer": "synthetic-producer", "keys": {"p1": producer_public}, "allowlist": ["synthetic-source"]}}
    content = json.dumps(data)
    fake = SimpleNamespace(parent=SimpleNamespace(is_symlink=lambda: False), is_symlink=lambda: False,
                           exists=lambda: True, stat=lambda: SimpleNamespace(st_size=len(content)), read_text=lambda **_: content)
    monkeypatch.setattr(run_local, "MANIFEST", fake)
    raw, fingerprint = run_local.trust_manifest()
    assert raw == content and len(fingerprint) == 64
    data["grant"]["keys"]["g1"] = private
    content = json.dumps(data)
    with pytest.raises(ValueError):
        run_local.trust_manifest()


def test_running_process_must_match_fingerprint():
    with patch.object(run_local, "compose", side_effect=["good", "wrong"]):
        with pytest.raises(ValueError):
            run_local.verify_running_trust({}, None, "good", timeout=0)
    with patch.object(run_local, "compose", side_effect=["good", "good"]):
        run_local.verify_running_trust({}, None, "good", timeout=0)


def test_start_stops_application_if_trust_fingerprint_is_wrong():
    services = {role: {"mem_limit": str(128 * 1024**2)} for role in ("collector", "processor", "query", "ingress")}
    with patch.object(runtime, "operation_lock", return_value=nullcontext()), \
         patch.object(runtime, "read_record", return_value=copy.deepcopy(RECORD)), \
         patch.object(runtime, "schema_history", return_value=HISTORY), \
         patch.object(runtime.db, "config", return_value={}), \
         patch.object(runtime, "verify_database", return_value=[("a003",)]), \
         patch.object(run_local, "trust_manifest", return_value=("public", "expected")), \
         patch.object(run_local, "write_trust_override", return_value=Path("override.json")), \
         patch.object(run_local.check_k3s, "local_docker_env"), patch.object(run_local, "verify_project"), \
         patch.object(run_local, "runtime_files_fingerprint", return_value="a" * 64), \
         patch.object(run_local, "resolve_verified_image", return_value="sha256:" + "a" * 64), \
         patch.object(run_local.check_k3s, "available_memory", return_value=4 * 1024**3), \
         patch.object(run_local, "verify_running_trust", side_effect=ValueError("wrong")), \
         patch.object(run_local, "compose", side_effect=[json.dumps({"services": services}), "", ""]) as compose:
        with pytest.raises(ValueError):
            run_local.start()
        assert [call.args[0] for call in compose.call_args_list] == [
            ["config", "--format", "json"], ["up", "-d", "--no-build"], ["stop"]]
