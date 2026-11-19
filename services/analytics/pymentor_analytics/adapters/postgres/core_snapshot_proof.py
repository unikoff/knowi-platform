"""Bounded subject snapshot admission and as-of verification in Analytics."""
import asyncio
from hashlib import sha256
import time
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text

from pymentor_analytics.adapters.postgres.backpressure import PostgresBackpressure
from pymentor_analytics.contracts.core_snapshot_proof_v1 import DOMAIN, MAX_HISTORIC_KEYS, MAX_SOURCE_BYTES, active_line, validate_proof
from pymentor_analytics.contracts.core_transfer_v1 import canonical, entity_key, fingerprint, validate_event
from pymentor_analytics.contracts.locking_v1 import event_dedup_lock_key, subject_lock_key
from pymentor_analytics.modules.collector.ports import IntakeForbidden, IntakeOverloaded, IntakeUnavailable

_CONSUMER = "core.transfer.projection"
_PAGE = 256
_MAX_ATTEMPTS = 8


class PostgresSnapshotProof:
    def __init__(self, database):
        self.database = database
        self.backpressure = PostgresBackpressure()
        self._cursor = None

    async def accept(self, namespace, proof, digest, *, deadline):
        validate_proof(proof, digest, check_id=False)
        subject, proof_id = UUID(proof["subject_ref"]), UUID(proof["proof_id"])
        args = {"ns": namespace, "subject": subject, "proof": proof_id, "digest": digest,
                "cut": proof["cut_revision"], "count": proof["entity_count"], "entities_digest": proof["entities_digest"],
                "created": datetime.fromisoformat(proof["created_at"]), "body": canonical(proof)}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntakeUnavailable()
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
                fence = (await session.execute(text("SELECT deleted FROM analytics.subject_fences WHERE subject_ref=:subject"), args)).scalar_one_or_none()
                if fence is True:
                    raise IntakeForbidden()
                first, second = event_dedup_lock_key(namespace, proof_id)
                await session.execute(text("SELECT pg_advisory_xact_lock(:first,:second)"), {"first": first, "second": second})
                prior = (await session.execute(text("""SELECT canonical_hash,subject_ref FROM analytics.core_snapshot_proofs
                    WHERE producer_namespace=:ns AND proof_id=:proof"""), args)).one_or_none()
                if prior:
                    return "accepted" if prior.canonical_hash == digest and prior.subject_ref == subject else "conflict"
                validate_proof(proof, digest)
                existing = (await session.execute(text("""SELECT 1 FROM analytics.core_snapshot_proofs
                    WHERE producer_namespace=:ns AND subject_ref=:subject AND cut_revision=:cut"""), args)).first()
                if existing:
                    return "conflict"
                # Existing admission singleton serializes cross-subject proof/fact quotas.
                await self.backpressure.check(session, subject=subject, new_fact=True, new_jobs=1)
                outstanding = (await session.execute(text("""SELECT count(*) FROM (SELECT 1 FROM analytics.core_snapshot_proofs
                    WHERE status IN ('pending','quarantined') LIMIT 10001) q"""))).scalar_one()
                personal = (await session.execute(text("""SELECT count(*) FROM (SELECT 1 FROM analytics.core_snapshot_proofs
                    WHERE producer_namespace=:ns AND subject_ref=:subject AND status IN ('pending','quarantined') LIMIT 5) q"""), args)).scalar_one()
                if outstanding >= 10000 or personal >= 4:
                    raise IntakeOverloaded(1)
                await session.execute(text("""INSERT INTO analytics.core_snapshot_proofs
                    (producer_namespace,proof_id,subject_ref,cut_revision,entity_count,entities_digest,source_created_at,canonical_hash,proof)
                    VALUES (:ns,:proof,:subject,:cut,:count,:entities_digest,CAST(:created AS timestamptz),:digest,CAST(:body AS jsonb))"""), args)
        return "accepted"

    async def receipt(self, namespace, proof_id, digest, *, deadline):
        if not isinstance(proof_id, UUID) or not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("invalid proof receipt")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntakeUnavailable()
        args = {"ns": namespace, "proof": proof_id}
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                subject = (await session.execute(text("""SELECT subject_ref FROM analytics.core_snapshot_proofs
                    WHERE producer_namespace=:ns AND proof_id=:proof"""), args)).scalar_one_or_none()
                if subject is None:
                    return {"status": "absent", "coverage": None}
                args["subject"] = subject
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
                if (await session.execute(text("SELECT deleted FROM analytics.subject_fences WHERE subject_ref=:subject"), args)).scalar_one_or_none() is True:
                    raise IntakeForbidden()
                row = (await session.execute(text("""SELECT canonical_hash,status,last_failure FROM analytics.core_snapshot_proofs
                    WHERE producer_namespace=:ns AND proof_id=:proof AND subject_ref=:subject"""), args)).one_or_none()
                if row is None:
                    return {"status": "absent", "coverage": None}
                if row.canonical_hash != digest:
                    return {"status": "conflict", "coverage": None}
                coverage = {"pending": "pending", "verified": "verified", "quarantined": "unavailable", "superseded": "unavailable"}[row.status]
                if row.status == "quarantined" and row.last_failure in {"snapshot_pending", "projection_pending"}:
                    coverage = "pending"
                return {"status": "accepted", "coverage": coverage}

    async def project_one(self, *, deadline):
        """Recompute one bounded as-of subject snapshot under its privacy lock."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntakeUnavailable()
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                query = """SELECT p.subject_ref,min(p.source_created_at) AS oldest FROM analytics.core_snapshot_proofs p
                    WHERE p.status='pending' AND p.next_attempt_at<=clock_timestamp()
                    AND NOT EXISTS (SELECT 1 FROM analytics.subject_fences s WHERE s.subject_ref=p.subject_ref AND s.deleted)
                    GROUP BY p.subject_ref """
                after = " HAVING (min(p.source_created_at),p.subject_ref)>(:created,:subject)" if self._cursor else ""
                params = {"created": self._cursor[0], "subject": self._cursor[1]} if self._cursor else {}
                candidate = (await session.execute(text(query + after + " ORDER BY oldest,p.subject_ref LIMIT 1"), params)).one_or_none()
                if candidate is None and self._cursor:
                    candidate = (await session.execute(text(query + " ORDER BY oldest,p.subject_ref LIMIT 1"))).one_or_none()
            if candidate is None:
                self._cursor = None
                return False
            self._cursor = (candidate.oldest, candidate.subject_ref)
            subject = candidate.subject_ref
            async with self.database.transaction() as session:
                if not (await session.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})).scalar_one():
                    return False
                fence = (await session.execute(text("SELECT epoch,deleted FROM analytics.subject_fences WHERE subject_ref=:subject"), {"subject": subject})).one_or_none()
                if fence and fence.deleted:
                    return False
                epoch = fence.epoch if fence else 0
                proof = (await session.execute(text("""SELECT * FROM analytics.core_snapshot_proofs
                    WHERE subject_ref=:subject AND status='pending' AND next_attempt_at<=clock_timestamp()
                    ORDER BY source_created_at,producer_namespace,proof_id LIMIT 1 FOR UPDATE SKIP LOCKED"""), {"subject": subject})).one_or_none()
                if proof is None:
                    return False
                args = {"ns": proof.producer_namespace, "subject": subject, "cut": proof.cut_revision, "proof": proof.proof_id}
                try:
                    body = validate_proof(proof.proof, proof.canonical_hash)
                    if (body["subject_ref"] != str(subject) or body["proof_id"] != str(proof.proof_id)
                            or body["cut_revision"] != proof.cut_revision or body["entity_count"] != proof.entity_count
                            or body["entities_digest"] != proof.entities_digest
                            or body["created_at"] != proof.source_created_at.astimezone(timezone.utc).isoformat()):
                        raise ValueError
                except (ValueError, TypeError, UnicodeError, AttributeError):
                    await session.execute(text("""UPDATE analytics.core_snapshot_proofs SET status='quarantined',last_failure='invalid_proof'
                        WHERE producer_namespace=:ns AND proof_id=:proof AND subject_ref=:subject"""), args)
                    return True
                digest, active_count, days, progress, bytes_count, seen, cursor = sha256(DOMAIN), 0, 0, 0, len(DOMAIN), 0, None
                include_tasks = body["scope"] == "subject_all_v2"
                failure = None
                while True:
                    after = ' AND e.entity_key COLLATE "C">:after' if cursor else ""
                    rows = (await session.execute(text("""SELECT DISTINCT ON (e.entity_key COLLATE "C") e.entity_key,e.revision,e.event_id,
                        e.canonical_hash,f.canonical_hash AS fact_hash,f.source_kind,f.accepted_event,p.revision AS projected_revision,
                        EXISTS(SELECT 1 FROM analytics.processing_receipts r WHERE r.producer_namespace=e.producer_namespace
                            AND r.event_id=e.event_id AND r.subject_ref=e.subject_ref AND r.consumer=:consumer AND r.consumer_version=1) AS applied
                        FROM analytics.core_transfer_entities e JOIN analytics.activity_facts f USING (producer_namespace,event_id,subject_ref)
                        LEFT JOIN analytics.core_transfer_projection p ON p.producer_namespace=e.producer_namespace
                            AND p.subject_ref=e.subject_ref AND p.entity_key=e.entity_key
                        WHERE e.producer_namespace=:ns AND e.subject_ref=:subject AND e.revision<=:cut
                        AND (:include_tasks OR (f.accepted_event->>'kind') IS DISTINCT FROM 'task_progress')""" + after + """
                        ORDER BY e.entity_key COLLATE "C",e.revision DESC LIMIT :limit"""),
                        {**args, "after": cursor, "consumer": _CONSUMER, "limit": _PAGE, "include_tasks": include_tasks})).all()
                    for row in rows:
                        seen += 1
                        if seen > MAX_HISTORIC_KEYS:
                            failure = "bound_exceeded"
                            break
                        try:
                            event = validate_event(row.accepted_event)
                            if (row.source_kind != "core_transfer_v1" or event["subject_ref"] != str(subject)
                                    or event["event_id"] != str(row.event_id) or event["revision"] != row.revision
                                    or fingerprint(event) != row.canonical_hash or row.fact_hash != row.canonical_hash
                                    or entity_key(event) != row.entity_key):
                                raise ValueError
                            line = active_line(event)
                        except (ValueError, TypeError, UnicodeError):
                            failure = "invalid_fact"
                            break
                        if not row.applied or row.projected_revision is None or row.projected_revision < row.revision:
                            failure = "projection_pending"
                            break
                        if line is not None:
                            active_count += 1
                            days += event["kind"] == "legacy_activity_day"
                            progress += event["kind"] in {"lesson_progress", "task_progress"}
                            bytes_count += len(line)
                            if days > 10000 or progress > 5000 or bytes_count > MAX_SOURCE_BYTES:
                                failure = "bound_exceeded"
                                break
                            digest.update(line)
                    if failure or len(rows) < _PAGE:
                        break
                    cursor = rows[-1].entity_key
                if failure is None and (active_count != proof.entity_count or digest.hexdigest() != proof.entities_digest):
                    failure = "snapshot_pending"
                if failure:
                    await session.execute(text("""UPDATE analytics.core_snapshot_proofs
                        SET attempts=attempts+1,status=CASE WHEN attempts+1>=:max OR :failure IN ('invalid_fact','bound_exceeded')
                            THEN 'quarantined' ELSE 'pending' END,
                            next_attempt_at=clock_timestamp() + ((LEAST(60,power(2,LEAST(attempts,5))::int)*1000
                                + get_byte(uuid_send(:proof),0)%250)*interval '1 millisecond'),
                            last_failure=:failure WHERE producer_namespace=:ns AND proof_id=:proof AND subject_ref=:subject"""),
                        {**args, "failure": failure, "max": _MAX_ATTEMPTS})
                    return True
                current = (await session.execute(text("""SELECT cut_revision FROM analytics.core_snapshot_proofs
                    WHERE producer_namespace=:ns AND subject_ref=:subject AND status='verified' FOR UPDATE"""), args)).scalar_one_or_none()
                if current is not None and current > proof.cut_revision:
                    await session.execute(text("""UPDATE analytics.core_snapshot_proofs SET status='superseded',last_failure=NULL
                        WHERE producer_namespace=:ns AND proof_id=:proof AND subject_ref=:subject"""), args)
                    return True
                await session.execute(text("""UPDATE analytics.core_snapshot_proofs SET status='superseded'
                    WHERE producer_namespace=:ns AND subject_ref=:subject AND status='verified'"""), args)
                await session.execute(text("""UPDATE analytics.core_snapshot_proofs SET status='verified',subject_epoch=:epoch,last_failure=NULL
                    WHERE producer_namespace=:ns AND proof_id=:proof AND subject_ref=:subject"""), {**args, "epoch": epoch})
                return True
