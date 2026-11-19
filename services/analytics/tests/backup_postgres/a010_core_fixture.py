"""Synthetic Core snapshot for the isolated encrypted a010 restore test.

The caller owns and attests both disposable PostgreSQL nodes. ``seed`` inserts
canonical wire facts and pending work directly as a fixture: it is not an
HTTP, P1 authentication, Collector, or Core outbox ingestion test. ``verify``
then runs the actual Analytics projectors and checks their durable receipts and
the cryptographically recomputed proof before the snapshot is made.
"""
import asyncio
from datetime import date, datetime, timezone
from hashlib import sha256
import re
import time
from uuid import UUID, uuid5

from psycopg import sql
from psycopg.types.json import Jsonb

from pymentor_analytics.adapters.postgres.core_snapshot_proof import PostgresSnapshotProof
from pymentor_analytics.adapters.postgres.core_transfer import PostgresCoreTransfer
from pymentor_analytics.contracts.auth_v1 import VerifiedGrant
from pymentor_analytics.contracts.core_snapshot_proof_v1 import (
    DOMAIN, NAMESPACE as PROOF_NAMESPACE, active_line, proof_fingerprint, validate_proof,
)
from pymentor_analytics.contracts.core_transfer_v1 import (
    NAMESPACE as TRANSFER_NAMESPACE, canonical, entity_key, fingerprint, validate_event,
)
from pymentor_analytics.modules.analytics.core_transfer.compatibility import MOSCOW, calendar, summary
from pymentor_analytics.modules.analytics.core_transfer.legacy_service import LegacyService

NAMESPACE = "core"
CONSUMER = "core.transfer.projection"
DAY_NULL = date(2026, 1, 5)
DAY_VISITED = date(2026, 1, 6)
VISITED_AT = "2026-01-06T00:30:00+00:00"
# 21:30 UTC on Jan 5 is Jan 6 in Moscow: never infer completion from browser.
COMPLETED_AT = "2026-01-05T21:30:00+00:00"
COURSE = "backup-course"
LESSON = "backup-lesson"


def _subjects(deleted, survivor):
    if not isinstance(deleted, UUID) or not isinstance(survivor, UUID) or deleted == survivor:
        raise ValueError("two distinct fixture lifecycles required")


def _guard(conn, sentinel):
    if not isinstance(sentinel, str) or not re.fullmatch(r"[0-9a-f]{64}", sentinel):
        raise ValueError("independent fixture sentinel required")
    if conn.info.host != "127.0.0.1" or conn.info.port != 57422:
        raise ValueError("isolated fixture connection required")
    name, owner = conn.execute("""SELECT current_database(),pg_get_userbyid(datdba)
        FROM pg_database WHERE datname=current_database()""").fetchone()
    if not re.fullmatch(r"analytics_test_[0-9a-f]{32}", name) or owner != name + "_migrate":
        raise ValueError("invalid isolated database owner")
    if conn.execute("SELECT token FROM public.analytics_test_sentinel").fetchall() != [(sentinel,)]:
        raise ValueError("isolated fixture sentinel mismatch")


def _event(subject, kind, revision, payload):
    body = {"schema_version": 1, "kind": kind, "subject_ref": str(subject), "revision": revision, "payload": payload}
    event = {"event_id": str(uuid5(TRANSFER_NAMESPACE, canonical(body))), **body}
    return validate_event(event)


def _events(subject):
    return (
        _event(subject, "legacy_activity_day", 0,
               {"activity_date": DAY_NULL.isoformat(), "visited_at": None, "provenance": "legacy_global"}),
        _event(subject, "legacy_activity_day", 1,
               {"activity_date": DAY_VISITED.isoformat(), "visited_at": VISITED_AT, "provenance": "legacy_global"}),
        _event(subject, "lesson_progress", 2,
               {"course_id": COURSE, "lesson_id": LESSON, "completed_at": COMPLETED_AT}),
    )


def _proof(subject):
    events = _events(subject)
    digest = sha256(DOMAIN)
    for event in sorted(events, key=entity_key):
        digest.update(active_line(event))
    body = {"schema_version": 1, "subject_ref": str(subject), "scope": "subject_all_v1", "cut_revision": 2,
            "entity_count": len(events), "entities_digest": digest.hexdigest(),
            "created_at": "2026-01-06T00:00:00+00:00"}
    proof = {"proof_id": str(uuid5(PROOF_NAMESPACE, canonical(body))), **body}
    validate_proof(proof, proof_fingerprint(proof))
    return proof


def seed(conn, deleted, survivor, *, sentinel):
    """Insert bounded, pending synthetic rows in the caller's attested database."""
    _subjects(deleted, survivor)
    _guard(conn, sentinel)
    with conn.transaction():
        if conn.execute("""SELECT EXISTS(SELECT 1 FROM analytics.activity_facts
            WHERE producer_namespace=%s AND subject_ref=ANY(%s)) OR EXISTS(
            SELECT 1 FROM analytics.core_snapshot_proofs WHERE producer_namespace=%s AND subject_ref=ANY(%s))""",
            (NAMESPACE, [deleted, survivor], NAMESPACE, [deleted, survivor])).fetchone()[0]:
            raise ValueError("fixture subjects already seeded")
        for subject in (deleted, survivor):
            for event in _events(subject):
                event_id, digest = UUID(event["event_id"]), fingerprint(event)
                conn.execute("""INSERT INTO analytics.activity_facts
                    (producer_namespace,event_id,subject_ref,course_id,catalog_version,lesson_id,started_at,
                     received_at,canonical_hash,hash_version,accepted_event,source_kind)
                    VALUES (%s,%s,%s,NULL,NULL,NULL,NULL,clock_timestamp(),%s,1,%s,'core_transfer_v1')""",
                    (NAMESPACE, event_id, subject, digest, Jsonb(event)))
                conn.execute("""INSERT INTO analytics.core_transfer_entities
                    (producer_namespace,event_id,subject_ref,entity_key,revision,canonical_hash)
                    VALUES (%s,%s,%s,%s,%s,%s)""",
                    (NAMESPACE, event_id, subject, entity_key(event), event["revision"], digest))
                conn.execute("""INSERT INTO analytics.processing_jobs
                    (producer_namespace,event_id,subject_ref,consumer,consumer_version)
                    VALUES (%s,%s,%s,%s,1)""", (NAMESPACE, event_id, subject, CONSUMER))
            proof = _proof(subject)
            conn.execute("""INSERT INTO analytics.core_snapshot_proofs
                (producer_namespace,proof_id,subject_ref,cut_revision,entity_count,entities_digest,
                 source_created_at,canonical_hash,proof)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (NAMESPACE, UUID(proof["proof_id"]), subject, proof["cut_revision"], proof["entity_count"],
                 proof["entities_digest"], datetime.fromisoformat(proof["created_at"]), proof_fingerprint(proof), Jsonb(proof)))


async def verify(processor_db, deleted, survivor):
    """Run the real projections and proof verifier; caller closes processor_db."""
    _subjects(deleted, survivor)
    worker = PostgresCoreTransfer(processor_db)
    proofs = PostgresSnapshotProof(processor_db)
    deadline = lambda: time.monotonic() + 5
    async with asyncio.timeout(30):
        for _ in range(6):
            if not await worker.project_one(deadline=deadline()):
                raise AssertionError("fixture projection did not finish")
        for _ in range(2):
            if not await proofs.project_one(deadline=deadline()):
                raise AssertionError("fixture proof verification did not finish")
        for subject in (deleted, survivor):
            for event in _events(subject):
                if await worker.receipt(NAMESPACE, UUID(event["event_id"]), fingerprint(event), deadline=deadline()) != "accepted":
                    raise AssertionError("fixture delivery receipt missing")
            proof = _proof(subject)
            receipt = await proofs.receipt(NAMESPACE, UUID(proof["proof_id"]), proof_fingerprint(proof), deadline=deadline())
            if receipt != {"status": "accepted", "coverage": "verified"}:
                raise AssertionError("fixture snapshot proof not verified")


def assert_restored(conn, deleted, survivor, *, sentinel):
    """Verify exact survivor rows and absence of deleted-subject shared data."""
    _subjects(deleted, survivor)
    _guard(conn, sentinel)
    for table in ("activity_facts", "processing_jobs", "processing_receipts", "processing_results", "processing_leases",
                  "processing_replays", "admission_subject_windows", "core_transfer_entities", "core_transfer_projection",
                  "core_snapshot_proofs", "activity_projection_buckets"):
        query = sql.SQL("SELECT EXISTS(SELECT 1 FROM analytics.{} WHERE subject_ref=%s)").format(sql.Identifier(table))
        if conn.execute(query, (deleted,)).fetchone()[0]:
            raise AssertionError("deleted fixture data remains in shared tables")
    fence = conn.execute("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=%s", (deleted,)).fetchone()
    if fence is None or fence[0] < 1 or fence[1] is not True:
        raise AssertionError("deleted lifecycle fence missing")
    rows = conn.execute("""SELECT f.accepted_event,f.canonical_hash,e.entity_key,e.revision,
            p.event_id,j.status,r.event_id
        FROM analytics.activity_facts f JOIN analytics.core_transfer_entities e USING(producer_namespace,event_id,subject_ref)
        JOIN analytics.core_transfer_projection p ON p.producer_namespace=e.producer_namespace AND p.subject_ref=e.subject_ref
            AND p.entity_key=e.entity_key AND p.revision=e.revision AND p.event_id=e.event_id
        JOIN analytics.processing_jobs j ON j.producer_namespace=f.producer_namespace AND j.event_id=f.event_id
            AND j.subject_ref=f.subject_ref AND j.consumer=%s AND j.consumer_version=1
        JOIN analytics.processing_receipts r ON r.producer_namespace=f.producer_namespace AND r.event_id=f.event_id
            AND r.subject_ref=f.subject_ref AND r.consumer=j.consumer AND r.consumer_version=j.consumer_version
        WHERE f.source_kind='core_transfer_v1' AND f.producer_namespace=%s AND f.subject_ref=%s""",
        (CONSUMER, NAMESPACE, survivor)).fetchall()
    by_id = {str(row[0]["event_id"]): row for row in rows}
    expected = _events(survivor)
    if len(rows) != len(expected) or set(by_id) != {event["event_id"] for event in expected}:
        raise AssertionError("survivor Core facts or receipts differ")
    for event in expected:
        body, digest, key, revision, projected_id, status, receipt_id = by_id[event["event_id"]]
        if (body != event or digest != fingerprint(event) or key != entity_key(event) or revision != event["revision"]
                or str(projected_id) != event["event_id"] or str(receipt_id) != event["event_id"] or status != "completed"):
            raise AssertionError("survivor Core payload or projection differs")
    proof = _proof(survivor)
    stored = conn.execute("""SELECT proof,canonical_hash,status,subject_epoch,entities_digest,entity_count
        FROM analytics.core_snapshot_proofs WHERE producer_namespace=%s AND subject_ref=%s""",
        (NAMESPACE, survivor)).fetchall()
    if stored != [(proof, proof_fingerprint(proof), "verified", 0, proof["entities_digest"], 3)]:
        raise AssertionError("survivor verified proof differs")
    validate_proof(stored[0][0], stored[0][1])


async def assert_reader(query_db, survivor):
    """Use actual query adapter/service with the caller's verified privacy guard."""
    if not isinstance(survivor, UUID) or not callable(getattr(query_db, "transaction_guard", None)):
        raise ValueError("guarded fixture Query database required")
    reader = PostgresCoreTransfer(query_db)
    deadline = lambda: time.monotonic() + 5
    visits, progress, coverage = await reader.read_subject(NAMESPACE, survivor, DAY_VISITED,
                                                          deadline=deadline(), require_coverage=True)
    expected_visits = [{"activity_date": DAY_NULL.isoformat(), "visited_at": None},
                       {"activity_date": DAY_VISITED.isoformat(), "visited_at": VISITED_AT}]
    expected_progress = [{"course_id": COURSE, "lesson_id": LESSON, "completed_at": COMPLETED_AT}]
    if visits != expected_visits or progress != expected_progress or coverage["coverage_through_revision"] != 2:
        raise AssertionError("restored Core Query read differs")
    days = calendar(visits, DAY_NULL, DAY_VISITED)
    pinned = summary(DAY_VISITED, visits, progress)
    if (days["days"] != [DAY_NULL.isoformat(), DAY_VISITED.isoformat()] or days["visits"] != expected_visits
            or pinned["current_streak"] != 2 or pinned["weekly_lessons_completed"][-1]["lessons_completed"] != 1):
        raise AssertionError("restored Moscow calendar or completion differs")
    grant = VerifiedGrant(survivor, "admin", "report:read", (), (), True, int(time.time()) + 30)
    profile = await LegacyService(reader, NAMESPACE, None, require_coverage=True).read_admin_profile(
        grant, survivor, 2026, 1, deadline=deadline())
    actual_today = datetime.now(timezone.utc).astimezone(MOSCOW).date()
    expected_data = {"activity_days_count": 2, "activity_days": [DAY_NULL.isoformat(), DAY_VISITED.isoformat()],
                     "total_activity_days": 2, "last_activity_date": DAY_VISITED.isoformat(),
                     "activity_summary": summary(actual_today, visits, progress)}
    if profile["coverage"] != "complete" or profile["coverage_through_revision"] != 2 or profile["data"] != expected_data:
        raise AssertionError("restored Core admin profile differs")
