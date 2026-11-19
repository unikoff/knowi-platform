"""PG16 tests on generated databases with sentinel/owner-checked teardown."""
import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from pymentor_analytics.adapters.postgres.backpressure import PostgresBackpressure
from pymentor_analytics.adapters.postgres.activity import ActivityStorage
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.intake import PostgresIntake
from pymentor_analytics.adapters.postgres.migrate import upgrade
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.factory import create_app
from pymentor_analytics.contracts.metadata_v1 import MetadataRejected
from pymentor_analytics.contracts.locking_v1 import event_dedup_lock_key
from pymentor_analytics.contracts.v2 import AcceptedEvent, parse_browser_item
from pymentor_analytics.modules.collector.ports import IntakeOverloaded, IntakeUnavailable
from tools.bootstrap_postgres import cleanup, config, connect, create

ROOT = Path(__file__).resolve().parents[2]
TIME = 1800000000


@pytest.fixture
def target():
    if os.environ.get("ANALYTICS_RUN_SYNTHETIC_BACKPRESSURE") != "1":
        pytest.skip("explicit synthetic runner required")
    data = config(ROOT / ".local" / "postgres-connection.json")
    name, token, urls = create(data)
    try:
        upgrade(Settings(database_url=urls["migrate"]), target_revision="a006")
        yield data, name, urls
    finally:
        cleanup(data, name, token)


def configure(target, *, subject=60, total=600, pending=10000, size=536870912):
    data, name, _ = target
    with connect(data, name) as connection:
        connection.execute("""UPDATE analytics.admission_budget SET subject_per_minute=%s,global_per_minute=%s,
            max_outstanding_jobs=%s,max_database_bytes=%s WHERE singleton=true""", (subject, total, pending, size))


def event(subject=None):
    raw = json.loads((ROOT / "contracts/fixtures/browser-batch.json").read_text(encoding="utf-8"))["events"][0]
    stamp = datetime.now(timezone.utc)
    raw.update(event_id=str(uuid4()), started_at=stamp.isoformat(), ended_at=stamp.isoformat(), last_action_at=stamp.isoformat())
    return AcceptedEvent(producer_namespace="synthetic", subject_ref=subject or uuid4(), source="browser",
                         event=parse_browser_item(json.dumps(raw).encode()), received_at=stamp)


def db(url):
    return Database(Settings(database_url=url, pool_size=2, pool_overflow=0, db_timeout_seconds=3))


class Clock(PostgresBackpressure):
    def __init__(self, now=TIME):
        super().__init__()
        self.now = now
    async def _clock_and_size(self, session):
        _, size = await super()._clock_and_size(session)
        return self.now, size


def intake(database, now=TIME):
    result = PostgresIntake(database)
    result.backpressure = Clock(now)
    return result


def snapshot(target):
    data, name, _ = target
    with connect(data, name) as connection:
        facts = connection.execute("SELECT count(*) FROM analytics.activity_facts").fetchone()[0]
        jobs = connection.execute("SELECT count(*) FROM analytics.processing_jobs").fetchone()[0]
        quota = connection.execute("SELECT accepted_count FROM analytics.admission_budget WHERE singleton=true").fetchone()[0]
        return facts, jobs, quota


def test_two_replicas_share_global_cap_without_erasing_accepted(target):
    configure(target, subject=3, total=3)
    _, _, urls = target
    async def run():
        databases = [db(urls["collector"]), db(urls["collector"])]
        writers = [intake(x) for x in databases]
        records = [event() for _ in range(8)]
        async def submit(i):
            try:
                return await writers[i % 2].accept(records[i], deadline=time.monotonic() + 3)
            except IntakeOverloaded:
                return "limited"
        try:
            results = await asyncio.gather(*(submit(i) for i in range(len(records))))
            assert results.count("accepted") == 3 and results.count("limited") == 5
            winner = records[results.index("accepted")]
            assert await writers[1].accept(winner, deadline=time.monotonic() + 2) == "duplicate"
            conflict = winner.model_copy(update={"event": winner.event.model_copy(update={"course_id": "different"})})
            assert await writers[0].accept(conflict, deadline=time.monotonic() + 2) == "conflict"
        finally:
            for database in databases: await database.close()
    asyncio.run(run())
    assert snapshot(target) == (3, 6, 3)


@pytest.mark.parametrize("limit", ["rate", "queue", "size"])
def test_capped_new_fact_never_attempts_insert(target, limit):
    _, _, urls = target
    async def run():
        database = db(urls["collector"])
        writer = intake(database)
        first, rejected = event(), event()
        async def no_insert(*args, **kwargs):
            raise AssertionError("raw insert must not be attempted")
        try:
            assert await writer.accept(first, deadline=time.monotonic() + 2) == "accepted"
            if limit == "rate": configure(target, subject=1, total=1)
            elif limit == "queue": configure(target, pending=2)
            else: configure(target, size=1)
            with patch.object(ActivityStorage, "insert", new=no_insert):
                with pytest.raises(IntakeOverloaded):
                    await writer.accept(rejected, deadline=time.monotonic() + 2)
                assert await writer.accept(first, deadline=time.monotonic() + 2) == "duplicate"
                changed = first.model_copy(update={"event": first.event.model_copy(update={"course_id": "different"})})
                assert await writer.accept(changed, deadline=time.monotonic() + 2) == "conflict"
        finally:
            await database.close()
    asyncio.run(run())
    assert snapshot(target) == (1, 2, 1)


def test_versioned_event_lock_identity_is_stable():
    event_id = UUID(int=0)
    assert event_dedup_lock_key("synthetic", event_id) == (1584372798, 1557328766)
    assert event_dedup_lock_key("other", event_id) != event_dedup_lock_key("synthetic", event_id)


@pytest.mark.parametrize("different_payload", [False, True])
def test_unsupported_old_writer_race_rolls_back_budget_and_reconciles(target, different_payload):
    _, _, urls = target
    item = event()
    legacy_item = item.model_copy(update={"event": item.event.model_copy(update={"course_id": "different"})}) if different_payload else item
    async def run():
        modern, legacy = db(urls["collector"]), db(urls["collector"])
        writer = intake(modern)
        class LegacyRace(Clock):
            async def check(self, session, **kwargs):
                await super().check(session, **kwargs)
                async with legacy.transaction() as legacy_session:
                    assert await ActivityStorage(legacy_session).insert(legacy_item) == "inserted"
        writer.backpressure = LegacyRace()
        try:
            with pytest.raises(IntakeUnavailable):
                await writer.accept(item, deadline=time.monotonic() + 2)
            assert snapshot(target) == (1, 0, 0)
            writer.backpressure = Clock()
            assert await writer.accept(item, deadline=time.monotonic() + 2) == ("conflict" if different_payload else "duplicate")
        finally:
            await modern.close(); await legacy.close()
    asyncio.run(run())
    assert snapshot(target) == (1, 0 if different_payload else 2, 0)


def test_subject_window_resets_and_backward_clock_cannot_reset(target):
    configure(target, subject=2, total=20)
    _, _, urls = target
    async def run():
        database = db(urls["collector"])
        writer = intake(database)
        subject = uuid4()
        try:
            first, second, third = (event(subject) for _ in range(3))
            assert await writer.accept(first, deadline=time.monotonic() + 2) == "accepted"
            assert await writer.accept(second, deadline=time.monotonic() + 2) == "accepted"
            with pytest.raises(IntakeOverloaded) as error:
                await writer.accept(third, deadline=time.monotonic() + 2)
            assert 1 <= error.value.retry_after <= 60
            writer.backpressure.now -= 120
            with pytest.raises(IntakeOverloaded):
                await writer.accept(third, deadline=time.monotonic() + 2)
            writer.backpressure.now = TIME + 60
            assert await writer.accept(third, deadline=time.monotonic() + 2) == "accepted"
        finally:
            await database.close()
    asyncio.run(run())
    assert snapshot(target) == (3, 6, 1)


def test_two_replicas_share_one_subject_quota(target):
    configure(target, subject=2, total=20)
    _, _, urls = target
    async def run():
        databases = [db(urls["collector"]), db(urls["collector"])]
        writers = [intake(database) for database in databases]
        subject = uuid4()
        async def submit(index):
            try:
                return await writers[index % 2].accept(event(subject), deadline=time.monotonic() + 3)
            except IntakeOverloaded:
                return "limited"
        try:
            outcomes = await asyncio.gather(*(submit(index) for index in range(6)))
            assert outcomes.count("accepted") == 2 and outcomes.count("limited") == 4
        finally:
            for database in databases: await database.close()
    asyncio.run(run())
    assert snapshot(target) == (2, 4, 2)


def test_queue_cap_without_processor_then_drain(target):
    configure(target, pending=4)
    _, _, urls = target
    async def run():
        collector, processor = db(urls["collector"]), db(urls["processor"])
        writer = intake(collector)
        first, second, third = event(), event(), event()
        try:
            for item in (first, second): assert await writer.accept(item, deadline=time.monotonic() + 2) == "accepted"
            with pytest.raises(IntakeOverloaded): await writer.accept(third, deadline=time.monotonic() + 2)
            assert await writer.accept(first, deadline=time.monotonic() + 2) == "duplicate"
            async with processor.transaction() as session:
                await session.execute(text("UPDATE analytics.processing_jobs SET status='completed' WHERE event_id=:event"), {"event": first.event.event_id})
            assert await writer.accept(third, deadline=time.monotonic() + 2) == "accepted"
        finally:
            await collector.close(); await processor.close()
    asyncio.run(run())
    assert snapshot(target) == (3, 6, 3)


def test_duplicate_with_missing_job_waits_for_queue_space(target):
    configure(target, pending=4)
    _, _, urls = target
    async def run():
        collector, processor, privacy = db(urls["collector"]), db(urls["processor"]), db(urls["privacy"])
        writer = intake(collector)
        first, second = event(), event()
        try:
            for item in (first, second): assert await writer.accept(item, deadline=time.monotonic() + 2) == "accepted"
            async with privacy.transaction() as session:
                await session.execute(text("DELETE FROM analytics.processing_jobs WHERE event_id=:id AND consumer='synthetic.activity'"), {"id": first.event.event_id})
            # Three remaining jobs fill the lowered limit; completion frees space.
            configure(target, pending=3)
            with pytest.raises(IntakeOverloaded): await writer.accept(first, deadline=time.monotonic() + 2)
            async with processor.transaction() as session:
                await session.execute(text("UPDATE analytics.processing_jobs SET status='completed' WHERE event_id=:id"), {"id": second.event.event_id})
            assert await writer.accept(first, deadline=time.monotonic() + 2) == "duplicate"
        finally:
            await collector.close(); await processor.close(); await privacy.close()
    asyncio.run(run())
    assert snapshot(target) == (2, 4, 2)


def test_warning_at_80_percent_without_early_rejection(target, caplog):
    configure(target, size=100)
    _, _, urls = target
    async def run():
        database = db(urls["collector"])
        writer = intake(database)
        class WarningClock(Clock):
            async def _clock_and_size(self, session): return TIME, 85
        writer.backpressure = WarningClock()
        try:
            assert await writer.accept(event(), deadline=time.monotonic() + 2) == "accepted"
            assert await writer.accept(event(), deadline=time.monotonic() + 2) == "accepted"
        finally:
            await database.close()
    asyncio.run(run())
    assert sum("analytics database size warning" in row.message for row in caplog.records) == 1
    assert snapshot(target) == (2, 4, 2)


def test_disk_cap_and_probe_failure_fail_closed(target):
    _, _, urls = target
    async def run():
        database = db(urls["collector"])
        writer = intake(database)
        first = event()
        try:
            assert await writer.accept(first, deadline=time.monotonic() + 2) == "accepted"
            configure(target, size=1)
            assert await writer.accept(first, deadline=time.monotonic() + 2) == "duplicate"
            with pytest.raises(IntakeOverloaded): await writer.accept(event(), deadline=time.monotonic() + 2)
            configure(target)
            class Broken(Clock):
                async def _clock_and_size(self, session):
                    raise OperationalError("probe", {}, RuntimeError("synthetic failure"))
            writer.backpressure = Broken()
            assert await writer.accept(first, deadline=time.monotonic() + 2) == "duplicate"
            with pytest.raises(IntakeUnavailable): await writer.accept(event(), deadline=time.monotonic() + 2)
        finally:
            await database.close()
    asyncio.run(run())
    assert snapshot(target) == (1, 2, 1)


@pytest.mark.parametrize("mode", ["sql", "timeout", "clock_policy"])
def test_failures_roll_back_counters_and_fact_jobs(target, mode):
    _, _, urls = target
    async def run():
        database = db(urls["collector"])
        class Broken(PostgresIntake):
            async def _before_commit(self):
                if mode == "sql": raise OperationalError("synthetic", {}, RuntimeError("failure"))
                if mode == "timeout": await asyncio.Event().wait()
        writer = Broken(database)
        writer.backpressure = Clock()
        item = event()
        if mode == "clock_policy": item = item.model_copy(update={"received_at": item.received_at + timedelta(days=8)})
        try:
            with pytest.raises(MetadataRejected if mode == "clock_policy" else IntakeUnavailable):
                await writer.accept(item, deadline=time.monotonic() + (.2 if mode == "timeout" else 2))
        finally:
            await database.close()
    asyncio.run(run())
    assert snapshot(target) == (0, 0, 0)


def test_db_unavailable_does_not_return_success(target):
    _, name, urls = target
    invalid_url = urls["collector"].replace(f"/{name}", "/analytics_test_" + uuid4().hex)
    async def run():
        database = db(invalid_url)
        try:
            with pytest.raises(IntakeUnavailable): await intake(database).accept(event(), deadline=time.monotonic() + 2)
        finally:
            await database.close()
    asyncio.run(run())


def test_bounded_expiry_cleanup_only_removes_technical_rows(target):
    data, name, urls = target
    with connect(data, name) as connection:
        for _ in range(70):
            connection.execute("INSERT INTO analytics.admission_subject_windows VALUES (%s,%s,1)", (uuid4(), TIME - 300))
    async def run():
        database = db(urls["collector"])
        try: assert await intake(database).accept(event(), deadline=time.monotonic() + 2) == "accepted"
        finally: await database.close()
    asyncio.run(run())
    with connect(data, name) as connection:
        assert connection.execute("SELECT count(*) FROM analytics.admission_subject_windows WHERE window_epoch=%s", (TIME - 300,)).fetchone()[0] == 6
    assert snapshot(target) == (1, 2, 1)


def test_policy_limits_are_not_mutable_by_collector(target):
    _, _, urls = target
    async def run():
        database = db(urls["collector"])
        try:
            with pytest.raises(DBAPIError):
                async with database.transaction() as session:
                    await session.execute(text("UPDATE analytics.admission_budget SET global_per_minute=9999"))
        finally:
            await database.close()
    asyncio.run(run())


def test_http_partial_batch_429_then_same_ids_reconcile(target):
    configure(target, subject=2, total=10)
    data, name, urls = target
    grant, producer = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    def pem(key):
        return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    manifest = {"grant": {"issuer": "synthetic-issuer", "keys": {"g": pem(grant)}},
                "producer": {"issuer": "synthetic-control", "keys": {"p": pem(producer)}, "allowlist": ["synthetic-producer"]}}
    now, subject = int(time.time()), uuid4()
    claims = {"grant_version": 1, "iss": "synthetic-issuer", "aud": "analytics", "sub": str(subject),
              "iat": now - 1, "nbf": now - 1, "exp": now + 299, "role": "user", "scope": "collect",
              "course_ids": ["backend"], "subject_refs": [], "global_read": False}
    token = jwt.encode(claims, grant, algorithm="EdDSA", headers={"typ": "JWT", "kid": "g"})
    headers = {"Authorization": "Bearer " + token}
    class Ready:
        async def is_ready(self): return True
    class Fence:
        async def permits(self, subject): return True
    class Catalog:
        async def known(self, *, course_id, catalog_version, lesson_id, deadline): return True
    app = create_app("collector", Settings(database_url=urls["collector"], trust_manifest_json=json.dumps(manifest),
                                            db_timeout_seconds=3), readiness=Ready(), fence=Fence(), catalog=Catalog())
    records = [event(subject).event.model_dump(mode="json") for _ in range(3)]
    with TestClient(app) as client:
        app.state.intake.backpressure = Clock()
        first = client.post("/api/analytics/events/batch", headers=headers, json={"events": records})
        assert first.status_code == 429 and 1 <= int(first.headers["retry-after"]) <= 60
        assert "items" not in first.json()
        assert snapshot(target) == (2, 4, 2)
        again = client.post("/api/analytics/events/batch", headers=headers, json={"events": records})
        assert again.status_code == 429
        assert snapshot(target) == (2, 4, 2)
        app.state.intake.backpressure.now = TIME + 60
        retry = client.post("/api/analytics/events/batch", headers=headers, json={"events": records})
        assert retry.status_code == 200
        assert [item["status"] for item in retry.json()["items"]] == ["duplicate", "duplicate", "accepted"]
    assert snapshot(target) == (3, 6, 1)
    with connect(data, name) as connection:
        for value in records:
            assert connection.execute("SELECT count(*) FROM analytics.activity_facts WHERE event_id=%s", (value["event_id"],)).fetchone() == (1,)
            assert connection.execute("SELECT count(*) FROM analytics.processing_jobs WHERE event_id=%s", (value["event_id"],)).fetchone() == (2,)
    invalid_url = urls["collector"].replace(f"/{name}", "/analytics_test_" + uuid4().hex)
    unavailable = create_app("collector", Settings(database_url=invalid_url, trust_manifest_json=json.dumps(manifest),
                                                    db_timeout_seconds=3), readiness=Ready(), fence=Fence(), catalog=Catalog())
    with TestClient(unavailable) as client:
        assert client.post("/api/analytics/events/batch", headers=headers, json={"events": [records[0]]}).status_code == 503


def test_ready_fingerprint_matches_across_roles_without_opening_privacy(target):
    _, _, urls = target
    grant, producer = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    def pem(key):
        return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    manifest = {"grant": {"issuer": "synthetic-issuer", "keys": {"g": pem(grant)}},
                "producer": {"issuer": "synthetic-control", "keys": {"p": pem(producer)}, "allowlist": ["synthetic-producer"]}}
    seen = []
    for role in ("collector", "query"):
        app = create_app(role, Settings(database_url=urls[role], trust_manifest_json=json.dumps(manifest)))
        with TestClient(app) as client:
            response = client.get("/ready")
            assert response.status_code == 503 and response.json() == {"status": "unavailable"}
            seen.append(response.headers["X-Analytics-Trust-Fingerprint"])
            assert seen[-1] == app.state.trust_manifest_fingerprint
    assert seen[0] == seen[1] and len(seen[0]) == 64
    app = create_app("query", Settings(database_url=urls["query"]))
    with TestClient(app) as client:
        assert "X-Analytics-Trust-Fingerprint" not in client.get("/ready").headers
