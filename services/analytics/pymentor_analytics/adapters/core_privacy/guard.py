"""Fail-closed Core deletion reconciliation for local Analytics readers."""
import asyncio
import json
from sqlalchemy import text

from pymentor_analytics.contracts.privacy_control_v1 import deletion_hash
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable


class CoreDeletionGuard:
    def __init__(self, source, status_database, producer):
        self.source, self.status_database, self.producer = source, status_database, producer

    async def check(self, session):
        try:
            controls = await self.source.snapshot()
            rows = [{"control": str(c.control_id), "subject": str(c.subject_ref), "epoch": c.epoch,
                     "hash": deletion_hash(self.producer, c)} for c in controls]
            async with asyncio.timeout(5):
                async with self.status_database.sessions.begin() as status:
                    good = (await status.execute(text("""WITH expected AS (
                        SELECT control::uuid,subject::uuid,epoch,hash
                        FROM jsonb_to_recordset(CAST(:items AS jsonb)) AS x(control text,subject text,epoch bigint,hash text)
                    ) SELECT EXISTS(SELECT 1 FROM analytics.privacy_state WHERE singleton=true)
                      AND NOT EXISTS (SELECT 1 FROM expected e
                        LEFT JOIN analytics.privacy_receipts r ON r.control_id=e.control
                        LEFT JOIN analytics.subject_fences f ON f.subject_ref=e.subject
                        LEFT JOIN analytics.privacy_state s ON s.singleton=true
                        WHERE r.control_id IS NULL OR r.subject_ref IS DISTINCT FROM e.subject
                          OR r.fence_epoch IS NULL OR r.fence_epoch < e.epoch OR r.payload_hash IS DISTINCT FROM e.hash
                          OR r.phase IS DISTINCT FROM 'complete' OR s.applied_through IS NULL OR r.seq > s.applied_through
                          OR f.deleted IS DISTINCT FROM true OR f.epoch IS NULL OR f.epoch < r.fence_epoch)"""),
                        {"items": json.dumps(rows, separators=(",", ":"))})).scalar_one()
            if good is not True: raise PrivacyUnavailable()
        except asyncio.CancelledError:
            raise
        except Exception:
            raise PrivacyUnavailable() from None
