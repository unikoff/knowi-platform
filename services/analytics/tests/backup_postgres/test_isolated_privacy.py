"""Opt-in encrypted restore with a stopped source and independent deletion journal."""
import asyncio
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import quote
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import synthetic_backup as backup
import run_local
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "privacy_integration"))
from test_signed_deletion_pipeline import authority_server, observe, producer_token, grant_token, pem
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient
from pymentor_analytics.adapters.privacy_authority.journal import Journal
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
from pymentor_analytics.adapters.postgres.database import Database
from pydantic import SecretStr
import isolated_resume as isolated
from pymentor_analytics.adapters.postgres.migrate import upgrade
from psycopg import sql
from pymentor_analytics.adapters.postgres.processor import PostgresProcessor, PostgresSyntheticCounts
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.factory import create_app
from pymentor_analytics.app.privacy import PrivacyRuntime
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.privacy_control_v1 import AuthorityProtocolError, AuthorityUnavailable
from pymentor_analytics.contracts.v2 import DeletionFence

pytestmark = pytest.mark.skipif(os.environ.get("ANALYTICS_RUN_ISOLATED_PRIVACY_BACKUP") != "1",
                                reason="requires explicitly enabled isolated node proof")


def memory_guard():
    if backup.check_k3s.available_memory() < 1536 * 1024**2:
        raise RuntimeError("insufficient fresh host memory")


def run(args, *, env=None, timeout=30, input_file=None):
    result = subprocess.run(["docker", *args], env=env, stdin=input_file, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("isolated Docker operation failed")
    return result.stdout.decode("utf-8").strip()


def attested_settings(record, role, **settings):
    """Test-only port adaptation after exact owned-node and DB identity checks."""
    isolated.validate_record(record)
    if record.get("host_reachable") is not True or record.get("host_port") != 57422 or role not in backup.db.ROLES:
        raise ValueError("invalid host fixture identity")
    isolated.attest(record)
    name = record["database"]
    if isolated.psql(record, name, "SELECT token FROM public.analytics_test_sentinel;") != record["sentinel"]:
        raise ValueError("fixture sentinel mismatch")
    owner_sql = sql.SQL("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname={};").format(sql.Literal(name)).as_string()
    if isolated.psql(record, "analytics_bootstrap", owner_sql) != f"{name}_migrate":
        raise ValueError("fixture owner mismatch")
    username = f"{name}_{role}"
    password = quote(record["role_passwords"][role], safe="")
    url = f"postgresql://{username}:{password}@127.0.0.1:57422/{name}"
    # No application policy is modified: only this already-attested fixture URL
    # is copied into a normally validated synthetic Settings instance.
    validated = Settings(database_url=f"postgresql://{username}:{password}@127.0.0.1:57392/{name}", **settings)
    return validated.model_copy(update={"database_url": SecretStr(url)})


def test_encrypted_isolated_restore_replays_later_signed_deletion(tmp_path):
    with authority_server(tmp_path) as authority_fixture:
        run_scenario(authority_fixture, tmp_path)


def run_scenario(authority_fixture, authority_folder):
    authority, authority_json, trust, producer, command, grant = authority_fixture
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

    # A nonce-owned temporary source node, independent from retained PG 57392.
    # Source and destination use the same loopback port sequentially, never together.
    source_id, key_id, artifact_id, operation_id = (str(uuid4()) for _ in range(4))
    source_nonce = uuid4().hex
    source_name = "knowi-analytics-restore-" + source_nonce
    source = {"operation_id": source_id, "kind": "isolated_source", "state": "provisioning",
              "synthetic_only": True, "do_not_drop": True, "privacy_opened": False,
              "nonce": source_nonce, "network": source_name, "container": source_name,
              "postgres_image": pg_image, "host_reachable": True, "host_port": 57422,
              "database": "analytics_test_" + uuid4().hex,
              "sentinel": uuid4().hex + uuid4().hex, "password": uuid4().hex + uuid4().hex,
              "role_passwords": {role: uuid4().hex + uuid4().hex for role in backup.db.ROLES},
              "source_ips": ["192.0.2.1"]}
    backup.write_record(source_id, source, create=True)
    isolated.create_node(source)
    deadline = time.monotonic() + 45
    while True:
        status = subprocess.run(["docker", "exec", source_name, "pg_isready", "-U", "analytics_bootstrap"],
                                capture_output=True, timeout=5)
        if status.returncode == 0:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("source PostgreSQL not ready")
        time.sleep(.25)
    isolated.attest(source)
    source_networks = json.loads(run(["inspect", "--format", "{{json .NetworkSettings.Networks}}", source_name]))
    source_ips = [net["IPAddress"] for net in source_networks.values() if net.get("IPAddress")]
    if not source_ips:
        raise ValueError("source address unavailable")
    target_name = source["database"]
    commands = []
    for role in backup.db.ROLES:
        commands.append(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;")
                        .format(sql.Identifier(f"{target_name}_{role}"), sql.Literal(source["role_passwords"][role])).as_string())
    commands.append(sql.SQL("CREATE DATABASE {} OWNER {};").format(sql.Identifier(target_name), sql.Identifier(f"{target_name}_migrate")).as_string())
    commands.append(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC;").format(sql.Identifier(target_name)).as_string())
    for role in backup.db.ROLES:
        commands.append(sql.SQL("GRANT CONNECT ON DATABASE {} TO {};").format(sql.Identifier(target_name), sql.Identifier(f"{target_name}_{role}")).as_string())
    isolated.psql(source, "analytics_bootstrap", "\n".join(commands))
    isolated.psql(source, target_name, sql.SQL("CREATE TABLE public.analytics_test_sentinel (token text NOT NULL); INSERT INTO public.analytics_test_sentinel VALUES ({});")
                  .format(sql.Literal(source["sentinel"])).as_string())
    source["state"] = "migration_pending"
    backup.write_record(source_id, source)
    upgrade(attested_settings(source, "migrate", pool_size=1, pool_overflow=0), target_revision=expected["head"])
    if isolated.psql(source, target_name, "SELECT version_num FROM public.alembic_version;") != expected["head"]:
        raise ValueError("source migration mismatch")
    deleted, survivor = uuid4(), uuid4()
    async def initialize_source():
        source_db = Database(attested_settings(source, "privacy", pool_size=1, pool_overflow=0))
        try:
            sync = PrivacyRuntime(AuthorityClient(authority), source_db, store=PostgresPrivacyStore(source_db))
            await observe(sync)
            assert await sync.is_ready()
        finally:
            await source_db.close()
    asyncio.run(initialize_source())
    for subject in (deleted, survivor):
        query = sql.SQL("INSERT INTO analytics.activity_facts (producer_namespace,event_id,subject_ref,course_id,catalog_version,lesson_id,started_at,received_at,canonical_hash,hash_version,accepted_event) VALUES ({},{},{},{},{},{},clock_timestamp(),clock_timestamp(),{},1,'{{}}'::jsonb);").format(
            *map(sql.Literal, ("synthetic-backup", uuid4(), subject, "backend", "v1", "lesson", "a" * 64))).as_string()
        isolated.psql(source, target_name, query)
    backup.new_file(backup.path_for("keys", key_id, ".key"), os.urandom(32))
    latest_fact = datetime.fromisoformat(isolated.psql(source, target_name, "SELECT max(received_at)::text FROM analytics.activity_facts;"))
    snapshot_started_at = datetime.now(timezone.utc)
    names = isolated.tables(source)
    before = isolated.table_digests(source, target_name, names)
    stage_plain = backup.BASE / "staging" / (uuid4().hex + ".dump")
    # Reuse the bounded streaming pg_dump with this exact attested source only.
    with patch.object(backup, "CONTAINER", source_name), patch.object(backup, "verify_container", side_effect=lambda: isolated.attest(source)):
        backup.dump(source, {"password": source["password"]}, stage_plain)
    after = isolated.table_digests(source, target_name, names)
    if before != after or backup.history_for_revision("a006") != expected:
        raise ValueError("source changed during snapshot")
    snapshot_finished_at = datetime.now(timezone.utc)
    metadata = {"synthetic_only": True, "source_id": source_id, "source_database": target_name,
                "history": expected, "format": "pg16-custom-analytics-data-only",
                "snapshot_started_at": snapshot_started_at.isoformat(), "snapshot_finished_at": snapshot_finished_at.isoformat(),
                "fixture_state_sha256": backup.state_digest(before)}
    try:
        backup.crypto.encrypt_file(stage_plain, backup.path_for("artifacts", artifact_id, ".enc"), backup.key_for(key_id), metadata)
    finally:
        stage_plain.unlink(missing_ok=True)
    header, stage = backup.crypto.decrypt_verified(backup.path_for("artifacts", artifact_id, ".enc"),
                                                   backup.BASE / "staging", backup.key_for(key_id))
    assert header["metadata"] == metadata
    async def append_deletion():
        deletion = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(), subject_ref=deleted,
                                 epoch=1, occurred_at=datetime.now(timezone.utc))
        receipt = await AuthorityClient(authority).append(deletion, producer="synthetic",
                    producer_token=producer_token(producer), command_key=command)
        assert receipt.seq == 1
    asyncio.run(append_deletion())
    # Reopen the committed independent file through a new journal instance.
    high, entries = Journal(authority_folder / "independent-authority.sqlite", authority.authority_id).snapshot(after=0)
    assert high == 1 and len(entries) == 1 and entries[0].control.subject_ref == deleted
    isolated.attest(source)
    run(["stop", "--time", "10", source_name], timeout=20)
    stopped = run(["inspect", "--format", '{{.Name}}|{{.Image}}|{{index .Config.Labels "knowi.analytics.fixture"}}|{{.State.Running}}', source_name])
    if stopped != f"/{source_name}|{pg_image}|{source_nonce}|false":
        raise ValueError("source node stop not established")
    loss_started = time.perf_counter()
    loss_at = datetime.now(timezone.utc)
    source.update(state="stopped_before_target", source_ips=source_ips, artifact_id=artifact_id)
    backup.write_record(source_id, source)

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
              "host_reachable": True, "host_port": 57422,
              "database": target_db, "sentinel": target_sentinel, "password": target_password,
              "role_passwords": passwords, "artifact_id": artifact_id, "key_id": key_id, "stage": stage.name,
              "source_ips": source_ips}
    backup.write_record(operation_id, record, create=True)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from isolated_resume import create_node, resume
    def prove_privacy(target):
        async def scenario():
            privacy_db, query_db, processor_db = (Database(attested_settings(target, role, pool_size=1, pool_overflow=0))
                                                  for role in ("privacy", "query", "processor"))
            sync = PrivacyRuntime(AuthorityClient(authority), privacy_db, store=PostgresPrivacyStore(privacy_db))
            gates = [PrivacyRuntime(AuthorityClient(authority), db) for db in (query_db, processor_db)]
            query_db.transaction_guard = gates[0].check_in_transaction
            processor_db.transaction_guard = gates[1].check_in_transaction
            try:
                with ExitStack() as stack:
                    apps = {role: stack.enter_context(TestClient(create_app(role, attested_settings(target, role, trust_manifest_json=trust, privacy_authority_json=authority_json,
                        pool_size=1, pool_overflow=0)))) for role in ("collector", "processor", "query")}
                    headers = {"Authorization": "Bearer " + grant_token(grant, deleted)}
                    for app in apps.values():
                        assert app.get("/ready").status_code == 503
                    assert apps["collector"].post("/api/analytics/events/batch", headers=headers, json={"events": []}).status_code == 503
                    wrong_key = authority.model_copy(update={"public_keys": {"a": pem(Ed25519PrivateKey.generate())}})
                    untrusted = PrivacyRuntime(AuthorityClient(wrong_key), query_db)
                    with pytest.raises(AuthorityProtocolError):
                        await untrusted.tick()
                    assert not await untrusted.is_ready()
                    with socket.socket() as sock:
                        sock.bind(("127.0.0.1", 0))
                        absent = authority.model_copy(update={"url": f"http://127.0.0.1:{sock.getsockname()[1]}"})
                        unavailable = PrivacyRuntime(AuthorityClient(absent), query_db)
                        with pytest.raises(AuthorityUnavailable):
                            await unavailable.tick()
                        assert not await unavailable.is_ready()
                    for gate in gates:
                        await observe(gate)
                        assert not await gate.is_ready()  # current signed HWM=1, restored prefix=0
                        assert gate._latest.frame.high_watermark == 1
                    assert isolated.psql(target, target["database"], "SELECT applied_through FROM analytics.privacy_state;") == "0"
                    with pytest.raises(PrivacyUnavailable):
                        await PostgresProcessor(processor_db).claim("synthetic.activity", 1, uuid4(), 1, 30)
                    with pytest.raises(PrivacyUnavailable):
                        await PostgresSyntheticCounts(query_db).read_counts("synthetic.activity", 1, uuid4(), deleted, ("backend",))
                    assert isolated.psql(target, target["database"],
                        f"SELECT count(*) FROM analytics.activity_facts WHERE subject_ref='{deleted}';") == "1"
                    restored_latest = datetime.fromisoformat(isolated.psql(target, target["database"],
                        "SELECT max(received_at)::text FROM analytics.activity_facts;"))
                    assert restored_latest == latest_fact
                    await observe(sync)
                    for gate in gates:
                        await observe(gate)
                        assert await gate.is_ready()
                    assert isolated.psql(target, target["database"],
                        f"SELECT count(*) FROM analytics.activity_facts WHERE subject_ref='{deleted}';") == "0"
                    assert isolated.psql(target, target["database"],
                        f"SELECT count(*) FROM analytics.activity_facts WHERE subject_ref='{survivor}';") == "1"
                    assert isolated.psql(target, target["database"],
                        f"SELECT deleted FROM analytics.subject_fences WHERE subject_ref='{deleted}';") == "t"
                    assert isolated.psql(target, target["database"], "SELECT applied_through FROM analytics.privacy_state;") == "1"
                    event = json.loads((Path(__file__).resolve().parents[2] / "contracts/fixtures/browser-batch.json").read_text())["events"][0]
                    now = datetime.now(timezone.utc).isoformat()
                    event.update(event_id=str(uuid4()), started_at=now, ended_at=now, last_action_at=now)
                    for app in apps.values():
                        deadline = time.monotonic() + 5
                        while app.get("/ready").status_code != 200 and time.monotonic() < deadline:
                            await asyncio.sleep(.05)
                        assert app.get("/ready").status_code == 200
                    fresh_headers = {"Authorization": "Bearer " + grant_token(grant, deleted)}
                    assert apps["collector"].post("/api/analytics/events/batch", headers=fresh_headers, json={"events": [event]}).status_code == 403
                    evidence = {"privacy_opened": True, "closed_before_current": True,
                                "authority_unavailable_closed": True, "untrusted_authority_closed": True,
                                "authority_high_watermark": 1, "restored_applied_before": 0, "applied_through": 1,
                                "deleted_subject_removed": True, "surviving_subject_preserved": True,
                                "local_recovery_seconds": time.perf_counter() - loss_started,
                                "snapshot_age_at_loss_seconds": max(0.0, (loss_at - snapshot_finished_at).total_seconds()),
                                "latest_committed_fact_gap_seconds": (latest_fact - restored_latest).total_seconds(),
                                "post_snapshot_fact_commits": 0, "post_snapshot_fact_commits_lost": 0}
                    print("signed_post_snapshot_deletion=true survivor_preserved=true current_barrier_opened=true "
                          f"local_recovery_seconds={evidence['local_recovery_seconds']:.3f} "
                          f"snapshot_age_at_loss_seconds={evidence['snapshot_age_at_loss_seconds']:.3f} "
                          "latest_committed_fact_gap_seconds=0 post_snapshot_fact_commits_lost=0")
                    return evidence
            finally:
                for db in (privacy_db, query_db, processor_db):
                    await db.close()
        return asyncio.run(scenario())

    # After loss, fail if recovery tries to use the original DB connector or name.
    source_container = source_name
    real_run = subprocess.run
    def forbid_source(command, *args, **kwargs):
        if isinstance(command, (list, tuple)) and source_container in command:
            raise AssertionError("source container referenced after loss")
        return real_run(command, *args, **kwargs)
    with patch.object(backup.db, "connect", side_effect=AssertionError("source connector used")), \
         patch.object(backup, "CONTAINER", "source-node-unavailable"), \
         patch.object(subprocess, "run", side_effect=forbid_source):
        create_node(record)
        resume(operation_id, image_id, expected_fact_count=2, after_parity=prove_privacy)
    # Delete only the same attested, stopped source node and its own network.
    if run(["inspect", "--format", '{{.Name}}|{{.Image}}|{{index .Config.Labels "knowi.analytics.fixture"}}|{{.State.Running}}', source_name]) != stopped:
        raise ValueError("source changed after proof")
    isolated.network_attest(source)
    run(["rm", source_name])
    isolated.network_attest(source)
    run(["network", "rm", source_name])
    source["state"] = "deleted_after_proof"
    backup.write_record(source_id, source)
