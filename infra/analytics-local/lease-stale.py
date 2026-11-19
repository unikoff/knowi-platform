"""Attempt a previously captured synthetic claim; input never contains credentials."""
import asyncio
import json
import os
import sys
from uuid import UUID

from pymentor_analytics.adapters.postgres.database import Database
from pymentor_analytics.adapters.postgres.processor import PostgresProcessor
from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient, AuthorityConfig
from pymentor_analytics.app.config import load_settings
from pymentor_analytics.app.privacy import PrivacyRuntime
from pymentor_analytics.contracts.processing_v1 import ClaimedBatch, ClaimedFact, StaleClaim, synthetic_delta
from pymentor_analytics.contracts.v2 import AcceptedEvent


async def main():
    value = json.load(sys.stdin)
    facts = tuple(ClaimedFact(AcceptedEvent.model_validate_json(json.dumps(item["record"])), item["already_applied"]) for item in value["facts"])
    batch = ClaimedBatch(value["consumer"], value["version"], UUID(value["subject_ref"]), value["subject_epoch"],
                         UUID(value["worker"]), value["generation"], UUID(value["batch_id"]), facts)
    database = Database(load_settings("processor"))
    try:
        config = AuthorityConfig.from_json(os.environ["ANALYTICS_PRIVACY_AUTHORITY_JSON"])
        gate = PrivacyRuntime(AuthorityClient(config), database)
        await gate.tick()
        if not await gate.is_ready():
            raise RuntimeError("privacy gate closed")
        database.transaction_guard = gate.check_in_transaction
        try:
            await PostgresProcessor(database).commit(batch, synthetic_delta(batch.facts))
        except StaleClaim:
            print(json.dumps({"status": "stale_claim_rejected"}), flush=True)
            return
        print(json.dumps({"status": "stale_claim_unexpectedly_accepted"}), flush=True)
        raise SystemExit(1)
    finally:
        await database.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(json.dumps({"status": "stale_check_failed_or_unknown", "type": type(error).__name__}), flush=True)
        raise SystemExit(1)
