"""Applied journal prefix and bounded local deletion. The caller verifies source."""
from hashlib import sha256
import re
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.core_transfer import has_core_schema, has_snapshot_proof_schema
from pymentor_analytics.adapters.postgres.activity_projection import has_activity_projection_schema
from pymentor_analytics.adapters.postgres.activity_course_projection import has_activity_course_projection_schema
from pymentor_analytics.adapters.postgres.activity_day_projection import has_activity_day_projection_schema
from pymentor_analytics.contracts.locking_v1 import subject_lock_key
from pymentor_analytics.contracts.privacy_control_v1 import JournalEntry, deletion_hash
from pymentor_analytics.contracts.privacy_storage_v1 import (
    MAX_DELETE_PAGE, AppliedCursor, DeletionProgress, PrivacyAuthorityMismatch,
    PrivacyConflict, PrivacyIntegrityError, PrivacySequenceGap,
)

_digest = sha256(b"analytics:privacy-control-order:v1\0").digest()
_CONTROL_LOCK = (int.from_bytes(_digest[:4], "big", signed=True),
                 int.from_bytes(_digest[4:8], "big", signed=True))
_AUTHORITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z", re.ASCII)


def _valid_authority(authority_id):
    if not isinstance(authority_id, str) or _AUTHORITY.fullmatch(authority_id) is None:
        raise ValueError("invalid privacy authority identity")


class PostgresPrivacyStore:
    """Use only with a dedicated privacy-role Database; never authenticate input here."""
    def __init__(self, database: Database):
        self.database = database

    async def _before_commit(self):
        """Private test seam for a crash before transaction commit."""

    async def _after_commit(self):
        """Private test seam for a lost reply after transaction commit."""

    async def _locked_state(self, session, authority_id):
        await session.execute(text("SELECT pg_advisory_xact_lock(:first,:second)"),
                              {"first": _CONTROL_LOCK[0], "second": _CONTROL_LOCK[1]})
        await session.execute(text("""INSERT INTO analytics.privacy_state(singleton,authority_id,applied_through)
            VALUES (true,:authority,0) ON CONFLICT (singleton) DO NOTHING"""), {"authority": authority_id})
        row = (await session.execute(text("SELECT authority_id,applied_through FROM analytics.privacy_state WHERE singleton=true"))).one()
        if row.authority_id != authority_id:
            raise PrivacyAuthorityMismatch("privacy authority is immutable")
        return row

    async def cursor(self, authority_id: str) -> AppliedCursor:
        _valid_authority(authority_id)
        async with self.database.transaction() as session:
            row = await self._locked_state(session, authority_id)
            return AppliedCursor(row.authority_id, row.applied_through)

    async def apply(self, authority_id: str, entry: JournalEntry, limit: int = MAX_DELETE_PAGE) -> DeletionProgress:
        _valid_authority(authority_id)
        if not isinstance(entry, JournalEntry) or type(limit) is not int or not 1 <= limit <= MAX_DELETE_PAGE:
            raise ValueError("invalid privacy entry or page bound")
        # Revalidate copied/constructed instances and never accept a trust flag.
        try:
            entry = JournalEntry.model_validate_json(entry.model_dump_json())
        except (ValidationError, ValueError, TypeError):
            raise PrivacyConflict("invalid privacy journal entry") from None
        if entry.payload_hash != deletion_hash(entry.producer, entry.control):
            raise PrivacyConflict("privacy control hash mismatch")
        if entry.seq > 2**63 - 1 or entry.control.epoch > 2**63 - 1:
            raise PrivacyConflict("privacy sequence or epoch exceeds storage range")
        subject, control_id = entry.control.subject_ref, entry.control.control_id
        args = {"seq": entry.seq, "control": control_id, "hash": entry.payload_hash, "subject": subject}
        async with self.database.transaction() as session:
            state = await self._locked_state(session, authority_id)
            previous_id = (await session.execute(text("""SELECT seq,payload_hash FROM analytics.privacy_receipts
                WHERE control_id=:control"""), args)).one_or_none()
            prior = (await session.execute(text("""SELECT seq,control_id,payload_hash,subject_ref,fence_epoch,phase
                FROM analytics.privacy_receipts WHERE seq=:seq"""), args)).one_or_none()
            if (previous_id and (previous_id.seq != entry.seq or previous_id.payload_hash != entry.payload_hash)) or (
                    prior and (prior.control_id != control_id or prior.payload_hash != entry.payload_hash or prior.subject_ref != subject)):
                raise PrivacyConflict("conflicting privacy journal identity")
            if entry.seq <= state.applied_through:
                if prior is None or prior.phase != "complete":
                    raise PrivacyIntegrityError("applied privacy receipt missing")
                return DeletionProgress(entry.seq, subject, prior.fence_epoch, state.applied_through, True)
            if entry.seq != state.applied_through + 1:
                raise PrivacySequenceGap("privacy journal sequence is not contiguous")
            if prior and prior.phase != "purging":
                raise PrivacyIntegrityError("privacy receipt and cursor disagree")
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": subject_lock_key(subject)})
            fence = (await session.execute(text("""SELECT epoch,deleted FROM analytics.subject_fences
                WHERE subject_ref=:subject"""), args)).one_or_none()
            if prior is None:
                other = (await session.execute(text("""SELECT subject_ref FROM analytics.subject_fences
                    WHERE control_id=:control AND subject_ref<>:subject"""), args)).first()
                if other:
                    raise PrivacyConflict("control identity belongs to another subject")
                epoch = max(fence.epoch if fence else 0, entry.control.epoch)
                await session.execute(text("""INSERT INTO analytics.subject_fences(subject_ref,epoch,deleted,control_id)
                    VALUES (:subject,:epoch,true,:control) ON CONFLICT (subject_ref) DO UPDATE
                    SET epoch=excluded.epoch,deleted=true,control_id=excluded.control_id"""), {**args, "epoch": epoch})
                await session.execute(text("""INSERT INTO analytics.privacy_receipts(seq,control_id,payload_hash,subject_ref,fence_epoch,phase)
                    VALUES (:seq,:control,:hash,:subject,:epoch,'purging')"""), {**args, "epoch": epoch})
            else:
                if fence is None or not fence.deleted or fence.epoch < prior.fence_epoch:
                    raise PrivacyIntegrityError("committed subject fence missing")
                epoch = prior.fence_epoch

            # All in-flight claims are fenced by the tombstone immediately.
            # Lease rows themselves are removed in bounded pages below.
            removed = {}
            proof_schema = await has_snapshot_proof_schema(session)
            activity_schema = await has_activity_projection_schema(session)
            day_schema = await has_activity_day_projection_schema(session)
            course_schema = await has_activity_course_projection_schema(session)
            tables = [
                ("facts", "activity_facts", "producer_namespace,event_id"),
                ("results", "processing_results", "consumer,consumer_version"),
                ("leases", "processing_leases", "consumer,consumer_version"),
                ("replays", "processing_replays", "consumer,consumer_version,generation"),
                ("windows", "admission_subject_windows", "subject_ref"),
            ]
            if proof_schema:
                tables.append(("proofs", "core_snapshot_proofs", "producer_namespace,proof_id"))
            if activity_schema:
                tables.append(("activity_buckets", "activity_projection_buckets", "bucket_start,metric_version"))
            if day_schema:
                # A bucket DELETE cannot re-register a deleted subject: the fence
                # above is committed with each bounded purge page.
                tables.extend([
                    ("activity_days", "activity_projection_days", "local_day,metric_version"),
                    ("activity_day_sources", "activity_projection_day_sources", "local_day,metric_version"),
                ])
            if course_schema:
                tables.extend([
                    ("activity_course_buckets", "activity_course_buckets", "bucket_start,metric_version,course_id"),
                    ("activity_course_days", "activity_course_days", "local_day,metric_version,course_id"),
                ])
            for label, table, ordering in tables:
                result = await session.execute(text(f"""DELETE FROM analytics.{table} WHERE ctid IN (
                    SELECT ctid FROM analytics.{table} WHERE subject_ref=:subject ORDER BY {ordering} LIMIT :limit)
                    RETURNING subject_ref"""), {**args, "limit": limit})
                removed[label] = len(result.all())
            # a005 validates subject-coherent FK ownership; fact deletion cascades
            # jobs/receipts. Explicit existence checks fail closed on any remainder.
            core_schema = await has_core_schema(session)
            extra_checks = (" OR EXISTS(SELECT 1 FROM analytics.core_transfer_entities WHERE subject_ref=:subject)"
                            " OR EXISTS(SELECT 1 FROM analytics.core_transfer_projection WHERE subject_ref=:subject)"
                            if core_schema else "")
            if proof_schema:
                extra_checks += " OR EXISTS(SELECT 1 FROM analytics.core_snapshot_proofs WHERE subject_ref=:subject)"
            if activity_schema:
                extra_checks += " OR EXISTS(SELECT 1 FROM analytics.activity_projection_buckets WHERE subject_ref=:subject)"
            if day_schema:
                extra_checks += (" OR EXISTS(SELECT 1 FROM analytics.activity_projection_days WHERE subject_ref=:subject)"
                                 " OR EXISTS(SELECT 1 FROM analytics.activity_projection_day_sources WHERE subject_ref=:subject)")
            if course_schema:
                extra_checks += (" OR EXISTS(SELECT 1 FROM analytics.activity_course_buckets WHERE subject_ref=:subject)"
                                 " OR EXISTS(SELECT 1 FROM analytics.activity_course_days WHERE subject_ref=:subject)")
            remaining = (await session.execute(text("""SELECT
                EXISTS(SELECT 1 FROM analytics.activity_facts WHERE subject_ref=:subject)
                OR EXISTS(SELECT 1 FROM analytics.processing_jobs WHERE subject_ref=:subject)
                OR EXISTS(SELECT 1 FROM analytics.processing_receipts WHERE subject_ref=:subject)
                OR EXISTS(SELECT 1 FROM analytics.processing_results WHERE subject_ref=:subject)
                OR EXISTS(SELECT 1 FROM analytics.processing_leases WHERE subject_ref=:subject)
                OR EXISTS(SELECT 1 FROM analytics.processing_replays WHERE subject_ref=:subject)
                OR EXISTS(SELECT 1 FROM analytics.admission_subject_windows WHERE subject_ref=:subject)
            """ + extra_checks), args)).scalar_one()
            applied = state.applied_through
            if not remaining:
                await session.execute(text("UPDATE analytics.privacy_receipts SET phase='complete' WHERE seq=:seq"), args)
                await session.execute(text("UPDATE analytics.privacy_state SET applied_through=:seq WHERE singleton=true"), args)
                applied = entry.seq
            result = DeletionProgress(entry.seq, subject, epoch, applied, not remaining,
                                      removed["facts"], removed["results"], removed["leases"],
                                      removed["replays"], removed["windows"])
            await self._before_commit()
        await self._after_commit()
        return result


class PostgresSubjectFence:
    """Early per-request check only; final write/read locks remain mandatory."""
    def __init__(self, database: Database):
        self.database = database

    async def permits(self, subject: UUID) -> bool:
        if not isinstance(subject, UUID):
            raise ValueError("invalid subject")
        async with self.database.transaction() as session:
            await session.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": subject_lock_key(subject)})
            value = (await session.execute(text("SELECT deleted FROM analytics.subject_fences WHERE subject_ref=:subject"),
                                           {"subject": subject})).scalar_one_or_none()
            return value is not True
