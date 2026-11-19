"""Opt-in: destructive only to a fresh fixture created by this test."""
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import synthetic_backup as backup

pytestmark = pytest.mark.skipif(os.environ.get("ANALYTICS_RUN_SYNTHETIC_BACKUP_POSTGRES") != "1",
                                reason="requires explicitly enabled isolated Analytics fixture")


def test_encrypted_restore_after_fresh_source_deletion():
    def memory_guard():
        if backup.check_k3s.available_memory() < 1536 * 1024**2:
            pytest.fail("insufficient fresh host memory for the synthetic backup proof")
    memory_guard()
    backup.tree()
    fixture_id, key_id, artifact_id, restore_id = (str(uuid4()) for _ in range(4))
    config = backup.connection_config()
    source = backup.provision(fixture_id, "fixture", config, backup.history_for_revision("a006"))
    source_time = datetime.now(timezone.utc)
    with backup.db.connect(config, source["database"]) as conn:
        conn.execute("INSERT INTO analytics.activity_facts "
                     "(producer_namespace,event_id,subject_ref,course_id,catalog_version,lesson_id,started_at,received_at,canonical_hash,hash_version,accepted_event) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                     ("synthetic-backup", uuid4(), uuid4(), "course", "v1", "lesson", source_time, source_time,
                      "a" * 64, 1, "{}"))
    backup.new_file(backup.path_for("keys", key_id, ".key"), os.urandom(32))
    memory_guard()
    backup.backup(fixture_id, artifact_id, key_id)
    header, stage = backup.crypto.decrypt_verified(backup.path_for("artifacts", artifact_id, ".enc"),
                                                   backup.BASE / "staging", backup.key_for(key_id))
    assert header["metadata"]["source_id"] == fixture_id
    stage.unlink()
    # Explicitly delete only the DB created above, after its encrypted copy authenticates.
    memory_guard()
    backup.db.cleanup(config, source["database"], source["nonce"])
    with backup.db.connect(config) as conn:
        assert conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (source["database"],)).fetchone() is None
    memory_guard()
    started = time.perf_counter()
    backup.restore(artifact_id, key_id, restore_id)
    backup.parity(restore_id)
    rto_seconds = time.perf_counter() - started
    target = backup.read_record(restore_id, "restore", "parity_verified")
    with backup.db.connect(config, target["database"]) as conn:
        restored_count, restored_latest = conn.execute("SELECT count(*),max(received_at) FROM analytics.activity_facts").fetchone()
    assert restored_count == 1 and restored_latest == source_time
    assert target["privacy_opened"] is False
    print(f"fixture_storage_restore_seconds={rto_seconds:.3f} snapshot_fact_parity=true privacy_opened=false")
    # After verified restore, remove only the target created in this test.
    backup.db.cleanup(config, target["database"], target["nonce"])
    target["target_deleted_after_proof"] = True
    backup.write_record(restore_id, target)


def test_fixture_loses_one_commit_after_snapshot():
    """Measure one controlled missed commit; this is not an operational RPO."""
    def guard():
        if backup.check_k3s.available_memory() < 1536 * 1024**2:
            pytest.fail("insufficient fresh host memory")
    guard()
    backup.tree()
    fixture_id, key_id, artifact_id, restore_id = (str(uuid4()) for _ in range(4))
    config = backup.connection_config()
    source = backup.provision(fixture_id, "fixture", config, backup.history_for_revision("a006"))
    subject = uuid4()
    def insert_fact(at):
        with backup.db.connect(config, source["database"]) as conn:
            conn.execute("INSERT INTO analytics.activity_facts "
                         "(producer_namespace,event_id,subject_ref,course_id,catalog_version,lesson_id,started_at,received_at,canonical_hash,hash_version,accepted_event) "
                         "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                         ("synthetic-rpo", uuid4(), subject, "course", "v1", "lesson", at, at, "a" * 64, 1, "{}"))
    checkpoint = datetime.now(timezone.utc)
    insert_fact(checkpoint)
    backup.new_file(backup.path_for("keys", key_id, ".key"), os.urandom(32))
    guard()
    backup.backup(fixture_id, artifact_id, key_id)
    header, stage = backup.crypto.decrypt_verified(backup.path_for("artifacts", artifact_id, ".enc"),
                                                   backup.BASE / "staging", backup.key_for(key_id))
    stage.unlink()
    late = datetime.now(timezone.utc)
    insert_fact(late)
    failure_time = datetime.now(timezone.utc)
    guard()
    backup.db.cleanup(config, source["database"], source["nonce"])
    guard()
    started = time.perf_counter()
    backup.restore(artifact_id, key_id, restore_id)
    backup.parity(restore_id)
    elapsed = time.perf_counter() - started
    target = backup.read_record(restore_id, "restore", "parity_verified")
    with backup.db.connect(config, target["database"]) as conn:
        count, restored_latest = conn.execute("SELECT count(*),max(received_at) FROM analytics.activity_facts").fetchone()
    assert count == 1 and restored_latest == checkpoint
    snapshot_end = datetime.fromisoformat(header["metadata"]["snapshot_finished_at"])
    age = max(0.0, (failure_time - snapshot_end).total_seconds())
    commit_gap = max(0.0, (late - restored_latest).total_seconds())
    print(f"fixture_lost_facts=1 snapshot_age_at_loss_seconds={age:.3f} latest_committed_fact_gap_seconds={commit_gap:.3f} storage_restore_seconds={elapsed:.3f} privacy_opened=false")
    backup.db.cleanup(config, target["database"], target["nonce"])
    target["target_deleted_after_proof"] = True
    backup.write_record(restore_id, target)
