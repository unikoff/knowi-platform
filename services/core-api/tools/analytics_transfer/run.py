"""Manual synthetic-only local transfer operations; never run by Core startup."""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _synthetic_database(url, expected):
    parsed = make_url(url)
    if (parsed.drivername != "postgresql+psycopg" or parsed.host not in {"127.0.0.1", "localhost", "::1"}
            or parsed.database != expected or not expected.startswith("analytics_delivery_test_")
            or parsed.query):
        raise ValueError("synthetic PostgreSQL target required")
    return parsed


def _create_engine(parsed_url):
    # This CLI runs one operation at a time. Close each connection immediately
    # instead of retaining an additional QueuePool alongside Core runtime pools.
    return create_engine(parsed_url, poolclass=NullPool,
                         connect_args={"connect_timeout": 3, "options": "-c statement_timeout=3000 -c lock_timeout=1000"})


def _relay(sessions, *, proofs_only=False):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key, Encoding, PublicFormat
    from pymentor_core.adapters.analytics_http.privacy import AuthorityClient
    from pymentor_core.adapters.analytics_http.transport import TransferHttp
    from pymentor_core.modules.analytics_delivery.signing import Signer
    from pymentor_core.adapters.sql.analytics_delivery_ops.worker import run_once
    env = os.environ
    signer = Signer(issuer=env["PYMENTOR_ANALYTICS_PRODUCER_ISSUER"], kid=env["PYMENTOR_ANALYTICS_PRODUCER_KID"],
                    private_pem=Path(env["PYMENTOR_ANALYTICS_PRODUCER_KEY_FILE"]).read_bytes(), purpose="producer")
    command = load_pem_private_key(Path(env["PYMENTOR_ANALYTICS_COMMAND_KEY_FILE"]).read_bytes(), password=None)
    producer = env["PYMENTOR_ANALYTICS_PRODUCER_ID"]
    grant_public = load_pem_public_key(Path(env["PYMENTOR_ANALYTICS_GRANT_PUBLIC_KEY_FILE"]).read_bytes())
    authority_pem = Path(env["PYMENTOR_ANALYTICS_AUTHORITY_PUBLIC_KEY_FILE"]).read_bytes()
    authority_public = load_pem_public_key(authority_pem)
    keys = [signer._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
            command.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
            grant_public.public_bytes(Encoding.Raw, PublicFormat.Raw),
            authority_public.public_bytes(Encoding.Raw, PublicFormat.Raw)]
    if len(set(keys)) != len(keys):
        raise ValueError("signing keys must be independent")
    token = lambda: signer.producer(producer)
    transport = TransferHttp(env["PYMENTOR_ANALYTICS_TRANSFER_URL"], token)
    authority = AuthorityClient(endpoint=env["PYMENTOR_ANALYTICS_AUTHORITY_URL"],
        authority_id=env["PYMENTOR_ANALYTICS_AUTHORITY_ID"], issuer=env["PYMENTOR_ANALYTICS_AUTHORITY_ISSUER"],
        public_keys={env["PYMENTOR_ANALYTICS_AUTHORITY_KID"]: authority_pem},
        producer=producer, producer_token=token, command_key=command)
    if proofs_only:
        from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_relay import run_once as run_proofs
        return {"claimed": asyncio.run(run_proofs(sessions, transport))}
    return {"claimed": asyncio.run(run_once(sessions, transport, authority=authority))}


def main():
    parser = argparse.ArgumentParser(description="Explicit synthetic Core transfer operation")
    parser.add_argument("operation", choices=["provision", "export-init", "export-page", "seal-backfill", "prepare-init", "prepare-page", "prepare-seal", "begin-outbox", "begin-replay", "replay-page", "finish-replay", "relay-once", "snapshot-stage", "proof-relay-once"])
    parser.add_argument("--expected-db", required=True)
    parser.add_argument("--stream", choices=["activity", "progress", "tasks"])
    parser.add_argument("--cursor", type=int, default=0)
    parser.add_argument("--high-watermark", type=int)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--writers-stopped", action="store_true", help="assert all activity writers are quiesced for replay")
    parser.add_argument("--coverage-proved", action="store_true", help="assert independent parity and coverage gates are satisfied")
    args = parser.parse_args()
    parsed_url = _synthetic_database(os.environ["DATABASE_URL"], args.expected_db)
    from pymentor_core.adapters.sql.analytics_delivery_ops import export, integration
    if not integration.enabled():
        raise ValueError("capture is disabled")
    if args.operation in {"export-init", "export-page", "seal-backfill"} and not args.stream:
        raise ValueError("stream required")
    if args.operation == "provision" and args.high_watermark is None:
        raise ValueError("high watermark required")
    engine = _create_engine(parsed_url)
    try:
        with engine.connect() as connection:
            if connection.execute(text("SELECT current_database()")).scalar_one() != args.expected_db:
                raise ValueError("database identity mismatch")
        sessions = sessionmaker(engine)
        if args.operation in {"relay-once", "proof-relay-once"}:
            result = _relay(sessions, proofs_only=args.operation == "proof-relay-once")
        else:
            with sessions() as db:
                if args.operation == "provision":
                    result = export.provision_lifecycles_page(db, cursor=args.cursor, high_watermark=args.high_watermark, limit=args.limit)
                elif args.operation == "export-init":
                    result = {"high_watermark": export.initialize(db, args.stream)}
                elif args.operation == "seal-backfill":
                    export.seal_backfill(db, args.stream)
                    result = {"sealed": True}
                elif args.operation == "replay-page":
                    result = export.replay_core_page(db, writers_stopped=args.writers_stopped, limit=args.limit)
                elif args.operation == "snapshot-stage":
                    from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_export import stage_subject
                    proof = stage_subject(db, int(os.environ["PYMENTOR_ANALYTICS_SNAPSHOT_USER_ID"]),
                                          os.environ["PYMENTOR_ANALYTICS_SNAPSHOT_ACCOUNT_REF"])
                    result = {"staged": True, "cut_revision": proof["cut_revision"], "entity_count": proof["entity_count"]}
                elif args.operation in {"prepare-init", "prepare-page", "prepare-seal", "begin-outbox", "begin-replay", "finish-replay"}:
                    from pymentor_core.adapters.sql.analytics_delivery_ops import cutover
                    if args.operation == "prepare-init":
                        result = cutover.begin_preparation(db, old_writers_stopped=args.writers_stopped)
                    elif args.operation == "prepare-page":
                        result = cutover.prepare_page(db, limit=args.limit)
                    elif args.operation == "prepare-seal":
                        cutover.seal_preparation(db)
                    elif args.operation == "begin-outbox":
                        cutover.begin_outbox(db, writers_stopped=args.writers_stopped, coverage_proved=args.coverage_proved)
                    elif args.operation == "begin-replay":
                        cutover.begin_replay(db, writers_stopped=args.writers_stopped)
                    else:
                        cutover.finish_replay(db, writers_stopped=args.writers_stopped)
                    if args.operation not in {"prepare-init", "prepare-page"}:
                        result = {"operation": args.operation}
                else:
                    result = export.export_page(db, args.stream, limit=args.limit)
                db.commit()
        print(result)
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Driver exceptions can contain credentials or URLs. Avoid echoing them.
        raise SystemExit("Synthetic transfer operation failed; check configuration and bounded state") from None
