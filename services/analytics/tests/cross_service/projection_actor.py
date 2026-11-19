"""One isolated, privacy-gated synthetic a010 worker claim/commit action."""
import asyncio
import json
import os
from pathlib import Path
import time
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from .guards import load, verify_databases
from pymentor_analytics.adapters.postgres.activity_projection import PostgresActivityProjection
from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.privacy import PrivacyRuntime
from pymentor_analytics.modules.analytics.activity.bucket import normalize_subject_bucket
from pymentor_analytics.modules.analytics.activity.projection_ports import BucketClaim, StaleBucketClaim

ROOT = Path(__file__).resolve().parents[2]


async def run():
    folder = Path(os.environ["CROSS_SERVICE_PRIVATE_DIR"]).resolve(strict=True)
    fixture = load(str(folder / "fixture.json"), ROOT)
    verify_databases(fixture)
    target = fixture["databases"]["analytics"]["name"]
    dsn = os.environ["CROSS_SERVICE_ACTOR_DSN"]
    parsed = urlsplit(dsn)
    if (parsed.scheme != "postgresql" or parsed.hostname != "127.0.0.1" or parsed.port != 57392
            or parsed.path != "/" + target or parsed.username != target + "_processor" or parsed.query or parsed.fragment):
        raise ValueError("invalid actor target")
    subject = UUID(os.environ["CROSS_SERVICE_ACTOR_SUBJECT"])
    run_id = UUID(os.environ["CROSS_SERVICE_ACTOR_RUN_ID"])
    phase = os.environ["CROSS_SERVICE_ACTOR_PHASE"]
    saved = folder / "worker-old-claim.json"
    database = Database(Settings(database_url=dsn, db_timeout_seconds=5))
    runtime = PrivacyRuntime(AuthorityClient(AuthorityConfig.from_json(os.environ["CROSS_SERVICE_ACTOR_AUTHORITY"])), database)
    store = PostgresActivityProjection(database, expected_run_id=run_id)
    deadline = lambda: time.monotonic() + 5
    try:
        await runtime.tick()
        if not await runtime.is_ready():
            raise RuntimeError("signed authority barrier is not ready")
        database.transaction_guard = runtime.check_in_transaction
        if phase in {"old-claim", "new-commit"}:
            claim = await store.claim(uuid4(), deadline=deadline())
            if claim is None or claim.subject_ref != subject:
                raise RuntimeError("unexpected worker claim")
            claim, records = await store.load(claim, deadline=deadline())
            projection = normalize_subject_bucket(records, subject_ref=subject, bucket_start=claim.bucket_start)
            if phase == "old-claim":
                value = {"claim": {"subject_ref": str(claim.subject_ref), "bucket_start": claim.bucket_start.isoformat(),
                         "generation": claim.generation, "subject_epoch": claim.subject_epoch, "claim_id": str(claim.claim_id),
                         "owner": str(claim.owner), "run_id": str(claim.run_id)}, "projection": projection}
                with saved.open("x", encoding="utf-8") as stream:
                    json.dump(value, stream)
            else:
                old = json.loads(saved.read_text(encoding="utf-8"))["claim"]
                if claim.generation != old["generation"] + 1:
                    raise RuntimeError("new worker did not obtain the next generation")
                await store.commit(claim, projection, deadline=deadline())
        elif phase == "old-stale":
            value = json.loads(saved.read_text(encoding="utf-8")); c = value["claim"]
            claim = BucketClaim(UUID(c["subject_ref"]), datetime.fromisoformat(c["bucket_start"]), c["generation"],
                                c["subject_epoch"], UUID(c["claim_id"]), UUID(c["owner"]), UUID(c["run_id"]))
            try:
                await store.commit(claim, value["projection"], deadline=deadline())
            except StaleBucketClaim:
                return
            raise RuntimeError("stale worker committed")
        else:
            raise ValueError("invalid actor phase")
    finally:
        await runtime.stop()
        await database.close()


if __name__ == "__main__":
    asyncio.run(run())
