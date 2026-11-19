"""Bounded single-subject source snapshot and proof staging, without network I/O."""
from datetime import datetime, timezone
import json
import os
import time
from uuid import UUID

from sqlalchemy import func, insert, select

from pymentor_core.adapters.sql import models
from pymentor_core.adapters.sql.analytics_delivery import activity_state, checkpoint, entity_key, next_revision, outbox, revisions
from pymentor_core.modules.analytics_delivery.contracts import activity, canonical, fingerprint, progress, task_progress, validate
from pymentor_core.adapters.sql.analytics_delivery_ops.integration import enabled, task_enabled, ensure_mirror_ready, guard_lifecycle, require_write_gate, write_mode
from pymentor_core.modules.analytics_delivery.snapshot_contract import MAX_DAYS, MAX_KEYS, MAX_PROGRESS, make_proof, proof_fingerprint, validate_proof
from pymentor_core.adapters.sql.analytics_delivery_ops.snapshot_sql import barriers, proofs


from pymentor_core.modules.analytics_delivery.snapshot_export import proof_enabled, SnapshotDeferred, SnapshotConflict


def _utc(value):
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def stage_subject(db, user_id: int, expected_ref: str, *, deadline_seconds: float = 10, clock=time.monotonic) -> dict:
    """Caller commits immediately; failure rolls all proof and rev0 inserts back.

    All old noncapturing writers must already be drained. The Core gate and user
    lock serialize this consistent source snapshot with capture-enabled writers.
    No network is performed, and all source facts must already be captured.
    """
    if not enabled() or not proof_enabled() or not 0 < deadline_seconds <= 30:
        raise ValueError("snapshot proof unavailable")
    deadline = clock() + deadline_seconds
    def check():
        if clock() >= deadline:
            raise TimeoutError("snapshot deadline exceeded")
    with db.begin_nested():
        mode = write_mode()
        require_write_gate(db, mode)
        if mode == "mirror":
            ensure_mirror_ready(db)
        sealed = db.execute(select(barriers.c.stream, barriers.c.high_watermark,
            checkpoint.c.high_watermark).join(checkpoint, checkpoint.c.stream == barriers.c.stream)).all()
        required = {"activity", "progress"} | ({"tasks"} if task_enabled() else set())
        relevant = [row for row in sealed if row[0] in required]
        if {row[0] for row in relevant} != required or any(row[1] != row[2] for row in relevant):
            raise ValueError("backfill is not sealed")
        subject = guard_lifecycle(db, user_id, expected_ref)
        cut = db.execute(select(revisions.c.revision).where(revisions.c.subject_ref == str(subject))).scalar_one_or_none() or 0
        if task_enabled():
            old = db.execute(select(proofs.c.payload, proofs.c.status).where(
                proofs.c.subject_ref == str(subject), proofs.c.cut_revision == cut)).first()
            if old is not None and json.loads(old.payload)["schema_version"] == 1:
                if old.status != "verified":
                    raise SnapshotDeferred("previous proof awaits verification")
                cut = next_revision(db, subject)
        # All callers, including the manual staging command, use this check
        # while holding gate -> user locks in the same transaction as the insert.
        if (db.execute(select(proofs.c.proof_id).where(proofs.c.subject_ref == str(subject),
                proofs.c.status == "quarantined").limit(1)).first() or
            db.execute(select(outbox.c.event_id).where(outbox.c.subject_ref == str(subject),
                outbox.c.status == "quarantined").limit(1)).first()):
            raise SnapshotConflict("snapshot subject has quarantined delivery")
        if db.execute(select(proofs.c.proof_id).where(proofs.c.subject_ref == str(subject),
                proofs.c.cut_revision != cut, proofs.c.status != "verified").limit(1)).first():
            raise SnapshotDeferred("snapshot subject has unverified coverage")
        check()
        legacy = db.query(models.UserActivityDay).filter_by(user_id=user_id).order_by(models.UserActivityDay.activity_date).limit(MAX_DAYS + 1).all()
        state = db.execute(select(activity_state).where(activity_state.c.subject_ref == str(subject)).limit(MAX_DAYS + 1)).mappings().all()
        if len(legacy) > MAX_DAYS or len(state) > MAX_DAYS:
            raise ValueError("snapshot day limit exceeded")
        days = {item.activity_date: item.visited_at for item in legacy}
        dirty_days = set()
        for row in state:
            day, value = row["activity_date"], row["visited_at"]
            previous = days.get(day)
            if (day not in days and not row["needs_core_replay"]) or (previous is not None and _utc(previous) != _utc(value)):
                raise ValueError("activity replay state conflicts with Core")
            if not row["needs_core_replay"] and _utc(previous) != _utc(value):
                raise ValueError("reconciled activity state conflicts with Core")
            days[day] = value
            if row["needs_core_replay"]:
                dirty_days.add(day)
        if len(days) > MAX_DAYS:
            raise ValueError("snapshot day limit exceeded")
        lessons = (db.query(models.UserLessonProgress).filter_by(user_id=user_id)
                   .order_by(models.UserLessonProgress.id).limit(MAX_PROGRESS + 1).all())
        if len(lessons) > MAX_PROGRESS:
            raise ValueError("snapshot progress limit exceeded")
        tasks = (db.query(models.UserTaskProgress).filter_by(user_id=user_id)
                 .order_by(models.UserTaskProgress.id).limit(MAX_PROGRESS + 1).all()) if task_enabled() else []
        if len(lessons) + len(tasks) > MAX_PROGRESS:
            raise ValueError("snapshot progress limit exceeded")
        events = [activity(subject, day, value, revision=0) for day, value in days.items()]
        events.extend(progress(subject, item.course_id, item.lesson_id, item.completed_at, revision=0) for item in lessons)
        events.extend(task_progress(subject, item.course_id, item.task_id, item.task_revision, item.completed_at, revision=0) for item in tasks)
        check()
        previous_proof = db.execute(select(proofs).where(proofs.c.subject_ref == str(subject), proofs.c.cut_revision == cut)).mappings().one_or_none()
        ranked = select(outbox.c.entity_key, outbox.c.payload, outbox.c.fingerprint, outbox.c.revision,
            func.row_number().over(partition_by=outbox.c.entity_key, order_by=outbox.c.revision.desc()).label("rank"))\
            .where(outbox.c.subject_ref == str(subject), outbox.c.revision <= cut,
                   outbox.c.kind.in_(("legacy_activity_day", "lesson_progress", "task_progress") if task_enabled() else ("legacy_activity_day", "lesson_progress"))).subquery()
        latest_rows = db.execute(select(ranked.c.entity_key, ranked.c.payload, ranked.c.fingerprint, ranked.c.revision)
                                 .where(ranked.c.rank == 1).limit(MAX_KEYS + 1)).all()
        if len(latest_rows) > MAX_KEYS:
            raise ValueError("snapshot history key limit exceeded")
        latest = {}
        for key, raw, stored_fingerprint, stored_revision in latest_rows:
            current = json.loads(raw)
            validate(current)
            if (current["subject_ref"] != str(subject) or current["revision"] != stored_revision
                    or entity_key(current) != key or fingerprint(current) != stored_fingerprint):
                raise ValueError("invalid transfer history")
            latest[key] = current
        source_keys = set()
        captured = []
        for index, event in enumerate(events):
            if index % 64 == 0:
                check()
            key = entity_key(event)
            source_keys.add(key)
            prior = latest.get(key)
            if event["kind"] == "legacy_activity_day" and date_from_event(event) in dirty_days and (prior is None or prior["revision"] == 0):
                raise ValueError("dirty activity lacks captured live revision")
            if prior is None:
                raise ValueError("source fact lacks captured backfill or live event")
            if (os.environ.get("PYMENTOR_ANALYTICS_HISTORY_DATES_ONLY") == "1"
                    and event["kind"] == "legacy_activity_day" and prior["kind"] == event["kind"]
                    and prior["revision"] == 0 and prior["payload"]["visited_at"] is None):
                # Backfilled history identifies a day, independent of its old timestamp.
                event = activity(subject, date_from_event(event), None, revision=0)
            if prior["kind"] != event["kind"] or prior["payload"] != event["payload"]:
                if previous_proof is not None:
                    raise ValueError("source changed without revision")
                raise ValueError("source fact changed without matching captured revision")
            captured.append(event)
        for key, prior in latest.items():
            if key not in source_keys and not (prior["kind"] in ("lesson_progress", "task_progress") and prior["payload"]["completed_at"] is None):
                raise ValueError("source removal lacks captured tombstone")
        check()
        now = datetime.now(timezone.utc)
        candidate = make_proof(subject, cut, captured, now, tasks=task_enabled())
        if previous_proof is not None:
            saved = json.loads(previous_proof["payload"])
            validate_proof(saved)
            if (proof_fingerprint(saved) != previous_proof["fingerprint"] or saved["subject_ref"] != str(subject)
                    or saved["cut_revision"] != cut or saved["schema_version"] != candidate["schema_version"]
                    or saved["entity_count"] != candidate["entity_count"] or saved["entities_digest"] != candidate["entities_digest"]):
                raise ValueError("source changed without revision")
            return saved
        db.execute(insert(proofs).values(proof_id=candidate["proof_id"], subject_ref=str(subject), cut_revision=cut,
            payload=canonical(candidate), fingerprint=proof_fingerprint(candidate), status="pending", accepted=False,
            uncertain=False, attempts=0, next_attempt_at=now))
        return candidate


def date_from_event(event):
    return datetime.fromisoformat(event["payload"]["activity_date"]).date()
