"""Bounded maintenance scan for current lifecycle snapshots; no network I/O."""
from dataclasses import dataclass
import json

from sqlalchemy import text

from pymentor_core.adapters.sql.analytics_delivery_ops.integration import guard_lifecycle, lifecycle, require_write_gate, task_enabled, write_mode
from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_export import stage_subject


@dataclass(frozen=True)
class ScanResult:
    cursor: int
    scanned: int
    staged: int
    blocked: int


def scan_page(sessions, cursor, *, page_size=20, stage_limit=2):
    """Scan a bounded keyset page; never skip unprocessed candidates at the limit.

    A new lifecycle is provisioned under the existing user lock. For an existing
    lifecycle stage_subject rechecks that same generation under lock. Failed
    subjects are revisited on the next full sweep; no identity is logged.
    """
    if type(cursor) is not int or cursor < 0 or not 1 <= page_size <= 100 or not 1 <= stage_limit <= 10:
        raise ValueError("invalid publisher scan bounds")
    with sessions() as db:
        rows = db.execute(text("""SELECT u.id, a.lifecycle_id, p.status, p.attempts, p.payload,
            EXISTS (SELECT 1 FROM analytics_transfer_snapshot_proofs q
                    WHERE q.subject_ref=a.lifecycle_id AND q.status='quarantined')
            OR EXISTS (SELECT 1 FROM analytics_transfer_outbox o
                       WHERE o.subject_ref=a.lifecycle_id AND o.status='quarantined') AS conflict,
            EXISTS (SELECT 1 FROM analytics_transfer_snapshot_proofs q
                    WHERE q.subject_ref=a.lifecycle_id AND q.status!='verified') AS waiting
            FROM users u LEFT JOIN account_lifecycles a ON a.user_id=u.id
            LEFT JOIN analytics_transfer_revisions r ON r.subject_ref=a.lifecycle_id
            LEFT JOIN analytics_transfer_snapshot_proofs p
              ON p.subject_ref=a.lifecycle_id AND p.cut_revision=COALESCE(r.revision,0)
            WHERE u.id>:cursor ORDER BY u.id LIMIT :limit"""),
            {"cursor": cursor, "limit": page_size}).all()
    if not rows:
        return ScanResult(0, 0, 0, 0)
    last, scanned, staged, blocked, attempted = cursor, 0, 0, 0, 0
    for uid, ref, status, attempts, payload, conflict, waiting in rows:
        if task_enabled() and status == "verified" and json.loads(payload)["schema_version"] == 1:
            status = None
        if status is None and not conflict and not waiting and attempted >= stage_limit:
            break
        last, scanned = uid, scanned + 1
        if conflict:
            blocked += 1
            continue
        if status is not None:
            blocked += int(status == "quarantined" or (status != "verified" and attempts >= 30))
            continue
        if waiting:
            continue
        attempted += 1
        try:
            with sessions() as db:
                # Lock in the same gate -> user order used by capture and cutover.
                # The page read is only a hint: another publisher may have staged
                # a proof since then, so recheck after acquiring both locks.
                require_write_gate(db, write_mode())
                current_ref = str(lifecycle(db, uid)) if ref is None else ref
                guard_lifecycle(db, uid, current_ref)
                unsafe = db.execute(text("""SELECT
                    EXISTS (SELECT 1 FROM analytics_transfer_snapshot_proofs
                            WHERE subject_ref=:ref AND status!='verified') OR
                    EXISTS (SELECT 1 FROM analytics_transfer_outbox
                            WHERE subject_ref=:ref AND status='quarantined')"""),
                    {"ref": current_ref}).scalar_one()
                if unsafe:
                    continue
                stage_subject(db, uid, current_ref, deadline_seconds=3)
                db.commit()
            staged += 1
        except Exception:
            # A lifecycle may have been replaced/deleted after the page read, or
            # a guard may fail. The next sweep retries from authoritative state.
            blocked += 1
    return ScanResult(last, scanned, staged, blocked)
