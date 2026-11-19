import copy
import hashlib
import io
import json
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

import pytest
from cryptography.exceptions import InvalidTag

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backup_postgres"))
import isolated_resume as isolated
import test_isolated_privacy as combined


NONCE = "a" * 32
RECORD = {"nonce": NONCE, "network": "knowi-analytics-restore-" + NONCE,
          "container": "knowi-analytics-restore-" + NONCE, "postgres_image": "sha256:" + "b" * 64,
          "database": "analytics_test_" + "c" * 32, "sentinel": "d" * 64, "password": "e" * 64,
          "role_passwords": {role: "f" * 64 for role in isolated.backup.db.ROLES}, "source_ips": ["172.19.0.2"]}


@pytest.mark.parametrize("field,value,role", [("host_port", 57382, "query"),
                                                ("container", "core", "query"),
                                                ("database", "core", "query"),
                                                ("host_port", 57422, "core")])
def test_attested_settings_rejects_foreign_identity_before_any_copy(field, value, role):
    record = {**copy.deepcopy(RECORD), "host_reachable": True, "host_port": 57422}
    record[field] = value
    with patch.object(isolated, "attest") as attest, patch.object(combined.Settings, "model_copy") as copy_settings:
        with pytest.raises(ValueError):
            combined.attested_settings(record, role)
        attest.assert_not_called()
        copy_settings.assert_not_called()


def test_host_fixture_rejects_foreign_network_attachment():
    record = {**copy.deepcopy(RECORD), "host_reachable": True, "host_port": 57422}
    name = record["container"]
    network = f"{name}|false|true|{NONCE}"
    container = "|".join(["/" + name, record["postgres_image"], "true", NONCE, "268435456", "true",
                          json.dumps({name: {"IPAddress": "172.20.0.2"}}),
                          json.dumps({"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "57422"}]}),
                          "[]", json.dumps({"/var/lib/postgresql/data": "rw,size=128m"})])
    attachments = json.dumps({"one": {"Name": name}, "two": {"Name": "foreign"}})
    with patch.object(isolated, "docker", side_effect=[network, "bridge|local|false", container, attachments]):
        with pytest.raises(ValueError, match="attachment"):
            isolated.attest(record)


@pytest.mark.parametrize("responses", [["wrong-sentinel"], [RECORD["sentinel"], "wrong-owner"]])
def test_attested_settings_rejects_database_identity_before_copy(responses):
    record = {**copy.deepcopy(RECORD), "host_reachable": True, "host_port": 57422}
    with patch.object(isolated, "attest"), patch.object(isolated, "psql", side_effect=responses), \
         patch.object(combined.Settings, "model_copy") as copy_settings:
        with pytest.raises(ValueError):
            combined.attested_settings(record, "query")
        copy_settings.assert_not_called()


@pytest.mark.parametrize("field,value", [("network", "core"), ("container", "other"), ("database", "core"),
                                         ("postgres_image", "postgres:16"), ("nonce", "bad"),
                                         ("source_ips", ["127.0.0.1"])])
def test_bad_node_metadata_refused_before_docker(field, value):
    record = copy.deepcopy(RECORD)
    record[field] = value
    with patch.object(isolated, "docker") as docker:
        with pytest.raises(ValueError):
            isolated.validate_record(record)
        docker.assert_not_called()


def test_sql_is_stdin_and_password_only_environment():
    record = copy.deepcopy(RECORD)
    with patch.object(isolated, "attest"), patch.object(isolated, "docker", return_value="0") as docker:
        assert isolated.psql(record, "analytics_bootstrap", "SELECT 0;") == "0"
    call = docker.call_args
    assert call.kwargs["input_data"] == b"SELECT 0;"
    assert record["password"] not in repr(call.args[0])
    assert call.kwargs["env"]["PGPASSWORD"] == record["password"]


@pytest.mark.parametrize("port,valid", [("57422", True), ("57382", False)])
def test_node_attestation_rejects_nonisolated_binding(port, valid):
    name = RECORD["container"]
    network = f"{name}|true|true|{NONCE}"
    networks = {name: {"IPAddress": "172.20.0.2"}}
    bindings = {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": port}]}
    mounts = []
    tmpfs = {"/var/lib/postgresql/data": "rw,size=128m"}
    container = "|".join(["/" + name, RECORD["postgres_image"], "true", NONCE, "268435456", "true",
                          json.dumps(networks), json.dumps(bindings), json.dumps(mounts), json.dumps(tmpfs)])
    with patch.object(isolated, "docker", side_effect=[network, container]):
        if valid:
            isolated.attest(RECORD)
        else:
            with pytest.raises(ValueError):
                isolated.attest(RECORD)


def test_tag_failure_prevents_target_inspection_or_mutation():
    record = {**copy.deepcopy(RECORD), "artifact_id": "11111111-1111-4111-8111-111111111111",
              "key_id": "22222222-2222-4222-8222-222222222222"}
    with patch.object(isolated.backup, "tree"), patch.object(isolated.backup, "read_record", return_value=record), \
         patch.object(isolated.backup.check_k3s, "local_docker_env"), \
         patch.object(isolated.backup, "key_for", return_value=b"x" * 32), \
         patch.object(isolated.backup.crypto, "decrypt_verified", side_effect=InvalidTag), \
         patch.object(isolated, "docker") as docker:
        with pytest.raises(InvalidTag):
            isolated.resume("33333333-3333-4333-8333-333333333333", "sha256:" + "1" * 64)
        docker.assert_not_called()


def test_table_digests_batch_streams_rows_and_checks_markers():
    lines = b'__TABLE__:activity_facts\n{"id": 1}\n__TABLE__:admission_budget\n{"limit": 2}\n'
    process = MagicMock()
    process.stdin = io.BytesIO()
    process.stdout = io.BytesIO(lines)
    process.returncode = 0
    with patch.object(isolated, "attest"), patch.object(isolated.subprocess, "Popen", return_value=process):
        actual = isolated.table_digests(RECORD, RECORD["database"], ["activity_facts", "admission_budget"])
    def expected(value):
        return hashlib.sha256(len(value).to_bytes(8, "big") + value).hexdigest()
    assert actual == {"activity_facts": (1, expected(b'{"id": 1}')),
                      "admission_budget": (1, expected(b'{"limit": 2}'))}
