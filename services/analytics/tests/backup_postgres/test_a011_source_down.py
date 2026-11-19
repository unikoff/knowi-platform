"""Opt-in owned-node proof; failure preserves fixture state for reconciliation.

No retained Analytics DB or authority journal is used. The source PostgreSQL
container belongs exclusively to this test and is stopped before target creation.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

SERVICE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(SERVICE / "tools"), str(Path(__file__).parent), str(SERVICE / "tests/privacy_integration")]
import synthetic_backup as backup
import unseal_a011 as unseal
import isolated_resume as isolated
import test_a010_source_down as fixture
import a010_core_fixture as core_fixture
from test_signed_deletion_pipeline import producer_token
from pymentor_analytics.adapters.postgres.activity_projection import PostgresActivityProjection
from pymentor_analytics.adapters.postgres.activity_day_projection import PostgresActivityDayProjection
from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient
from pymentor_analytics.adapters.privacy_authority.journal import Journal
from pymentor_analytics.app.activity_projection_runtime import ActivityProjectionRuntime
from pymentor_analytics.app.activity_day_projection_runtime import ActivityDayProjectionRuntime
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.privacy import PrivacyRuntime
from pymentor_analytics.contracts.auth_v1 import VerifiedGrant
from pymentor_analytics.contracts.reports_v1 import ActivityQuery
from pymentor_analytics.contracts.v2 import AcceptedEvent, DeletionFence, canonical_hash

pytestmark = pytest.mark.skipif(os.environ.get("ANALYTICS_RUN_ISOLATED_A011_BACKUP") != "1",
                                reason="requires exclusive isolated a011 PostgreSQL slot")


def fingerprint():
    digest = hashlib.sha256()
    for path in (Path(__file__), SERVICE / "tools/unseal_a011.py",
                 SERVICE / "pymentor_analytics/adapters/postgres/activity_day_projection.py",
                 SERVICE / "pymentor_analytics/app/activity_day_projection_runtime.py"):
        digest.update(path.relative_to(SERVICE).as_posix().encode() + b"\0" + path.read_bytes())
    return fixture.source_fingerprint(), digest.hexdigest()


def guard(expected, *, prestart=False):
    if fingerprint() != expected:
        raise ValueError("source changed during a011 proof")
    fixture.resource_guard(expected[0], prestart=prestart)


def observation_gaps(*, snapshot_started, snapshot_finished, loss_confirmed, source_received, restored_received):
    """Wall-clock intervals describe received timestamps, not exact commit times."""
    values = (snapshot_started, snapshot_finished, loss_confirmed, source_received, restored_received)
    if any(not isinstance(value, datetime) or value.utcoffset() is None for value in values):
        raise ValueError("aware observation timestamps required")
    if snapshot_started > snapshot_finished or snapshot_finished > loss_confirmed or source_received < restored_received:
        raise ValueError("observation timestamps are out of order")
    return (loss_confirmed - snapshot_finished).total_seconds(), (source_received - restored_received).total_seconds()


def node(image, identity, ips):
    record = fixture.node(image, identity, ips)
    record["schema_revision"] = "a011"
    return record


def patches(record, local):
    fixture.fixture_patches(record, local)
    local.setattr(backup, "connection_config", lambda: {"host": "127.0.0.1", "port": 57392,
                  "user": "analytics_bootstrap", "password": record["password"]})
    local.setattr(unseal, "Database", lambda settings: Database(fixture.adjusted(settings)))


async def source_rebuild(urls, authority, run_id, check):
    owner = Database(fixture.adjusted(Settings(database_url=urls["migrate"], pool_size=1, pool_overflow=0)))
    privacy = Database(fixture.adjusted(Settings(database_url=urls["privacy"], pool_size=1, pool_overflow=0)))
    gate = PrivacyRuntime(AuthorityClient(authority), privacy, store=PostgresPrivacyStore(privacy))
    worker = None
    try:
        for _ in range(100):
            check()
            await gate.tick()
            if await gate.is_ready(): break
            await asyncio.sleep(.05)
        else: raise TimeoutError("source privacy gate not ready")
        owner.transaction_guard = gate.check_in_transaction
        for table, store, kind in (
            ("activity_projection_backfill", PostgresActivityProjection(owner, expected_run_id=run_id), ActivityProjectionRuntime),
            ("activity_projection_day_backfill", PostgresActivityDayProjection(owner, expected_run_id=run_id), ActivityDayProjectionRuntime),
        ):
            worker = kind(store, gate, enabled=True, operation_timeout=5)
            worker.start()
            deadline = time.monotonic() + 60
            while True:
                check()
                await gate.tick()
                async with owner.transaction() as session:
                    from sqlalchemy import text
                    phase = (await session.execute(text(f"SELECT run_id,phase FROM analytics.{table} WHERE singleton=true"))).one()
                if tuple(phase) == (run_id, "complete") and await gate.is_ready(): break
                if time.monotonic() >= deadline: raise TimeoutError("source projection did not complete")
                await asyncio.sleep(.05)
            await worker.stop()
            worker = None
    finally:
        if worker is not None: await worker.stop()
        await gate.stop()
        await owner.close()
        await privacy.close()


def cleanup_authority(folder):
    # Remove only files created by this test, after the server has stopped.
    parent = (backup.BASE / "operations").resolve()
    if folder.is_symlink() or folder.is_junction() or folder.resolve().parent != parent or not re.fullmatch(r"authority-[0-9a-f]{32}", folder.name):
        raise ValueError("unsafe authority cleanup")
    allowed = {"response.private.pem", "producer.private.pem", "command.private.pem", "grant.private.pem",
               "reader.secret", "authority-config.json", "independent-authority.sqlite",
               "independent-authority.sqlite-wal", "independent-authority.sqlite-shm"}
    files = list(folder.iterdir())
    if any(path.name not in allowed or path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1 for path in files):
        raise ValueError("unexpected authority cleanup contents")
    for path in files: path.unlink()
    folder.rmdir()


def test_a011_encrypted_source_down_rebuild_and_guarded_grant(monkeypatch):
    backup.check_k3s.local_docker_env()
    backup.tree()
    frozen = fingerprint()
    guard(frozen, prestart=True)
    image = isolated.docker(["image", "inspect", "--format", "{{.Id}}", "postgres:16"])
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image): raise ValueError("cached PostgreSQL image unavailable")
    fixture_id, artifact_id, key_id, target_id, source_id, target_node_id, authority_key_id = (str(uuid4()) for _ in range(7))
    folder = backup.BASE / "operations" / ("authority-" + uuid4().hex)
    folder.mkdir(mode=0o700)
    with fixture.authority_server(folder) as (authority, _raw, _trust, producer, command, _grant):
        authority_path = backup.path_for("keys", authority_key_id, ".authority.json")
        backup.new_file(authority_path, _raw.encode("utf-8"))
        source = node(image, source_id, ["192.0.2.1"])
        source.update(authority_dir=folder.name, backup_key_id=key_id, authority_key_id=authority_key_id)
        fixture.launch(source)
        source_ip = isolated.docker(["inspect", "--format", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", source["container"]])
        if not source_ip: raise ValueError("source address unavailable")
        source.update(source_ips=[source_ip], state="running")
        backup.write_record(source_id, source)
        urls = fixture.create_database(source)
        upgrade(fixture.adjusted(Settings(database_url=urls["migrate"], pool_size=1, pool_overflow=0)), target_revision="a011")
        # The deliberately corrupt trigger is tested on the pristine owned
        # source before it becomes a fixture or receives any data.
        with monkeypatch.context() as local:
            patches(source, local)
            pristine = {"kind": "restore", "state": "restore_pending", "history": backup.history_for_revision("a011"),
                        "database": source["database"], "nonce": source["sentinel"], "urls": urls}
            with backup.db.connect({}, source["database"]) as conn:
                conn.execute("DROP TRIGGER activity_projection_fact_inserted ON analytics.activity_facts")
                conn.execute("CREATE TRIGGER activity_projection_fact_inserted AFTER INSERT ON analytics.activity_facts FOR EACH ROW WHEN (NEW.source_kind='core_transfer_v1') EXECUTE FUNCTION analytics.activity_projection_fact_inserted()")
            before = backup.table_digests({}, pristine)
            with pytest.raises(ValueError, match="trigger definition differs"):
                backup.clear_fresh_defaults_a011({}, pristine)
            assert backup.table_digests({}, pristine) == before
            with backup.db.connect({}, source["database"]) as conn:
                assert conn.execute("SELECT has_database_privilege(%s,current_database(),'CONNECT')", (source["database"] + "_query",)).fetchone() == (True,)
                conn.execute("DROP TRIGGER activity_projection_fact_inserted ON analytics.activity_facts")
                conn.execute("CREATE TRIGGER activity_projection_fact_inserted AFTER INSERT ON analytics.activity_facts FOR EACH ROW WHEN (NEW.source_kind='browser_v2') EXECUTE FUNCTION analytics.activity_projection_fact_inserted()")
        old_run, deleted, survivor = uuid4(), uuid4(), uuid4()
        moscow_date = (datetime.now(timezone.utc) + timedelta(hours=3)).date() - timedelta(days=2)
        day_start = datetime(moscow_date.year, moscow_date.month, moscow_date.day, tzinfo=timezone.utc) - timedelta(hours=3)
        started = day_start + timedelta(hours=12)
        template = json.loads((SERVICE / "contracts/fixtures/browser-batch.json").read_text())["events"][1]
        with psycopg.connect(host="127.0.0.1", port=57422, user="analytics_bootstrap", password=source["password"],
                             dbname=source["database"], connect_timeout=3) as conn:
            conn.execute("SELECT analytics.begin_activity_projection_backfill(NULL,%s)", (old_run,))
            for subject in (deleted, survivor):
                event = {**template, "event_id": str(uuid4()), "session_id": str(uuid4()),
                         "started_at": started.isoformat(), "ended_at": (started + timedelta(seconds=60)).isoformat(),
                         "last_action_at": started.isoformat()}
                item = AcceptedEvent.model_validate_json(json.dumps({"producer_namespace": "synthetic-backup",
                    "subject_ref": str(subject), "source": "browser", "event": event,
                    "received_at": datetime.now(timezone.utc).isoformat()}))
                conn.execute("INSERT INTO analytics.activity_facts (producer_namespace,event_id,subject_ref,course_id,catalog_version,lesson_id,started_at,received_at,canonical_hash,hash_version,accepted_event) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s)",
                    (item.producer_namespace, item.event.event_id, item.subject_ref, item.event.course_id,
                     item.event.catalog_version, item.event.lesson_id, item.event.started_at, item.received_at,
                     canonical_hash(item), Jsonb(item.model_dump(mode="json"))))
                if subject == survivor:
                    expected_survivor_active_ms = item.event.active_ms
            core_fixture.seed(conn, deleted, survivor, sentinel=source["sentinel"])
        asyncio.run(source_rebuild(urls, authority, old_run, lambda: guard(frozen)))
        async def verify_source_core():
            processor = Database(fixture.adjusted(Settings(database_url=urls["processor"], pool_size=1, pool_overflow=0)))
            try:
                await core_fixture.verify(processor, deleted, survivor)
            finally:
                await processor.close()
        asyncio.run(verify_source_core())
        with monkeypatch.context() as local:
            patches(source, local)
            record = {"operation_id": fixture_id, "kind": "fixture", "state": "fixture_ready", "synthetic_only": True,
                      "do_not_drop": True, "privacy_opened": False, "database": source["database"], "nonce": source["sentinel"],
                      "urls": urls, "history": backup.history_for_revision("a011")}
            with backup.db.connect({}, source["database"]) as conn:
                assert conn.execute("SELECT count(*) FROM analytics.activity_projection_days WHERE run_id=%s", (old_run,)).fetchone() == (2,)
                snapshot_survivor_received_at = conn.execute(
                    "SELECT max(received_at) FROM analytics.activity_facts WHERE subject_ref=%s", (survivor,)).fetchone()[0]
                assert conn.execute("SELECT count(*) FROM analytics.activity_facts WHERE subject_ref=%s", (survivor,)).fetchone() == (4,)
            backup.write_record(fixture_id, record, create=True)
            backup.new_file(backup.path_for("keys", key_id, ".key"), os.urandom(32))
            backup.backup(fixture_id, artifact_id, key_id)
        # Authenticate and record the snapshot boundary before making one
        # controlled post-snapshot commit on the same surviving lifecycle.
        backup_done_monotonic = time.monotonic()
        preflight_header, preflight_stage = backup.crypto.decrypt_verified(backup.path_for("artifacts", artifact_id, ".enc"),
                                                                           backup.BASE / "staging", backup.key_for(key_id))
        snapshot_meta = preflight_header["metadata"]
        preflight_stage.unlink()
        if (snapshot_meta.get("source_id") != fixture_id or snapshot_meta.get("source_database") != source["database"]
                or snapshot_meta.get("history") != backup.history_for_revision("a011")
                or snapshot_meta.get("synthetic_only") is not True):
            raise ValueError("snapshot metadata differs")
        snapshot_started_at = datetime.fromisoformat(snapshot_meta["snapshot_started_at"])
        snapshot_finished_at = datetime.fromisoformat(snapshot_meta["snapshot_finished_at"])
        assert snapshot_started_at <= snapshot_finished_at
        late_start = started + timedelta(minutes=10)
        late_event = {**template, "event_id": str(uuid4()), "session_id": str(uuid4()), "active_ms": 30000,
                      "started_at": late_start.isoformat(), "ended_at": (late_start + timedelta(seconds=30)).isoformat(),
                      "last_action_at": late_start.isoformat()}
        late = AcceptedEvent.model_validate_json(json.dumps({"producer_namespace": "synthetic-backup",
            "subject_ref": str(survivor), "source": "browser", "event": late_event,
            "received_at": datetime.now(timezone.utc).isoformat()}))
        isolated.attest(source)
        with psycopg.connect(host="127.0.0.1", port=57422, user="analytics_bootstrap", password=source["password"],
                             dbname=source["database"], connect_timeout=3) as conn:
            core_fixture._guard(conn, source["sentinel"])
            late_received_at = conn.execute("""INSERT INTO analytics.activity_facts
                (producer_namespace,event_id,subject_ref,course_id,catalog_version,lesson_id,started_at,received_at,canonical_hash,hash_version,accepted_event)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s) RETURNING received_at""",
                (late.producer_namespace, late.event.event_id, late.subject_ref, late.event.course_id,
                 late.event.catalog_version, late.event.lesson_id, late.event.started_at, late.received_at, canonical_hash(late),
                 Jsonb(late.model_dump(mode="json")))).fetchone()[0]
            conn.commit()
            late_commit_monotonic = time.monotonic()
            late_commit_observed_at = datetime.now(timezone.utc)
            assert conn.execute("SELECT count(*) FROM analytics.activity_facts WHERE subject_ref=%s", (survivor,)).fetchone() == (5,)
            latest_source_received_at = conn.execute("SELECT max(received_at) FROM analytics.activity_facts WHERE subject_ref=%s",
                                                     (survivor,)).fetchone()[0]
            assert latest_source_received_at == late_received_at
        assert late_commit_monotonic > backup_done_monotonic
        async def append():
            deletion = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(), subject_ref=deleted,
                                     epoch=1, occurred_at=datetime.now(timezone.utc))
            assert (await AuthorityClient(authority).append(deletion, producer="synthetic",
                    producer_token=producer_token(producer), command_key=command)).seq == 1
        asyncio.run(append())
        high, entries = Journal(folder / "independent-authority.sqlite", authority.authority_id).snapshot(after=0)
        assert high == 1 and len(entries) == 1 and entries[0].control.subject_ref == deleted
        isolated.attest(source)
        source["state"] = "stop_pending"
        backup.write_record(source_id, source)
        isolated.docker(["stop", "--time", "10", source["container_id"]], timeout=20)
        if isolated.docker(["inspect", "--format", "{{.Id}}|{{.State.Running}}", source["container_id"]]) != source["container_id"] + "|false":
            raise ValueError("source loss not established")
        loss_confirmed_monotonic = time.monotonic()
        loss_confirmed_at = datetime.now(timezone.utc)
        source["state"] = "stopped_before_target"
        backup.write_record(source_id, source)
        header, stage = backup.crypto.decrypt_verified(backup.path_for("artifacts", artifact_id, ".enc"),
                                                       backup.BASE / "staging", backup.key_for(key_id))
        metadata = header["metadata"]
        expected = backup.history_for_revision("a011")
        if (metadata != snapshot_meta or metadata.get("history") != expected or metadata.get("synthetic_only") is not True
                or metadata.get("source_id") != fixture_id or metadata.get("source_database") != source["database"]
                or metadata.get("format") != "pg16-custom-analytics-data-only"
                or not re.fullmatch(r"[0-9a-f]{64}", metadata.get("fixture_state_sha256", ""))):
            stage.unlink(missing_ok=True)
            raise ValueError("invalid authenticated a011 artifact")
        # The public restore will independently authenticate again after loss.
        stage.unlink()
        target = node(image, target_node_id, [source_ip])
        target.update(authority_dir=folder.name, backup_key_id=key_id, authority_key_id=authority_key_id)
        guard(frozen, prestart=True)
        fixture.launch(target)
        target["state"] = "running"
        backup.write_record(target_node_id, target)
        unavailable = subprocess.run(["docker", "exec", target["container"], "pg_isready", "-h", source_ip, "-p", "5432", "-t", "2"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        if unavailable.returncode != 2 or target["database"] == source["database"]:
            raise ValueError("source isolation was not established")
        with monkeypatch.context() as local:
            patches(target, local)
            original_connect = psycopg.connect
            def guarded_connect(*args, **kwargs):
                if (kwargs.get("host") == source_ip or kwargs.get("dbname") == source["database"]
                        or any(isinstance(arg, str) and (source_ip in arg or source["database"] in arg) for arg in args)):
                    raise AssertionError("source connector accessed after loss")
                return original_connect(*args, **kwargs)
            local.setattr(psycopg, "connect", guarded_connect)
            original_popen = subprocess.Popen
            def guarded_popen(args, *extra, **options):
                if isinstance(args, (list, tuple)) and any(str(value) in {source_ip, source["container"], source["container_id"], source["database"]} for value in args):
                    raise AssertionError("source process accessed after loss")
                return original_popen(args, *extra, **options)
            local.setattr(subprocess, "Popen", guarded_popen)
            local.setattr(backup.db, "create", lambda _config, revision: (target["database"], target["sentinel"], fixture.create_database(target)))
            target_config = backup.connection_config()
            restore_started_monotonic = time.monotonic()
            with local.context() as cli:
                cli.setattr(sys, "argv", ["synthetic_backup.py", "restore", "--backup", artifact_id,
                                          "--key", key_id, "--restore", target_id])
                backup.main()
            restored = backup.read_record(target_id, "restore", "restored_unverified")
            assert restored["data_restored"] is True and restored["application_access"] == "revoked"
            backup.parity(target_id)
            restore_to_parity_seconds = time.monotonic() - restore_started_monotonic
            for role in (*unseal.ROLES, "privacy"):
                with pytest.raises(psycopg.OperationalError):
                    psycopg.connect(host="127.0.0.1", port=57422, user=target["database"] + "_" + role,
                                    password=target["role_passwords"][role], dbname=target["database"], connect_timeout=3)
            with pytest.raises(ValueError, match="new dataset identity"):
                asyncio.run(unseal.recover(target_id, authority, old_run, resource_check=lambda: guard(frozen)))
            run_id = uuid4()
            guard(frozen)
            timings = {}
            real_bucket_runtime, real_day_runtime = unseal.ActivityProjectionRuntime, unseal.ActivityDayProjectionRuntime
            real_guarded_reader = unseal.guarded_reader_check
            def timed_runtime(kind, implementation):
                class TimedRuntime:
                    def __init__(self, *args, **kwargs):
                        self.wrapped = implementation(*args, **kwargs)
                        self.started = None
                    def start(self):
                        self.started = time.monotonic()
                        self.wrapped.start()
                    async def stop(self):
                        await self.wrapped.stop()
                        if self.started is not None:
                            timings[kind + "_rebuild_seconds"] = time.monotonic() - self.started
                return TimedRuntime
            async def timed_guarded_reader(*args, **kwargs):
                await real_guarded_reader(*args, **kwargs)
                timings.setdefault("first_guarded_reader_monotonic", time.monotonic())
            with local.context() as cli:
                cli.setattr(unseal, "ActivityProjectionRuntime", timed_runtime("bucket", real_bucket_runtime))
                cli.setattr(unseal, "ActivityDayProjectionRuntime", timed_runtime("day", real_day_runtime))
                cli.setattr(unseal, "guarded_reader_check", timed_guarded_reader)
                cli.setattr(sys, "argv", ["unseal_a011.py", "--restore", target_id, "--authority", authority_key_id,
                                          "--run-id", str(run_id)])
                unseal.main()
            assert backup.read_record(target_id, "restore", "unsealed")["application_access"] == "granted"
            with pytest.raises(ValueError): asyncio.run(unseal.recover(target_id, authority, run_id))
            guard(frozen)
            with backup.db.connect(target_config, target["database"]) as conn:
                assert conn.execute("SELECT applied_through FROM analytics.privacy_state").fetchone() == (1,)
                for table in ("activity_facts", "activity_projection_buckets", "activity_projection_day_sources", "activity_projection_days"):
                    assert conn.execute(f"SELECT count(*) FROM analytics.{table} WHERE subject_ref=%s", (deleted,)).fetchone() == (0,)
                assert conn.execute("SELECT count(*) FROM analytics.activity_facts WHERE subject_ref=%s", (survivor,)).fetchone() == (4,)
                assert conn.execute("SELECT count(*) FROM analytics.activity_facts WHERE subject_ref=%s AND source_kind='browser_v2'", (survivor,)).fetchone() == (1,)
                assert conn.execute("SELECT count(*) FROM analytics.activity_facts WHERE producer_namespace=%s AND event_id=%s",
                                    (late.producer_namespace, late.event.event_id)).fetchone() == (0,)
                restored_survivor_received_at = conn.execute("SELECT max(received_at) FROM analytics.activity_facts WHERE subject_ref=%s",
                                                             (survivor,)).fetchone()[0]
                assert restored_survivor_received_at == snapshot_survivor_received_at
                assert conn.execute("SELECT run_id,phase FROM analytics.activity_projection_backfill").fetchone() == (run_id, "complete")
                assert conn.execute("SELECT run_id,phase FROM analytics.activity_projection_day_backfill").fetchone() == (run_id, "complete")
                assert conn.execute("SELECT count(*) FROM analytics.activity_projection_days WHERE subject_ref=%s AND run_id=%s", (survivor, run_id)).fetchone() == (1,)
                core_fixture.assert_restored(conn, deleted, survivor, sentinel=target["sentinel"])
            async def final_grant_and_reader():
                state = backup.read_record(target_id, "restore", "unsealed")
                owner = Database(fixture.adjusted(Settings(database_url=state["urls"]["migrate"], pool_size=1, pool_overflow=0)))
                privacy = Database(fixture.adjusted(Settings(database_url=state["urls"]["privacy"], pool_size=1, pool_overflow=0)))
                gate = PrivacyRuntime(AuthorityClient(authority), privacy, store=PostgresPrivacyStore(privacy))
                reader = None
                try:
                    await gate.tick()
                    assert await gate.is_ready()
                    owner.transaction_guard = gate.check_in_transaction
                    await gate.tick()
                    assert await gate.is_ready()
                    await unseal.reconcile_days(owner, PostgresActivityDayProjection(owner, expected_run_id=run_id), run_id,
                                                deadline=time.monotonic() + 30)
                    async with owner.transaction() as session:
                        from sqlalchemy import text
                        await unseal._ready_state(session, run_id, target["database"])
                        for role in unseal.ROLES:
                            assert (await session.execute(text("SELECT has_database_privilege(:role,current_database(),'CONNECT')"),
                                      {"role": target["database"] + "_" + role})).scalar_one() is True
                    reader = Database(fixture.adjusted(Settings(database_url=state["urls"]["query"], pool_size=1, pool_overflow=0)))
                    reader.transaction_guard = gate.check_in_transaction
                    grant = VerifiedGrant(sub=survivor, role="user", scope="report:read", course_ids=("backend-copy",),
                                          subject_refs=(), global_read=False, expires_at=int(time.time()) + 60)
                    query = ActivityQuery(from_time=day_start, to_time=day_start + timedelta(days=1), course_id=None, subject_ref=None)
                    report = await PostgresProjectionActivityReport(reader, None, expected_run_id=run_id,
                                day_projection_enabled=True).read_activity(grant, query)
                    assert report["totals"]["unique_visitors"] == 1 and report["totals"]["active_ms"] == expected_survivor_active_ms
                    await core_fixture.assert_reader(reader, survivor)
                    timings["positive_browser_and_core_reader_monotonic"] = time.monotonic()
                finally:
                    if reader is not None: await reader.close()
                    await gate.stop()
                    await privacy.close()
                    await owner.close()
            asyncio.run(final_grant_and_reader())
            snapshot_age, received_gap = observation_gaps(snapshot_started=snapshot_started_at,
                snapshot_finished=snapshot_finished_at, loss_confirmed=loss_confirmed_at,
                source_received=latest_source_received_at, restored_received=restored_survivor_received_at)
            assert timings["first_guarded_reader_monotonic"] >= loss_confirmed_monotonic
            assert timings["positive_browser_and_core_reader_monotonic"] >= timings["first_guarded_reader_monotonic"]
            metrics = {"snapshot_started_at": snapshot_started_at.isoformat(),
                       "snapshot_finished_at": snapshot_finished_at.isoformat(),
                       "loss_confirmed_at": loss_confirmed_at.isoformat(),
                       "latest_snapshot_survivor_fact_received_at": snapshot_survivor_received_at.isoformat(),
                       "latest_source_fact_received_at": latest_source_received_at.isoformat(),
                       "latest_source_commit_observed_at": late_commit_observed_at.isoformat(),
                       "latest_restored_survivor_fact_received_at": restored_survivor_received_at.isoformat(),
                       "snapshot_age_at_confirmed_loss_seconds": round(snapshot_age, 6),
                       "source_vs_restored_fact_received_gap_seconds": round(received_gap, 6),
                       "controlled_post_snapshot_commits": 1, "controlled_late_facts_lost": 1,
                       "survivor_facts_at_snapshot": 4, "survivor_facts_before_loss": 5,
                       "survivor_facts_restored": 4, "survivor_browser_active_ms": expected_survivor_active_ms,
                       "storage_restore_to_parity_seconds": round(restore_to_parity_seconds, 6),
                       "bucket_worker_rebuild_seconds": round(timings["bucket_rebuild_seconds"], 6),
                       "day_worker_rebuild_seconds": round(timings["day_rebuild_seconds"], 6),
                       "loss_to_first_guarded_reader_seconds": round(timings["first_guarded_reader_monotonic"] - loss_confirmed_monotonic, 6),
                       "loss_to_positive_browser_and_core_reader_seconds": round(timings["positive_browser_and_core_reader_monotonic"] - loss_confirmed_monotonic, 6)}
        target["state"] = "cleanup_pending"
        backup.write_record(target_node_id, target)
        fixture.owned_remove(target)
        target["state"] = "deleted_after_proof"
        backup.write_record(target_node_id, target)
        source["state"] = "cleanup_pending"
        backup.write_record(source_id, source)
        fixture.owned_remove(source, already_stopped=True)
        source["state"] = "deleted_after_proof"
        backup.write_record(source_id, source)
    cleanup_authority(folder)
    if authority_path.is_symlink() or authority_path.is_junction() or authority_path.stat().st_nlink != 1:
        raise ValueError("unsafe authority config cleanup")
    authority_path.unlink()
    print(json.dumps({"fixture_recovery_measurements": metrics}, sort_keys=True), flush=True)
