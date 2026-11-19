"""An explicit bounded worker pass; no thread, scheduler or network on import."""
from datetime import datetime, timezone
import json
import random
import time

from pymentor_core.adapters.sql.analytics_delivery import claim, finish, mark_sending
from pymentor_core.modules.analytics_delivery.errors import AuthorityConflict
from pymentor_core.modules.analytics_delivery.relay import Receipt, deliver, retry_delay


async def run_once(session_factory, transport, *, authority=None, batch_size=20,
                   lease_seconds=30, max_attempts=20, deadline_seconds=5):
    if not 0 < deadline_seconds <= 10 or lease_seconds <= deadline_seconds:
        raise ValueError("invalid worker deadline")
    deadline = time.monotonic() + deadline_seconds
    now = datetime.now(timezone.utc)
    with session_factory() as db:
        rows = claim(db, now, limit=batch_size, lease_seconds=lease_seconds, max_attempts=max_attempts)
        db.commit()
    for row in rows:
        event = json.loads(row["payload"])
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            with session_factory() as db:
                finish(db, row["event_id"], row["claim_token"],
                       "reconcile" if row["uncertain"] else "retry", datetime.now(timezone.utc))
                db.commit()
            continue
        with session_factory() as db:
            mark_sending(db, row["event_id"], row["claim_token"])
            db.commit()
        remaining = deadline - time.monotonic()
        action = "reconcile"
        try:
            if remaining <= 0:
                pass
            elif event["kind"] == "subject_deleted":
                # Journal append and projection purge are different durability boundaries.
                seq = await authority.append(event, row["created_at"], timeout_seconds=remaining) if authority else None
                remaining = deadline - time.monotonic()
                if seq is not None and remaining > 0:
                    status = await transport.deletion_status(event, remaining)
                    if status is Receipt.ACCEPTED:
                        action = "ack"
                    elif status is Receipt.CONFLICT:
                        action = "quarantine"
            else:
                action = (await deliver(transport, event, row["fingerprint"], uncertain=row["uncertain"],
                                        deadline_seconds=remaining)).action
        except AuthorityConflict:
            action = "quarantine"
        except Exception:
            # mark_sending has committed: preserve unknown state, never claim failure.
            action = "reconcile"
        delay = retry_delay(row["attempts"], random.SystemRandom().random()) if action in {"retry", "reconcile"} else 0
        with session_factory() as db:
            finish(db, row["event_id"], row["claim_token"], action,
                   datetime.now(timezone.utc), delay_seconds=delay)
            db.commit()
    return len(rows)
