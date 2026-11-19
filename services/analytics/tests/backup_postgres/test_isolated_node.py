"""Opt-in isolated-node recovery; source fixture is deleted before new node restore."""
from datetime import datetime, timezone
from unittest.mock import patch
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import synthetic_backup as backup
import run_local

pytestmark = pytest.mark.skipif(os.environ.get("ANALYTICS_RUN_ISOLATED_BACKUP") != "1",
                                reason="requires explicitly enabled isolated node proof")


def memory_guard():
    if backup.check_k3s.available_memory() < 1536 * 1024**2:
        raise RuntimeError("insufficient fresh host memory")


def run(args, *, env=None, timeout=30, input_file=None):
    result = subprocess.run(["docker", *args], env=env, stdin=input_file, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("isolated Docker operation failed")
    return result.stdout.decode("utf-8").strip()


def test_isolated_node_restore_without_source_fixture():
    memory_guard()
    backup.check_k3s.local_docker_env()
    backup.tree()
    image_id = os.environ.get("ANALYTICS_MIGRATOR_IMAGE_ID", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise ValueError("immutable migrator image ID required")
    if run(["image", "inspect", "--format", "{{.Id}}", image_id]) != image_id:
        raise ValueError("migrator image identity mismatch")
    expected = backup.history_for_revision("a006")
    memory_guard()
    probe_id = uuid4().hex
    probe = run(["run", "--rm", "--pull=never", "--name", "knowi-analytics-probe-" + probe_id,
                 "--label", "knowi.analytics.synthetic=true", "--label", "knowi.analytics.fixture=" + probe_id,
                 "--network=none", "--read-only", "--memory=128m",
                 "--cpus=0.25", "--pids-limit=64", "--user=10001", "--workdir=/app", "--entrypoint=python",
                 image_id, "-c", run_local.IMAGE_HISTORY_PROBE], timeout=30)
    if json.loads(probe)["history"] != [expected["head"], expected["chain"], expected["sha256"]]:
        raise ValueError("migration history in image differs")
    pg_image = run(["image", "inspect", "--format", "{{.Id}}", "postgres:16"])
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", pg_image):
        raise ValueError("invalid postgres image ID")

    # The source is a new synthetic fixture; never use the retained runtime DB.
    source_id, key_id, artifact_id, operation_id = (str(uuid4()) for _ in range(4))
    source_config = backup.connection_config()
    source = backup.provision(source_id, "fixture", source_config, expected)
    source_time = datetime.now(timezone.utc)
    with backup.db.connect(source_config, source["database"]) as conn:
        conn.execute("INSERT INTO analytics.activity_facts "
                     "(producer_namespace,event_id,subject_ref,course_id,catalog_version,lesson_id,started_at,received_at,canonical_hash,hash_version,accepted_event) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                     ("synthetic-backup", uuid4(), uuid4(), "course", "v1", "lesson", source_time, source_time, "a" * 64, 1, "{}"))
    backup.new_file(backup.path_for("keys", key_id, ".key"), os.urandom(32))
    memory_guard()
    backup.backup(source_id, artifact_id, key_id)
    header, stage = backup.crypto.decrypt_verified(backup.path_for("artifacts", artifact_id, ".enc"),
                                                   backup.BASE / "staging", backup.key_for(key_id))
    assert header["metadata"]["source_id"] == source_id and header["metadata"]["history"] == expected
    source_networks = json.loads(run(["inspect", "--format", "{{json .NetworkSettings.Networks}}", backup.CONTAINER]))
    source_ips = [net["IPAddress"] for net in source_networks.values() if net.get("IPAddress")]
    if not source_ips:
        raise ValueError("source address unavailable before simulated loss")
    memory_guard()
    backup.db.cleanup(source_config, source["database"], source["nonce"])
    with backup.db.connect(source_config) as conn:
        assert conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (source["database"],)).fetchone() is None
    del source_config

    # Credentials below are newly generated for the isolated node only.
    nonce = uuid4().hex
    network = "knowi-analytics-restore-" + nonce
    name = "knowi-analytics-restore-" + nonce
    target_db = "analytics_test_" + uuid4().hex
    target_sentinel = uuid4().hex + uuid4().hex
    target_password = uuid4().hex + uuid4().hex
    passwords = {role: uuid4().hex + uuid4().hex for role in backup.db.ROLES}
    record = {"operation_id": operation_id, "kind": "isolated_node", "state": "provisioning",
              "synthetic_only": True, "do_not_drop": True, "privacy_opened": False,
              "network": network, "container": name, "nonce": nonce, "postgres_image": pg_image,
              "database": target_db, "sentinel": target_sentinel, "password": target_password,
              "role_passwords": passwords, "artifact_id": artifact_id, "key_id": key_id, "stage": stage.name,
              "source_ips": source_ips}
    backup.write_record(operation_id, record, create=True)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from isolated_resume import create_node, resume
    # After loss, fail if recovery tries to use the original DB connector or name.
    source_container = backup.CONTAINER
    real_run = subprocess.run
    def forbid_source(command, *args, **kwargs):
        if isinstance(command, (list, tuple)) and source_container in command:
            raise AssertionError("source container referenced after loss")
        return real_run(command, *args, **kwargs)
    with patch.object(backup.db, "connect", side_effect=AssertionError("source connector used")), \
         patch.object(backup, "CONTAINER", "source-node-unavailable"), \
         patch.object(subprocess, "run", side_effect=forbid_source):
        create_node(record)
        resume(operation_id, image_id)
