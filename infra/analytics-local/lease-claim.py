"""One-shot synthetic processor claim; credentials are supplied by Kubernetes Secrets."""
import asyncio
import json
import os
from uuid import UUID, uuid4

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.processor import PostgresProcessor
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
from pymentor_analytics.app.config import load_settings
from pymentor_analytics.app.privacy import PrivacyRuntime


async def main():
    database = Database(load_settings("processor"))
    try:
        config = AuthorityConfig.from_json(os.environ["ANALYTICS_PRIVACY_AUTHORITY_JSON"])
        gate = PrivacyRuntime(AuthorityClient(config), database)
        await gate.tick()
        if not await gate.is_ready():
            raise RuntimeError("privacy gate closed")
        database.transaction_guard = gate.check_in_transaction
        batch = await PostgresProcessor(database).claim("synthetic.activity", 1, uuid4(), 100, 30)
        if (batch is None or str(batch.subject_ref) != os.environ["FIXTURE_SUBJECT"]
                or len(batch.facts) != 1 or str(batch.facts[0].record.event.event_id) != os.environ["FIXTURE_EVENT"]):
            raise RuntimeError("unexpected claim")
        value = {"consumer": batch.consumer, "version": batch.version, "subject_ref": str(batch.subject_ref),
                 "subject_epoch": batch.subject_epoch, "worker": str(batch.worker), "generation": batch.generation,
                 "batch_id": str(batch.batch_id), "facts": [{"record": f.record.model_dump(mode="json"),
                 "already_applied": f.already_applied} for f in batch.facts]}
        print(json.dumps({"status": "claimed_without_finish", "batch": value}), flush=True)
    finally:
        await database.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(json.dumps({"status": "claim_failed_or_unknown", "type": type(error).__name__}), flush=True)
        raise SystemExit(1)
