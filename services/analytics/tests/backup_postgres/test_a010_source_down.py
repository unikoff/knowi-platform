"""Opt-in: encrypted a010 backup, source loss, quarantined restore and unseal.

Only a nonce-owned temporary Docker PostgreSQL on 127.0.0.1:57422 is used.
On failure, preserve the exact fixture state for reconciliation.
"""
import asyncio
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import patch
from urllib.parse import quote
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest
from pydantic import SecretStr
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import uvicorn

SERVICE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(SERVICE / "tools"), str(Path(__file__).parent), str(SERVICE / "tests/privacy_integration")]
import synthetic_backup as backup
import unseal_a010 as unseal
import isolated_resume as isolated
import run_local
import a010_core_fixture as core_fixture
from test_signed_deletion_pipeline import producer_token, pem
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
from pymentor_analytics.adapters.privacy_authority.journal import Journal
from pymentor_analytics.adapters.privacy_authority.server import BoundedFixtureProtocol, create_authority_app
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.privacy import PrivacyRuntime
from pymentor_analytics.contracts.v2 import AcceptedEvent, DeletionFence, canonical_hash

pytestmark = pytest.mark.skipif(os.environ.get("ANALYTICS_RUN_ISOLATED_A010_BACKUP") != "1",
                                reason="requires exclusive isolated a010 PostgreSQL slot")


@contextmanager
def authority_server(folder):
    """Persist this synthetic authority's keys so an interrupted run is reconcilable."""
    response, producer, command, grant = (Ed25519PrivateKey.generate() for _ in range(4))
    secret = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
    for name, key in (("response", response), ("producer", producer), ("command", command), ("grant", grant)):
        raw = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        backup.new_file(folder / (name + ".private.pem"), raw)
    backup.new_file(folder / "reader.secret", secret.encode("ascii"))
    authority_id = "independent-synthetic-authority"
    journal = Journal.initialize(folder / "independent-authority.sqlite", authority_id)
    app = create_authority_app(journal, issuer="authority-issuer", kid="a", signing_key=response,
            producer_issuer="control-issuer", producer_public_keys={"p": pem(producer).encode()},
            allowed_producers={"synthetic"}, producer_command_public_keys={"synthetic": pem(command).encode()},
            reader_secret=secret)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, http=BoundedFixtureProtocol, ws="none", backlog=16,
                         limit_concurrency=16, proxy_headers=False, h11_max_incomplete_event_size=8192,
                         log_level="error", access_log=False))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        if not server.started:
            server.should_exit = True
            thread.join(timeout=5)
            raise RuntimeError("synthetic authority did not start")
        raw = json.dumps({"url": f"http://127.0.0.1:{port}", "authority_id": authority_id,
                          "issuer": "authority-issuer", "public_keys": {"a": pem(response)},
                          "reader_secret": secret, "fixture_mode": True})
        backup.new_file(folder / "authority-config.json", raw.encode("utf-8"))
        authority = AuthorityConfig.from_json(raw)
        try:
            yield authority, raw, None, producer, command, grant
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError("synthetic authority stop unverified")


def source_fingerprint():
    files = [Path(__file__), SERVICE / "tools/synthetic_backup.py", SERVICE / "tools/unseal_a010.py",
             SERVICE / "tools/bootstrap_postgres.py", SERVICE / "tools/bootstrap_runtime.py",
             SERVICE / "tools/run_local.py", SERVICE / "tools/check_k3s.py",
             SERVICE / "contracts/fixtures/browser-batch.json", SERVICE / "tests/backup_postgres/isolated_resume.py",
             SERVICE / "tests/backup_postgres/a010_core_fixture.py",
             SERVICE / "tests/privacy_integration/test_signed_deletion_pipeline.py"]
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(SERVICE).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
    return {"fixtures": digest.hexdigest(), "runtime": run_local.runtime_files_fingerprint(SERVICE),
            "history": backup.runtime.schema_history()[2]}


def resource_guard(frozen, *, prestart=False):
    if source_fingerprint() != frozen:
        raise ValueError("source changed during isolated proof")
    available = backup.check_k3s.available_memory()
    disk = shutil.disk_usage(backup.BASE).free
    required = (2048 if prestart else 1536) * 1024**2
    if available < required or disk < 8192 * 1024**2:
        raise ValueError(f"insufficient isolated proof resources: available={available}, required={required}, disk_free={disk}")


def node(image, operation, source_ips):
    nonce = uuid4().hex
    name = "knowi-analytics-restore-" + nonce
    return {"operation_id": operation, "kind": "isolated_node", "state": "provisioning",
            "synthetic_only": True, "do_not_drop": True, "privacy_opened": False,
            "schema_revision": "a010", "nonce": nonce, "network": name, "container": name,
            "postgres_image": image, "host_reachable": True, "host_port": 57422,
            "database": "analytics_test_" + uuid4().hex, "sentinel": os.urandom(32).hex(),
            "password": os.urandom(32).hex(),
            "role_passwords": {role: os.urandom(32).hex() for role in backup.db.roles_for_revision("a010")},
            "source_ips": source_ips}


def launch(record):
    backup.write_record(record["operation_id"], record, create=True)
    isolated.memory_guard()
    isolated.create_node(record)
    limit = time.monotonic() + 45
    while time.monotonic() < limit:
        result = subprocess.run(["docker", "exec", record["container"], "pg_isready", "-U", "analytics_bootstrap"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        if result.returncode == 0:
            isolated.attest(record)
            container_id = isolated.docker(["inspect", "--format", "{{.Id}}", record["container"]])
            network_id = isolated.docker(["network", "inspect", "--format", "{{.Id}}", record["network"]])
            if not re.fullmatch(r"[0-9a-f]{64}", container_id) or not re.fullmatch(r"[0-9a-f]{64}", network_id):
                raise ValueError("fixture exact identities unavailable")
            record.update(container_id=container_id, network_id=network_id)
            backup.write_record(record["operation_id"], record)
            return
        time.sleep(.25)
    raise TimeoutError("fixture PostgreSQL did not become ready")


def create_database(record):
    isolated.attest(record)
    name = record["database"]
    statements = []
    for role, password in record["role_passwords"].items():
        statements.append(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;")
                          .format(sql.Identifier(name + "_" + role), sql.Literal(password)).as_string())
    statements.extend([sql.SQL("CREATE DATABASE {} OWNER {};").format(sql.Identifier(name), sql.Identifier(name + "_migrate")).as_string(),
                       sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC;").format(sql.Identifier(name)).as_string()])
    statements.extend(sql.SQL("GRANT CONNECT ON DATABASE {} TO {};").format(sql.Identifier(name), sql.Identifier(name + "_" + role)).as_string()
                      for role in record["role_passwords"])
    isolated.psql(record, "analytics_bootstrap", "\n".join(statements))
    isolated.psql(record, name, sql.SQL("CREATE TABLE public.analytics_test_sentinel (token text NOT NULL); "
                  "INSERT INTO public.analytics_test_sentinel VALUES ({}); REVOKE ALL ON public.analytics_test_sentinel FROM PUBLIC; "
                  "GRANT SELECT ON public.analytics_test_sentinel TO {};")
                  .format(sql.Literal(record["sentinel"]), sql.Identifier(name + "_migrate")).as_string())
    with psycopg.connect(host="127.0.0.1", port=57422, user="analytics_bootstrap", password=record["password"],
                         dbname=name, connect_timeout=3) as conn:
        for role in record["role_passwords"]:
            allowed = conn.execute("SELECT has_table_privilege(%s,'public.analytics_test_sentinel','SELECT')",
                                   (name + "_" + role,)).fetchone()[0]
            if allowed != (role == "migrate"):
                raise ValueError("fixture sentinel role privilege mismatch")
        public = conn.execute("SELECT EXISTS (SELECT 1 FROM pg_class c, aclexplode(c.relacl) a "
                              "WHERE c.oid='public.analytics_test_sentinel'::regclass AND a.grantee=0 AND a.privilege_type='SELECT')").fetchone()[0]
        if public:
            raise ValueError("fixture sentinel publicly readable")
    return {role: f"postgresql://{name}_{role}:{quote(password, safe='')}@127.0.0.1:57392/{name}"
            for role, password in record["role_passwords"].items()}


def adjusted(settings):
    raw = settings.database_url.get_secret_value()
    if "@127.0.0.1:57392/analytics_test_" not in raw:
        raise ValueError("unexpected fixture URL")
    return settings.model_copy(update={"database_url": SecretStr(raw.replace("@127.0.0.1:57392/", "@127.0.0.1:57422/", 1))})


def fixture_patches(record, monkeypatch):
    name = record["database"]
    isolated.attest(record)
    def connect(_config, database=None):
        if database not in (None, name):
            raise ValueError("fixture database mismatch")
        return psycopg.connect(host="127.0.0.1", port=57422, user="analytics_bootstrap",
                               password=record["password"], dbname=database or "analytics_bootstrap",
                               connect_timeout=3, autocommit=True)
    def validate_db(candidate, config):
        expected = {role: f"postgresql://{name}_{role}:{quote(password, safe='')}@127.0.0.1:57392/{name}"
                    for role, password in record["role_passwords"].items()}
        if candidate["database"] != name or candidate["nonce"] != record["sentinel"] or candidate["urls"] != expected:
            raise ValueError("fixture metadata mismatch")
        with connect(config, name) as conn:
            if conn.execute("SELECT token FROM public.analytics_test_sentinel").fetchall() != [(record["sentinel"],)]:
                raise ValueError("fixture sentinel mismatch")
            owner = conn.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone()
            if owner != (name + "_migrate",):
                raise ValueError("fixture owner mismatch")
            return conn.execute("SELECT version_num FROM public.alembic_version").fetchall()
    monkeypatch.setattr(backup.db, "connect", connect)
    monkeypatch.setattr(backup, "validate_db", validate_db)
    monkeypatch.setattr(backup, "connection_config", lambda: {"isolated": True, "password": record["password"]})
    monkeypatch.setattr(backup, "verify_container", lambda: isolated.attest(record))
    monkeypatch.setattr(backup, "CONTAINER", record["container"])
    monkeypatch.setattr(backup, "upgrade", lambda settings, **kwargs: upgrade(adjusted(settings), **kwargs))
    monkeypatch.setattr(unseal, "Database", lambda settings: Database(adjusted(settings)))


def owned_remove(record, *, already_stopped=False):
    if not already_stopped:
        isolated.attest(record)
    isolated.network_attest(record)
    fmt = '{{.Id}}|{{.Name}}|{{.Image}}|{{index .Config.Labels "knowi.analytics.fixture"}}|{{.State.Running}}'
    expected = f"{record['container_id']}|/{record['container']}|{record['postgres_image']}|{record['nonce']}|" + ("false" if already_stopped else "true")
    if isolated.docker(["inspect", "--format", fmt, record["container_id"]]) != expected:
        raise ValueError("owned fixture identity mismatch")
    if isolated.docker(["network", "inspect", "--format", "{{.Id}}", record["network"]]) != record["network_id"]:
        raise ValueError("owned network identity mismatch")
    if not already_stopped:
        isolated.docker(["stop", "--time", "10", record["container_id"]], timeout=20)
    expected = f"{record['container_id']}|/{record['container']}|{record['postgres_image']}|{record['nonce']}|false"
    if isolated.docker(["inspect", "--format", fmt, record["container_id"]]) != expected:
        raise ValueError("owned fixture stop not verified")
    isolated.docker(["rm", record["container_id"]])
    result = subprocess.run(["docker", "inspect", "--format", "{{.Id}}", record["container_id"]], capture_output=True, timeout=10)
    if result.returncode == 0:
        raise ValueError("owned container still present")
    isolated.network_attest(record)
    attached = json.loads(isolated.docker(["network", "inspect", "--format", "{{json .Containers}}", record["network_id"]]))
    if attached not in ({}, None):
        raise ValueError("owned network has other attachments")
    isolated.docker(["network", "rm", record["network_id"]])
    result = subprocess.run(["docker", "network", "inspect", "--format", "{{.Id}}", record["network_id"]], capture_output=True, timeout=10)
    if result.returncode == 0:
        raise ValueError("owned network still present")


def test_a010_encrypted_restore_after_source_loss(monkeypatch):
    backup.check_k3s.local_docker_env()
    backup.tree()
    frozen = source_fingerprint()
    resource_guard(frozen, prestart=True)
    print("source_fingerprint=" + json.dumps(frozen, sort_keys=True), flush=True)
    image = isolated.docker(["image", "inspect", "--format", "{{.Id}}", "postgres:16"])
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("cached PostgreSQL image unavailable")
    fixture_id, artifact_id, key_id, target_id, source_node_id, target_node_id = (str(uuid4()) for _ in range(6))
    authority_folder = backup.BASE / "operations" / ("authority-" + uuid4().hex)
    authority_folder.mkdir(mode=0o700)
    with authority_server(authority_folder) as (authority, _raw, _trust, producer, command, _grant):
        source = node(image, source_node_id, ["192.0.2.1"])
        source.update(authority_dir=authority_folder.name, backup_key_id=key_id)
        resource_guard(frozen, prestart=True)
        launch(source)
        resource_guard(frozen)
        source_ip = isolated.docker(["inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", source["container"]])
        if not source_ip:
            raise ValueError("source address unavailable")
        source.update(source_ips=[source_ip], state="running")
        backup.write_record(source_node_id, source)
        urls = create_database(source)
        upgrade(adjusted(Settings(database_url=urls["migrate"], pool_size=1, pool_overflow=0)), target_revision="a010")
        old_run, deleted, survivor = uuid4(), uuid4(), uuid4()
        async def initialize():
            privacy_db = Database(adjusted(Settings(database_url=urls["privacy"], pool_size=1, pool_overflow=0)))
            gate = PrivacyRuntime(AuthorityClient(authority), privacy_db, store=PostgresPrivacyStore(privacy_db))
            try:
                for _ in range(20):
                    await gate.tick()
                    if await gate.is_ready(): return
                    await asyncio.sleep(.05)
                raise TimeoutError("source authority not ready")
            finally:
                await gate.stop()
                await privacy_db.close()
        asyncio.run(initialize())
        template = json.loads((SERVICE / "contracts/fixtures/browser-batch.json").read_text())["events"][0]
        with psycopg.connect(host="127.0.0.1", port=57422, user="analytics_bootstrap", password=source["password"],
                             dbname=source["database"], connect_timeout=3) as conn:
            conn.execute("SELECT analytics.begin_activity_projection_backfill(NULL,%s)", (old_run,))
            for subject in (deleted, survivor):
                now = datetime.now(timezone.utc).replace(microsecond=0)
                event = {**template, "event_id": str(uuid4()), "session_id": str(uuid4()),
                         "started_at": now.isoformat(), "ended_at": now.isoformat(), "last_action_at": now.isoformat()}
                item = AcceptedEvent.model_validate_json(json.dumps({"producer_namespace": "synthetic-backup", "subject_ref": str(subject),
                                                     "source": "browser", "event": event, "received_at": now.isoformat()}))
                conn.execute("INSERT INTO analytics.activity_facts (producer_namespace,event_id,subject_ref,course_id,catalog_version,lesson_id,started_at,received_at,canonical_hash,hash_version,accepted_event) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s)",
                             (item.producer_namespace, item.event.event_id, item.subject_ref, item.event.course_id, item.event.catalog_version,
                              item.event.lesson_id, item.event.started_at, item.received_at, canonical_hash(item), Jsonb(item.model_dump(mode="json"))))
            core_fixture.seed(conn, deleted, survivor, sentinel=source["sentinel"])
        async def verify_source_core():
            processor_db = Database(adjusted(Settings(database_url=urls["processor"], pool_size=1, pool_overflow=0)))
            try:
                await core_fixture.verify(processor_db, deleted, survivor)
            finally:
                await processor_db.close()
        asyncio.run(verify_source_core())
        with monkeypatch.context() as local:
            fixture_patches(source, local)
            record = {"operation_id": fixture_id, "kind": "fixture", "state": "fixture_ready", "synthetic_only": True,
                      "do_not_drop": True, "privacy_opened": False, "database": source["database"], "nonce": source["sentinel"],
                      "urls": urls, "history": backup.history()}
            backup.write_record(fixture_id, record, create=True)
            backup.new_file(backup.path_for("keys", key_id, ".key"), os.urandom(32))
            resource_guard(frozen)
            backup.backup(fixture_id, artifact_id, key_id)
        source.update(state="backup_verified", artifact_id=artifact_id)
        backup.write_record(source_node_id, source)
        async def delete_after_backup():
            deletion = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(), subject_ref=deleted,
                                     epoch=1, occurred_at=datetime.now(timezone.utc))
            receipt = await AuthorityClient(authority).append(deletion, producer="synthetic",
                       producer_token=producer_token(producer), command_key=command)
            assert receipt.seq == 1
        asyncio.run(delete_after_backup())
        resource_guard(frozen)
        isolated.attest(source)
        source["state"] = "stop_pending"
        backup.write_record(source_node_id, source)
        loss_started = time.monotonic()
        isolated.docker(["stop", "--time", "10", source["container_id"]], timeout=20)
        if isolated.docker(["inspect", "--format", "{{.Id}}|{{.State.Running}}", source["container_id"]]) != source["container_id"] + "|false":
            raise ValueError("source loss not established")
        source["state"] = "stopped_before_target"
        backup.write_record(source_node_id, source)
        target = node(image, target_node_id, [source_ip])
        target.update(authority_dir=authority_folder.name, backup_key_id=key_id)
        resource_guard(frozen, prestart=True)
        launch(target)
        resource_guard(frozen)
        target["state"] = "running"
        backup.write_record(target_node_id, target)
        unavailable = subprocess.run(["docker", "exec", target["container"], "pg_isready", "-h", source_ip,
                                      "-p", "5432", "-t", "2"], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, timeout=5)
        if unavailable.returncode != 2:
            raise ValueError("source isolation was not established")
        if target["database"] == source["database"]:
            raise ValueError("source and target alias")
        with monkeypatch.context() as local:
            fixture_patches(target, local)
            local.setattr(backup.db, "create", lambda _config, revision: (target["database"], target["sentinel"], create_database(target)))
            resource_guard(frozen)
            backup.restore(artifact_id, key_id, target_id)
            resource_guard(frozen)
            backup.parity(target_id)
            restored = backup.read_record(target_id, "restore", "parity_verified")
            assert restored["application_access"] == "revoked"
            with backup.db.connect({}, target["database"]) as conn:
                assert conn.execute("SELECT applied_through FROM analytics.privacy_state").fetchone() == (0,)
                assert conn.execute("SELECT count(*) FROM analytics.activity_projection_buckets").fetchone()[0] == 2
            for role in unseal.ROLES:
                with pytest.raises(psycopg.OperationalError):
                    psycopg.connect(host="127.0.0.1", port=57422, user=target["database"] + "_" + role,
                                    password=target["role_passwords"][role], dbname=target["database"], connect_timeout=3)
            with pytest.raises(ValueError, match="identity invalid"):
                asyncio.run(unseal.recover(target_id, authority, old_run, resource_check=lambda: resource_guard(frozen)))
            assert backup.read_record(target_id, "restore", "parity_verified")["application_access"] == "revoked"
            fresh_run = uuid4()
            assert fresh_run != old_run
            result = asyncio.run(unseal.recover(target_id, authority, fresh_run, resource_check=lambda: resource_guard(frozen)))
            assert result["status"] == "unsealed" and result["privacy_opened"] is False
            with backup.db.connect({}, target["database"]) as conn:
                assert conn.execute("SELECT applied_through FROM analytics.privacy_state").fetchone() == (1,)
                assert conn.execute("SELECT count(*) FROM analytics.activity_facts WHERE subject_ref=%s", (deleted,)).fetchone() == (0,)
                assert conn.execute("SELECT count(*) FROM analytics.activity_facts WHERE subject_ref=%s AND source_kind='browser_v2'", (survivor,)).fetchone() == (1,)
                assert conn.execute("SELECT run_id,phase FROM analytics.activity_projection_backfill").fetchone() == (fresh_run, "complete")
                assert conn.execute("SELECT count(*) FROM analytics.activity_projection_buckets WHERE state<>'ready'").fetchone() == (0,)
                core_fixture.assert_restored(conn, deleted, survivor, sentinel=target["sentinel"])
            for role in unseal.ROLES:
                with psycopg.connect(host="127.0.0.1", port=57422, user=target["database"] + "_" + role,
                                     password=target["role_passwords"][role], dbname=target["database"], connect_timeout=3) as conn:
                    assert conn.execute("SELECT current_user").fetchone() == (target["database"] + "_" + role,)
            async def verify_restored_reader():
                query_db = Database(adjusted(Settings(database_url=restored["urls"]["query"], pool_size=1, pool_overflow=0)))
                gate = PrivacyRuntime(AuthorityClient(authority), query_db)
                try:
                    await gate.tick()
                    if not await gate.is_ready():
                        raise ValueError("restored Query privacy gate not ready")
                    query_db.transaction_guard = gate.check_in_transaction
                    await core_fixture.assert_reader(query_db, survivor)
                finally:
                    await gate.stop()
                    await query_db.close()
            asyncio.run(verify_restored_reader())
            loss_to_reader_seconds = time.monotonic() - loss_started
        target["state"] = "cleanup_pending"
        backup.write_record(target_node_id, target)
        owned_remove(target)
        target["state"] = "deleted_after_proof"
        backup.write_record(target_node_id, target)
        # Source remains stopped until the exact identity is reattested.
        source["state"] = "cleanup_pending"
        backup.write_record(source_node_id, source)
        owned_remove(source, already_stopped=True)
        source["state"] = "deleted_after_proof"
        backup.write_record(source_node_id, source)
        print(json.dumps({"status": "passed", "artifact_id": artifact_id, "restore_id": target_id,
                          "source_container_id": source["container_id"], "source_network_id": source["network_id"],
                          "target_container_id": target["container_id"], "target_network_id": target["network_id"],
                          "post_snapshot_journal_hwm": 1, "new_run_id": str(fresh_run),
                          "rebuild_seconds": result["rebuild_seconds"],
                          "loss_to_reader_seconds": loss_to_reader_seconds, "cleanup_verified": True}), flush=True)
