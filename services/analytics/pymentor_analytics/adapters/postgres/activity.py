"""Fact primitive only: no authorization, privacy admission, jobs or HTTP ACK."""
from typing import Literal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from pymentor_analytics.contracts.v2 import AcceptedEvent, canonical_hash


class ActivityStorage:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def insert(self, record: AcceptedEvent) -> Literal["inserted", "duplicate", "conflict"]:
        event = record.event
        digest = canonical_hash(record)
        params = {"producer": record.producer_namespace, "event_id": event.event_id,
                  "subject": record.subject_ref, "course": event.course_id,
                  "catalog": event.catalog_version, "lesson": event.lesson_id,
                  "started": event.started_at, "received": record.received_at,
                  "digest": digest, "payload": record.model_dump_json()}
        source_column = ""
        source_value = ""
        if event.type == "tool_segment":
            source_column = ",source_kind"
            source_value = ",'browser_tool_v1'"
        sql = f"""
            INSERT INTO analytics.activity_facts
              (producer_namespace,event_id,subject_ref,course_id,catalog_version,
               lesson_id,started_at,received_at,canonical_hash,accepted_event{source_column})
            VALUES (:producer,:event_id,:subject,:course,:catalog,:lesson,
                    :started,:received,:digest,CAST(:payload AS jsonb){source_value})
            ON CONFLICT (producer_namespace,event_id) DO NOTHING RETURNING canonical_hash
        """
        result = await self.session.execute(text(sql), params)
        if result.scalar_one_or_none() is not None:
            return "inserted"
        existing = await self.session.execute(text("""
            SELECT canonical_hash FROM analytics.activity_facts
            WHERE producer_namespace=:producer AND event_id=:event_id
        """), params)
        return "duplicate" if existing.scalar_one() == digest else "conflict"
