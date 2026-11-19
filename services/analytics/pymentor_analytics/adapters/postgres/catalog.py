"""Immutable catalog history; intentionally independent of subject data."""
import asyncio
import hashlib
import json
import time

from sqlalchemy import text

from pymentor_analytics.contracts.catalog_v1 import Delta, Snapshot, _preserves
from pymentor_analytics.modules.collector.ports import IntakeUnavailable


async def has_catalog_schema(session):
    return (await session.execute(text("SELECT to_regclass('analytics.catalog_versions') IS NOT NULL"))).scalar_one()


class PostgresCatalog:
    def __init__(self, database, namespace):
        if not namespace:
            raise ValueError("catalog namespace required")
        self.database, self.namespace = database, namespace

    async def accept(self, namespace, message, fingerprint, *, deadline):
        args = {"ns": namespace, "course": message.catalog.course_id, "version": message.catalog_version,
                "sequence": message.sequence, "fingerprint": fingerprint,
                "body": json.dumps(message.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntakeUnavailable()
        lock = int.from_bytes(hashlib.sha256(("catalog-v1\0" + namespace + "\0" + message.catalog.course_id).encode()).digest()[:8], "big", signed=True)
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
                old = (await session.execute(text("SELECT fingerprint FROM analytics.catalog_versions WHERE producer_namespace=:ns AND course_id=:course AND catalog_version=:version"), args)).scalar_one_or_none()
                if old is not None:
                    return "accepted" if old == fingerprint else "conflict"
                prior = (await session.execute(text("SELECT body FROM analytics.catalog_versions WHERE producer_namespace=:ns AND course_id=:course ORDER BY sequence DESC LIMIT 1"), args)).scalar_one_or_none()
                if prior is None:
                    if not isinstance(message, Snapshot):
                        return "conflict"
                else:
                    prior_snapshot = {k: v for k, v in prior.items() if k != "base_version"}
                    previous = Snapshot.model_validate_json(json.dumps({**prior_snapshot, "kind": "analytics_catalog_snapshot"}, ensure_ascii=False))
                    if not isinstance(message, Delta) or message.sequence != previous.sequence + 1 or message.base_version != previous.catalog_version:
                        return "conflict"
                    try:
                        _preserves(previous.catalog, message.catalog)
                    except ValueError:
                        return "conflict"
                await session.execute(text("""INSERT INTO analytics.catalog_versions
                    (producer_namespace,course_id,catalog_version,sequence,fingerprint,body)
                    VALUES (:ns,:course,:version,:sequence,:fingerprint,CAST(:body AS jsonb))"""), args)
        return "accepted"

    async def receipt(self, namespace, course_id, catalog_version, fingerprint, *, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntakeUnavailable()
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                stored = (await session.execute(text("SELECT fingerprint FROM analytics.catalog_versions WHERE producer_namespace=:ns AND course_id=:course AND catalog_version=:version"), {"ns": namespace, "course": course_id, "version": catalog_version})).scalar_one_or_none()
        return "absent" if stored is None else "accepted" if stored == fingerprint else "conflict"

    async def known(self, *, course_id, catalog_version, lesson_id, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IntakeUnavailable()
        async with asyncio.timeout(remaining):
            async with self.database.transaction() as session:
                body = (await session.execute(text("SELECT body FROM analytics.catalog_versions WHERE producer_namespace=:ns AND course_id=:course AND catalog_version=:version"), {"ns": self.namespace, "course": course_id, "version": catalog_version})).scalar_one_or_none()
        if body is None:
            return False
        image = body["catalog"]
        if lesson_id is None:
            return True
        active_tracks = {x["track_id"] for x in image["tracks"] if not x["tombstone"]}
        return any(not x["tombstone"] and x["track_id"] in active_tracks and lesson_id in (x["lesson_id"], *x["aliases"]) for x in image["lessons"])
