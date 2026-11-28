"""Independent read-only observation of both sides of the historical transfer."""
import asyncio
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from hashlib import sha256
from datetime import datetime, timezone
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text

from pymentor_analytics.adapters.postgres.student_windows_backfill import (
    FACTS, FENCES, GATE, JOURNAL, KEY, MEASURED, SCHEMA_READY, WORK, BackfillError, json_digest, stat_values,
)
from pymentor_analytics.adapters.postgres.student_windows_backfill_coverage import (
    CORE_QUERIES, CoreMeasure, CoreObservation, Measure, check_core_url, inspect_core,
)
from pymentor_analytics.adapters.postgres.student_windows_backfill_oracle import read_independent_stats
from pymentor_analytics.contracts.core_transfer_v1 import fingerprint, validate_event
from pymentor_analytics.contracts.v2 import AcceptedEvent, canonical_hash
from pymentor_analytics.contracts.windows_v1 import WindowKey, WindowStat, canonical_stats
from pymentor_analytics.modules.analytics.activity.bucket import BucketOverflow

WINDOWS = "analytics.student_activity_windows"
CONTROL = "analytics.student_activity_window_control"
TIME_GRID = "date_bin(interval '15 minutes',stamp,'2000-01-01T00:00:00Z') AT TIME ZONE 'Europe/Moscow'"
LIVE_FACT = f"NOT EXISTS (SELECT FROM {FENCES} f WHERE f.subject_ref=src.subject_ref AND f.deleted)"

CANDIDATES = text(f"""WITH measured AS (
    SELECT subject_ref AS student_id, ({TIME_GRID}) AS msk
    FROM {FACTS} src
    CROSS JOIN LATERAL (VALUES (src.started_at), (src.started_at+interval '59.999 seconds')) pos(stamp)
    WHERE ({MEASURED}) AND {LIVE_FACT}
), keys AS (
    SELECT student_id,msk::date AS activity_date,
           (extract(hour FROM msk)::int*4+extract(minute FROM msk)::int/15+1)::smallint AS slot_no
    FROM measured
    UNION SELECT {KEY} FROM {WINDOWS}
    UNION SELECT {KEY} FROM {CONTROL}
)
SELECT keys.*,w.stats,w.revision,work.result,c.revision AS control_revision,c.seq,
       f.deleted=TRUE AS deleted
FROM keys LEFT JOIN {WINDOWS} w USING ({KEY}) LEFT JOIN {WORK} work USING ({KEY})
LEFT JOIN {CONTROL} c USING ({KEY}) LEFT JOIN {FENCES} f ON f.subject_ref=keys.student_id
ORDER BY keys.student_id,keys.activity_date,keys.slot_no""")

KNOWN_NONWINDOW = {
    ("browser_v2", "course_opened"): "legacy_course_visit_zero_ms",
    ("core_transfer_v1", "legacy_activity_day"): "trusted_legacy_activity_day",
    ("core_transfer_v1", "lesson_progress"): "trusted_lesson_progress",
    ("core_transfer_v1", "task_progress"): "trusted_task_progress",
}
MEASURED_KINDS = {("browser_v2", "activity_segment"), ("browser_tool_v1", "tool_segment")}


@dataclass
class Aggregate:
    count: int = 0
    active_ms: int = 0
    _hash: object = field(default_factory=sha256)

    def add(self, item, active_ms=0):
        self.count += 1
        self.active_ms += active_ms
        self._hash.update(bytes.fromhex(json_digest(item)))

    def result(self):
        return dict(count=self.count, active_ms=self.active_ms, sha256=self._hash.hexdigest())


class WindowTotals:
    def __init__(self):
        self.keys = Aggregate()
        self.contexts = Aggregate()
        self.days = defaultdict(Aggregate)
        self.courses = defaultdict(Aggregate)

    def add(self, key, stats):
        coords = key.model_dump(mode="json")
        self.keys.add((coords, stat_values(stats)), sum(row.active_ms for row in stats))
        for row in stats:
            identity = coords, row.model_dump(mode="json")
            self.contexts.add(identity, row.active_ms)
            self.days[key.activity_date.isoformat()].add(identity, row.active_ms)
            self.courses[row.course_id or "(global)"].add(identity, row.active_ms)

    def result(self):
        return dict(keys=self.keys.result(), contexts=self.contexts.result(),
                    days={key: value.result() for key, value in sorted(self.days.items())},
                    courses={key: value.result() for key, value in sorted(self.courses.items())})


async def analytics_sources(session):
    classes = defaultdict(Aggregate)
    days = [None, None]
    facts = await session.stream(text(f"""SELECT subject_ref,producer_namespace,event_id,source_kind,
            COALESCE(accepted_event#>>'{{event,type}}',accepted_event->>'kind','') AS kind,
            EXISTS (SELECT FROM analytics.core_transfer_entities entity
              WHERE entity.producer_namespace=src.producer_namespace AND entity.event_id=src.event_id
                AND entity.subject_ref=src.subject_ref AND entity.canonical_hash=src.canonical_hash) AS core_entity,
            CASE WHEN started_at IS NOT NULL THEN (started_at AT TIME ZONE 'Europe/Moscow')::date
                 WHEN accepted_event->>'kind'='legacy_activity_day'
                    THEN (accepted_event#>>'{{payload,activity_date}}')::date
                 WHEN accepted_event->>'kind' IN ('lesson_progress','task_progress')
                    THEN ((accepted_event#>>'{{payload,completed_at}}')::timestamptz AT TIME ZONE 'Europe/Moscow')::date
            END AS day, canonical_hash, course_id,catalog_version,lesson_id,started_at,received_at,accepted_event
        FROM {FACTS} src WHERE {LIVE_FACT} ORDER BY producer_namespace,event_id"""))
    async for row in facts:
        name = "measured" if (row.source_kind, row.kind) in MEASURED_KINDS else KNOWN_NONWINDOW.get(
            (row.source_kind, row.kind), "unsupported_or_unconfirmed_" + row.source_kind)
        try:
            if row.source_kind == "core_transfer_v1":
                transfer = validate_event(row.accepted_event)
                if (not row.core_entity or UUID(transfer["event_id"]) != row.event_id
                        or UUID(transfer["subject_ref"]) != row.subject_ref
                        or fingerprint(transfer) != row.canonical_hash):
                    raise ValueError("Core identity diverges from its accepted receipt")
            elif row.source_kind in ("browser_v2", "browser_tool_v1"):
                browser = AcceptedEvent.model_validate_json(json.dumps(row.accepted_event))
                event = browser.event
                if (browser.producer_namespace, browser.subject_ref, event.event_id, event.course_id,
                    event.catalog_version, event.lesson_id, event.started_at, browser.received_at,
                    canonical_hash(browser)) != (
                        row.producer_namespace, row.subject_ref, row.event_id, row.course_id,
                        row.catalog_version, row.lesson_id, row.started_at, row.received_at, row.canonical_hash):
                    raise ValueError("browser identity diverges from the accepted fact")
        except (ValidationError, ValueError):
            name = "unsupported_or_unconfirmed_" + row.source_kind
        classes[name].add((row.subject_ref,row.producer_namespace,row.event_id,row.source_kind,row.kind,
                           row.core_entity,row.day,row.canonical_hash))
        if row.day is not None:
            days[0] = row.day if days[0] is None else min(days[0], row.day)
            days[1] = row.day if days[1] is None else max(days[1], row.day)
    for name, order in (("activity_credit_windows", "subject_ref,bucket_start,metric_version,credit_version"),
                        ("activity_credit_days", "subject_ref,local_day,metric_version,credit_version")):
        stats = classes[name]
        rows = await session.stream(text(f"""SELECT to_jsonb(src) AS value
            FROM analytics.{name} src WHERE {LIVE_FACT} ORDER BY {order}"""))
        async for row in rows:
            stats.add(row.value)
    return {name: value.result() for name, value in sorted(classes.items())}, days


def _normalized_timestamp(value):
    if value is None:
        return None
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    return stamp.astimezone(timezone.utc).isoformat()


def _semantic_digest(subject, kind, body):
    if kind == "legacy_activity_day":
        key = [str(body["activity_date"])]
        value = [_normalized_timestamp(body["visited_at"])]
    elif kind == "lesson_progress":
        key = [body["course_id"], body["lesson_id"]]
        value = [_normalized_timestamp(body["completed_at"])]
    else:
        key = [body["course_id"], body["task_id"]]
        value = [body["task_revision"], _normalized_timestamp(body["completed_at"])]
    return json_digest([str(subject), kind, key]), json_digest(value)


async def latest_transfer_observation(session):
    rows = await session.stream(text(f"""SELECT DISTINCT ON (e.subject_ref,e.entity_key,e.producer_namespace)
          src.accepted_event
        FROM analytics.core_transfer_entities e JOIN {FACTS} src
          USING (producer_namespace,event_id,subject_ref)
        WHERE src.source_kind='core_transfer_v1' AND e.canonical_hash=src.canonical_hash AND {LIVE_FACT}
        ORDER BY e.subject_ref,e.entity_key,e.producer_namespace,e.revision DESC"""))
    observed = {}
    conflicted = set()
    async for row in rows:
        try:
            event = validate_event(row.accepted_event)
        except ValueError:
            continue  # The complete activity source scan records the unsupported row.
        k, value = _semantic_digest(event["subject_ref"], event["kind"], event["payload"])
        if k in observed and observed[k] != value:
            conflicted.add(k)
        observed[k] = value
    return observed, len(conflicted)


async def verify(backfill, *, core_sources=None, core_reader=None, witness=None, require_witness=True, details=None):
    guard = backfill.store._guard()
    async with backfill.database.sessions.begin() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await guard(session)
        checkpoint = dict(await backfill._require_run(session, lock=False))
        snapshot = (await session.execute(text("SELECT pg_current_snapshot()::text"))).scalar_one()
        counts = (await session.execute(text(f"""SELECT
            (SELECT count(*) FROM {JOURNAL} WHERE position<=:cut) AS captured_pending,
            (SELECT count(*) FROM {JOURNAL} WHERE position>:cut) AS uncaptured_cut,
            (SELECT count(*) FROM {WORK} WHERE result IS NULL) AS keys_pending,
            (SELECT v2_closed FROM {GATE} WHERE singleton) AS v2_closed"""),
            {"cut": checkpoint["accepted_position"]})).mappings().one()
        captured = (await session.execute(SCHEMA_READY)).scalar_one()
        source, days = await analytics_sources(session)
        conflicting_transfers = 0
        if core_reader is not None:
            transfers, conflicting_transfers = await latest_transfer_observation(session)
            core_sources = await asyncio.to_thread(core_reader, transfers)
        expected = WindowTotals()
        actual = WindowTotals()
        revisions = Aggregate()
        comparable = Counter()
        reasons = Counter()
        mismatches = Aggregate()
        rows = await session.stream(CANDIDATES)
        async for row in rows.mappings():
            coords = {label: row[label] for label in ("student_id", "activity_date", "slot_no")}
            calculated = None
            key = None
            values = canonical_stats(tuple(WindowStat.model_validate(item) for item in (row["stats"] or [])))
            try:
                key = WindowKey(**coords)
                calculated = await read_independent_stats(session, key)
            except (BucketOverflow, ValidationError, ValueError):
                reason = "unsupported_interval"
            else:
                if calculated:
                    expected.add(key, calculated)
                if values:
                    actual.add(key, values)
                    revisions.add((coords, row["revision"], stat_values(values)))
                if row["deleted"]:
                    reason = "purged_residue"
                elif (row["revision"] is not None and row["revision"] != row["control_revision"]
                      or row["seq"] is not None and not values):
                    reason = "control_business_revision_or_payload"
                elif calculated == values:
                    if calculated:
                        comparable["equal"] += 1
                    elif row["control_revision"] is not None:
                        comparable["control_only_empty"] += 1
                    continue
                else:
                    reason = "different" if calculated and values else "missing" if calculated else "extra"
            comparable[reason] += 1
            why = row["result"] if row["result"] in (
                "live_overlap_unknown", "still_collecting", "unsupported_interval") else reason
            reasons[why] += 1
            mismatches.add((coords, None if calculated is None else stat_values(calculated), stat_values(values), why))
            if details is not None:
                details.write(json.dumps(
                    dict(key_sha256=json_digest(coords), reason=why,
                         expected_sha256=None if calculated is None else json_digest(stat_values(calculated)),
                         actual_sha256=json_digest(stat_values(values)), revision=row["revision"]),
                    ensure_ascii=False) + "\n")
        await guard(session)
    unknown_sources = {name: count["count"] for name, count in source.items()
                       if name.startswith("unsupported_or_unconfirmed_")}
    stop = []
    if not captured:
        stop.append("capture_unavailable")
    if not checkpoint["scan_complete"]:
        stop.append("base_scan_incomplete")
    if counts["captured_pending"] or counts["uncaptured_cut"] or counts["keys_pending"]:
        stop.append("work_pending_or_new_commits")
    if checkpoint["accepted_position"] != checkpoint["applied_position"]:
        stop.append("commit_cut_not_applied")
    if checkpoint["final_position"] is not None and (
        not counts["v2_closed"] or checkpoint["final_position"] > checkpoint["applied_position"]
    ):
        stop.append("final_v2_cut_unconfirmed")
    if mismatches.count:
        stop.append("window_union_mismatch")
    if unknown_sources:
        stop.append("source_class_unknown")
    observed_core = isinstance(core_sources, CoreObservation) and set(core_sources.sources) == set(CORE_QUERIES)
    if not observed_core:
        stop.append("direct_core_sources_unobserved_or_unverified")
    if require_witness and witness is None:
        stop.append("independent_coverage_witness_missing")
    if witness is not None and (witness.run_id != backfill.run_id or witness.t0 != backfill.t0):
        stop.append("coverage_witness_run_mismatch")
    comparison = core_sources.transfer_comparison if observed_core else None
    if require_witness and (comparison is None or core_reader is None):
        stop.append("direct_and_transferred_core_overlap_unobserved")
    if conflicting_transfers or (comparison is not None and comparison.get("mismatch", 0)):
        stop.append("confirmed_core_sources_disagree")
    coverage = None
    if witness is not None:
        mismatch = dict(core=sorted(
            name for name in CORE_QUERIES if not observed_core or core_sources.sources[name] != witness.core_sources[name]),
            analytics=sorted(name for name in set(source) | set(witness.analytics_sources)
                             if name not in source or name not in witness.analytics_sources
                             or Measure.model_validate(source[name]) != witness.analytics_sources[name]))
        if observed_core and core_sources.target != witness.core_target:
            stop.append("coverage_witness_core_target_mismatch")
        if any(mismatch.values()):
            stop.append("independent_coverage_values_differ")
        coverage = dict(evidence_sha256=witness.evidence_sha256, differences=mismatch)
    return dict(status="STOP" if stop else "PASS", stop_reasons=stop, checkpoint=checkpoint, coverage=coverage,
                verification_snapshot=snapshot, queue=dict(counts), msk_day_range=days,
                source_classes=source, direct_core_sources=core_sources.report() if observed_core else None,
                unknown_source_classes=unknown_sources,
                windows=dict(union=sum(comparable.values()), classes=dict(comparable), reasons=dict(reasons),
                             mismatches=mismatches.result(), expected=expected.result(), actual=actual.result(),
                             actual_revisions=revisions.result()))


def observe_core(psycopg, dsn, target, transferred=None, *, require_tls=False):
    """Observe the individually pinned role and the declared Core set under one read-only snapshot."""
    source_dsn = check_core_url(dsn, target)
    result = {}
    comparisons = Counter() if transferred is not None else None
    unmatched = set(transferred) if transferred is not None else None
    direct_seen = set()
    kinds = {"user_activity_days": "legacy_activity_day", "user_lesson_progress": "lesson_progress",
             "user_task_progress": "task_progress"}
    secure = {"sslmode": "verify-full"} if require_tls else {}
    with psycopg.connect(source_dsn, connect_timeout=5, options="-c statement_timeout=10000", **secure) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        connection.execute("SET LOCAL search_path TO pg_catalog")
        connection.execute("SET LOCAL TIME ZONE 'UTC'")
        identity = inspect_core(connection, target, require_tls=require_tls)
        snapshot, timestamp = connection.execute("SELECT pg_current_snapshot()::text,transaction_timestamp()").fetchone()
        for table, query in CORE_QUERIES.items():
            aggregate = Aggregate()
            mapped = 0
            with connection.cursor(name="window_backfill_source_scan") as cursor:
                cursor.execute(query)
                for row in cursor:
                    aggregate.add(row)
                    if row[-1] is None:
                        if comparisons is not None:
                            comparisons["direct_unmapped"] += 1
                        continue
                    mapped += 1
                    if comparisons is None:
                        continue
                    keys = {
                        "user_activity_days": ("activity_date", "visited_at"),
                        "user_lesson_progress": ("course_id", "lesson_id", "completed_at"),
                        "user_task_progress": ("course_id", "task_id", "task_revision", "completed_at"),
                    }[table]
                    key, value = _semantic_digest(row[-1], kinds[table], dict(zip(keys, row[1:-1], strict=True)))
                    if key in transferred:
                        unmatched.discard(key)
                        comparisons["mismatch" if key in direct_seen or transferred[key] != value else "matched"] += 1
                        direct_seen.add(key)
                    else:
                        comparisons["direct_only"] += 1
            result[table] = CoreMeasure(**aggregate.result(), mapped=mapped, unmapped=aggregate.count - mapped)
    if comparisons is not None:
        comparisons["analytics_only"] = len(unmatched)
    return CoreObservation(target, identity, snapshot, timestamp, result,
                           None if comparisons is None else dict(comparisons))
