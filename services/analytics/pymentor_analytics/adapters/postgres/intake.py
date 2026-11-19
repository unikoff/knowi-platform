"""Transactional, idempotent synthetic Collector storage adapter."""
import asyncio
import time
from typing import Literal

import asyncpg
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from pymentor_analytics.adapters.postgres.activity import ActivityStorage
from pymentor_analytics.adapters.postgres.backpressure import PostgresBackpressure
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.contracts.locking_v1 import event_dedup_lock_key, subject_lock_key
from pymentor_analytics.contracts.metadata_v1 import validate_new_record
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.v2 import AcceptedEvent, canonical_hash
from pymentor_analytics.contracts.work_v1 import SYNTHETIC_SUBSCRIPTIONS
from pymentor_analytics.modules.collector.ports import IntakeForbidden, IntakeUnavailable


class PostgresIntake:
    def __init__(self, database: Database, *, subscriptions=SYNTHETIC_SUBSCRIPTIONS):
        if (not isinstance(subscriptions, tuple) or len(set(subscriptions)) != len(subscriptions)
                or not set(subscriptions) <= set(SYNTHETIC_SUBSCRIPTIONS)):
            raise ValueError("invalid intake subscriptions")
        self.database = database
        self.subscriptions = subscriptions
        self.backpressure = PostgresBackpressure()

    async def _after_fact(self):
        """Internal test seam: subclasses may inject faults inside the transaction."""

    async def _before_commit(self):
        """Internal test seam immediately before the transaction exits."""

    async def _after_commit(self):
        """Internal test seam for a lost response after a successful commit."""

    async def accept(self, record: AcceptedEvent, *, deadline: float) -> Literal["accepted", "duplicate", "conflict"]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntakeUnavailable("intake deadline expired")
        try:
            async with asyncio.timeout(remaining):
                async with self.database.transaction() as session:
                    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                                          {"key": subject_lock_key(record.subject_ref)})
                    fence = await session.execute(text("""
                        SELECT deleted FROM analytics.subject_fences WHERE subject_ref=:subject
                    """), {"subject": record.subject_ref})
                    if fence.scalar_one_or_none() is True:
                        raise IntakeForbidden("subject lifecycle is deleted")
                    first, second = event_dedup_lock_key(record.producer_namespace, record.event.event_id)
                    await session.execute(text("SELECT pg_advisory_xact_lock(:first,:second)"),
                                          {"first": first, "second": second})
                    prior = (await session.execute(text("""
                        SELECT canonical_hash FROM analytics.activity_facts
                        WHERE producer_namespace=:producer AND event_id=:event_id
                    """), {"producer": record.producer_namespace,
                           "event_id": record.event.event_id})).scalar_one_or_none()
                    outcome = ("inserted" if prior is None else
                               "duplicate" if prior == canonical_hash(record) else "conflict")
                    if outcome != "conflict":
                        subscriptions = () if record.event.type == "tool_segment" else self.subscriptions
                        existing_jobs = 0
                        if outcome == "duplicate":
                            for sub in subscriptions:
                                result = await session.execute(text("""
                                    SELECT 1 FROM analytics.processing_jobs
                                    WHERE producer_namespace=:producer AND event_id=:event_id
                                      AND consumer=:consumer AND consumer_version=:version
                                """), {"producer": record.producer_namespace, "event_id": record.event.event_id,
                                       "consumer": sub.consumer, "version": sub.version})
                                existing_jobs += int(result.scalar_one_or_none() is not None)
                        if outcome == "inserted":
                            validate_new_record(record)
                        await self.backpressure.check(session, subject=record.subject_ref,
                                                      new_fact=outcome == "inserted",
                                                      new_jobs=len(subscriptions) - existing_jobs)
                        if outcome == "inserted":
                            # A legacy writer bypassing the event lock is unsupported.
                            # Any race rolls back quota and is reconciled by the same ID.
                            if await ActivityStorage(session).insert(record) != "inserted":
                                raise IntakeUnavailable("concurrent writer; reconcile using the same identity")
                            await self._after_fact()
                        for subscription in subscriptions:
                            await session.execute(text("""
                                INSERT INTO analytics.processing_jobs
                                  (producer_namespace,event_id,consumer,consumer_version,subject_ref)
                                VALUES (:producer,:event_id,:consumer,:version,:subject)
                                ON CONFLICT (producer_namespace,event_id,consumer,consumer_version) DO NOTHING
                            """), {"producer": record.producer_namespace, "event_id": record.event.event_id,
                                   "consumer": subscription.consumer, "version": subscription.version,
                                   "subject": record.subject_ref})
                    await self._before_commit()
                await self._after_commit()
            return "accepted" if outcome == "inserted" else outcome
        except (TimeoutError, OSError, SQLAlchemyError, asyncpg.PostgresError, PrivacyUnavailable):
            raise IntakeUnavailable("intake outcome unavailable; reconcile using the same identity") from None
