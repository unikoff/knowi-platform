"""Opt-in synthetic PostgreSQL + real privacy authority + Query ASGI route.

The Query transport is in-process ASGI; authority traffic uses loopback mTLS.
This is not a full Query service, browser, Collector, or Core gateway test.
"""
import asyncio
import base64
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
import time
from uuid import uuid4

import httpx
import jwt
import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI

from test_production_authority import _certs, journal
from test_production_chain_synthetic import ROOT, pem, tls_server
from pymentor_analytics.adapters.auth.verifier import GrantVerifier
from pymentor_analytics.adapters.postgres.activity import ActivityStorage
from pymentor_analytics.adapters.postgres.activity_day_projection import PostgresActivityDayProjection
from pymentor_analytics.adapters.postgres.activity_projection import PostgresActivityProjection
from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.adapters.postgres.privacy import PostgresPrivacyStore
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
from pymentor_analytics.adapters.privacy_authority.server import create_authority_app
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.privacy import PrivacyRuntime
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.v2 import AcceptedEvent, BrowserEvent, EmptyPayload
from pymentor_analytics.modules.analytics.activity.bucket import normalize_subject_bucket
from pymentor_analytics.modules.analytics.activity.day import normalize_day
from pymentor_analytics.modules.analytics.api.activity_router import router
from pymentor_analytics.modules.analytics.api.course_visitors_router import router as course_router
from pymentor_analytics.modules.analytics.api.measured_calendar_router import router as measured_router
from tools.bootstrap_postgres import cleanup, config, create


def _token(key, *, subject, role, scope, subjects=()):
    now = int(time.time())
    return jwt.encode({
        "grant_version": 1, "iss": "core", "aud": "analytics", "sub": str(subject),
        "iat": now, "nbf": now, "exp": now + 50, "role": role, "scope": scope,
        "course_ids": ["backend"], "subject_refs": [str(value) for value in subjects],
        "global_read": False,
    }, key, algorithm="EdDSA", headers={"typ": "JWT", "kid": "test"})


async def _drain(store, *, day=False):
    owner = uuid4()
    deadline = time.monotonic() + 15
    async with asyncio.timeout(15):
        while True:
            phase = await store.backfill_step(deadline=deadline)
            while (claim := await store.claim(owner, deadline=deadline)) is not None:
                loaded, rows = await store.load(claim, deadline=deadline)
                output = normalize_day(rows) if day else normalize_subject_bucket(
                    rows, subject_ref=loaded.subject_ref, bucket_start=loaded.bucket_start)
                await store.commit(loaded, output, deadline=deadline)
            if phase == "complete":
                return


async def _course_aggregate(client, key, *, leader, student, metric):
    nonce = uuid4().hex
    at = datetime.now(timezone.utc)
    payload = {
        "at": at.isoformat(), "nonce": nonce, "operation": "aggregate",
        "active_subscriptions": [sha256((nonce + str(student)).encode()).hexdigest()],
        "inactive_subscriptions": [],
    }
    if metric == "study-time":
        payload["excluded_subjects"] = []
    body = json.dumps(payload).encode()
    stamp = int(at.timestamp())
    grant = jwt.encode({
        "grant_version": 1, "iss": "core", "aud": f"analytics-course-{metric}",
        "sub": str(leader), "iat": stamp, "nbf": stamp, "exp": stamp + 15,
        "course_id": "backend", "body_sha256": sha256(body).hexdigest(),
    }, key, algorithm="EdDSA", headers={"typ": "JWT", "kid": "test"})
    return await client.post(f"/internal/analytics/v1/course/{metric}", content=body,
                             headers={"Authorization": "Bearer " + grant, "Content-Type": "application/json"})


def test_scoped_fixture_grant_is_signed_and_single_subject():
    key = Ed25519PrivateKey.generate()
    student, leader = uuid4(), uuid4()
    verifier = GrantVerifier(issuer="core", public_keys={"test": pem(key).encode()})
    grant = verifier.verify(_token(key, subject=leader, role="leader", scope="report:student", subjects=(student,)))
    assert grant.sub == leader and grant.subject_refs == (student,) and grant.course_ids == ("backend",)


@pytest.mark.skipif(os.environ.get("ANALYTICS_RUN_SYNTHETIC_PRIVACY_INTEGRATION") != "1",
                    reason="explicit synthetic privacy integration opt-in required")
def test_protected_query_scoped_http_on_postgres(tmp_path):

    data = config(ROOT / ".local/postgres-connection.json")
    name, sentinel, urls = create(data, revision="a014")
    try:
        upgrade(Settings(database_url=urls["migrate"]), target_revision="a014")
        run_id = uuid4()
        with psycopg.connect(urls["migrate"]) as connection:
            connection.execute("SELECT analytics.begin_activity_projection_backfill(NULL, %s)", (run_id,))
        ca, (cert, key), (client_cert, client_key) = _certs(tmp_path)
        authority_key, producer, command, grant_key = (Ed25519PrivateKey.generate() for _ in range(4))
        secret = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
        authority_journal = journal(tmp_path)
        authority_app = create_authority_app(
            authority_journal, issuer=authority_journal.authority_id, kid="a", signing_key=authority_key,
            producer_issuer="core", producer_public_keys={"p": pem(producer).encode()},
            allowed_producers={"core"}, producer_command_public_keys={"core": pem(command).encode()},
            reader_secret=secret,
        )
        with tls_server(authority_app, ca, cert, key) as authority_url:
            authority_config = AuthorityConfig.from_json(json.dumps({
                "url": authority_url, "authority_id": authority_journal.authority_id,
                "issuer": authority_journal.authority_id, "public_keys": {"a": pem(authority_key)},
                "reader_secret": secret, "fixture_mode": False, "runtime_mode": "production",
                "tls_ca_file": str(ca), "tls_cert_file": str(client_cert), "tls_key_file": str(client_key),
            }))

            async def scenario():
                databases = {role: Database(Settings(database_url=urls[role], pool_size=2, pool_overflow=0))
                             for role in ("privacy", "collector", "processor", "query")}
                runtimes = {}
                try:
                    privacy = PrivacyRuntime(AuthorityClient(authority_config), databases["privacy"],
                                             store=PostgresPrivacyStore(databases["privacy"]))
                    runtimes["privacy"] = privacy
                    await privacy.tick()
                    assert await privacy.is_ready()
                    for role in ("collector", "processor", "query"):
                        db = databases[role]
                        runtime = PrivacyRuntime(AuthorityClient(authority_config), db)
                        runtimes[role] = runtime
                        db.transaction_guard = runtime.check_in_transaction
                        db.transaction_exit_guard = runtime.check_in_transaction
                        await runtime.tick()
                        assert await runtime.is_ready()
                        runtime.start()

                    student, leader, outsider = uuid4(), uuid4(), uuid4()
                    start = (datetime.now(timezone.utc) - timedelta(days=2)).replace(
                        hour=12, minute=0, second=0, microsecond=0)
                    event = BrowserEvent(schema_version=2, event_id=uuid4(), type="activity_segment",
                        course_id="backend", catalog_version="fixture", lesson_id="lesson-1", section="theory",
                        session_id=uuid4(), started_at=start, ended_at=start + timedelta(seconds=10),
                        last_action_at=start, visible=True, focused=True, active_ms=10_000, payload=EmptyPayload())
                    record = AcceptedEvent(producer_namespace="fixture", subject_ref=student, source="browser",
                                           event=event, received_at=datetime.now(timezone.utc))
                    async with databases["collector"].transaction() as session:
                        assert await ActivityStorage(session).insert(record) == "inserted"

                    await _drain(PostgresActivityProjection(databases["processor"], expected_run_id=run_id))
                    await _drain(PostgresActivityDayProjection(databases["processor"], expected_run_id=run_id), day=True)
                    await runtimes["query"].tick()
                    assert await runtimes["query"].is_ready()

                    app = FastAPI()
                    app.include_router(router)
                    app.include_router(measured_router)
                    app.include_router(course_router)
                    app.state.course_visitor_slots = asyncio.Semaphore(1)
                    app.state.database = databases["query"]
                    app.state.trust_manifest_validated = True  # fixture trust, not production manifest validation
                    app.state.grant_verifier = GrantVerifier(issuer="core", public_keys={"test": pem(grant_key).encode()})
                    app.state.probe = runtimes["query"]
                    app.state.settings = Settings(database_url=urls["query"], db_timeout_seconds=5)
                    app.state.activity_reports = PostgresProjectionActivityReport(
                        databases["query"], None, expected_run_id=run_id, day_projection_enabled=True)
                    token = _token(grant_key, subject=leader, role="leader", scope="report:student", subjects=(student,))
                    params = {"from_time": start.isoformat(), "to_time": (start + timedelta(hours=1)).isoformat(),
                              "course_id": "backend", "subject_ref": str(student)}
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://query") as client:
                        path = "/api/analytics/reports/activity"
                        headers = {"Authorization": "Bearer " + token}
                        response = await client.get(path, params=params, headers=headers)
                        assert response.status_code == 200, response.text
                        report = response.json()
                        assert report["course_id"] == "backend"
                        assert report["totals"]["unique_visitors"] == 1
                        assert report["totals"]["active_ms"] == report["totals"]["theory_ms"] == 10_000
                        assert report["totals"]["practice_ms"] == 0
                        learner_token = _token(grant_key, subject=student, role="user", scope="report:read")
                        day = start.date().isoformat()
                        calendar = await client.get("/api/analytics/reports/measured-calendar", params={
                            "year": start.year, "month": start.month, "course_id": "backend"},
                            headers={"Authorization": "Bearer " + learner_token})
                        assert calendar.status_code == 200, calendar.text
                        assert calendar.json()["course_id"] == "backend" and calendar.json()["days"] == [day]
                        bad_course = await client.get("/api/analytics/reports/measured-calendar", params={
                            "year": start.year, "month": start.month, "course_id": "other"},
                            headers={"Authorization": "Bearer " + learner_token})
                        assert bad_course.status_code == 403

                        for metric, expected in (("visitors", 1), ("study-time", {"active_ms": 10_000, "learners": 1})):
                            aggregate = await _course_aggregate(client, grant_key, leader=leader, student=student,
                                                                metric=metric)
                            assert aggregate.status_code == 200, aggregate.text
                            assert aggregate.headers["cache-control"] == "no-store"
                            summary = aggregate.json()
                            assert summary["course_id"] == "backend"
                            row = next(item for item in summary["daily"] if item["date"] == day)
                            assert row["with_subscription"] == expected
                            assert row["without_subscription"] is None

                        denied = await client.get(path, params={**params, "subject_ref": str(outsider)}, headers=headers)
                        assert denied.status_code == 403
                        assert (await client.get(path, params=params, headers={"Authorization": "Bearer invalid"})).status_code == 401
                        await runtimes["query"].stop()
                        assert not await runtimes["query"].is_ready()
                        with pytest.raises(PrivacyUnavailable):
                            async with databases["query"].transaction():
                                pytest.fail("closed privacy gate opened a transaction")
                        closed = await client.get(path, params=params, headers=headers)
                        assert closed.status_code == 503 and "totals" not in closed.json()
                finally:
                    for runtime in runtimes.values():
                        await runtime.stop()
                    for database in databases.values():
                        await database.close()

            asyncio.run(scenario())
    finally:
        cleanup(data, name, sentinel, revision="a014")
