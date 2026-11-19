"""Real signed HTTP authority + PG + operation gate; synthetic identities only.

The logical snapshot transplant tests deletion ordering. It is not a pg_dump,
encrypted backup, off-node recovery or RTO/RPO test (those belong to CORP-65).
"""
import asyncio
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import threading
import time
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
import jwt
from psycopg import sql
import pytest
import uvicorn

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
from pymentor_analytics.adapters.postgres.processor import PostgresProcessor, PostgresSyntheticCounts
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
from pymentor_analytics.adapters.privacy_authority.journal import Journal
from pymentor_analytics.adapters.privacy_authority.server import BoundedFixtureProtocol, create_authority_app
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.factory import create_app
from pymentor_analytics.app.privacy import PrivacyRuntime
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.privacy_control_v1 import AuthorityProtocolError, AuthorityUnavailable
from pymentor_analytics.contracts.processing_v1 import synthetic_delta
from pymentor_analytics.contracts.v2 import DeletionFence
from tools.bootstrap_postgres import cleanup, config, connect, create

ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = "independent-synthetic-authority"
TABLES = ("subject_fences", "activity_facts", "processing_jobs", "processing_leases",
          "processing_receipts", "processing_results", "processing_replays", "admission_budget",
          "admission_subject_windows", "privacy_state", "privacy_receipts")


def pem(key):
    return key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode("ascii")


@contextmanager
def authority_server(folder):
    response, producer, command, grant = (Ed25519PrivateKey.generate() for _ in range(4))
    secret = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
    journal = Journal.initialize(folder / "independent-authority.sqlite", AUTHORITY)
    app = create_authority_app(journal, issuer="authority-issuer", kid="a", signing_key=response,
                               producer_issuer="control-issuer", producer_public_keys={"p": pem(producer).encode()},
                               allowed_producers={"synthetic"}, producer_command_public_keys={"synthetic": pem(command).encode()},
                               reader_secret=secret)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, http=BoundedFixtureProtocol, ws="none", backlog=16,
                                              limit_concurrency=16, proxy_headers=False,
                                              h11_max_incomplete_event_size=8192, log_level="error", access_log=False))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        deadline = time.monotonic()+5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        authority = AuthorityConfig.from_json(json.dumps({"url": f"http://127.0.0.1:{port}",
            "authority_id": AUTHORITY, "issuer": "authority-issuer", "public_keys": {"a": pem(response)},
            "reader_secret": secret, "fixture_mode": True}))
        raw = json.dumps({"url": authority.url, "authority_id": AUTHORITY, "issuer": "authority-issuer",
                          "public_keys": {"a": pem(response)}, "reader_secret": secret, "fixture_mode": True})
        trust = json.dumps({"grant": {"issuer": "grant-issuer", "keys": {"g": pem(grant)}},
                            "producer": {"issuer": "control-issuer", "keys": {"p": pem(producer)}, "allowlist": ["synthetic"]}})
        try:
            yield authority, raw, trust, producer, command, grant
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()


def database(url):
    return Database(Settings(database_url=url, pool_size=2, pool_overflow=0))


def grant_token(key, subject):
    now = int(time.time())
    return jwt.encode({"grant_version": 1, "iss": "grant-issuer", "aud": "analytics", "sub": str(subject),
        "iat": now-1, "nbf": now-1, "exp": now+100, "role": "user", "scope": "collect",
        "course_ids": ["backend"], "subject_refs": [], "global_read": False}, key, algorithm="EdDSA", headers={"kid": "g", "typ": "JWT"})


def producer_token(key):
    now = int(time.time())
    return jwt.encode({"grant_version": 1, "iss": "control-issuer", "aud": "analytics-control", "sub": "synthetic",
        "iat": now-1, "nbf": now-1, "exp": now+40, "scope": "control:write"}, key, algorithm="EdDSA", headers={"kid": "p", "typ": "JWT"})


def old_logical_snapshot(data, name):
    with connect(data, name) as connection:
        return {table: [row[0] for row in connection.execute(sql.SQL("SELECT row_to_json(t)::text FROM analytics.{} t").format(sql.Identifier(table)))]
                for table in TABLES}


def transplant_to_fresh_database(data, name, sentinel, snapshot):
    # Never truncates an existing runtime target: this target was freshly created
    # by this fixture and retains its distinct public sentinel and role owner.
    with connect(data, name) as connection:
        assert connection.execute("SELECT token FROM public.analytics_test_sentinel").fetchall() == [(sentinel,)]
        names = sql.SQL(",").join(sql.Identifier("analytics", table) for table in TABLES)
        connection.execute(sql.SQL("TRUNCATE {} CASCADE").format(names))
        for table in TABLES:
            for record in snapshot[table]:
                connection.execute(sql.SQL("INSERT INTO analytics.{} SELECT (jsonb_populate_record(NULL::analytics.{}, %s::jsonb)).*")
                                   .format(sql.Identifier(table), sql.Identifier(table)), (record,))


async def observe(runtime):
    for _ in range(20):
        try:
            await runtime.tick()
            return
        except (OSError, PrivacyUnavailable, AuthorityUnavailable):
            await asyncio.sleep(.05)
    raise AssertionError("bounded authority observation did not succeed")


def test_signed_intake_delete_and_old_snapshot_cannot_open_before_current_journal(tmp_path):
    if os.environ.get("ANALYTICS_RUN_SYNTHETIC_PRIVACY_INTEGRATION") != "1":
        pytest.skip("explicit signed privacy integration runner required")
    data = config(ROOT / ".local/postgres-connection.json")
    targets = []
    try:
        for _ in range(2):
            name, sentinel, urls = create(data)
            targets.append((name, sentinel, urls))
            upgrade(Settings(database_url=urls["migrate"]), target_revision="a006")
        name, _, urls = targets[0]
        restored, restored_nonce, restored_urls = targets[1]
        with authority_server(tmp_path) as (authority, authority_json, trust, producer, command, grant):
            async def scenario():
                subject, reader = uuid4(), uuid4()
                privacy_db, processor_db, query_db = (database(urls[r]) for r in ("privacy", "processor", "query"))
                sync = PrivacyRuntime(AuthorityClient(authority), privacy_db, store=PostgresPrivacyStore(privacy_db))
                processor_gate = PrivacyRuntime(AuthorityClient(authority), processor_db)
                query_gate = PrivacyRuntime(AuthorityClient(authority), query_db)
                processor_db.transaction_guard = processor_gate.check_in_transaction
                query_db.transaction_guard = query_gate.check_in_transaction
                try:
                    await observe(sync)
                    assert await sync.is_ready()
                    catalog = json.dumps([{"schema_version": 2, "kind": "catalog_snapshot", "control_id": str(uuid4()),
                        "course_id": "backend", "catalog_version": "catalog-1", "track_id": "synthetic-track",
                        "lessons": [{"lesson_id": "lesson-1"}]}])
                    app = create_app("collector", Settings(database_url=urls["collector"], trust_manifest_json=trust,
                                                            privacy_authority_json=authority_json, fixture_catalog_json=catalog))
                    with TestClient(app) as http:
                        deadline = time.monotonic()+5
                        while http.get("/ready").status_code != 200 and time.monotonic() < deadline:
                            await asyncio.sleep(.05)
                        assert http.get("/ready").status_code == 200
                        event = json.loads((ROOT / "contracts/fixtures/browser-batch.json").read_text())["events"][0]
                        now = datetime.now(timezone.utc).isoformat()
                        event.update(event_id=str(uuid4()), started_at=now, ended_at=now, last_action_at=now)
                        headers = {"Authorization": "Bearer " + grant_token(grant, subject)}
                        response = http.post("/api/analytics/events/batch", headers=headers, json={"events": [event]})
                        assert response.status_code == 200, response.text
                        assert response.json()["items"][0]["status"] == "accepted"
                        metrics = http.get("/metrics").text
                        assert 'analytics_collector_ack_events_total{outcome="accepted"} 1' in metrics
                        assert str(subject) not in metrics and headers["Authorization"] not in metrics
                        await observe(processor_gate)
                        store = PostgresProcessor(processor_db)
                        batch = await store.claim("synthetic.activity", 1, uuid4(), 100, 30)
                        assert batch
                        await store.commit(batch, synthetic_delta(batch.facts))
                        await observe(query_gate)
                        counts = PostgresSyntheticCounts(query_db)
                        assert (await counts.read_counts("synthetic.activity", 1, reader, subject, ("backend",))).rows[0].event_count == 1
                        snapshot = old_logical_snapshot(data, name)
                        deletion = DeletionFence(schema_version=2, kind="deletion_fence", control_id=uuid4(), subject_ref=subject,
                                                 epoch=1, occurred_at=datetime.now(timezone.utc))
                        receipt = await AuthorityClient(authority).append(deletion, producer="synthetic", producer_token=producer_token(producer), command_key=command)
                        assert receipt.seq == 1  # journal commit is not deletion ACK
                        await observe(query_gate)
                        assert not await query_gate.is_ready()
                        with pytest.raises(PrivacyUnavailable):
                            await counts.read_counts("synthetic.activity", 1, reader, subject, ("backend",))
                        await observe(sync)
                        assert await sync.is_ready()
                        await observe(query_gate)
                        assert await counts.read_counts("synthetic.activity", 1, reader, subject, ("backend",)) is None
                        deadline = time.monotonic()+5
                        while http.get("/ready").status_code != 200 and time.monotonic() < deadline:
                            await asyncio.sleep(.05)
                        assert http.get("/ready").status_code == 200
                        assert http.post("/api/analytics/events/batch", headers=headers, json={"events": [event]}).status_code == 403
                        assert 'analytics_collector_requests_total{outcome="forbidden"} 1' in http.get("/metrics").text
                    transplant_to_fresh_database(data, restored, restored_nonce, snapshot)
                    r_privacy, r_query = database(restored_urls["privacy"]), database(restored_urls["query"])
                    restored_gate = PrivacyRuntime(AuthorityClient(authority), r_query)
                    r_query.transaction_guard = restored_gate.check_in_transaction
                    restored_sync = PrivacyRuntime(AuthorityClient(authority), r_privacy, store=PostgresPrivacyStore(r_privacy))
                    try:
                        assert not await restored_gate.is_ready()  # persisted prefix cannot open a new process
                        wrong_key = authority.model_copy(update={"public_keys": {"a": pem(Ed25519PrivateKey.generate())}})
                        untrusted = PrivacyRuntime(AuthorityClient(wrong_key), r_query)
                        with pytest.raises(AuthorityProtocolError):
                            await untrusted.tick()
                        assert not await untrusted.is_ready()
                        with socket.socket() as unavailable_socket:
                            unavailable_socket.bind(("127.0.0.1", 0))  # deliberately not listening
                            absent = authority.model_copy(update={"url": f"http://127.0.0.1:{unavailable_socket.getsockname()[1]}"})
                            unavailable = PrivacyRuntime(AuthorityClient(absent), r_query)
                            with pytest.raises(AuthorityUnavailable):
                                await unavailable.tick()
                            assert not await unavailable.is_ready()
                        await observe(restored_gate)
                        assert not await restored_gate.is_ready()  # current external HWM=1, restored cursor=0
                        with pytest.raises(PrivacyUnavailable):
                            await PostgresSyntheticCounts(r_query).read_counts("synthetic.activity", 1, reader, subject, ("backend",))
                        await observe(restored_sync)
                        await observe(restored_gate)
                        assert await restored_gate.is_ready()
                        assert await PostgresSyntheticCounts(r_query).read_counts("synthetic.activity", 1, reader, subject, ("backend",)) is None
                        with connect(data, restored) as connection:
                            assert connection.execute("SELECT count(*) FROM analytics.activity_facts WHERE subject_ref=%s", (subject,)).fetchone() == (0,)
                            assert connection.execute("SELECT deleted FROM analytics.subject_fences WHERE subject_ref=%s", (subject,)).fetchone() == (True,)
                            assert connection.execute("SELECT applied_through FROM analytics.privacy_state").fetchone() == (1,)
                    finally:
                        await r_privacy.close(); await r_query.close()
                finally:
                    for db in (privacy_db, processor_db, query_db): await db.close()
            asyncio.run(scenario())
    finally:
        for name, sentinel, _ in reversed(targets): cleanup(data, name, sentinel)
