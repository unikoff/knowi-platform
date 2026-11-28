"""Join CORP-607 independent expectations, browser evidence and the read-only 606 report."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Literal
from uuid import UUID

from psycopg import Error as PostgresError
from sqlalchemy.exc import SQLAlchemyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import check_student_windows as diagnostic
from pymentor_analytics.contracts.privacy_v1 import PrivacyUnavailable
from pymentor_analytics.contracts.windows_v1 import WindowKey, WindowStat, canonical_stats, split_interval

DEFAULT_EXPECTED = Path(__file__).resolve().parents[1] / "tests/student_windows/fixtures/corp_607_expected.json"
STAT_FIELDS = {"tool_id", "course_id", "lesson_id", "active_ms"}
OUTCOMES = diagnostic.MISMATCH | {"matched", "old_only", "unknown"}


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    group: Literal["normal", "fault"]
    key: WindowKey
    expected: tuple[WindowStat, ...]
    outcome: str
    v2_complete: bool
    before_mismatch: bool | None = None
    require_reason: str | None = None
    require_browser_ack: bool = False
    require_browser_v2: bool = False


@dataclass(frozen=True, slots=True)
class BrowserWitness:
    stats: tuple[WindowStat, ...]
    revision: int
    source: str
    kind: Literal["browser", "http"] = "browser"


def key_from(raw, subject=None) -> WindowKey:
    return WindowKey(student_id=UUID(str(raw.get("student_id", subject))),
                     activity_date=date.fromisoformat(raw["activity_date"]), slot_no=raw["slot_no"])


def stats_from(raw) -> tuple[WindowStat, ...]:
    return canonical_stats(tuple(WindowStat(**item) for item in raw))


def model_cases(plan, members, *, date_shift=0, midnight_shift=0):
    if plan["fixture_version"] != 1 or plan["timezone"] != "Europe/Moscow":
        raise ValueError("incompatible CORP-607 expectation")
    live = plan["live_first"]
    actual_lesson = live["stats"][0]["lesson_id"]
    cases = {}
    for item in plan["expected_windows"]:
        name = item["subject"]
        if name not in members:
            continue
        subject = UUID(plan["subjects"][name])
        if subject != members[name]:
            raise ValueError("model subject differs from the enrolled test lifecycle")
        shift = date_shift + (midnight_shift if name == "multi" and item["slot_no"] in {96, 1} else 0)
        day = date.fromisoformat(item["date"]) + timedelta(days=shift)
        key = WindowKey(student_id=subject, activity_date=day, slot_no=item["slot_no"])
        expected = []
        for context, amount in item["active_ms"].items():
            value = {**plan["contexts"][context], "active_ms": amount}
            if value["lesson_id"] == str(live["model_lesson_number"]):
                value["lesson_id"] = actual_lesson
            assert set(value) == STAT_FIELDS
            expected.append(value)
        if name == "first" and stats_from(live["stats"]) != stats_from(expected):
            raise ValueError("independent first-window expectations disagree")
        match = item["comparison"] == "match"
        expected_outcome = "matched" if match else "both_lost"
        if key in cases or item["comparison"] not in {"match", "unknown"}:
            raise ValueError("repeated or unsupported independent expectation")
        cases[key] = Case(name, "normal" if match else "fault", key, stats_from(expected),
                          expected_outcome, v2_complete=match, before_mismatch=False if match else True)
    return cases


def final_run_cases(plan, *, run, members, cohort_t0):
    if (plan["run_id"] != run or plan["timezone"] != "Europe/Moscow"
            or plan["declared_before_browser_activity"] is not True
            or datetime.fromisoformat(plan["cohort_t0"]) != cohort_t0):
        raise ValueError("final independent manifest does not bind the run and T0")
    contexts = plan["common_lesson"]
    cases = {}
    for name, student, day, slot, times, coverage in plan["cases"]:
        key = key_from({"student_id": student, "activity_date": day, "slot_no": slot})
        if key.student_id not in members:
            continue
        stats = stats_from([
            {"tool_id": tool, "course_id": None if tool == "catalog" else contexts["course_id"],
             "lesson_id": None if tool == "catalog" else contexts["lesson_id"], "active_ms": amount}
            for tool, amount in times.items()
        ])
        if key in cases:
            raise ValueError("final manifest repeats a key")
        if name == "both-lost" and coverage == "unknown/both channels lost":
            item = Case(name, "fault", key, stats, "both_lost", False, before_mismatch=True)
        elif name == "reload" and coverage == "recovery expected; v2 completeness determined independently":
            item = Case(name, "fault", key, stats, "unknown", False, require_browser_ack=True)
        elif coverage == "comparable":
            item = Case(name, "normal", key, stats, "matched", True, before_mismatch=False)
        else:
            raise ValueError("unknown case in the final independent manifest")
        cases[key] = item
    return cases


def add_cases(cases, plan, *, run, members):
    if plan["schema_version"] != 1 or plan["run_id"] != run:
        raise ValueError("supplemental independent plan must bind this run")
    for raw in plan["windows"]:
        key = key_from(raw["key"])
        outcome, group = raw["expected_outcome"], raw["group"]
        completeness = raw["v2_source_complete"]
        before_mismatch = raw.get("before_mismatch")
        require_browser_ack = raw.get("require_browser_ack", False)
        require_browser_v2 = raw.get("require_browser_v2", False)
        if key in cases or key.student_id not in members:
            raise ValueError("supplemental plan duplicates a key or uses an unenrolled lifecycle")
        if group not in {"normal", "fault"} or outcome not in OUTCOMES or type(completeness) is not bool:
            raise ValueError("invalid planned scenario")
        if (type(require_browser_ack) is not bool or type(require_browser_v2) is not bool
                or (before_mismatch is not None and type(before_mismatch) is not bool)):
            raise ValueError("invalid pre-repair expectation")
        cases[key] = Case(raw["scenario"], group, key, stats_from(raw["expected_stats"]), outcome,
                          completeness, before_mismatch, raw.get("require_reason"), require_browser_ack, require_browser_v2)


def managed_manifest(cases, *, cohort_t0):
    rows = []
    for key, case in cases.items():
        row = {"key": key.model_dump(mode="json")}
        if case.outcome == "old_only":
            row["old_web_only"] = True
        elif case.outcome != "unknown":
            row["expected_stats"] = [stat.model_dump(mode="json") for stat in case.expected]
            row["v2_source_complete"] = case.v2_complete
        rows.append(row)
    return {"protocol_version": 1, "cohort_t0": cohort_t0.isoformat(), "windows": rows}


def _ack_matches(ack, key, stats, revision):
    if ack.get("status") not in {"committed", "duplicate"}:
        return False
    confirmed = ack.get("confirmed") or {}
    return (
        ack.get("activity_date") == key.activity_date.isoformat()
        and ack.get("slot_no") == key.slot_no
        and confirmed.get("revision") == revision
        and stats_from(confirmed.get("stats", ())) == stats
    )


def browser_witnesses(document, *, run, source):
    if document.get("run_id", document.get("run")) != run:
        raise ValueError("browser evidence belongs to a different run")
    if document.get("error") or document.get("result", document.get("outcome")) == "FAIL":
        return {}
    browser_schema = document.get("versions", {}).get("analytics_schema")
    if browser_schema is not None and browser_schema != "a015":
        raise ValueError("browser evidence did not use a015")
    witnesses = {}
    records = document.get("windows")
    if records is not None:
        if document.get("schema_version") != 1:
            raise ValueError("unsupported normalized browser timeline")
        candidates = [(row["key"], row.get("before"), row.get("ack")) for row in records]
    else:
        subject = document.get("subject_ref")
        result = document.get("recovered") or document.get("commit") or {}
        body = result.get("body", {})
        initial = document.get("postgres_before_replay", result.get("postgres", []))
        candidates = []
        for row in initial:
            key = key_from(row, subject)
            ack = next((item for item in body.get("items", [])
                        if _ack_matches(item, key, stats_from(row["stats"]), row["revision"])), None)
            candidates.append((key.model_dump(mode="json"), row, ack))
    for coordinate, observed, ack in candidates:
        if observed is None or ack is None:
            continue
        key = key_from(coordinate)
        if "student_id" in observed and UUID(observed["student_id"]) != key.student_id:
            raise ValueError("PostgreSQL observation belongs to a different lifecycle")
        candidate = BrowserWitness(stats_from(observed["stats"]), observed["revision"], source)
        if type(candidate.revision) is not int or candidate.revision < 1 or not _ack_matches(
            ack, key, candidate.stats, candidate.revision,
        ):
            raise ValueError("browser acknowledgement differs from the recorded PostgreSQL row")
        if key in witnesses and witnesses[key] != candidate:
            raise ValueError("conflicting browser observations of an initial window")
        witnesses[key] = candidate
    return witnesses


def browser_acks(document, *, source):
    if document.get("error") or document.get("result", document.get("outcome")) == "FAIL":
        return {}
    receipts = {}
    for item in document.get("windows", []):
        candidate = item.get("ack")
        if candidate is not None:
            key = key_from(item["key"])
            receipts.setdefault(key, []).append((source, candidate))
    return receipts


def delivery_witnesses(document, *, run, source):
    if (document.get("instance_id") != run or document.get("result") != "PASS"
            or document.get("worker_enabled") is not False or document["databases"]["analytics"]["revision"] != "a015"):
        raise ValueError("delivery evidence must bind the successful a015 run with the worker off")
    stamp = document["source"]["sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", stamp):
        raise ValueError("delivery evidence requires a versioned Core and Collector build")
    lost = document["after_commit_before_retry"]
    retry = document["exact_retry"]
    if (lost["fault"]["after_commit"] is not True or lost["socket"]["peer_disconnected"] is not True
            or lost["second"] is not None or retry["status"] != 200
            or retry["body_sha256"] != document["request_body_sha256"]):
        raise ValueError("delivery evidence has no observed commit and exact retry after the lost ACK")
    witnesses = {}
    for coordinate, persisted, ack, status in zip(
        document["input_windows"], document["postgres_after_retry"], retry["reply"]["items"],
        ("duplicate", "committed"), strict=True,
    ):
        key = key_from(coordinate, document["subject_ref"])
        stats, revision = stats_from(persisted["stats"]), persisted["revision"]
        if (ack["status"] != status or ack.get("payload_hash") != persisted["payload_hash"]
                or not _ack_matches(ack, key, stats, revision)):
            raise ValueError("retry ACK differs from the independently read committed state")
        if status == "duplicate" and (lost["first"]["revision"] != revision or stats_from(lost["first"]["stats"]) != stats):
            raise ValueError("retry changed the initially committed state")
        witnesses[key] = BrowserWitness(stats, revision, source, "http")
    return witnesses, stamp


def raw_v2_windows(document):
    """Keep correlated committed v2 ACKs even when a later browser step failed."""
    events = document.get("v2_events", document.get("v2_confirmed"))
    subject = document.get("subject_ref", document.get("student_id"))
    if events is None and document.get("requests"):
        events = []
        for request in document["requests"]:
            if request["path"] != "/api/analytics/events/batch" or request.get("status") != 200:
                continue
            receipts = {item["event_id"]: item for item in request.get("response", {}).get("items", [])
                        if item["status"] in {"accepted", "duplicate"}}
            events.extend({"event": event, "ack": receipts[event["event_id"]]}
                          for event in request.get("body", {}).get("events", []) if event["event_id"] in receipts)
    if events is None and isinstance(document.get("v2"), list):
        events = [{"event": {"event_id": item["event_id"], "started_at": item["start"], "ended_at": item["end"]},
                   "ack": item.get("ack", {})} for item in document["v2"] if item.get("status") == 200]
    if events is None:
        receipts = {item["event_id"] for batch in document.get("v2_acks", []) if batch.get("status") == 200
                    for item in batch.get("body", {}).get("items", []) if item["status"] in {"accepted", "duplicate"}}
        events = [{"event": event, "ack": {"status": "accepted"}} for event in document.get("events", [])
                  if event["event_id"] in receipts]
    boundary = document.get("account_boundary", {})
    if boundary:
        if (boundary.get("same_partition") is not True or boundary.get("logout", {}).get("guest_status") != 401
                or boundary["a"]["student_id"] != subject or boundary["a"]["student_id"] == boundary["b"]["student_id"]):
            raise ValueError("account-switch record does not prove separate authenticated lifecycles")
    result = {}
    for entry in events:
        ack = entry.get("ack", {})
        if ack.get("status") not in {"accepted", "duplicate"}:
            continue
        event = entry.get("event", entry)
        phase = str(entry.get("phase", "")).lower()
        phase_subject = boundary[phase]["student_id"] if phase in {"a", "b"} and boundary else subject
        student = UUID(entry.get("subject_ref") or entry.get("student_id") or phase_subject)
        start = datetime.fromisoformat(event["started_at"])
        end = datetime.fromisoformat(event["ended_at"])
        for slot, active_ms in split_interval(start, end):
            key = WindowKey(student_id=student, **slot.model_dump())
            item = {"event_id": event["event_id"], "started_at": start.isoformat(),
                    "ended_at": end.isoformat(), "active_ms_in_slot": active_ms}
            if item not in result.setdefault(key, []):
                result[key].append(item)
    return result


SCHEMA_ROOT = "services/analytics/migrations/"
WINDOW_MIGRATION = SCHEMA_ROOT + "versions/a015_student_activity_windows.py"


def frozen_candidate_schema(doc, builds_path, source_path, freeze_path, repo):
    """Verify the migration component and the a015 file as separate scopes."""
    build_digest = sha256(builds_path.read_bytes()).hexdigest()
    source_digest = sha256(source_path.read_bytes()).hexdigest()
    if (doc.get("candidate_builds_sha256") != build_digest
            or doc.get("candidate_source_sha256") != source_digest):
        raise ValueError("candidate inputs do not bind the browser build")

    builds = read_json(builds_path)
    component = read_json(source_path)["components"]["schema"]
    frozen_entries = [
        entry for entry in read_json(freeze_path) if entry["path"].startswith(SCHEMA_ROOT)
    ]
    frozen = {entry["path"]: entry["sha256"] for entry in frozen_entries}
    listed = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", SCHEMA_ROOT], cwd=repo,
    ).decode("utf-8").split(chr(0))
    migration_paths = {path for path in listed if path and (repo / path).is_file()}
    if (len(frozen_entries) != len(frozen) or len(frozen) != component["files"]
            or WINDOW_MIGRATION not in frozen or migration_paths != set(frozen)):
        raise ValueError("candidate schema tree has a different file scope")
    tree = sha256()
    for path, expected in sorted(frozen.items()):
        digest = sha256((repo / path).read_bytes())
        if digest.hexdigest() != expected:
            raise ValueError("local migration differs from frozen file")
        tree.update(path.encode("utf-8") + bytes(1) + digest.digest())
    tree_digest = tree.hexdigest()
    if any(value != tree_digest for value in (doc.get("schema_tree_sha256"), builds["schema"], component["sha256"])):
        raise ValueError("schema tree digest differs from the admitted candidate")
    migration_digest = frozen[WINDOW_MIGRATION]
    if "schema_sha256" in doc and doc["schema_sha256"] != migration_digest:
        raise ValueError("a015 file digest differs from the admitted migration")
    return {"schema_tree_sha256": tree_digest, "schema_tree_files": len(frozen),
            "a015_file_sha256": migration_digest, "freeze_sha256": sha256(freeze_path.read_bytes()).hexdigest()}


def version_digest(path, run, timelines, *, schema_tree_verified=False):
    doc = read_json(path)
    digest = sha256(path.read_bytes()).hexdigest()
    if "components" in doc or "schema" in doc:
        if "components" in doc:
            required = ("web", "core", "analytics_runtime_including_collector_and_worker", "schema")
            hashes = tuple(doc["components"].get(name, {}).get("sha256", "") for name in required)
        else:
            if doc.get("run") != run or doc.get("schema") != "a015":
                raise ValueError("runtime build does not bind a015 to this run")
            if "schema_tree_sha256" in doc and not schema_tree_verified:
                raise ValueError("schema tree requires its pinned candidate inputs")
            schema_field = "schema_tree_sha256" if schema_tree_verified else "schema_sha256"
            hashes = tuple(doc.get(name, "") for name in ("web", "core", "collector", "worker", schema_field))
        if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes):
            raise ValueError("component build record is incomplete")
        observed = [document.get("versions_sha256", document.get("versions", {}).get("build_sha256"))
                    for document in timelines if document.get("run_id", document.get("run")) == run]
        return digest if observed and all(current == digest for current in observed) else None
    files = doc.get("files")
    required = ("apps/web/", "services/core-api/", "services/analytics/pymentor_analytics/",
                "services/analytics/migrations/versions/a015_")
    if (doc.get("run", doc.get("run_id")) != run or doc.get("analytics_schema") != "a015"
            or not isinstance(files, dict)
            or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in files.values())
            or any(not any(name.startswith(prefix) for name in files) for prefix in required)):
        raise ValueError("version record does not bind Web, Core, Analytics and a015 to this run")
    return digest


def lost_attempt_witnesses(document, cases):
    if document.get("scenario") != "both-lost" or document.get("result", document.get("outcome")) not in {"PASS", "PASS_BROWSER_CAPTURE"}:
        return set()
    subject = document["student_id"]
    legacy, windows = [], []
    for request in document["requests"]:
        path = request["path"]
        if path not in {"/api/analytics/events/batch", "/api/analytics/windows/batch"}:
            continue
        if not request.get("transport_failure") or request.get("status") == 200:
            return set()
        if path == "/api/analytics/events/batch":
            legacy.append(request["body"].get("events", ()))
        else:
            windows.extend(request["body"].get("windows", ()))
    witnessed = set()
    for row in windows:
        key = key_from(row, subject)
        case = cases.get(key)
        if case is None or case.outcome != "both_lost" or stats_from(row["stats"]) != case.expected:
            continue
        for events in legacy:
            windows_ms = 0
            for event in events:
                for coord, active_ms in split_interval(datetime.fromisoformat(event["started_at"]),
                                                      datetime.fromisoformat(event["ended_at"])):
                    if (coord.activity_date, coord.slot_no) == (key.activity_date, key.slot_no):
                        windows_ms += active_ms
            if windows_ms == sum(item.active_ms for item in case.expected):
                witnessed.add(key)
    return witnessed


def validate_runtime_attestation(attestation_path, corrected_path, original_path, *, run, build_digest):
    original, corrected = read_json(original_path), read_json(corrected_path)
    attestation = read_json(attestation_path)
    root = Path(__file__).resolve().parents[3]
    core = root / "services/core-api"
    if (build_digest is None or attestation["schema_version"] != 1 or attestation["run_id"] != run
            or Path(attestation["worktree"]).resolve() != root
            or Path(attestation["core_workdir"]).resolve() != core
            or attestation["prior_build_manifest_sha256"] != build_digest
            or attestation["prior_gateway_sha256"] != original["core"]
            or corrected["run"] != run or corrected["original_manifest_sha256"] != build_digest
            or type(attestation["live_pid"]["core"]) is not int or attestation["live_pid"]["core"] <= 0
            or datetime.fromisoformat(attestation["started_utc"]["core"]) > datetime.fromisoformat(attestation["observed_utc"])):
        raise ValueError("Core attestation is not bound to the captured build and run")
    for key in ("head", "web", "collector", "collector_factory", "worker", "schema", "schema_sha256"):
        if corrected[key] != original[key]:
            raise ValueError("Core attestation changed an unrelated component from the captured manifest")
    for field, file, proof in (
        ("core", "pymentor_core/app/analytics_windows_gateway.py", "corrected_gateway_sha256"),
        ("core_factory", "pymentor_core/app/factory.py", "corrected_core_factory_sha256"),
    ):
        current = sha256((core / file).read_bytes()).hexdigest()
        if corrected[field] != attestation[proof] or corrected[field] != current:
            raise ValueError("loaded Core proof differs from the recorded component digest")
    return {
        "sha256": sha256(attestation_path.read_bytes()).hexdigest(),
        "corrected_manifest_sha256": sha256(corrected_path.read_bytes()).hexdigest(),
        "core_gateway_sha256": corrected["core"], "core_factory_sha256": corrected["core_factory"],
    }


def summarize(cases, exported, witnesses, *, run, versions_sha256, v2_windows=None, v3_acks=None,
              loss_witnesses=None):
    actual = {}
    for row in exported["rows"]:
        key = key_from(row["key"])
        if key in actual:
            raise ValueError("606 reported a key twice")
        actual[key] = row
    if exported["mode"] != "read_only" or exported["denominator"] != len(actual):
        raise ValueError("606 report did not cover its declared read-only population")

    groups = {group: Counter() for group in ("normal", "fault", "unplanned")}
    output, open_keys = [], []
    for key in sorted(cases.keys() | actual.keys(), key=lambda value: (value.activity_date, value.slot_no, value.student_id)):
        case, row = cases.get(key), actual.get(key, {})
        totals = groups[case.group if case else "unplanned"]
        diagnostics = row.get("diagnostics") or {}
        comparison_reasons = diagnostics.get("coverage_reasons", [])
        comparison_outcome = row.get("outcome", "unobserved")
        outcome = comparison_outcome
        reasons = comparison_reasons
        if comparison_outcome == "both_lost" and (
            case is None or case.outcome != "both_lost" or (loss_witnesses is not None and key not in loss_witnesses)
        ):
            outcome = "unobserved"
            reasons = [reason for reason in comparison_reasons if reason != "absent_both"]
        before, before_from = None, None
        witness = witnesses.get(key)
        durable = row.get("durable_repair")
        final = row.get("before") or {}
        after_stats = stats_from(final["stats"]) if "stats" in final else None
        confirmation = diagnostics.get("v3_confirmed") or {}
        original = confirmation.get("server_epoch") == 1 and confirmation.get("payload_hash") and durable is None
        if witness is None and original and after_stats is not None:
            for source, ack in (v3_acks or {}).get(key, ()):
                if (ack.get("payload_hash") == confirmation["payload_hash"]
                        and _ack_matches(ack, key, after_stats, final.get("revision"))):
                    witness = BrowserWitness(after_stats, final["revision"], source)
                    break
        if (durable and durable.get("expected_hash") == row.get("expected_hash")
                and durable.get("before", {}).get("context_diff") is not None):
            before, before_from = bool(durable["before"]["context_diff"]), "durable_repair"
        elif witness is not None and case is not None:
            before, before_from = witness.stats != case.expected, f"{witness.kind}_ack_and_postgres"
        elif outcome == "both_lost":
            before, before_from = True, "absent_in_both_observed_sources"

        after = None if outcome in {"unobserved", "unknown", "old_only"} else outcome in diagnostic.MISMATCH
        if case and case.group == "normal" and outcome != "unobserved" and after_stats is not None and after_stats != case.expected:
            after = True
        coverage_unknown = outcome in {"unknown", "both_lost", "unobserved"}
        verified = outcome == "matched" and row.get("repairable") is True
        durable_event_ids = {item["event_id"] for item in diagnostics.get("v2_confirmed", ())}
        v2_browser_intersection = [item for item in (v2_windows or {}).get(key, ())
                                   if item["event_id"] in durable_event_ids]
        acceptable = case is not None and (
            outcome == case.outcome and (case.require_reason is None or case.require_reason in reasons)
        )
        if case and case.outcome == "old_only" and case.expected:
            acceptable &= stats_from(row.get("expected_stats") or ()) == case.expected
        if case and case.outcome == "unknown" and case.expected:
            acceptable &= after_stats == case.expected
        if case and case.require_browser_ack:
            acceptable &= witness is not None and witness.kind == "browser"
        if case and case.require_browser_v2:
            acceptable &= bool(v2_browser_intersection)
        if case and case.outcome == "both_lost" and loss_witnesses is not None:
            acceptable &= key in loss_witnesses
        if case and case.before_mismatch is not None:
            acceptable &= before == case.before_mismatch
        if case and case.group == "normal":
            acceptable &= witness is not None and verified and after is False
            if v2_windows is not None:
                acceptable &= bool(v2_browser_intersection)
        if not acceptable:
            open_keys.append(key.model_dump(mode="json"))

        totals["denominator"] += 1
        totals["verified"] += int(verified)
        totals["stored_verified_or_repaired"] += int(diagnostics.get("state") in {"verified", "repaired"})
        totals["old_only"] += int(outcome == "old_only")
        totals["unknown"] += int(coverage_unknown)
        totals["v3_only"] += int("v3_only" in reasons)
        totals["conflict"] += int(bool({"writer_conflict", "catalog_conflict"}.intersection(reasons)))
        totals["both_lost"] += int(outcome == "both_lost")
        totals["mismatch_before"] += int(before is True)
        totals["unknown_before"] += int(before is None)
        totals["mismatch_after"] += int(after is True)
        totals["unknown_after"] += int(after is None)
        totals["unobserved"] += int(outcome == "unobserved")
        output.append({
            "key": key.model_dump(mode="json"), "scenario": case.name if case else None,
            "group": case.group if case else "unplanned", "expected_outcome": case.outcome if case else None,
            "observed_outcome": outcome, "reasons": reasons, "verified": verified, "accepted": bool(acceptable),
            "comparison_outcome": comparison_outcome, "comparison_reasons": comparison_reasons,
            "expected_stats": [stat.model_dump(mode="json") for stat in case.expected] if case else None,
            "v3_current_stats": final.get("stats"), "v3_current_revision": final.get("revision"),
            "mismatch_before": before, "before_source": before_from,
            "browser_revision": witness.revision if witness and witness.kind == "browser" else None,
            "browser_evidence": witness.source if witness and witness.kind == "browser" else None,
            "http_revision": witness.revision if witness and witness.kind == "http" else None,
            "http_evidence": witness.source if witness and witness.kind == "http" else None,
            "v2_browser_and_database_events": v2_browser_intersection,
            "browser_loss_witnessed": key in (loss_witnesses or ()),
            "mismatch_after": after, "current_context_diff": final.get("context_diff"),
            "source_generation": diagnostics.get("source_generation"),
            "reconciled_generation": diagnostics.get("reconciled_generation"), "durable_repair": durable,
        })
    zero = {name: 0 for name in ("denominator", "verified", "stored_verified_or_repaired", "old_only",
            "unknown", "v3_only", "conflict", "both_lost", "mismatch_before", "unknown_before",
            "mismatch_after", "unknown_after", "unobserved")}
    counts = Counter(zero)
    for totals in groups.values():
        counts.update(totals)
    scoped = counts["denominator"] > 0 and not open_keys and not exported["truncated"]
    complete = scoped and versions_sha256 is not None
    return {
        "schema_version": 1, "run_id": run, "mode": "read_only", "verdict": "pass" if complete else "open",
        "scope_data_verdict": "pass" if scoped else "open",
        "versions_sha256": versions_sha256, "target": exported["target"], "truncated": exported["truncated"],
        "counts": dict(counts), "groups": {group: {**zero, **values} for group, values in groups.items()},
        "reason_counts": dict(Counter(reason for row in output for reason in row["reasons"])),
        "open_keys": open_keys, "windows": output,
    }


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _target(run):
    from tools import shadow_607_mixed_target as mixed
    from tools import shadow_607_target as target

    _, _, _, configs, data, identity, revision, learners = mixed.selected(run)
    if revision != "a015":
        raise ValueError("read-only compare requires a015 on the enrolled 607 target")
    with target.connect_owned(data, identity["database"]) as connection:
        with connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            prefix = connection.execute(
                "SELECT authority_id, applied_through FROM analytics.privacy_state WHERE singleton"
            ).fetchone()
            pending = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM analytics.privacy_receipts WHERE phase <> 'complete')"
            ).fetchone()[0]
    if prefix is None or pending:
        raise PrivacyUnavailable()
    return learners, identity["database"], prefix, configs["processor"]


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--run", required=True, type=UUID)
    cli.add_argument("--learner", action="append", required=True)
    cli.add_argument("--skip-model-learner", action="append", default=[],
                     help="use only predeclared supplemental cases for this selected learner")
    cli.add_argument("--cohort-t0", type=diagnostic._clock, required=True)
    cli.add_argument("--through", type=diagnostic._clock, required=True)
    cli.add_argument("--expected", type=Path, default=DEFAULT_EXPECTED)
    cli.add_argument("--case-manifest", type=Path, action="append", default=[])
    cli.add_argument("--date-shift-days", type=int, default=0)
    cli.add_argument("--midnight-shift-days", type=int, default=0,
                     help="additional predeclared date shift for the two multi midnight keys")
    cli.add_argument("--timeline", type=Path, action="append", default=[])
    cli.add_argument("--delivery-evidence", type=Path, help="607.B versioned HTTP+PostgreSQL evidence")
    cli.add_argument("--versions", type=Path)
    cli.add_argument("--candidate-schema-inputs", type=Path, nargs=3, metavar=("BUILDS", "SOURCE", "FREEZE"))
    cli.add_argument("--runtime-attestation", type=Path)
    cli.add_argument("--attested-build", type=Path)
    cli.add_argument("--output", type=Path)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    run = str(args.run)
    try:
        learners, target, prefix, processor = _target(run)
        members = {name: UUID(learners[name]["subject_ref"]) for name in args.learner}
        plan = read_json(args.expected)
        if any(name not in members for name in args.skip_model_learner):
            raise ValueError("skipped model learner must be selected")
        model_members = {name: student for name, student in members.items() if name not in args.skip_model_learner}
        if "cases" in plan:
            if args.date_shift_days or args.midnight_shift_days:
                raise ValueError("the final independent manifest already fixes its calendar dates")
            cases = final_run_cases(plan, run=run, members=model_members.values(), cohort_t0=args.cohort_t0)
        else:
            cases = model_cases(plan, model_members, date_shift=args.date_shift_days, midnight_shift=args.midnight_shift_days)
        for path in args.case_manifest:
            add_cases(cases, read_json(path), run=run, members=members.values())
        witnesses, v2_windows, v3_acks = {}, {}, {}
        timeline_digests, timeline_documents = {}, []
        for path in args.timeline:
            timeline_digests[path.name] = sha256(path.read_bytes()).hexdigest()
            document = read_json(path)
            timeline_documents.append(document)
            observed = browser_witnesses(document, run=run, source=path.name)
            for key, items in browser_acks(document, source=path.name).items():
                v3_acks.setdefault(key, []).extend(items)
            for key, receipts in raw_v2_windows(document).items():
                v2_windows.setdefault(key, []).extend(item for item in receipts if item not in v2_windows[key])
            for key, witness in observed.items():
                if key in witnesses and (witnesses[key].stats, witnesses[key].revision) != (witness.stats, witness.revision):
                    raise ValueError("browser evidence files disagree")
                witnesses[key] = witness
        versions_digest = None
        if args.delivery_evidence is not None:
            if args.timeline or args.versions:
                raise ValueError("HTTP delivery and browser comparisons use separate run reports")
            delivered, versions_digest = delivery_witnesses(read_json(args.delivery_evidence), run=run,
                                                              source=args.delivery_evidence.name)
            witnesses.update(delivered)
        frozen_schema = None
        if args.candidate_schema_inputs:
            if args.versions is None:
                raise ValueError("candidate schema provenance requires browser version evidence")
            frozen_schema = frozen_candidate_schema(
                read_json(args.versions), *args.candidate_schema_inputs, Path(__file__).resolve().parents[3],
            )
        if args.versions is not None:
            versions_digest = version_digest(args.versions, run, timeline_documents,
                                             schema_tree_verified=frozen_schema is not None)
        attestation = None
        if args.runtime_attestation or args.attested_build:
            if not (args.versions and args.attested_build and args.runtime_attestation):
                raise ValueError("both runtime attestation and corrected build are required")
            attestation = validate_runtime_attestation(
                args.runtime_attestation, args.attested_build, args.versions, run=run, build_digest=versions_digest,
            )

        with TemporaryDirectory(prefix="corp607-compare-") as temp:
            manifest = Path(temp) / "window-evidence.json"
            manifest.write_text(json.dumps(managed_manifest(cases, cohort_t0=args.cohort_t0)), encoding="utf-8")
            options = SimpleNamespace(target=target, student_id=tuple(members.values()), cohort_t0=args.cohort_t0,
                                      through=args.through, manifest=manifest, limit=1000, repair=False)
            report = asyncio.run(diagnostic.inspect_windows(
                options, processor.database_url.get_secret_value(), privacy_prefix=prefix, readonly_settings=processor,
            ))
        loss_witnesses = set().union(*(lost_attempt_witnesses(document, cases) for document in timeline_documents))
        report = summarize(cases, report, witnesses, run=run, versions_sha256=versions_digest,
                           v2_windows=v2_windows, v3_acks=v3_acks, loss_witnesses=loss_witnesses)
        report["runtime_attestation"] = attestation
        report["frozen_candidate_schema"] = frozen_schema
        report["input_sha256"] = {
            "independent_plan": sha256(args.expected.read_bytes()).hexdigest(),
            "supplemental_plans": {path.name: sha256(path.read_bytes()).hexdigest() for path in args.case_manifest},
            "browser_timelines": timeline_digests,
            "http_delivery": sha256(args.delivery_evidence.read_bytes()).hexdigest() if args.delivery_evidence else None,
        }
        encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as file:
                file.write(encoded)
        else:
            print(encoded, end="")
    except (OSError, ValueError, KeyError, TypeError, SQLAlchemyError, PostgresError, PrivacyUnavailable):
        print(json.dumps({"verdict": "unavailable", "error": "invalid inputs or unavailable synthetic target"}))
        return 2
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
