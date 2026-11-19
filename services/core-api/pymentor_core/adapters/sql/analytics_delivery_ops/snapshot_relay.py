"""Explicit bounded proof relay; accepted is not treated as verified coverage."""
from datetime import datetime, timezone
import json
import random
import time

from pymentor_core.adapters.sql.analytics_delivery_ops import snapshot_sql
from pymentor_core.modules.analytics_delivery.relay import Receipt, retry_delay
from pymentor_core.modules.analytics_delivery.snapshot_contract import proof_fingerprint, validate_proof
from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_export import proof_enabled


async def run_once(session_factory, transport, *, limit=10, lease_seconds=30, max_attempts=30, deadline_seconds=5):
    if not proof_enabled() or not 0 < deadline_seconds <= 10 or lease_seconds <= deadline_seconds:
        raise ValueError("proof relay unavailable")
    deadline = time.monotonic() + deadline_seconds
    with session_factory() as db:
        rows = snapshot_sql.claim(db, datetime.now(timezone.utc), limit=limit, lease_seconds=lease_seconds, max_attempts=max_attempts)
        db.commit()
    for row in rows:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            with session_factory() as db:
                snapshot_sql.finish(db, row["proof_id"], row["claim_token"],
                    "reconcile" if row["uncertain"] else "retry", datetime.now(timezone.utc))
                db.commit()
            continue
        with session_factory() as db:
            snapshot_sql.mark_sending(db, row["proof_id"], row["claim_token"])
            db.commit()
        action = "reconcile"
        try:
            proof = json.loads(row["payload"])
            validate_proof(proof)
            if proof["proof_id"] != row["proof_id"] or proof_fingerprint(proof) != row["fingerprint"]:
                raise ValueError("proof identity conflict")
            remaining = deadline - time.monotonic()
            if remaining > 0:
                if row["accepted"] or row["uncertain"]:
                    receipt, coverage = await transport.lookup_proof(row["proof_id"], row["fingerprint"], remaining)
                    if receipt is Receipt.CONFLICT or (receipt is Receipt.ABSENT and row["accepted"]):
                        action = "quarantine"
                    elif receipt is Receipt.ACCEPTED:
                        action = {"verified": "verified", "pending": "accepted", "unavailable": "quarantine"}[coverage]
                    elif receipt is Receipt.ABSENT:
                        remaining = deadline - time.monotonic()
                        if remaining > 0:
                            status = await transport.send_proof(proof, remaining)
                            action = "accepted" if status is Receipt.ACCEPTED else "quarantine" if status is Receipt.CONFLICT else "reconcile"
                else:
                    status = await transport.send_proof(proof, remaining)
                    action = "accepted" if status is Receipt.ACCEPTED else "quarantine" if status is Receipt.CONFLICT else "reconcile"
        except (ValueError, TypeError, KeyError):
            action = "quarantine"
        except Exception:
            action = "reconcile"
        delay = retry_delay(row["attempts"], random.SystemRandom().random()) if action in {"accepted", "retry", "reconcile"} else 0
        with session_factory() as db:
            snapshot_sql.finish(db, row["proof_id"], row["claim_token"], action,
                                datetime.now(timezone.utc), delay_seconds=delay)
            db.commit()
    return len(rows)
